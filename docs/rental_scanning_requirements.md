# Rental Scanning — Prepared-Package & Set Picking
*Functional & Technical Requirements — Backend / Inventory + Barcode (v5, FINAL)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Module (proposed)** | rental_scanning — home for all future barcode/scanning work |
| **Depends on** | stock, stock_barcode (Enterprise), rental_set, rental_serial_log — all four are hard dependencies |
| **Channel** | Backend / Inventory + Barcode only — NOT visible in Sales/Website |
| **Languages** | Multilingual: English source + Dutch (nl) & French (fr) translations |
| **Author** | Pro-Designed.com |
| **Status** | Implemented (models, wizards, views, Barcode-app patch, 20 tests); migrated to Odoo 20 |
| **Date** | 2026-08-28 |

# 1. Purpose & Business Context
Warehouse staff prepare sets (combined products) into physical packages ahead of orders to speed up picking. Which prepared package serves which order is decided only at pick time ("late binding"), by manually scanning or selecting the package. This document specifies the behaviour, the technical background that motivated it, and — in full — WHY each option was chosen or rejected. Preparation (building packages) is done manually with standard Odoo tools.
# 2. Background & Root-Cause (why this work exists)
The feature grew out of a real defect: scanning / moving a physical package on a set delivery produced phantom quantities ("40 with demand 0", earlier "80"). Root cause, established by reading the records and the code:
- The picking reserved LOOSE stock, not the package. The reserved move-lines carried no package_id, while the package's quants were unreserved.
- The operation type had "Move Entire Packages" (show_entire_packs) = False, so the package was not treated as a movable unit.
- The barcode routine _processPackage, finding no move-lines already linked to the scanned package, falls through to "create a line per quant" — adding NEW lines for the package contents ON TOP of the loose reservation. Result: doubling / demand-0 overflow.
- Separately confirmed NOT to be the cause: the rental-set logic is not gated by operation type (it works on all outbound steps), and the zero-demand set header is barcode-safe (no move-line, so no barcode line, and it never blocks button_validate, where the rental_set _sanity_check override neutralises it).
**Conclusion: the fix is to reconcile a scanned package against EXISTING demand (never create overflow), and to build the "prepared package / set scan" flow on top of that. Hence rental_scanning.**
# 3. Confirmations (verified in code)
- Multi-step routes: rental-set grouping, the "[Set] Product" prefix, indentation and the zero-demand header work identically on Pick, Pack, Ship and single-step Delivery (keys off "outbound sale chain": sale_id set AND return_id not set — no per-operation-type branch).
- Set header disappears at the client: grouping/header show ONLY on the outbound sale chain; return pickings (return_id set) show NO header and NO prefix. So once goods reach the client and come back, the header is gone and items return as plain products.
# 4. Design Decisions — Options, Pros/Cons & Rationale
## 4.1 Binding & selection: manual late binding vs automatic reservation
**Option A — Manual late binding (scan/select at pick time)**
*Pros:*
- Closest to Odoo standard; the picker decides at the moment of picking.
- No need to know in advance which package serves which order.
- Robust to varying package contents; simple and predictable.
*Cons:*
- Relies on the picker to grab an appropriate package (no automatic optimisation).
**Option B — Automatically reserve a matching package**
*Pros:*
- Less picker decision-making; potential optimisation.
*Cons:*
- Heavy matching logic (which package matches which demand/set).
- Many edge cases: partial packages, competing orders, varying contents.
- Reservation-time binding contradicts "we do not know which package serves which order".
**Decision & rationale:** Option A. Option B is deferred and can be layered on later; the matching complexity is not justified now.
## 4.2 What defines the pick: actual package contents vs inferring a set from the container
**Actual package contents (read the real quants)**
*Pros:*
- Truthful when contents vary (40 glasses today, 40 plates tomorrow).
- Native — a package's quants (incl. lot/serial) are authoritative.
- No stale assumptions to maintain.
*Cons:*
- Must read and reconcile live contents; a non-matching package needs handling (split/reject).
**Infer contents/"the set" from the container or its crate serial (old "Approach B")**
*Pros:*
- One scan would imply everything; less reading.
*Cons:*
- Wrong the moment contents change — the crate identity says nothing about what is inside.
- Brittle; would silently deliver the wrong things.
**Decision & rationale:** Reconcile by ACTUAL contents; never infer a set from a container or serial. This is the single most important correctness decision.
## 4.3 Returnable container modelling
**Serial-tracked PRODUCT (Eurobak 40)**
*Pros:*
- Native; counted like any product; per-crate delivery/return identity via the serial.
- The product already exists as a set component.
- No custom lifecycle code.
*Cons:*
- None material.
**A custom "returnable packaging" object**
*Pros:*
- Could model a bespoke lifecycle.
*Cons:*
- Reinvents what serial tracking already provides; more code and maintenance.
**Force package-ID = crate serial**
*Pros:*
- A single identifier for convenience.
*Cons:*
- Couples two independent objects; dedicates a "reusable" tote to one crate (contradicts the generic/reusable model); custom logic fighting Odoo's grain.
**Decision & rationale:** Model the returnable crate as a serial-tracked product. Do not couple package name to serial; the package already references the serial via its quants.
## 4.4 What happens to the package at customer delivery
**(i) Dissolve — goods delivered loose, label freed immediately**
*Pros:*
- Simple; native for reusable totes; no clutter at customer locations.
*Cons:*
- No container record at the customer.
**(ii) Travel with the goods — package goes to the customer**
*Pros:*
- Physical fidelity; "package at customer" visibility; return-as-a-unit.
*Cons:*
- Odoo traveling boxes are the "disposable" type — no native reuse lifecycle.
- Packages accumulate at customer locations (must be emptied on return).
- Redundant with the serial identity; needs manual reuse or custom code.
*Sub-options within (ii):*
- (ii-a) Disposable box that travels: native travel, but reuse of the label is manual.
- (ii-b) Reusable box + custom override to travel: reuse lifecycle AND travel, at the cost of custom code.
**(iii) Serial is the identity; package dissolves at delivery**
*Pros:*
- Minimal custom code.
- The serial-tracked crate itself travels and returns, so it IS the durable reusable label — "travels + reused" comes from the product, not a customised package.
- Per-crate identity via serial; native reusable behaviour; no clutter.
*Cons:*
- No live "package at customer" object — traceability is via the delivery record + serial.
- Container return is reconciled per serial + quantity, not as one package unit.
**Decision & rationale:** Option (iii). The serial-tracked crate gives travels-and-reused from the product side with almost no customisation. (ii-a) has no reuse lifecycle and clutters customer locations; (ii-b) adds custom code to make a reusable box travel — unnecessary given the serial already does the job.
## 4.5 Overflow handling (package holds more than demand)
**Hard reject the scan**
*Pros:*
- Strict and safe.
*Cons:*
- Blocks legitimate partial use; forces manual repackaging with no guidance.
**Silently split (consume up to demand, leave the rest)**
*Pros:*
- Convenient; no interruption.
*Cons:*
- Hides real physical work; risk of unaccounted items and confusion.
**Prompt the picker to split**
*Pros:*
- Explicit acknowledgement that opening a package is real work; flexible; elegant.
*Cons:*
- One extra interaction.
**Decision & rationale:** Prompt to split (PPB-06). If genuinely extra stock is needed, the picker adds a line manually (PPB-05) — additions stay explicit and auditable.
## 4.6 Operation-type scope
**Any operation type (outbound, internal, inbound)**
*Pros:*
- Reusable on inbound (e.g. regroup returned products into packages before moving to stock) and internal transfers; consistent behaviour everywhere.
*Cons:*
- None.
**Outbound only**
*Pros:*
- Slightly smaller surface.
*Cons:*
- Misses the inbound regroup use case that was explicitly requested.
**Decision & rationale:** Any operation type; not gated.
## 4.7 Module placement
**One general module: rental_scanning (depends on stock_barcode)**
*Pros:*
- Dependency hygiene — does not force Enterprise stock_barcode as a hard dependency on rental_set.
- One home for all future scanning work; the feature is generic (works for non-set pickings too).
*Cons:*
- None material.
**Put it inside rental_set**
*Pros:*
- Fewer modules.
*Cons:*
- Forces a hard Enterprise dependency onto rental_set; the feature is not set-specific.
**Many small modules**
*Pros:*
- Fine-grained install.
*Cons:*
- Proliferation; risk when two modules patch the SAME barcode JS method.
**Decision & rationale:** One module, rental_scanning. Split only when a genuinely different dependency footprint appears; keep same-JS-method patches together and compose via super().
## 4.8 Reusable-container identification
**Native reusable package types (package_use = "reusable")**
*Pros:*
- Scanning a reusable box adds its products, and the box is emptied/freed after use — both native.
*Cons:*
- Package types are internally oriented (they do not travel to the customer — which suits Option iii).
**Custom "reusable-label" metadata field on packages**
*Pros:*
- Bespoke reporting hooks.
*Cons:*
- Redundant with native reusable types + the set barcode; extra surface; keeps packages non-generic.
**Decision & rationale:** Reuse native reusable package types; drop the custom metadata field (packages stay generic).
## 4.9 Other confirmed choices
- Serial-scan is equivalent to package-scan (PPB-13): scanning a content serial resolves to the package holding it and picks that package's actual contents. If the serial is NOT in a package, it falls back to a standard single-serial scan (only that product is added). Chosen because it is contents-driven (truthful) and uses a native serial->package lookup; the alternative (serial drives a set) was rejected in 4.2.
- Set-scan (PPB-12) is a SEPARATE, definition-driven feature: it fills a set's expected components when no physical package exists. Kept separate from the container scan so physical truth and a template are never conflated.
- Multilingual (en + nl + fr): Belgian operation; warehouse staff use Dutch/French.
## 4.10 Decision summary

