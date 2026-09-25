# Rental Availability — Repairs, Sets & Warehouse Breakdown
*Functional & Technical Requirements, Goals & Tests — Backend / Sales (Rental) + Inventory (v9, for approval)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 19.0 (Odoo.sh) |
| **Where it lives** | Folded into **rental_set** (no new module) |
| **Hard deps** | sale_stock_renting, sale_renting, sale_stock, stock (already in rental_set) |
| **Optional dep** | **repair** — used only if installed; must not crash or show when absent |
| **Channel** | Rental order line availability + its pop-up; set availability |
| **Author** | Pro-Designed.com |
| **Status** | Requirements v2 — for approval; no code yet |
| **Date** | 2026-08-30 |

# 0. Changes vs v1 (from review)
1. **Repair is optional.** If the `repair` module is not installed: no repair deduction,
   no repair line in the pop-up, **no crash** (soft model check).
2. **"At Customer" is a location** (the company rental location). The pop-up breakdown
   is therefore **location-driven**, which simplifies it.
3. **Repairs must be woven into the availability figure.** So we *do* extend the
   availability computation (a targeted override), not just the display. The "keep
   native, don't rewrite" decision applies **only** to the multi-step over-count, not to
   repairs.
4. **Own-demand / no-double-count is a first-class requirement to verify** (see §3.3):
   the current code adds back own demand for confirmed orders but then caps at
   `qty_available`, which may negate the add-back. This must be verified and tested.
5. **Tests reassessed** (see §7) to match: folding into rental_set, optional repair,
   own-demand correctness, and location-based breakdown.
6. **Decisions:** multi-step over-count → **keep native + show portion in pop-up (1B)**;
   placement → **fold into rental_set**.
7. **Reservation split is shown explicitly (v3).** Rather than hide the own-demand
   correction inside add-back/cap math, the pop-up shows **Reserved by this order** and
   **Reserved by other orders** as their own lines. The availability number then becomes
   self-explanatory and auditable — you can *see* that this order's own reservation is
   counted as available to itself.
8. **"At Customer" line removed (v4).** A unit physically at a customer is a unit
   **committed to an order**, so it is already represented (period-correctly) inside
   **Reserved by other orders** (or this order). A separate "At Customer" line would
   double-count it — and, being period-blind, would wrongly reduce availability for a
   future rental that the unit returns in time for. The period-aware reserved/other-orders
   figure is the correct and sufficient representation.
9. **Native forecast already handles "returned early → available" (v5).** Verified in
   code: confirming a rental order registers **both** the pickup **and** the return
   transfer immediately. So a competing order that returns before our period holds
   nothing during it, and the full stock is available again — the native figure is
   correct, with **no forecast rewrite and no padding crutch needed** for this case.
   Consequently **Available for Rent = Total stock − Reserved(others) − In Repair**
   equals the native availability figure *exactly* (checked for both overlapping and
   future periods). There is therefore no "Option A vs B" — it is one number.
10. **Pop-up reordered to an accounting flow (v5).** `Total stock − Reserved by other
    orders − In Repair = Available for Rent`, then *of which reserved for this order*,
    then *Located in: Input / QC / Stock*. "Total stock" is computed as
    `Available + Reserved(others) + In Repair` so it is warehouse-scoped and always
    reconciles exactly with the figure shown.
11. **Location list = CURRENT on-hand per location, summing to Total (v6→v7).** To keep
    "Total stock" a stable anchor, the location list partitions Total across physical
    buckets: warehouse internal locations (Input/QC/Stock…), **At customer**, **In
    repair**, and a small reconciling **Other / in transit** bucket. It uses **current
    physical quants** (always ≥ 0) — deliberately **not** a per-location forecast to
    pickup. Rationale (v7): forecasting per location blindly applied pending internal
    staging/pick moves (Stock→Packing Zone) and partial reservations, producing a
    phantom "Packing Zone 8" and a negative "In transit −1" on a real order (S01532,
    only 7 units, both in Stock). Current on-hand is robust and honest; period-awareness
    stays in "Available for Rent". This is a *physical-presence* lens (a unit can sit at
    Stock yet be reserved by another order), distinct from the availability math; both
    anchor to the same Total.

