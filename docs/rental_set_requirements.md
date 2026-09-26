# Rental Sets — Composition, Customer Visibility & Presentation
*Functional & Technical Requirements, Goals & Tests — Sales / Rental (v1)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Module** | rental_set |
| **Depends on** | sale, sale_stock, sale_renting, sale_stock_renting, stock |
| **Channel** | Sales backend (order line list) + customer documents (quotation / order / portal / invoice); transfers are covered by `rental_scanning_requirements.md` |
| **Author** | Pro-Designed.com |
| **Status** | Implemented on dev (rental_set 20.0.1.45.0) — see §9 |
| **Date** | 2026-09-26 |

> This is the **first** requirements document for the rental-set feature itself. Until now
> only the *availability* half of `rental_set` was written down
> (`rental_availability_requirements.md`); set composition, customer visibility, the
> order-line presentation and set deletion existed in code only. Anything here that
> merely records existing behaviour is marked **(as built)**; everything else is new and
> supersedes the code.

# 1. Purpose & Business Context

A **rental set** is a product that, when put on a rental order, expands into hidden
component lines (`Front Light Set` → 4 × LED Par 64, 3 × DMX Cable, 4 × Clamp). The set
line is the commercial object: it carries the description, the price and the customer's
expectation. The components are the logistic objects: they carry the stock, the
availability, the pickings and the serials.

Two consequences drive this document:

1. **The customer normally buys a package, not a parts list.** The price belongs to the
   set; the contents are internal. But some deals — tenders, insurance-relevant hires,
   technically demanding clients — need the contents spelled out on the offer. That must
   be a decision per deal, not a code change.
2. **Whatever the client was offered must be what the client is invoiced.** If the
   quotation listed the contents, the invoice lists them too, and in both cases the
   component prices stay invisible: the set price is the price.

# 2. Background — what exists today (as built)

## 2.1 The composition model
- `sale.order.line.is_set` (the set line), `is_set_component` (a component), both true on a
  **nested** set; `set_parent_line_id`, `set_level` (depth, 0 = top), `set_sequence_path`.
- Expansion happens in `_expand_set_children()`; nesting is supported and real
  (`rental_availability_requirements.md` RAV-08/09 traverses to leaf components).
- **Components carry no price**: expansion writes `price_unit = 0.0` and
  `technical_price_unit = 0.0`; the set line carries the whole amount
  (`'price_unit': total`). `set_allocated_price` is an internal-only breakdown and never
  affects order totals.

## 2.2 Customer visibility — hidden in practice, by accident of defaults
`visible_to_customer` is declared `default=True`, but **both** creation paths override it:
`_expand_set_children()` writes `False`, and the Add Component wizard's own field defaults
to `False`. The list column is `optional="hide"`, so the toggle is not even visible until a
user enables the optional column.

Net effect, unchanged from v19 (the Odoo 20 migration touched none of it): **set contents
have never appeared on a commercial document.** `test_10_invoice_shows_only_parent`
encodes that (`len(order._get_order_lines_to_report()) == 1`).

Filtering happens in three hooks, all in `rental_set`, and nowhere else in the codebase:
| Hook | File | Effect |
|---|---|---|
| `sale.order._get_order_lines_to_report()` | `models/sale_order.py` | quotation PDF, order PDF **and** portal (both templates call it) |
| `sale.order._get_invoiceable_lines()` | `models/sale_order.py` | components are never invoiced |
| `account.move._get_move_lines_to_report()` | `models/account_move.py` | invoice PDF |

Also `sale.order.line._show_in_cart()` hides hidden components in the website cart.

## 2.3 The delivery slip already shows contents
`report/report_deliveryslip.xml` overrides `stock.report_delivery_document` and renders a
bold tinted header row per set with its components indented, from
`stock.picking._get_set_grouped_moves()` — which deliberately **ignores**
`visible_to_customer`, because a delivery slip lists what is physically in the box.