| # | Chosen | Rejected |
|---|---|---|
| D-01 | Manual late binding (A) | Auto-reserve (B) |
| D-02 | Reconcile actual contents | Infer set from container/serial |
| D-03 | Serial-tracked product | Custom returnable object / package-ID=serial |
| D-04 | Option (iii) package dissolves; serial=label | (i) dissolve-only / (ii-a) / (ii-b) travel |
| D-05 | Overflow -> ask to split | Hard reject / silent split |
| D-06 | Any operation type | Outbound only |
| D-07 | One module rental_scanning | Inside rental_set / many modules |
| D-08 | Native reusable package types | Custom reusable-label field |
| D-09 | Serial-scan = package-scan (loose serial = standard) | Serial drives a set |
| D-10 | Set-scan separate (definition-driven) | Merge with container scan |
| D-11 | Multilingual en/nl/fr | English only |

# 5. Standard Odoo Mechanisms Reviewed

| Mechanism | Relevance |
|---|---|
| Reusable package type | package_use="reusable": scanning adds contents; box emptied and reused. Basis for 4.8. |
| Box freed after use | _check_entire_pack attaches a DISPOSABLE box to the goods (travels) but leaves a REUSABLE box behind (stays, reused). Basis for 4.4. |
| Serial / lot tracking | A package's quants carry lot_id; delivery/return verified per serial/lot. Basis for 4.3/4.9. |
| Usable packages in barcode | _get_usable_packages loads reusable / location-less packages into the client. |
| Kits / phantom BoM | Explodes a product into components on delivery — same idea as our sets; no need to switch. |
| Product packaging (UoM) | "Sell in packs of N" as a UoM — different concept, not used. |
| Returnable container | No first-class object; standard practice is a tracked product (4.3). |