12. **Option A implemented — two independent lenses (v8).** The single accounting flow
    (`Total − Reserved − Repair = Available`) is dropped. A rental line's pop-up now has
    two sections that do **not** net to each other:
    - **For this rental** (time-based): Reserved by other orders, Available to this order
      (of which reserved for this order), Requested by this order, Missing for this order.
    - **Physical stock (right now)** (point-in-time): **Total stock** = real units owned =
      current on-hand across the warehouse's internal locations **plus** the internal
      rental (at customer) location; then the per-location partition (which sums to
      Total). Total is **conserved and stable** — picking a unit just moves it from Stock
      to "At customer", Total unchanged. This kills the earlier "Total jumped by the
      amount picked" artifact (the old Total was back-derived from the forecast, which
      credits scheduled returns). Consequence (accepted): "Available to this order" can
      exceed physically-free stock when units return within the window — correct, and
      why the two lenses are shown separately.

13. **Availability formula replaced (v9).** "Available to this order" no longer uses the
    old move-walk / own-demand add-back. It is now:
    `Available = max(Total physical stock − Reserved by other orders − In Repair, 0)`,
    where *Reserved by other orders* is the native period-aware peak-concurrent demand of
    **other** confirmed rentals (excludes this line). Reason: the old code excluded
    **done** pickup moves, so once an order was (partially) picked it lost its own-demand
    add-back and looked *short*, while an un-picked competitor looked *fully available* —
    the shortfall landed on the wrong order (real case: two orders wanting 4 from 7 →
    picked S01532 showed Missing 1 while un-picked S01549 wrongly showed Missing 0). The
    new formula is symmetric and correct: both competing orders show Available 3 / Missing
    1 (honest — 8 demanded from 7), own demand is never subtracted from itself (it's not
    an "other order"), and it no longer depends on pick/delivery state. Same formula is
    used for set components. (Tested: T-14 competing orders.)

# 1. Purpose & Business Context
Rental staff need a **trustworthy "available for this period" figure** and a way to
**see where the stock actually is**. Two gaps cause wrong numbers and confusion:
1. **Repairs are invisible to availability.** A broken unit in an open repair order is
   physically present but **not rentable** — yet standard availability still counts it.
2. **The figure is a single opaque number.** Staff cannot see that (e.g.) of 10 units, 3
   are at a customer, 2 are in QC (not put away), 1 is in repair — so only 4 are pickable.

Separately, **set availability** (rental_set) uses custom own-demand/competing-demand
math that is fragile. It should be the **limiting component's whole-set count**, built on
the same (now repair-aware) component availability, with own-demand handled correctly.

# 2. Background & Root-Cause (verified in code)
- **Native rental availability** (`sale_stock_renting.sale.order.line._compute_qty_at_date`):
  for start ≤ now uses `qty_available(from_date,to_date,warehouse_id)`; for a future
  start uses `virtual_available`. Other rentals are subtracted via
  `_get_unavailable_qty(..., ignored_soline_id=line.id, warehouse_id=...)`, which
  excludes **this line's** rental demand.
- **rental_set already overrides this** with a custom forecast
  (`_compute_forecast_availability`) that walks stock moves over the period and, for
  confirmed orders, **adds back own outgoing demand** — then **caps at
  `qty_available`** (`current_stock_original`). Because `qty_available` is already
  reduced by the order's own reservation, **the cap can cancel the add-back** for
  current-start orders → confirmed orders may under-count their own availability.
  **This is the double-count logic to verify (§3.3).**
- **Warehouse scoping:** with `warehouse_id` context, `qty_available`/`virtual_available`
  cover the warehouse **view location** = **Input + Quality Control + Stock**. QC/Input
  stock is not yet rentable → the raw figure can **over-count** during multi-step
  reception.
- **Repairs:** `repair.order.move_id` is created **only in `action_repair_done`**. While
  `confirmed`/`under_repair` there is **no stock move**, so the forecast never deducts
  it. Usable fields: `product_id`, `lot_id`, `product_qty`, `state`, `create_date`,
  `schedule_date`, `location_id`.