## 2.4 The order-line list
Hierarchy is drawn with server-computed box-drawing text (`set_indent_label`: `└─`, `▶`,
NBSP indents) in a 72px column, plus a chevron button — so a nested set shows *two*
disclosure markers. The monospace CSS class that the indent string needs
(`.o_rental_set_indent`) is **never applied by any template**, so the characters are laid
out in the proportional UI font and do not line up. `.o_rental_set_fold_btn` is likewise
dead, and its `.fa` rule is doubly dead (Odoo 20 dropped Font Awesome).

# 3. Design decisions

## 3.1 Three tiers: company allows, order decides, line excepts
Rejected: a single company-wide switch. Whether a client sees the contents is a commercial
decision per deal; and a company boolean alone is ambiguous, because components are created
hidden — an admin would switch it on and see nothing change.

Rejected: a three-state company selection (*Never / Per line / Always*). It removes the
ambiguity but puts a per-deal decision in Settings, and departs from the module's own
pattern.

Adopted: the pattern this module already uses for *on option* — a **company flag that
gates the capability** (`rental_flag_options`, documented as *"off → behaves exactly as
before"*) plus an **order-level opt-in** (`rental_on_option`, *"an explicit opt-in: the
quotation carries a boolean"*). The existing per-line `visible_to_customer` becomes the
exception within it.

Because the decision is stored on the order, changing the company setting can never
retroactively alter a quotation that has already been sent.

## 3.2 The invoice matches the offer
The client must not be offered a detailed package and then invoiced an opaque one, or the
reverse. So the order's decision drives **both** documents, and it drives *invoice
creation*, not just invoice printing: when contents are shown, components are invoiced as
0.00 lines so the invoice can list them.

Accepted consequence: 0.00 component lines exist in accounting. They move no money
(totals, taxes and the general ledger are unchanged), but they are visible in
product-level sales analysis. This is the price of matching documents and is deliberate.

## 3.3 Component prices are never shown — anywhere
Not "shown as 0.00": **not shown**. A component's Unit Price, Discount, Taxes and
Subtotal cells render empty on every customer document. Quantity and UoM stay, because
what the client cares about is *how many* are included. Printing `0.00` reads as a
give-away or a pricing bug, which is exactly the defect this work removes.

## 3.4 Hierarchy is drawn with layout, never with characters
Indentation, weight and tint — no `└─`, no `▶`, no monospace, no coloured text for
structure. On the customer documents this follows Odoo 20's own report convention
(`padding-left: level * 8px`); in the backend list it follows the section/subsection
styling Odoo 20 uses in that very list.

## 3.5 The delivery slip stays outside the flags
It lists the physical contents of the box for the driver and the client receiving it. It is
not a commercial document and is not gated (§2.3 behaviour is kept, minus the `└`
character, for consistency with §3.4).

## 3.6 The decision is frozen when the order is confirmed
A confirmed order is the agreement. Changing afterwards what the client sees would mean
invoicing a document shaped differently from the one they accepted — exactly the mismatch
§3.2 exists to prevent. So the flag locks at confirmation rather than being merely
discouraged, and it locks for everybody: there is no legitimate reason for a manager to
re-cut an accepted offer, and if the contents genuinely must change, the order is reset to
draft or a new one is made.

Consequence, accepted: an order confirmed **before** this feature ships can never show its
contents. That is correct — it was offered without them.

## 3.7 The flag never defaults from the customer
No `res.partner` field seeds it. A per-customer default would quietly change what an offer
looks like based on data edited elsewhere, and the salesperson would not see why. Each
quotation states its own answer, visibly, on the order.

# 4. Requirements

## 4.1 Visibility gating (RS-01…07)

| ID | Requirement |
|---|---|
| **RS-01** | `res.company.rental_set_allow_detail` (Boolean, **default False**) — *"Allow showing rental set contents on customer documents"*. Mirrored on `res.config.settings` and shown in the existing rental block of the Sales/Rental settings page, beside `rental_show_stock_locations` and `rental_flag_options`. |
| **RS-02** | `sale.order.rental_set_show_detail` (Boolean, **default False**) — *"Show set contents"*. Invisible unless the company allows it (RS-01). Placed with the other rental order fields, as `rental_on_option` is. |
| **RS-02a** | The flag is editable **only while `state in ('draft', 'sent')`** and is **frozen from confirmation onwards** — for every user, with no manager exception. A quotation reset to draft becomes editable again. Enforced twice: `readonly` in the view *and* a guard in `write()` raising a `UserError`, because a view attribute is not enforcement. The guard does **not** exempt `env.su`: this is immutability, not a permission, so a sudo or server-action write is refused too (duplicating an order is unaffected — the copy starts as a draft). |
| **RS-03** | `sale.order.line.visible_to_customer` keeps its meaning and its declared `default=True`; the explicit `False` overrides in `_expand_set_children()` and in the Add Component wizard are **removed**. Gating moves entirely to RS-01/RS-02, so the visible outcome for existing data is unchanged (company flag off). |
| **RS-04** | With `rental_set_show_detail = False` (or the company flag off), **no** component appears on any commercial document, whatever `visible_to_customer` says. Identical to today's behaviour, byte for byte. |
| **RS-05** | With `rental_set_show_detail = True`, every component appears **except** those whose `visible_to_customer` is unticked. So the line flag is an exclusion, never something the user must first discover to make the feature work. |
| **RS-06** | The optional "Visible" column in the order-line list is offered only when the company allows detail (RS-01); otherwise it is not an available optional column, because it would have no effect. |
| **RS-07** | A hidden component (RS-04/RS-05) is also hidden from the website cart (`_show_in_cart`, as built) and from the portal document (same hook as the PDF — RS-11). |

## 4.2 Customer documents — presentation (RS-10…17)

Applies to the quotation PDF, the sales-order PDF, the customer portal and the invoice
PDF, which must be indistinguishable in structure.

| ID | Requirement |
|---|---|
| **RS-10** | The set line prints as a normal, priced line, in bold: it carries the description and the whole amount. |
| **RS-11** | A shown component prints **indented by `set_level * 8px`**, added to whatever indentation the native template already computed for sections/subsections — never replacing it, so an order that uses both sections and sets nests correctly. Indentation caps at level 4 to protect the column width. *Exception:* the portal's phone card layout has no table cells to indent, so there the contents are only price-suppressed (RS-13), not indented — a deliberate limitation of that layout. |
| **RS-12** | A shown component prints its **Quantity and UoM**. |
| **RS-13** | A shown component prints **no Unit Price, no Discount, no Taxes and no Subtotal** — the cells are empty, never `0.00`. |
| **RS-14** | Document totals, tax summaries and the amount due are **identical** whether or not contents are shown. (They are, because components are 0.00; this is an invariant to test, not an assumption.) |
| **RS-15** | A nested set prints as a component of its parent (indented, no prices) **and** as the parent of its own components (one level deeper). |
| **RS-16** | No box-drawing or arrow characters appear on any document (§3.4). This includes replacing the `└` currently printed by the delivery slip. |
| **RS-17** | `set_allocated_price` never appears on a customer document (as built — it is an internal figure). |

## 4.3 Invoicing (RS-20…24)

| ID | Requirement |
|---|---|
| **RS-20** | `_get_invoiceable_lines()` includes a component when, and only when, that component would be shown on the order's documents (RS-04/RS-05). The set line is always invoiceable. |
| **RS-21** | Invoiced components carry their quantity and `price_unit = 0.0`; the set line carries the amount. No component ever contributes to an invoice total. |
| **RS-22** | `account.move._get_move_lines_to_report()` follows the **originating order's** decision (through `sale_line_ids`), not `visible_to_customer` alone, so a component that was invoiced is also printed (with RS-11…RS-13 applied) and one that was not is absent. |
| **RS-23** | An invoice line with no `sale_line_ids` (manual line, credit note adjustment) is never treated as a set component. |
| **RS-24** | Because the flag is frozen at confirmation (RS-02a), the offer and every invoice drawn from it **cannot diverge** — there is no window in which the order could be invoiced differently from how it was accepted. An invoice already created is in any case its own document and is never rewritten. |

## 4.4 Delivery slip (RS-30…31)

| ID | Requirement |
|---|---|
| **RS-30** | The delivery slip always groups components under their set header with the set's quantity, regardless of RS-01/02/03 (as built). |
| **RS-31** | Its indentation is layout-only (RS-16); the group header keeps its bold tinted band. |

## 4.5 Order-line list presentation (RS-40…48)

The redesign of the internal list. Nothing in `docs/` previously constrained it.

| ID | Requirement |
|---|---|
| **RS-40** | Hierarchy is shown by **indenting the product cell** by depth, using a per-depth class added in a `getCellClass` override — the pattern core uses in `account_account_list_view` (`o_list_indent_#{i}`), including its guard: indentation is suppressed while the list is filtered, sorted or grouped. |
| **RS-41** | `set_indent_label` and its `└─` / `▶` glyphs are removed from the model and from every view (list + picking). The dead `.o_rental_set_indent` and `.o_rental_set_fold_btn` CSS goes with them. |
| **RS-42** | Exactly **one** disclosure control per row, and only on a row that has children: a `<button>` in a narrow gutter column carrying `<i class="oi oi-fw" data-icon="arrow_right \| arrow_drop_down"/>` — Odoo 20's disclosure pair, and already what the current widget uses. A leaf component has no marker at all. |
| **RS-43** | A set line is subordinated the way Odoo 20 subordinates a section in this same list: background tint through the Bootstrap table variables (`--table-bg-type`, `--table-hover-bg`) at `rgba($body-emphasis-color, .08)` for a top-level set and `.03` for a nested one, with `--ListRenderer-data-row-focused-striped-bg` set to match so keyboard focus does not fight the tint; weight `$font-weight-bolder` then `$font-weight-bold`. |
| **RS-44** | Colour is never used to convey structure: the `decoration-primary` on nested set rows is removed. Colour stays for semantics only (archived product, warnings, not-visible-to-customer). |
| **RS-45** | A **collapsed** set states what it hides: the component count, muted, with a "N components hidden" tooltip. It sits in the caret gutter rather than beside the set name, because the name lives inside the `product_and_description` `<column>` group, which cannot take an inline injection (§5.3, the Odoo 20 `<column>` trap). |
| **RS-46** | The set-level availability widget keeps the `area_chart` glyph it shares with the standard `sale_stock` indicator (per CLAUDE.md — the two must read alike). |
| **RS-47** | The picking (`stock.move`) list gets the same visual treatment, as a **strictly cosmetic** change: the grouping key, the `[Set] ` prefix and the zero-demand header move keep the semantics fixed by `rental_scanning_requirements.md` (§5.3 below). |
| **RS-48** | The set-block drag-and-drop invariant is unchanged: a set parent is immediately followed by its descendants in DFS order, enforced in `normalizeSetBlocks()` after every `sortDrop`. |

## 4.6 Deleting a set (RS-50…53)

| ID | Requirement |
|---|---|
| **RS-50** | Deleting a set line deletes its whole descendant tree — client-side cascade bottom-up, plus the server-side `unlink()` which pulls descendants in and cancels their pending stock moves (as built). |
| **RS-51** | On a **confirmed** order, where Odoo forbids deleting lines, components are zeroed (`product_uom_qty = 0`) with their moves cancelled instead of unlinked (as built). |
| **RS-52** | Clicking the row bin on a set that has descendants opens a **confirmation dialog** naming the set and listing its descendants at their nesting depth with quantities, before anything is deleted. A collapsed set can therefore never delete rows the user has not seen. |
| **RS-53** | The dialog's wording follows the outcome: *"…will be removed together with the N lines it contains"* on a draft order, *"…will be emptied (quantities set to 0)…"* on a confirmed one (RS-51). |

## 4.7 Security (RS-60…61)

| ID | Requirement |
|---|---|
| **RS-60** | Changing set composition (add / edit / delete a component) stays restricted to Sales Managers and Warehouse Managers, and only while no related picking is validated — `_check_set_edit_permission()`, as built. Pickers (`stock.group_stock_user` without a manager group) may never change composition. |
| **RS-61** | `rental_set_show_detail` (RS-02) is a commercial field: any user who may edit the quotation may set it. After confirmation nobody may change it (RS-02a) — not a Sales Manager, not a Warehouse Manager. |

# 5. Implementation notes & traps

## 5.1 Odoo 20 has native fields that look like ours — do not assume they fit
`sale.order.line` and `account.move.line` both carry native `parent_id`,
`collapse_prices` and `collapse_composition` (v20), which overlap
`set_parent_line_id` / `set_components_folded`. Two traps before reusing them:

- On `account.report_invoice_document` the guard is
  `t-if="not hide_details and not hide_prices"` where `hide_prices = line.collapse_prices`
  — so `collapse_prices` on a line **removes the whole line** from the invoice PDF. It
  cannot express "show the line, hide its prices", which is RS-13.
- `line_padding` there is derived from section/subsection context only, **not** from
  `parent_id`, so setting `parent_id` does not indent anything.

Therefore RS-11/RS-13 need our own `t-inherit` on the sale and invoice templates.
Consolidating `set_parent_line_id` onto the native `parent_id` is a **separate, future**
refactor; it is recorded here so nobody "simplifies" this into a bug.

## 5.2 Templates to inherit
`sale`'s quotation/order document and portal template (both resolve their rows through
`_get_order_lines_to_report()`), and `account.report_invoice_document` (through
`_get_move_lines_to_report()`), which also has a second, grouped-summary rendering path
that must be checked for the same rules.

