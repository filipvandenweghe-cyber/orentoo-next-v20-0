# Rental Serial Log — Per-Serial Rental Traceability
*Functional & Technical Requirements, Goals & Tests — Backend / Inventory (v1)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Module** | rental_serial_log |
| **Depends on** | sale_stock_renting (rental + stock), repair |
| **Channel** | Backend / Inventory only — not visible in Sales/Website |
| **Author** | Pro-Designed.com |
| **Status** | Implemented, installed & verified on dev |
| **Date** | 2026-08-30 |

# 1. Purpose & Business Context
Rental operators need to answer commercial and audit questions about an individual
serial-tracked unit (e.g. a returnable crate "EUROBAKSN02"): *who rented it, on
which sales order, in which physical package (and what else was in that package),
when it came back, and its repair history.* Standard stock traceability answers a
different question — *where did the unit physically move?* — so a small, stored,
searchable business log is added alongside it.

The log is written automatically as part of the normal rental flow: when a rental
delivery/return is validated, and when a repair order changes state. It is surfaced
as a **Rental History** tab on the serial/lot form.

# 2. Background & Root-Cause (why this work exists)
- The native **Traceability Report** (`stock.traceability.report`) is a
  `TransientModel`: it recomputes a tree live from `stock.move.line` (state `done`)
  and renders through a bespoke OWL client action with a **hardcoded** column list
  `[reference, product, date, lot, source, destination, qty]`. It is not a normal
  extendable list/form.
- Three needs are **structurally absent** from that data source:
  1. **Repair start** — a repair produces no `stock.move.line` until `repair_done`,
     and `repair_start` has no move at all; "in repair since date X (not yet fixed)"
     is not in the moves.
  2. **Package-contents snapshot** — "this crate went out with 40 glasses in
     PACK7" is a point-in-time fact; move lines carry `result_package_id` but never
     the contents *at that instant*.
  3. **Cross-serial search / filter / group** — the transient, per-record report
     cannot answer "all deliveries to client X in July" or "all repairs this month".
- Extending native would mean patching **three layers** (Python overrides + OWL JS +
  QWeb PDF), be upgrade-fragile, and still not represent (1)–(3). A small stored
  model is the smaller, robust path.

**Conclusion:** keep native traceability for physical movement; add `rental_serial_log`
for the commercial/audit view. They are complementary, not redundant.

# 3. Goals
- **G1 — Business audit trail per serial:** one curated row per rental *event*
  (delivered / returned / repair started / repair done), not one row per stock hop.
- **G2 — Commercial context:** each delivered/returned event records the client and
  the sales order.
- **G3 — Packaging evidence:** at delivery, record which package the serial was in
  and a human-readable snapshot of the package contents.
- **G4 — Repair history:** record when a serial enters and leaves repair, including
  a recycle/scrap marker.
- **G5 — Navigable:** every row links to its underlying transaction
  (picking / sales order / repair order).
- **G6 — Searchable:** a searchable/filterable/sortable list across all serials.
- **G7 — Low footprint & robust:** no duplicate rows on re-validation; no impact on
  non-rental transfers; read-only for regular users.

