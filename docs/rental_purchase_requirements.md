# Rental Purchase — Hired Equipment (supplier-owned)
*Functional & Technical Requirements, Goals & Tests — Backend / Purchase + Inventory + Accounting (v1, FINAL)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Module** | `rental_purchase` (core) |
| **Depends** | `purchase_stock`, `stock_account` |
| **Soft integration** | `rental_set` (availability) — no hard dependency, gated on field existence |
| **Author** | Orentoo |
| **Status** | Implemented, tested (17/17) & verified on dev |
| **Date** | 2026-09-07 |

---

# 1. Purpose & Business Context

A Purchase Order can represent equipment we **hire from a supplier** rather than **buy**.
The equipment is received into our warehouse and operationally used, but it stays **owned
by the supplier** for the whole rental period and is **returned** to them at the end.

The architectural objective:

> A Rental Purchase Order lets one company temporarily receive and operationally use
> supplier-owned equipment **without acquiring it**. Received goods do not increase our
> inventory valuation, and a traceable return obligation to the supplier is created up front.

This module covers the **external-supplier** flow end to end. The intercompany variant
(supplier = another company in the same database ⇒ a genuine counterpart **Rental Sales
Order**) is a **separate module** `rental_purchase_intercompany` (see §16, deferred).

# 2. Architecture

- **`rental_purchase` (this module)** — the receipt leg: flag + dates, supplier ownership,
  the supplier-return chain, the return-demand reconciliation, the rental expense account,
  cancellation guard, PDF/UX. Works with any external (non-Odoo) supplier and **never
  depends on an intercompany Sales Order existing**.
- **`rental_purchase_intercompany` (deferred)** — turns the auto-generated counterpart SO
  into a genuine Rental Order, one-way date sync, rental revenue account.

**Principle:** reuse standard Odoo (Purchase receipts, stock ownership/consignment,
warehouse routes, valuation) and add the *smallest* extensions on top. Normal Purchase
Orders remain completely unaffected.

# 3. Data model & UI (PO)

| Field (technical) | Type | Meaning |
|---|---|---|
| `purchase.order.is_rental_purchase` | Boolean | UI label **"To be returned"**. Marks the PO as a hire. |
| `purchase.order.rental_start_date` | Datetime | Planned start of the hire; drives the receipt scheduling. |
| `purchase.order.rental_return_date` | Datetime | Date the equipment must leave and return to the supplier; drives the return scheduling. |
| `purchase.order.rental_return_picking_ids` | One2many | The supplier-return pickings (smart button). |
| `purchase.order.line.is_rental_purchase` | Boolean (related, stored) | Convenience for account determination / views. |
| `stock.move.rental_purchase_order_id` | Many2one | Traceability: a return-chain move → its Rental Purchase order. |
| `stock.picking.rental_purchase_return_order_id` | Many2one (computed, stored) | The return picking → its Rental Purchase order. |
| `product.category.rental_purchase_expense_account_id` | Many2one, **company-dependent** | Rental expense account for the vendor bill. |
| `res.company.rental_purchase_expense_account_id` | Many2one | Company-level fallback expense account. |

**Requirements**
- **RP-01** — When "To be returned" is off, the PO behaves as a standard Purchase Order in
  every respect (no dates required, no return, standard accounting).
- **RP-02** — When on, both dates are **required** and `rental_return_date > rental_start_date`
  (validated by constraint).
- **RP-03** — UI shows the flag + dates near the order scheduling info; a **"Rental Returns"**
  smart button navigates to the return picking(s).

# 4. Dates → scheduling (reuse standard)

- **RP-10** — On confirmation (and on later change), the **Rental Start Date** is pushed onto
  the order lines' standard `date_planned`, so the incoming **receipt is scheduled for the
  rental start** through native `purchase_stock` propagation (`_update_date_planned` →
  move `date` / `date_deadline`). No independent scheduling logic.
- **RP-11** — The **Rental Return Date** is the scheduling basis of the return moves
  (`date` / `date_deadline`). Changing it re-schedules **only open** return moves; done or
  cancelled moves are never rewritten.

# 5. Supplier ownership / consignment (do not increase our valuation)

- **RP-20** — On confirmation the incoming receipt is stamped **`owner_id = supplier`**
  (standard consignment). At validation Odoo writes the owner onto the moves, move lines and
  quants; it **propagates through internal transfers** automatically.
- **RP-21** — Because the received quants have `owner_id != company.partner_id`, standard
  `stock_account` **excludes them from valuation** (`_should_exclude_for_valuation` /
  location `_should_be_valued`). The receipt therefore **does not increase our inventory
  asset**; the vendor bill is a plain rental **expense** (see §8).
- **RP-22** — Lot/serial tracked equipment keeps the supplier owner per serial through
  receipt, internal transfers and return (standard lot/quant behaviour). We add nothing.

# 6. Supplier-return logistics (route-aware, owner-preserving)