# 6. Container & Returnable-Packaging Model (final)
- The container is a generic stock.package; picking is reconciled against ACTUAL contents (never a set). Contents may include serial/lot-tracked items (the crate) and untracked items (glasses).
- The returnable crate is a serial-tracked PRODUCT (Eurobak 40): delivered, returned, re-packed next cycle, counted like any product. Its serial is the durable, reusable identity.
- Option (iii): at delivery the stock.package dissolves; the crate serial + glasses go to the client as products. The physical crate (the serial) provides "travels + reused"; no traveling-package customisation.
- Serial-scan equals package-scan: scanning the crate serial resolves to its current package and picks that package's actual contents. If the crate serial is NOT in a package, scanning it adds only that crate (standard single-serial behaviour) - no container/set expansion.
- Ad-hoc containers are supported alongside pre-prepared ones (native Put in Pack); same reconciliation.
*Accepted caveats: serial-scan only behaves as a "container" while the crate is actually packed; container-at-customer traceability is via the delivery record + serial (not a live package); glasses are fungible so their return is verified by count while the crate is verified per-serial.*
# 7. Functional Requirements

| ID | Title | Requirement |
|---|---|---|
| PPB-01 | Scan/assign | On any picking, scan a package barcode or select it via a backend action to assign it; native reusable types make scan-adds-contents work, with the rules below layered on. |
| PPB-02 | Location rule (all locations) | Eligible only if the package is in (a sublocation of) the picking source location — same as normal picking, for ALL locations. |
| PPB-03 | Reconcile actual contents | Fill matching open-demand move-lines from the package (stamp source package + lot/serial). Never create demand-0 lines; never infer a set. |
| PPB-04 | No silent overflow | Never exceed demand silently (fixes the demand-0/40/80 doubling). |
| PPB-05 | Exact-fit + manual add | Accepted when contents fit within remaining demand; extra stock is added by the picker MANUALLY (explicit, never auto-pulled). |
| PPB-06 | Overflow -> ask to split | More than needed -> prompt to split (yes = up to demand, keep remainder; no = reject). |
| PPB-07 | Partial allowed | Covers only part of demand -> accept, fill what it can, leave rest open (a backorder carries the remaining demand). |
| PPB-08 | Set header no-op | Zero-demand header never blocks validation. |
| PPB-09 | Any operation type | Outbound, internal AND inbound (e.g. regroup returns before stock). |
| PPB-10 | Container lifecycle (iii) | Package dissolves at delivery; the serial-tracked product is the reusable identity; return verifies crate per-serial and glasses by count. |
| PPB-11 | Backend parity | A backend action applies identical reconciliation/validation to the scan. |
| PPB-12 | Set barcode (definition-driven) | Scanning a set barcode fills the set's DEFINED components (PPB-05/06 rules); no physical package; does not pin serials. |
| PPB-13 | Serial-scan -> container (with fallback) | Scanning a content serial resolves to its current package and picks that package's ACTUAL contents (= package scan). If the serial's product is NOT currently in a package, it falls back to STANDARD behaviour: only that one product/serial is added, nothing else (no container/set expansion). |
| PPB-14 | Multilingual | All user-facing strings translatable; nl & fr ship with the en source. |