# 4. Scope
**In scope:** serial-tracked (`tracking='serial'`) products on rental orders; repair
orders on those serials. **Out of scope (by decision):** lot-tracked (non-serial)
and untracked products; packages as first-class tracked entities (only captured as a
contents snapshot on the serial's delivered event — see §6.2).

# 5. Design Decisions — Options, Pros/Cons & Rationale
## 5.1 Where to store the history
**Option A — Stored model (`rental.serial.log`).**
*Pros:* searchable/filterable/sortable; can hold facts absent from moves (repair start,
package snapshot); immutable audit record; small, standard list/form.
*Cons:* a new (tiny) model.
**Option B — Extend the native Traceability Report.**
*Pros:* reuses a familiar screen.
*Cons:* transient/non-queryable; hardcoded columns; needs Python+JS+QWeb patches;
cannot represent repair-start, package snapshot, or cross-serial search;
upgrade-fragile.
**Decision:** **Option A.** (Full analysis in §2.)

## 5.2 Serials only vs serials + packages + lots
**Decision:** **Serials only.** Packages are captured as a *snapshot on the serial's
delivered event* (which package + contents), not tracked as their own history. This
matches the business ask ("only serials, but note the package and its contents").

## 5.3 When to capture package contents
**Option A — at delivery (point-in-time snapshot).**
*Pros:* truthful to what physically left; contents can vary per trip.
*Cons:* stored as text (not a live link).
**Option B — recompute later from the package.**
*Cons:* package may be dissolved/refilled; the historical truth is lost.
**Decision:** **Option A** — snapshot the contents string on the delivered event.

## 5.4 Idempotency
Pickings can be (re)validated; `write` on repair orders can fire repeatedly.
**Decision:** log creation is **idempotent** — an identical (lot, event,
picking/repair) row is not duplicated.

# 6. Functional Requirements
## 6.1 Events (RSL-01…04)
- **RSL-01 Delivered** — on validation of a customer-facing **outgoing** rental
  delivery (not a return), for each serial move line with quantity > 0: log
  `delivered` with client, sales order, picking, the package the serial was in, and a
  contents snapshot.
- **RSL-02 Returned** — on validation of the **return** receipt, for each serial move
  line: log `returned` with client, sales order, picking.
- **RSL-03 Repair started** — when a repair order enters `confirmed` (Odoo 20 dropped
  `under_repair`): log
  `repair_start` for the serial.
- **RSL-04 Repair done** — when a repair order reaches `done`: log `repair_done`;
  if a recycle/scrap location is set, add a "Recycled/scrapped" note.

## 6.2 Package contents snapshot (RSL-05)
The snapshot lists the *other* move lines sharing the same package, human-readable,
e.g. `1× RSL Crate [RSL-0001], 40× RSL Glas`. Stored as text on the delivered event.

## 6.3 Guards (RSL-06…08)
- **RSL-06** Only rental orders (`is_rental_order`) are logged; ordinary transfers are ignored.
- **RSL-07** Only serial-tracked lines are logged.
- **RSL-08** Idempotent: no duplicate row for the same (lot, event, transaction).

## 6.4 Presentation (RSL-09…11)
- **RSL-09** A **Rental History** notebook tab on the serial/lot form (read-only list).
- **RSL-10** Each row has an **Open** action → opens the repair order, else the
  picking, else the sales order.
- **RSL-11** A searchable/filterable/sortable list (date-first; Sales Order + Client
  columns; search by serial/product, client, order, transfer, repair, package
  contents; Delivered/Returned/Repairs + Today/Last 7 Days/This Month filters; group
  by serial/product/client/event/date). Reachable via the retained (menu-less) action
  — no separate Inventory menu.

## 6.5 Security (RSL-12)
- Regular stock users: **read-only**. Stock managers: full. Rows are created `sudo`
  as system audit records.

# 7. Data Model (`rental.serial.log`)
| Field | Type | Notes |
|---|---|---|
| lot_id | Many2one stock.lot | required, indexed, ondelete cascade |
| product_id | Many2one product.product | related lot_id.product_id, stored |
| event_type | Selection | delivered / returned / repair_start / repair_done / repair_override |
| date | Datetime | default now, indexed |
| sale_order_id | Many2one sale.order | delivered/returned |
| partner_id | Many2one res.partner | client |
| picking_id | Many2one stock.picking | transfer |
| package_id | Many2one stock.package | captured at delivery |
| package_contents | Char | snapshot text |
| repair_order_id | Many2one repair.order | repair events |
| note | Char | e.g. recycle/scrap |

`stock.lot` gains `rental_log_ids = One2many(rental.serial.log)` for the tab.

# 8. Tests (`tests/test_rental_serial_log.py`)
| # | Test | Verifies | Requirement |
|---|---|---|---|
| T-01 | test_delivered_with_package_and_contents | delivered event records SO, client, package, and a contents snapshot containing both the crate serial and the co-packed product | RSL-01, RSL-05 |
| T-02 | test_returned | return receipt logs a `returned` event linked to the sales order | RSL-02 |
| T-03 | test_idempotent | re-running the delivery log does not create a duplicate row | RSL-08 |
| T-04 | test_open_transaction | `action_open_transaction` opens the correct picking | RSL-10 |
| T-05 | test_non_rental_not_logged | a non-rental order produces no log rows | RSL-06 |
| T-06 | test_repair_events | `confirmed` logs `repair_start`; `done` logs `repair_done` | RSL-03, RSL-04 |

Tests are `post_install` (they rely on the full rental/stock/repair setup) and set
`is_rental_order` explicitly on the test orders.

# 9. Out of Scope / Deferred
- **Backfill** of historical pickings/repairs (records start from install forward).
- **"History" smart button** on the serial form (action already exists, menu-less).
- Lot/untracked-product history; package-as-entity history.