## 5.3 Constraints imported from other documents
- **Transfers / Barcode** (`rental_scanning_requirements.md` §3, PPB-08, PPB-12): set
  grouping, the `[Set] ` prefix and indentation exist on the **outbound sale chain only**,
  keyed off `sale_id` set **and** `return_id` not set, with *no per-operation-type branch*;
  **return pickings show no header and no prefix**; the header move stays zero-demand,
  move-line-less and non-blocking (`_sanity_check`). RS-47 must not disturb any of it.
- **SO form view** (`rental_availability_requirements.md`): the inherited `sale.order`
  form stays at **priority 25** so it applies after `sale_stock` (20) has installed
  `qty_at_date_widget`, the anchor the availability widget attaches to.
- **`<column>` anchoring** (CLAUDE.md): in Odoo 20 the SO line list wraps
  `product_template_id` / `product_id` / `name` / `label` in
  `<column name="product_and_description">`. A field injected *after a field inside* that
  group renders stacked in the Description cell and an injected `<button>` is dropped
  silently — that is how `visible_to_customer`, `set_allocated_price`, `set_availability`
  and the row "+" button were lost once already. Anchor on the `<column>` element.
- **Availability report** (`availability_report_balancing_analysis.md` §27): set rows are
  deliberately omitted from the matrix (fix the component, the set is fixed); `clamp=True`
  stays the default at engine call sites.