- **Rental "At Customer"** is an internal location (company rental location,
  `company.rental_loc_id`) — so it is naturally part of a location breakdown.

# 3. Design Decisions — Options, Pros/Cons & Rationale
## 3.1 Multi-step over-count (QC/Input not rentable)
**Decision:** **keep native availability** (do not re-implement the forecast engine) and
make the reality **visible** via the per-location breakdown in the pop-up; rely on
**standard padding/preparation time** (config only, no custom padding code) as the
reception buffer.

## 3.2 Repairs → availability, and optional dependency
- **Extend the availability computation** (targeted override of the rental_set forecast):
  after the normal figure, **subtract** the quantity tied up in **open** repairs
  (`state not in ('done','cancel')`) whose window `[create_date → schedule_date,
  extended to now if overdue]` overlaps the rental period. Keyed by product (and `lot_id`
  for serials). The unit stays at its location; this is a logical deduction.
- **Optional `repair`:** guard every repair access with a model-presence check
  (`'repair.order' in self.env`). If absent: **no deduction, no pop-up repair line, no
  crash**. `repair` is **not** added to rental_set's hard `depends`.

## 3.3 Own-demand for confirmed orders (no double-count) — SHOW IT, don't hide it
The requirement **still holds**: a confirmed order must see its **own** reserved units as
available **to itself** (not subtracted twice). Today's code tries to solve this with a
hidden add-back that is then negated by a cap at `qty_available` — opaque and buggy.
**Decision (the agreed solution):** make the reservation split **explicit in the pop-up**
rather than hide a correction. The pop-up shows:
- **Reserved by this order** — quantity this order has already reserved (its own pickup
  moves), which **counts as available to this order**;
- **Reserved by other orders** — quantity reserved by *other* orders, which is **not**
  available to this order.
The headline availability number is then defined transparently and *verifiably* from
those lines, so the double-count question is answered by inspection. The underlying figure
still counts this order's own reservation as available to itself; the pop-up now proves it.
Covered by tests T-05 (number correct) and T-12 (both reservation lines shown).

## 3.4 Set availability
**Decision:** set availability = `min over leaf components of
floor( component_availability / cumulative_qty_per_set )`, where `component_availability`
is the same repair-aware, own-demand-correct figure used for standalone lines. Remove the
separate manual competing-demand arithmetic where the component figure already accounts
for it. Non-storable components remain limitless; nested sets traverse to leaves.

## 3.5 Placement
**Decision:** **fold into rental_set** (no separate module). Availability, sets and the
pop-up already live there; one module keeps the logic coherent and avoids cross-module
override ordering.

## 3.6 Pop-up breakdown = location-driven
**Decision:** build the breakdown from **warehouse internal locations** + the
period-aware reservation split (no separate "At Customer" line — see §0.8):
- per-location on-hand in the warehouse (Input / Quality Control / Stock …),
- **Reserved by this order** — period-overlapping commitment of *this* order (its
  reservations + not-yet-returned deliveries); **available to this order** (§3.3),
- **Reserved by other orders** — period-overlapping commitment of *other* orders
  (includes their units still out at customers); **not** available to this order (§3.3),
- **In Repair** = open-repair qty (only if `repair` installed; shown as its own line
  because the unit still sits in a physical location and would otherwise be hidden),
- **Pickable / Available to this order** = usable-stock on-hand − reserved-by-others −
  in-repair (+ reserved-by-this-order counts as available to itself).

*Note:* "Reserved by …" here means the **period-aware rental commitment** (the same basis
as `_get_unavailable_qty`), not merely current stock reservations — that is what lets it
correctly absorb units currently at a customer without a separate, period-blind line.

# 4. Goals
- **G1** Availability excludes units in open repair over the repair window — **only when
  `repair` is installed**, and never crashes when it isn't.
- **G2** The pop-up shows a location-driven breakdown (incl. At Customer and, if present,
  In Repair) and the net **Pickable** figure.
- **G3** Set availability is the limiting component's whole-set count, built on the same
  component availability.
- **G4** No custom padding logic — standard preparation/padding config only.
- **G5** **No double-count:** a confirmed order sees its own reserved units as available
  to itself (verified, not assumed).