# 8. Reconciliation Algorithm (scan/assign)
1. Resolve the scanned barcode: package -> that package; content serial -> the package holding it (PPB-13) or, if the serial is NOT in any package, just that single product/serial (standard); set barcode -> the set's defined components (PPB-12). Verify PPB-02 location.
1. Read contents as product -> qty (actual contents, or set components), incl. lot/serial.
1. Compute remaining demand per product (open moves).
1. For each product: package_qty <= remaining_demand -> apply (fill, cap, stamp package+lot/serial); product not demanded -> reject with a clear message (add manually per PPB-05).
1. package_qty > remaining_demand -> prompt "split the package?" (PPB-06).
1. Leave the zero-demand header untouched; proceed to button_validate; partial demand -> backorder.
# 9. Acceptance Test Scenarios

| ID | Scenario | Expected result |
|---|---|---|
| T-01 | Exact match, single-step | Fills all moves, no overflow, validates. |
| T-02 | Exact match, multi-step | Works on each Pick->Pack->Ship step. |
| T-03 | Extra product | Rejected; picker may add a line manually. |
| T-04 | Quantity overflow | Picker prompted to split. |
| T-05 | Partial | Accepted; remainder open. |
| T-06 | Wrong location | Ineligible. |
| T-07 | Set header no-op | Never blocks validation. |
| T-08 | Inbound regroup | Scanning regroups products into a package on inbound/internal. |
| T-09 | Set barcode | Fills the set's defined components. |
| T-10 | Serial-scan -> container | Picks the crate's current package contents. |
| T-11 | Serial delivery/return | Serial recorded on delivery; return reconciles serial + glasses. |
| T-12 | Multilingual | Error and split-prompt strings translated in nl/fr. |
| T-13 | Loose serial (not packed) | Scanning a package-product serial that is NOT inside any package adds only that one product/serial (standard) - no container/set expansion (PPB-13). |
| T-14 | Partial delivery + backorder | A package (or set/serial scan) covers only PART of a delivery: validate creates a backorder with the remaining demand; the set header is recreated on the backorder; no overflow; scanning the rest on the backorder completes it. Verifies partial fulfilment, backorder demand carry-over, header re-creation and reconciliation together. |