# 6. Non-goals
- Changing how set prices are computed or allocated (`set_allocated_price`, fixed vs sum).
- Invoicing components at anything other than 0.00, or letting a component affect a total.
- Gating the delivery slip, the picking, the Barcode app or the availability pop-up by
  RS-01/RS-02 — those are internal/logistic surfaces.
- Migrating `set_parent_line_id` / `set_components_folded` onto the native v20 fields (§5.1).
- Defaulting `rental_set_show_detail` from the customer (`res.partner`) or from anything
  other than `False` (§3.7).
- Allowing the flag to change after confirmation, by any user or any group (§3.6, RS-02a).
- Any change to the website/eCommerce set-browsing helpers, which are a separate,
  non-canonical path (`availability_report_balancing_analysis.md` §50).

# 7. Tests

| Area | Test |
|---|---|
| RS-04 | Company flag off (default): `_get_order_lines_to_report()` returns the set line only — the existing `test_10_invoice_shows_only_parent`, kept green unchanged. |
| RS-05 | Company + order flags on: every component is reported; unticking `visible_to_customer` on one removes exactly that one. |
| RS-14 | `amount_untaxed` / `amount_tax` / `amount_total` identical with the flag on and off, for a fixed-price and a sum-price set. |
| RS-20/21 | Flag on → invoice contains component lines at 0.00 and the set line at full price; `move.amount_total` equals the order's. Flag off → invoice contains the set line only. |
| RS-22 | The invoice PDF's reported lines match the quotation's reported lines, set for set. |
| RS-15 | A nested set reports at both levels with the right `set_level`. |
| RS-02a | Writing `rental_set_show_detail` on a confirmed order raises `UserError`, for a plain user **and** for a Sales Manager; after a reset to draft the same write succeeds. |
| RS-24 | Flipping the order flag after invoicing leaves the existing invoice's reported lines unchanged (and on a confirmed order is refused outright — RS-02a). |
| RS-52 | Deleting a set with descendants asks first; confirming removes the whole tree; cancelling removes nothing. |
| RS-41 | No customer document and no list view contains `└`, `─` or `▶`. |
| RS-47 | Picking-side: outbound chain still groups with the `[Set] ` prefix and a zero-demand header; a return picking still has neither. |