- **G6** Small, isolated footprint inside rental_set; native forecast engine not rewritten.

# 5. Functional Requirements
## 5.1 Repair-aware availability (RAV-01…04)
- **RAV-01** Availability is reduced by product qty tied up in **open** repairs
  (`state not in ('done','cancel')`) whose window overlaps the rental period.
- **RAV-02** Serial products: a repair on a `lot_id` removes that one unit; qty products:
  remove `product_qty`.
- **RAV-03** `done`/`cancel` repairs do not reduce availability.
- **RAV-04** If `repair` is not installed: no deduction, no repair UI, **no error**.

## 5.2 Availability pop-up breakdown (RAV-05…07)
- **RAV-05** Show, for the picking warehouse + period, an accounting flow:
  **Total stock** − **Reserved by other orders** − **In Repair** (if repair installed)
  = **Available for Rent**; then **of which reserved for this order**. Available for Rent
  equals the native availability figure (§0.9). No "At Customer" line in this
  *availability* section — those units are already inside the reserved figures (§0.8).
- **RAV-14** Below it, a **physical partition by current on-hand** that sums to Total:
  warehouse locations (Input/QC/Stock…), **At customer**, **In repair**, and a small
  reconciling **Other / in transit** bucket (§0.11). Uses current quants (robust, ≥ 0) —
  not a per-location pickup forecast (which produced phantom/negative buckets). Keeps
  Total a stable anchor; period-awareness lives in "Available for Rent".
- **RAV-06** Repairs get an explicit line (only when repair installed).
- **RAV-07** Read-only; adds no blocking behaviour.
- **RAV-13** The **Reserved by this order** and **Reserved by other orders** lines are
  always shown (rental storable lines), making the own-demand handling auditable.

## 5.3 Set availability (RAV-08…09)
- **RAV-08** Set availability = `min over leaf components of
  floor( component_availability / cumulative_qty_per_set )`.
- **RAV-09** Non-storable components are limitless; nested sets traverse to leaves; own
  demand handled per §3.3.

## 5.4 Own-demand & guards (RAV-10…12)
- **RAV-10** A confirmed order's own reserved units count as available to itself
  (no double-count; the cap must not negate the add-back).
- **RAV-11** Padding/preparation time is standard config; this work adds none.
- **RAV-12** Only rental orders (`is_rental_order`) and storable products are affected.

# 6. Technical Approach (proposed)
- **Optional repair helper:** on `product.product`, a method returning open-repair qty
  overlapping `[from_date, to_date]` for a warehouse (and optional lot), implemented as
  `if 'repair.order' not in self.env: return 0.0` then a search on open repairs. No
  import of the repair module; pure registry check.
- **Availability:** extend rental_set's `_compute_forecast_availability` /
  `_compute_qty_at_date` to (a) subtract the repair helper and (b) fix the own-demand cap
  (§3.3). Standalone and component lines share this figure.
- **Set availability:** `_compute_set_availability` reads the component figure and applies
  `floor(min(avail / qty_per_set))`.
- **Pop-up:** extend the native `qty_at_date_widget` (OWL) + server data with the
  location-driven breakdown; repair line rendered only when repair is installed.