- **RP-30** — On confirmation a **return move chain** to the **supplier location**
  (`partner.property_stock_supplier`) is prepared, honouring the warehouse's **configured
  outgoing steps** read relationally from the warehouse (no hardcoded codes/IDs):
  - 1-step (`ship_only`): `Stock → Supplier`
  - 2-step (`pick_ship`): `Stock → Output → Supplier`
  - 3-step (`pick_pack_ship`): `Stock → Packing → Output → Supplier`
  Final destination is always the **supplier/vendor location**, never the customer location.
- **RP-31** — The chain root is chained (`move_orig_ids`, `make_to_order`) onto the **receipt
  move(s)**. This is the only reservation path in Odoo 20 that **preserves the owner**, so
  the return can *only ever* reserve the **hired-in supplier-owned units it received** — never
  unrelated company-owned stock (the classic "20 own chairs must not be grabbed" case).
- **RP-32** — Multi-step returns chain hop-to-hop; only the final supplier-bound hop is a
  departure of owned possession.

# 7. Return obligation = what will ULTIMATELY be received (mirror of Option B)

The supplier-return demand is **reconciled** as receipts complete — it is not frozen at the
ordered quantity. This is the **mirror, on the receipt leg**, of `sale_flow`'s delivery-side
return-demand reconciliation ("until it is out, the client is not expected to return it"):

> **We owe back exactly what comes in.**

- **RP-40** — Per product, the open return demand is set to
  **`received (done inbound) + still-pending (open inbound) − already returned`**:
  - **receive less, no back-order** → shortfall written off → obligation shrinks to what arrived;
  - **back-order pending** → the pending units stay in the obligation (they are still coming);
  - **over-receipt** → obligation grows to what actually arrived.
- **RP-41** — The chain root is **re-linked to all current receipt moves** so a back-order's
  later units can still feed the return through the owner-preserving MTO chain.
- **RP-42** — We do **NOT** scrap or financially reconcile a shortfall. The equipment is
  supplier-owned, so **the supplier settles differences** (this is the *opposite* of the
  delivery side, where we own the goods and scrap the loss). Purely logistical right-sizing.
- **Trigger** — `stock.move._action_done`, filtered to Rental Purchase moves only (incoming
  receipts whose PO `is_rental_purchase`, plus the return moves). Ordinary purchases and
  every sales/rental move are excluded, so `sale_flow` and standard rental are untouched.

# 8. Accounting — Rental Purchase Expense

- **RP-50** — Vendor **bill** lines of a Rental Purchase use the **Rental Purchase Expense
  Account** where configured: `product.category.rental_purchase_expense_account_id`
  (company-dependent), with a `res.company` fallback. Implemented by overriding
  `account.move.line._compute_account_id` **only** for lines whose `purchase_line_id.order_id`
  is a Rental Purchase; all other bills keep standard determination.
- **RP-51** — No inventory-asset entry is created on receipt (goods are supplier-owned,
  §5), so the bill is simply `Dr Rental Expense / Cr Payable`. Compatible with both
  Anglo-Saxon and Continental (no journal hardcoding).
- **RP-52** — Invoicing is **not** auto-created on confirmation (standard behaviour).

# 9. Rental availability integration (soft, via `rental_set`)

Hired-in equipment must appear as **temporarily rentable** during the hire and disappear
after the return. The `rental_set` availability engine ("Option A") was extended
symmetrically (both changes gated on the optional `rental_purchase` fields; sales/rental
logic unchanged):

- **RP-60 (supply, operational)** — Rental Purchase supply is **credited operationally**
  from its **arrival date** (= rental start), regardless of the receipt picking type's
  `rental_incoming_policy`. It is trusted because it is paired with a scheduled return.
  **Caveat (see §16):** a receipt type set to `rental_incoming_policy = 'ignore'`
  short-circuits *before* the Rental-Purchase test, so the hired-in supply is then **not**
  credited while its paired return is still counted as a departure — a net phantom
  subtraction. "Regardless of the policy" holds for `projected` and `operational`.
- **RP-61 (return, departure)** — The supplier return is counted as a **departure** from its
  **scheduled date**, even while still `waiting` on the receipt.
- **RP-62 (net effect)** — Because arrival and departure are credited together, availability
  shows a **clean bump**: `baseline` before the hire → `baseline + hired` during
  `[start, return]` → `baseline` after — and **own stock is never hidden**. Consistent both
  before and after the physical receipt is validated (physical on-hand simply replaces the
  operational credit once the receipt is done).
- **RP-63** — Since availability reads the return move's `product_uom_qty`, the §7
  reconciliation makes the report self-correct (received less ⇒ smaller bump).

*(See the addendum in `docs/rental_availability_requirements.md` for the exact terms.)*

# 10. Cancellation

- **RP-70 (before receipt)** — Cancelling a Rental Purchase with nothing received cancels the
  open return chain too (standard-safe).