Browser-level checks (list rendering, the caret, the confirmation dialog) belong in
`rental_set/tests/test_rental_set_ui.py`, which is tagged `-standard` because the AI
sandbox cannot fork Chrome; run it with `--test-tags rental_set_ui`.

# 9. As implemented (rental_set 20.0.1.45.0)

| Requirement | Where |
|---|---|
| RS-01 | `res.company.rental_set_allow_detail` + `res.config.settings` mirror + a "Rental Sets" block in the rental settings page |
| RS-02 / RS-02a | `sale.order.rental_set_show_detail`, the read-only company mirror `rental_set_detail_allowed`, `readonly` in the form and the absolute `sale.order.write()` guard |
| RS-03 | the `visible_to_customer = False` overrides removed from `_expand_set_children()` and from the Add Component wizard |
| RS-04…RS-07 | one helper, `sale.order._rental_set_shows_line()`, used by `_get_order_lines_to_report`, `_get_invoiceable_lines`, `account.move._get_move_lines_to_report` and `_show_in_cart` |
| RS-10…RS-17 | `report/report_set_customer_documents.xml` — three `t-inherit`s (quotation/order PDF, portal, invoice PDF) that extend `padding_style` and reuse each template's own price-suppression flag; see its header comment for the two traps |
| RS-20…RS-24 | `_get_invoiceable_lines` + `account.move.line._rs_shown_to_customer()` / `_rs_is_set_component()` / `_rs_set_level()` |
| RS-40…RS-48 | `getCellClass` / `getRowClass` in `rental_set_list_renderer.js`, the rewritten fold widgets, and `rental_set.scss` (`o_rental_set_indent_1…4`, the tint ladder, the gutter) |
| RS-41 | `set_indent_label` and `stock.move.rental_set_indent_label` deleted, with their computes and the dead CSS |
| RS-52 / RS-53 | `_confirmSetDeletion()` in `rental_set_list_renderer.js` (ConfirmationDialog via `useOwnedDialogs`) |
| Tests | `rental_set/tests/test_rental_set_customer_detail.py` (17 tests) |

# 8. Decision log

| Question | Decision | Where |
|---|---|---|
| May `rental_set_show_detail` be changed after the order is confirmed? | **No** — frozen at confirmation, for every user, enforced in `write()`. A pre-existing confirmed order therefore never shows contents. | §3.6, RS-02a, RS-61 |
| Should the flag default from a field on the customer (`res.partner`)? | **No** — each quotation states its own answer; no hidden per-customer default. | §3.7, non-goals |
| Should the invoice follow the offer, or stay summary-only? | **Follow the offer** — the order's decision drives invoice *creation*, not just printing. | §3.2, RS-20…24 |
| Company-wide selection (*Never / Per line / Always*) or company flag + order opt-in? | **Company flag + order opt-in**, mirroring this module's *on option* pattern; the line flag is the exception. | §3.1, RS-01…05 |
| Are component prices printed as `0.00`? | **Never printed at all** — empty cells. | §3.3, RS-13 |