*Note on existing coverage gaps (rationale for the new tests): today's rental_set suite has NO physical-package tests; its multi-step tests select the warehouse via search([],limit=1) (may miss the order's warehouse in a multi-warehouse DB) and contain silent early-returns that can pass without asserting. T-01..T-14 close these gaps.*
# 10. Module Architecture
- Single module rental_scanning, depending on stock, stock_barcode, rental_set and rental_serial_log (the last for the repair-scan warning).
- Reuse native reusable package types; the module adds strict-fit rules, the split prompt, serial-scan resolution (with loose-serial fallback), the set barcode, and set composition.
- Keep improvements that patch the SAME barcode JS method (e.g. _processPackage) in this one module and compose them in a single patch; always call super().
# 11. Technical Findings (appendix, for implementers)
- Barcode payload sends all move_ids, but the client builds display LINES from move_line_ids; a zero-demand header move has no move-line, so it renders no line.
- Validate uses button_validate (validateMethod), so the rental_set _sanity_check override applies and neutralises the zero-demand header.
- _processPackage: if no existing line is linked to the scanned package, it creates a line per quant -> the overflow source; rental_scanning must instead reconcile against existing demand.
- _moveEntirePackage() returns picking_type_entire_packs (the "Move Entire Packages" flag).
- _check_entire_pack: reusable boxes are not set as result_package (stay behind); disposable boxes become the result_package (travel).
- _get_usable_packages loads reusable / location-less packages into the client cache.
# 12. Scope Boundaries
**In scope:**
- Manual scan/assign of a package (or crate serial, or set barcode) on any operation.
- Strict-fit reconciliation with split prompt; serial/lot handling; loose-serial fallback; multilingual.
- All routes; set-header no-op; ad-hoc and pre-prepared containers; partial + backorder.
**Out of scope (with reason):**
- Automated kitting/preparation build — done manually to keep scope limited.
- Auto-reservation of a matching package (Option B) — deferred; heavy matching logic.
- Sales/Website/Kiosk visibility — warehouse-only concept.
- A new product for the package, or package-ID=serial coupling — rejected (4.3).
- A custom reusable-label metadata field — rejected (4.8); native types suffice.
- Making a package physically travel (Option ii) — rejected (4.4) in favour of the serial.

# 13. Addendum v6 — Remove/Unassign, Package Visibility, Put-in-Pack
Follows live testing on PRO/OUT/00004. Adds two requirements and clarifies the source-vs-result package model. Reconfirms Option (iii).
## 13.1 PPB-15 — Remove / unassign a scanned package
- A previously scanned package can be removed from a transfer: the picked move-lines sourced from that package are cleared, reverting that quantity to OPEN demand, so a different package can be scanned instead.
- No "replace" convenience (by decision): to swap, remove then scan the new package. This keeps the behaviour explicit.
- Backend: "Remove Scanned Package" action on the picking (visible only when a scanned package is present). Barcode scan-to-remove is Phase 2.
*Rationale: scanning is idempotent PER package, but a partial package (e.g. BAK02 = 35 of 40) leaves 5 open; scanning a full package (BAK01) afterwards would only top-up the 5 (mixed sources). Removing first gives a clean single-source pick. Rejected: an implicit "replace" (hidden, harder to reason about).*
## 13.2 PPB-16 — Package visibility & source-vs-result packages
- Applied SOURCE packages are shown on the picking via a "Scanned Packages" field (many2many tags). The Operations move list shows product/demand/qty; the per-line source package is only in the detailed move-lines — hence the picking-level field for at-a-glance visibility.
- Prepared packages (BAK01/02) are SOURCE packages you pick FROM (set as the move-line package_id).
- "Put the whole operation in one pack, then just scan that pack at checkout" = native Odoo Put-in-Pack, which builds ONE result/destination package; in a multi-step flow the next step scans that single package and the reconciliation fills it. Source scan and result Put-in-Pack are complementary.
- Option (iii) reconfirmed: at the FINAL customer delivery the pack dissolves (goods leave as products; the crate travels as a serial-tracked product). "One pack to scan" is therefore ideal for internal handoffs (Pick/Pack/Ship), not retained at the customer.
## 13.3 Additional acceptance tests
- T-15 Reserved picking: scanning works when the picking is already reserved (move.quantity == demand, picked=False) — regression from live testing.
- T-16 Idempotent re-scan: scanning the same package twice does not double.
- T-17 Remove: removing a scanned package reverts its quantity to open demand.
- T-18 Swap: remove a partial package, scan a full one -> single-source fill.