# 7. Tests (reassessed — `rental_set/tests/test_rental_availability.py`)
| # | Test | Verifies | Requirement |
|---|---|---|---|
| T-01 | test_open_repair_reduces_availability | open repair over the period lowers availability by product_qty | RAV-01 |
| T-02 | test_done_repair_does_not_reduce | a `done` repair does not reduce availability | RAV-03 |
| T-03 | test_repair_outside_period_ignored | repair window not overlapping the period → no effect | RAV-01 |
| T-04 | test_serial_repair_removes_one_unit | repair on a serial removes exactly that unit | RAV-02 |
| T-05 | test_confirmed_own_demand_not_double_counted | confirmed order sees its own reserved units as available (add-back not negated by the cap) | RAV-10, G5 |
| T-06 | test_set_availability_limiting_component | set avail = floor(min component avail / qty-per-set) | RAV-08 |
| T-07 | test_set_non_storable_limitless | non-storable components don't constrain the set | RAV-09 |
| T-08 | test_breakdown_is_consistent | Pickable = usable on-hand − reserved-by-others − in-repair; no double-count of at-customer units | RAV-05 |
| T-13 | test_at_customer_not_double_counted | a unit out at a customer for another order is reflected once (via reserved-by-others), and a future period it returns in time for is not reduced | §0.8 |
| T-09 | test_repair_not_installed_no_crash | with repair absent (simulated), availability computes and no repair UI/deduction | RAV-04 |
| T-10 | test_non_rental_untouched | non-rental sale line availability unchanged | RAV-12 |
| T-11 | test_existing_set_availability_regression | prior rental_set set-availability tests still pass (no regression) | G3 |
| T-12 | test_reservation_split_shown | pop-up exposes Reserved-by-this-order and Reserved-by-other-orders with correct values | RAV-13 |

*Note:* existing rental_set availability tests will be **re-run and adjusted** where the
figure legitimately changes (repair deduction, cap fix); any that encoded the old capped
behaviour will be updated to the corrected expectation, documented in the commit.

# 8. Out of Scope / Deferred
- Rewriting the native forecast to a usable-location min-over-period engine (kept native).
- Any change to native padding/preparation-time logic.
- Cleaning/QC as an explicit rentability gate beyond pop-up visibility (a return-route
  location step, tracked separately).

---

# 9. Addendum — Hired-in (Rental Purchase) equipment

*Added 2026-09-07 alongside the `rental_purchase` module. These terms are **soft-gated**:
they only apply when `rental_purchase` (and `purchase_stock`) are installed, checked via
field existence on `stock.move`. With those modules absent the engine is unchanged.*

## 9.1 Goal
Equipment **hired from a supplier** (see `docs/rental_purchase_requirements.md`) is received
into our warehouse **supplier-owned** and returned at the end of the hire. During the hire it
must appear as **temporarily rentable**; after the return date availability must fall back to
baseline — a clean bump, with **our own stock never hidden**.

## 9.2 Terms (both legs "operational")
The canonical formula is unchanged:

```
available = physical_total − reserved_by_others − in_repair − transfer_out + transfer_in
```

Two soft extensions, applied in `product.product._rental_transfer_sum`:

- **RAV-P01 — supply credited operationally.** An **incoming** move that is a Rental Purchase
  supply (`move.purchase_line_id.order_id.is_rental_purchase`) is credited in `transfer_in`
  from its **arrival date** (= rental start) **regardless** of the picking type's
  `rental_incoming_policy`. Rationale: hired supply is trusted because it is *paired* with a
  scheduled supplier return. Normal purchases are unchanged (still governed by their policy).
- **RAV-P02 — return counted as a departure.** A Rental Purchase **return** (identified in the
  transfer-out moveset by `move.rental_purchase_order_id`, destination = supplier location) is
  counted in `transfer_out` from its **scheduled return date**, even while still `waiting` on
  the receipt. The earlier "wait until received" guard was **removed** — it is no longer needed
  because the paired arrival is credited in the same breath (RAV-P01), so the two net out and
  own stock is never subtracted phantom-wise.

Both the scalar engine (`_rental_transfer_moves` / `_get_transfer_*_qty`) and the batch
reporting engine (`_rental_available_batch`) include the Rental Purchase return in the
transfer-out moveset and share the single `_rental_transfer_sum`, so report and pop-up agree.

## 9.3 Net behaviour (why it is symmetric and safe)
For a hire of N units received into warehouse W over `[start, return]`, with `own` = our own
on-hand of the product at W:

| window | transfer_in | transfer_out | availability |
|---|---|---|---|
| before `start` | 0 (arrival not yet present) | 0 | `own` |
| during `[start, return)` | +N | 0 | `own + N` |
| after `return` | +N (open) / 0 (once received=physical) | −N | `own` |

Consistent **before and after** the receipt is validated: once the receipt is done the units
move from the operational `transfer_in` credit into `physical_total`, while `transfer_out`
keeps subtracting the (reconciled) return demand — the number never jumps at validation.

