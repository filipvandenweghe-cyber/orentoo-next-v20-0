# Sale Flow — Rental Return (Receipt) Demand
*Functional & Technical Requirements, Goals & Tests — Backend / Sales (Rental) + Inventory (v1, FINAL)*

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Module** | sale_flow |
| **Where** | `services/sale_flow_sync_service.py` → `_reconcile_return_pickings` |
| **Trigger** | Every picking validation (`stock.move._action_done` → `_sync_moves_to_flow`) |
| **Author** | Pro-Designed.com |
| **Status** | Implemented, tested & verified on dev; migrated to Odoo 20 (§8) |
| **Date** | 2026-09-25 (Odoo 20 migration) |

# 1. Purpose & Business Context
When a rental is delivered, a **return picking ("receipt")** anticipates the goods coming
back. Its demand must equal **what the customer actually has to return**. Getting this
wrong causes two visible problems: a **wrong receipt quantity**, and a **phantom
Forecasted‑Report figure** (the return moves feed Odoo's native `virtual_available`).

# 2. Background & Root-Cause (verified in code)
`_reconcile_return_pickings` runs on **every** picking `_action_done` and rewrote the
return demand. Originally it computed `expected = delivered + Σ(pending outbound moves)`.
In a multi‑step delivery route (`Pick → Pack → Ship`) **the same units appear as a move on
every leg**, so summing the pending moves **double/triple‑counted** them:

```
confirm          receipt 4
validate PICK    receipt 8    ← +4 (same units, counted again)
validate PACK    receipt 12   ← +4
validate SHIP    receipt 4    ← collapses to delivered
```
Reproduced on a **plain, non‑set** product, so it was **not** rental_set and **not** native
Odoo — it was this module's leg‑summing. (S00703 / S01788 were the field reports.)

# 3. Design Decision — Options & final rule
Several formulations were tried and reproduced:
1. `current_qty` (ordered) — stable across steps, but **missed over‑delivery** (S01788: ordered 5, delivered 7 → must expect 7).
2. `max(current_qty, delivered)` — fixed over‑delivery, but **missed a pending back‑order** that exceeds the order (S00703).
3. Origin leg (units that left Stock) — robust for multi‑step / over‑delivery / back‑order, but **over‑counts an over‑pick left stranded** in the warehouse (never shipped).

**Final decision — Option B: the receipt = what the customer still holds.**
> "Until it is out, the client is not expected to return it —
> and once it is back, it is not expected again."

```
expected return = Σ DONE outbound moves that reached the customer / rental location
                − Σ DONE return moves that left the customer / rental location
```
(per product, clamped at 0). A **guard** leaves the return untouched while a delivery is
still in progress (nothing shipped yet + outbound still pending), so the return picking is
**not cancelled mid‑multi‑step**; it is only reduced/cancelled once nothing more is coming.

The second term was added for **Odoo 20** (§9). Under 19.0 a return was created ad‑hoc for
one delivery and validated in a single go, so "what went out" and "what is still expected"
were the same number. Odoo 20 creates the rental return picking **together with the
delivery** and links it via `return_id` — which also propagates to its **back‑orders** — so
a return is routinely validated in several legs. Without subtracting what has already been
received, the still‑open leg keeps re‑demanding the full delivered quantity.

# 4. Goals
- **G1** Receipt equals what the customer actually holds (delivered‑to‑customer), never inflated by internal warehouse legs.
- **G2** Over‑delivery is expected back in full.
- **G3** Over‑pick that is never shipped is **not** expected (no special undo — just don't ship it, or put it back).
- **G4** A **pending** back‑order is not expected until it ships; then it is added.
- **G5** No churn: the return picking is not cancelled/recreated while a delivery is in progress.
- **G6** A partial return leaves only the **remainder** expected back; what already came
  back is never re‑demanded, however many legs the return takes.

# 5. Functional Requirements
- **SFR-01** On outbound validation, set each rental product's return demand to the sum of
  **done** outbound moves that reached the customer/rental location, **minus** the sum of
  **done** return moves that left it (clamped at 0).
- **SFR-02** Intermediate legs (to Packing/Output — internal warehouse locations) do **not**
  count → multi‑step never inflates the receipt (it grows only as goods ship).
- **SFR-03** Over‑delivery: whatever shipped is expected back (ship 7 on a 5‑line → 7).
- **SFR-04** Over‑pick not shipped: excess stays in the warehouse → not expected; the
  moment it ships it becomes expected, if put back it simply returns to stock.
- **SFR-05** Back‑order: pending part not expected until shipped; then added.
- **SFR-06** While delivery is in progress and nothing has reached the customer yet, leave
  the return demand as‑is (do not cancel the return picking).
- **SFR-07** Only rental orders (`is_rental_order`) with return pickings are affected; sale
  products untouched.
- **SFR-08** A return validated in several legs (partial return → back‑order) converges:
  each leg reduces the outstanding demand by what it received. Back‑orders of a return
  inherit `return_id`, so they are reconciled like any other open return leg.
- **SFR-09** Units written off at the customer by the **lost/broken wizard** are removed
  from the return demand **by the wizard itself**. The scrap it creates runs under
  `skip_sale_flow_sync=True` so this reconciliation does not also reduce the demand — the
  two paths must never both subtract the same unit.

# 6. Interaction with availability (rental_set)
Consistent single trigger — **"out to the customer"**: crossing into the customer/rental
location is the one event that (a) makes a unit "expected back" (this module) and (b) moves
it from warehouse availability to at‑customer (rental_set). Units in warehouse locations
(Stock/Packing/Output) remain **present in stock** and do not add return demand.

# 7. Tests (`sale_flow/tests/test_sale_flow.py`)
| # | Test | Verifies | Req |
|---|---|---|---|
| test_17 | partial delivery, no back‑order | receipt = delivered (2 of 3) | SFR-01 |
| test_23 | multi‑step Pick→Pack→Ship | receipt never inflates across legs (stays 4) | SFR-02 |
| test_24 | over‑delivery (ship 7 on 5‑line) | receipt = 7 | SFR-03 |
| test_32 | back‑order | pending → receipt 2; after it ships → 3 | SFR-05 |
| test_33 | over‑pick 8, ship only 5 | receipt = 5 (3 unshipped not expected) | SFR-04 |

All `sale_flow` tests green on the dev DB against the full Enterprise addons path.

# 8. Odoo 20 migration notes
- **The rental return picking now belongs to the delivery.** `sale_stock_renting` creates it
  with the delivery and sets `return_id`; back‑orders of a return inherit it. Consequence:
  `_reconcile_return_pickings` now sees *every* open leg of a multi‑leg return, which is why
  SFR‑01 gained its second term.
- **`stock.return.picking` (the return wizard) no longer exists.** A return picking is a copy
  of the original: `picking._create_return()`, per‑move values from
  `_prepare_return_move_default_values()`. The tests build their return picking through the
  order's own return picking rather than a wizard.
- **`sale_stock_renting._create_return()` deletes rental moves** from any *second* return
  built off a rental delivery — by design, since the round‑trip return already exists. A user
  pressing *Return* on a rental delivery therefore gets an empty picking. **Open question:**
  decide whether the rental round‑trip return is the only supported return path, and drop or
  re‑target this module's "return of the delivery" handling accordingly.
- **Field renames used by this service:** `stock.move.product_uom` → `uom_id`;
  `product.rent_ok` → `rent_periodicity` (the rentable test in the reconciliation loop).
- **Scrap** is no longer a `stock.scrap` record: the lost/broken service creates a
  `stock.move` with `is_scrap=True` and calls `_action_scrap()` (see SFR‑09).

# 9. Out of Scope / Notes
- The native **Forecasted Report** is standard Odoo; it now reads correct return moves, so
  its transient inflation is gone.
- The one lens caveat: while over‑picked units sit **staged** in Packing/Output for a
  delivery, availability still treats them as ordinary present stock (physical‑presence
  lens); they convert to "expected back" only when they actually ship.