# 14. Addendum v7 — PPB-17 (retain through internal steps) + nested packs
Answers "can I scan the package again at the follow-up warehouse step?"
## 14.1 PPB-17 — retain the scanned package through internal steps
- When a scanned package fills a move whose DESTINATION is an internal location (Pick->Output, Pack->...), the scanned package is set as the RESULT (destination) package, so the goods travel inside it and it can be re-scanned at the next step.
- When the destination is the CUSTOMER (final delivery), no result package is set -> the pack dissolves and goods ship as products (Option iii reconfirmed: "dissolve at delivery" = at the customer, not internal handoffs).
- Side effect: at internal steps the native "Packages" column now shows the pack (it has a result package); at the customer delivery it stays empty.
## 14.2 Nested / general packs (Path A) — supported natively
- Odoo supports package-in-package (parent_package_id, contained_quant_ids aggregates all nested children). Our scan reads contained_quant_ids, so scanning a general/outer pack fulfils demand from nested packages (e.g. BAK01 inside a GENERAL pack).
- Each line is stamped with the quant's ACTUAL (innermost) source package, not the outer one, so traceability stays correct.
- Put-in-Pack sets a result package that travels downstream, so consolidating into a general pack and scanning it at the next step also works natively.
## 14.3 Tests added
- T-19 nested general pack: scanning an outer pack fills from nested BAK01.
- PPB-17 internal: scanning on an internal-destination transfer retains the package as result_package.
- PPB-17 customer: scanning on a customer delivery leaves no result package (dissolve).

# 15. Addendum v8 — Phase 2: Barcode app integration
Routes a scanned package in the Barcode app through the same server reconciliation as the backend, replacing the native line-per-quant overflow.
## 15.1 Implemented
- OWL patch of BarcodePickingModel._processBarcode AND _processPackage: when a package WITH contents is scanned on a stock.picking, it calls stock.picking.rental_scanning_scan (strict-fit, idempotent, PPB-17) and reloads the client (trigger "refresh").
- Overflow -> a confirmation dialog ("Split & continue") re-calls with allow_split; server errors (not demanded / wrong location) show as notifications.
- Defensive: empty packages, package types, put-in-pack, batches and any unexpected failure fall back to native behaviour, so the patch cannot break the Barcode app.
- Set barcode in the Barcode app: _processBarcode parses the scan and, when the product is a rental set (is_rental_set, exposed through product.product._get_fields_stock_barcode), routes it to the same strict-fit apply path.
- Repair warning on serial scans: a scanned serial tied to an ACTIVE repair (repair.order state == 'confirmed') raises a reusable confirmation modal (Proceed Anyway / Cancel); proceeding writes a repair_override event to rental.serial.log. The check only runs when the Barcode model is on stock.picking — not in inventory-adjustment or batch-transfer modes.

## 15.2 Deferred (follow-up, need UX/browser verification)
- Scanning a SERIAL directly in the Barcode app (native lot handling). It works today via the backend "Assign Prepared Package" action. **Set-barcode scanning is no longer deferred — it is implemented** (see 15.1).
- Scan-to-remove in the Barcode app: semantics overlap with idempotent re-scan; needs a UX decision. Backend "Remove Scanned Package" works.
- Browser/tour verification required (cannot be run headless in this env).

# 16. Addendum v9 — split dissolves the whole package
- Decision: on a split (package has more than needed), the package is dissolved ENTIRELY, not partially. The whole package is unpacked (stock.package.unpack) so all contents become loose; the needed portion is picked, the excess stays loose at the source, and the package no longer exists in the system.
- The split confirmation warning now states the package will be dissolved and lost.
- Rationale: once a package is opened to remove excess, it is no longer a sealed package; keeping a partial package (source=result) is illegal in Odoo (cannot split a package across two locations). For multi-step consolidation use Put in Pack (Path A) to build a fresh traveling package.