## 9.4 Reconciliation coupling
The report reads the return move's `product_uom_qty`. The `rental_purchase` module reconciles
that demand to **`received + still-pending − returned`** (see RP-40), so:
- **received less, no back-order** → smaller departure → the bump shrinks to what arrived;
- **back-order pending** → departure stays full (units still coming);
- **over-receipt** → departure grows.

## 9.5 Not changed
Standard rental delivery/return (sales side), the reserved-by-others term, repair, at-customer
attribution, and native forecast are all untouched. The receipt credit is gated on
`is_rental_purchase`; the transfer-out change only affects supplier-bound Rental Purchase
returns.

## 9.6 Open items (see rental_purchase §16)
Re-renting hired-in gear past its return date (over-commitment signal), owner-agnostic
physical base, multi-step reception chaining, effective-vs-declared return date — tracked in
`docs/rental_purchase_requirements.md`.

## 10. On option by other orders (awareness, informational)

### 10.1 Problem
A quotation can already hold stock before the client confirms — the order is **"on option"**
until its `validity_date`. Those units may firm up, so a salesperson creating a *new* order
should be warned, even though the option is only a **soft** hold.

### 10.2 Definition
On option is an **explicit opt-in**: the quotation carries a **`rental_on_option`** boolean
("On option", shown next to the **Expiration** field on rental quotations). The option's end
date is that **Expiration** (`validity_date`).

`product._get_on_option_qty(from, to, ignored_order_id, warehouse_id)` = the peak concurrent
quantity, over `[from, to]`, of **other** orders' rental lines where:
- `is_rental`, product matches, `order.warehouse_id` matches;
- `order.state in ('draft', 'sent')` (not yet confirmed);
- **`order.rental_on_option` is True** (opt-in — an unflagged quotation never counts);
- `order.validity_date` is empty or **≥ today** (option still alive; empty = indefinite);
- `order_id != ignored_order_id` — the **whole current order is excluded**, so an order never
  counts itself (even a draft order does not count its own option);
- effective `[pickup, return]` overlaps the window.

Computed with the **same step-function** (`_get_rented_quantities` + `_reserved_peak`) as the
confirmed reserved term, so overlapping options are counted at their peak, never double-counted
across time. Confirmed orders are excluded here (they already count under *Reserved by other
orders*).

### 10.3 It never changes committed availability
`Available to this order` stays exactly `max(Total − Reserved(confirmed) − InRepair − transfers,
0)`. On-option is purely additive display plus a derived worst case:
```
available_if_options_confirm = max(Available − OnOptionByOthers, 0)
```

### 10.4 Surfacing
- Fields on `sale.order.line`: `rental_on_option_other`, `rental_on_option_until` (earliest
  overlapping option's `validity_date`, ISO string), both from `_compute_rental_breakdown`.
- Pop-up ("For this rental"): an **orange** line *On option by other orders (until <date>)* and
  *Available if those options confirm*.
- **Availability icon** turns **red — risk-based**: `demand > available_if_options_confirm`
  (`demand = max(order demand, line qty)`). Because `available_if_options_confirm ≤ Available`,
  this can only turn the icon red *earlier*, never hide a real shortage. Not noisy: no red when
  there is ample stock.
- **Availability report**:
  - drill-down (`get_cell_detail`): adds `on_option` (scalar) and `option_orders` (list, with
    each option's `until` date), rendered as an orange "On option by other orders (soft hold)"
    table.
  - matrix: an **"Include options (on option)"** checkbox (`include_options`, default off). Off →
    cells show committed availability (unchanged). On → each cell's `available` becomes the worst
    case `committed − on_option` (step-function built once per product/warehouse over the window,
    peak read per column), and "Only show unavailability" then reflects that worst case. Capacity
    is never changed.

### 10.5 Toggle
Company setting `rental_flag_options` (Inventory/Rental settings, default **on**) gates the whole
feature (compute, pop-up lines, red icon). Off → behaves exactly as before.

### 10.6 Not changed
Committed `Available`, the reserved/repair/transfer terms, set availability, and the report
matrix numbers are all untouched. Only display + the icon's red trigger change.