- **RP-71 (after receipt)** — If supplier-owned goods are physically present, cancellation is
  **blocked** with a clear `UserError` requiring the return to be processed first. The
  invariant: **a received rental must never lose its traceable return obligation** because the
  PO was cancelled. Completed stock moves are never cancelled or rewritten automatically.

# 11. Traceability (relational, not `origin` text)

- **RP-80** — Explicit relational links (§3): return move → PO (`rental_purchase_order_id`),
  return picking → PO (`rental_purchase_return_order_id`), plus native `purchase_line_id` on
  the receipt moves. The return chain uses a dedicated `stock.reference` (the replacement
  for the procurement group; still present in Odoo 20).

# 12. PDF / UX

- **RP-90** — The RFQ and Purchase Order PDFs show a **"Rental — equipment to be returned to
  the supplier"** block with the rental start/return dates (only when the flag is on).
- **RP-91** — Smart button "Rental Returns" to the return picking(s); native "Receipt" smart
  button continues to show only the receipt (return moves carry no `purchase_line_id`).

# 13. Multi-company / multi-warehouse

- **RP-95** — Everything resolves relationally: company, warehouse (from the PO picking type),
  supplier location (`property_stock_supplier`), warehouse routes/steps, picking types,
  company-dependent category account. No assumption of a particular warehouse code, picking
  type name, location ID, company or partner.

# 14. What is standard Odoo (reused, not rebuilt)

Receipt creation, ownership propagation, valuation exclusion for owner≠company, multi-step
outbound routing, partial-receipt/back-order behaviour, lot/serial ownership, and
account determination — all standard. This module only adds: the flag + dates, owner
assignment, the owner-preserving return chain, the return-demand reconciliation, the two
category accounts, the availability credit, the cancellation guard, and the PDF/UX.

# 15. Tests (module `rental_purchase`, 17 passing)

| # | Test | Verifies | Req |
|---|---|---|---|
| 1 | test_01_normal_purchase | non-rental PO: no return, no owner, standard | RP-01 |
| 2 | test_02_external_rental_purchase | receipt + owner=supplier + future return exists | RP-20, RP-30 |
| 3 | test_03_one_step | return `Stock → Supplier`, dated return date | RP-30 |
| 4 | test_04_two_step | return `Stock → Output → Supplier` | RP-30 |
| 5 | test_05_three_step | return `Stock → Packing → Output → Supplier` | RP-30 |
| 6 | test_06_unrelated_stock_not_reserved | return never reserves the 20 own chairs | RP-31 |
| 7 | test_07_partial_receipt | 8 of 10 → return reserves only the 8 received | RP-31, RP-40 |
| 8 | test_08_serial_ownership | every received serial stays supplier-owned | RP-22 |
| 16 | test_16_cancel_before_receipt | cancel cleans up the open return | RP-70 |
| 17 | test_17_cancel_after_receipt_blocked | cancel blocked once goods present | RP-71 |
| 18 | test_18_partial_final_return | fewer units may be returned than received | RP-42 |
| 19 | test_19_expense_account | rental bill line uses the rental expense account | RP-50 |
| 20 | test_20_normal_bill_account_unchanged | normal PO bill keeps standard account | RP-01, RP-50 |
| 21 | test_21_return_reduces_rental_availability | availability drops after the return date | RP-61, RP-62 |
| 22 | test_22_partial_no_backorder_shrinks_return | no-backorder short → return shrinks; own stock not hidden | RP-40, RP-63 |
| 23 | test_23_backorder_keeps_full_return | back-order pending → obligation stays full, then reserves all | RP-40, RP-41 |
| 24 | test_24_operational_projection_before_receipt | projected bump/drop **before** any receipt (0/10/0) | RP-60, RP-61, RP-62 |

# 16. Out of scope / Deferred

- **Intercompany** (`rental_purchase_intercompany`): counterpart Rental Sales Order, one-way
  date sync + readonly guards, Rental Sales Revenue account, intercompany stock/invoice
  synchronisation. **Not built yet.**
- **Known open items** (analysis captured, not yet actioned):
  1. *Re-renting hired-in gear past its return date* = silent over-commitment; the signed
     (`clamp=False`) availability already exposes it — could be surfaced as a warning/block.
  2. *Owner-agnostic physical stock*: the availability base counts **all** on-hand
     regardless of owner (any consignment inflates rental availability) — policy decision.
  3. *Multi-step reception*: the return chains to the receipt move; if reception is
     multi-step and the `…→Stock` leg is created only at validation, the owner-preserving
     MTO chain must be revisited (current dev warehouse is one-step).
  4. *Effective vs declared return date* for the departure term (symmetric to the sales side).
  5. *`rental_incoming_policy` semantics* when a receipt type is set to Ignore (documented).
  6. *Sets*: hired-in product as a set component — expected to propagate via
     `_rental_available_qty`; a targeted test would confirm.
- **Live UX**: aligning the PO "Expected Arrival" as the user types (before save) — declined;
  it is aligned on save/confirm.
