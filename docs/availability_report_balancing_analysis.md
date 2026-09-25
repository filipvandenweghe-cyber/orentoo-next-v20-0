# Availability Report & Rental Stock Balancing — Analysis

**Project:** Orentoo — Odoo 20.0 on Odoo.sh
**Scope of this document:** Analysis only *at the time of writing*. **The report has since been implemented** — see `rental_set/models/rental_availability_report.py`, `static/src/js/availability_matrix.{js,xml}` and `views/rental_availability_report_views.xml`. Read the recommendations below as design history; where they differ from the shipped code, the code wins (notable: the `clamp` kwarg and the batch-reuse rule were implemented; *Projected Availability* and `include_projected` were not).
**Purpose:** Feed a functional analysis (for review by ChatGPT and the team) of a new
**Availability Report** and later **Rental Stock Balancing** feature, built strictly on top of
the existing (canonical) Orentoo availability engine.

---

## Headline findings (read these first — they reframe the brief)

1. **There is exactly ONE availability calculation in Orentoo, and it is "Operational
   Availability."** It is `product.product._rental_available_qty(...)` in
   `rental_set/models/product_product.py`. Everything (order lines, set components,
   kiosk/website/POS via MCRF) funnels through it. This is genuinely canonical and reusable.

2. **"Projected Availability" does NOT currently exist as a calculation.** This is the most
   important correction to the brief. "Projected" exists only as a *per-operation-type policy
   value* (`stock.picking.type.rental_incoming_policy = 'projected'`) whose meaning is *"this
   incoming supply is deliberately EXCLUDED from Operational Availability."* There is **no
   method that returns a projected number**, and nothing stores or exposes one. So the
   instruction "reuse the existing Projected Availability calculation, do not redefine it"
   cannot be met literally — there is nothing to reuse yet. The report will have to **derive**
   Projected as a thin reporting layer on top of the same engine primitives. The building
   blocks already exist and a faithful derivation is clean (see §11, §39, Q2/Q10).

3. **The engine already solves the "complete-interval" problem in a single call.** Native
   `_get_unavailable_qty(from,to)` returns the *peak concurrent* reservation across the window,
   and every other term in the formula is already evaluated worst-case over the interval. So
   `_rental_available_qty(col_start, col_end)` called **once per cell** already yields "max
   additional units rentable for the COMPLETE interval." No per-cell sub-sampling/min-loop is
   needed. The 7/2/5 → 2 example maps exactly onto the peak-concurrency algorithm.

4. **Company and warehouse are explicit method arguments, not context.**
   `_rental_available_qty(from, to, warehouse=, company=, ignored_soline_id=)`. Ideal for a
   multi-company/multi-warehouse report: no `with_company` juggling, no context leakage;
   results are independent per company and per warehouse by construction.

5. **One tiny, backward-compatible engine touch is warranted** (an optional `clamp` flag) so
   the report can show negative availability / >100 % utilisation that the brief explicitly
   requires. Everything else is pure new reporting code.

---

## Modules & models

### 1. Relevant Orentoo custom modules

| Module | Role for this work |
|---|---|
| `rental_set` | **The** availability engine (`product_product.py`, `sale_order_line.py`), the pop-up widget, transfer grounding, per-operation policy, capacity primitives. Primary module. |
| `sale_flow` | Return-demand reconciliation; **no** availability engine. Relevant only because it moves real stock (affects the DONE moves the engine reads). |
| `multi_channel_rental_flow` | A *consumer* of the engine (`mcrf_service.py` calls `_rental_available_qty` directly). Confirms the reuse contract. |
| `rental_serial_log` | Pure history log; **zero** availability impact. |
| `rental_scanning` | Barcode picking; no availability impact. |
| `pro_designed_setup` | Sets up the PRO company/warehouse/rental-location/routes topology used in testing. |
| `website_sale_stock_renting_set` | Has its *own* ecommerce set-browsing helper (`_get_set_availabilities` / `_get_set_free_qty`) that is **not** period-aware and **not** used by the rental engine — do not confuse with the canonical path. |

### 2. Relevant standard Odoo modules

- `sale_renting` — fields `start_date`, `return_date`, `reservation_begin`, `is_rental`.
- `sale_stock_renting` — native `_get_unavailable_qty`, `_get_active_rental_lines`,
  `_get_rented_quantities`, `preparation_time`, `res.company.rental_loc_id`. **This is the
  native rental availability core the Orentoo engine extends.**
- `stock` — quants, moves, picking types, locations, warehouses, routes.
- `repair` — optional/soft dependency (repair deduction).
- `sale_purchase_stock_inter_company_rules` + `purchase` + `mrp` — installed; intercompany
  flow config exists but has never actually run in the dev DB.

### 3–4. Models & methods — Operational Availability

**`product.product`** (`rental_set/models/product_product.py`):

- `_rental_available_qty(from, to, warehouse, ignored_soline_id, company)` — **the formula**:
  `max(total − reserved_other − in_repair − transfer_out + transfer_in, 0)`.
- `_rental_physical_total` → `_rental_warehouse_onhand` + `_rental_at_customer_qty`
  (conserved now-snapshot).
- `_get_repair_unavailable_qty`, `_get_transfer_out_qty`, `_get_transfer_in_qty`,
  `_rental_transfer_qty`.
- Override of native `_get_active_rental_lines` (adds effective-interval overlap).

**`sale.order.line`** (`rental_set/models/sale_order_line.py`):

- `_compute_forecast_availability` / `_get_component_available_qty` — both delegate to
  `_rental_available_qty`.
- `_get_rented_quantities` (override) + `_rental_effective_pickup_date` /
  `_rental_effective_return_date` / `_rental_effective_reserved_qty` / `_rental_scrapped_qty`
  — operation-grounded timing.
- `get_rental_warehouse_availability`, `_compute_rental_breakdown`, `_rental_stock_partition`
  — the pop-up's data providers.

**Native** (`sale_stock_renting`): `_get_unavailable_qty`, `_get_rented_quantities`,
`preparation_time`.

### 5–6. Models & methods — Projected Availability

**None exist.** The only artifact is `stock.picking.type.rental_incoming_policy`
(`rental_set/models/stock_picking_type.py`) with values `projected` (default) / `operational` /
`ignore`, consumed *only* inside `_rental_transfer_qty(direction='in')` to decide whether an
incoming move counts operationally. Projected must be assembled by the report (see §39).

### 7. Reusable without changing semantics?

- Operational: **yes**, directly.
- Projected: **must be newly derived** (nothing to reuse).
- One optional `clamp` flag recommended for signed values (§30).

---

## Exact current behaviour

### 8. Operational Availability — exact behaviour

```
Available(product, company, warehouse, [from,to]) =
    max( total_physical
         − reserved_by_other_orders   (native peak-concurrency over [from,to])
         − in_repair                  (open repairs overlapping the window)
         − transfer_out               (confirmed relocations leaving, departing ≤ to)
         + transfer_in                (confirmed relocations / opted-in supply, arriving ≤ from)
       , 0)
```

- **total_physical** = on-hand across the warehouse's own internal/transit locations **+**
  units still out at a customer attributed to this warehouse (net DONE moves to/from
  `company.rental_loc_id`, keyed by `order.warehouse_id`). Conserved and stable across
  pick/pack/ship steps.
- **reserved_by_other_orders** = native `_get_unavailable_qty`: filters
  `is_rental & state='sale'` lines on this warehouse (`order_id.warehouse_id`), builds a
  step-function of pickup(+)/return(−) deltas, walks it in date order and takes the
  **maximum cumulative concurrent** quantity between `from` and `to`. Orentoo grounds those
  deltas on **operation dates** (effective pickup = earliest outbound leg; effective return =
  first inbound leg, floored at now if overdue, or the actual done date for early returns)
  rather than declared header dates. Excludes `ignored_soline_id`. **Quotations do not count**
  (state must be `sale`).
- **in_repair** = sum of `product_qty` of open repairs (`state ∉ done/cancel`) whose
  `[create_date, schedule_date]` (floored at now if overdue) overlaps the window, optionally
  scoped to the warehouse's locations. 0 if `repair` not installed.
- **transfer_out** = confirmed (not done/cancel) moves leaving the warehouse view-tree into
  another internal/transit location, `date ≤ to_date`; excludes moves to a rental location.
- **transfer_in** = confirmed moves arriving from outside, `date ≤ from_date`; relocations of
  owned stock (internal/transit source) count by default, external/produced supply counts only
  if the operation type is `operational`, `ignore` excludes; rental-return moves excluded.
- **Padding/turnaround**: native `preparation_time` (company-dependent float hours on the
  product) shifts `reservation_begin` earlier (pickup side only) — already baked into the
  reserved term via the effective pickup date. The report inherits it automatically.

### 9. Projected Availability — exact behaviour

*Does not exist.* Conceptually it would be Operational **plus** the incoming supply currently
held back by the `projected` policy (and any non-`ignore` supply not yet counted), each item
traceable to a `stock.move` / picking / PO / MO. See §39.

### 29. Complete-interval reuse

Call `_rental_available_qty(col_start, col_end)` **once** per (product, company, warehouse,
column). The peak-concurrency reserved term + worst-case evaluation of the other terms already
gives the min availability over the interval. Do **not** sample sub-intervals and min them —
that would re-implement what the engine already does and risk drift.

---

## Factor-by-factor verification

| # | Factor | In Operational today? | How |
|---|---|---|---|
| 10 | Rental quotations | **No** | `_get_active_rental_lines` requires `state='sale'`. (Ambiguity §51.) |
| 10 | Confirmed rental orders | Yes | reserved term (peak concurrency) |
| 11 | Stock | Yes | `total_physical` (on-hand + at-customer) |
| 12 | Planned delivery | Yes (timing) | effective **pickup** date grounds reserved-from on the outbound move |
| 12 | Actual delivery | Yes | moves to rental_loc become at-customer; conserved total unchanged |
| 13 | Planned return | Yes (timing) | effective **return** date = scheduled first inbound leg (floored at now if overdue) |
| 13 | Actual return | Yes | early return released on real done date; then back in on-hand |
| 14 | Late return | Yes | overdue pending return floored at `now` → stays reserved until physically back |
| 15 | Repairs | Yes | `_get_repair_unavailable_qty` hard deduction (soft dep) |
| 16 | Serialised stock | Quantity only | `rental_serial_log` is history-only; serials don't change the number |
| 17 | Padding/turnaround | Yes | native `preparation_time` via `reservation_begin` |
| 18 | Company | Yes | explicit `company=` arg (rental_loc + at-customer attribution); never aggregated across companies |
| 19 | Warehouse | Yes | explicit `warehouse=` arg; on-hand scoped to view-tree; reservations keyed by `order.warehouse_id`; repairs by location; transfers by tree boundary |
| 20 | Internal transfer | Yes | source ↓ `transfer_out` (departure), dest ↑ `transfer_in` (arrival), grounded on `move.date` |
| 21 | Future internal transfer | Yes | same terms — confirmed/not-done moves are exactly the "future" ones |
| 22 | Purchase / incoming supply | **Only if op type = `operational`** | otherwise `projected` (excluded) — intended policy |
| 23 | Manufacturing / incoming production | Same as PO | production source → counts only if type `operational` |
| 24 | Confirmed non-rental SO / outbound | **Partially** | if a confirmed move leaves the view-tree to a non-rental internal/transit dest it hits `transfer_out`; a normal customer delivery (dest = customer) is **not** deducted by the transfer term — reduces on-hand only when done. (Ambiguity §51.) |
| 25 | Intercompany | As a relocation | via transit source → counts on the receiving side like an interwarehouse transfer; source side deducted by `transfer_out` |
| 26 | Multi-step delivery/return | Yes, safe | at-customer measured from the final leg only; total conserved across Input/QC/Stock legs (this is exactly the "stuck in Input" behaviour observed — availability is not distorted by staging) |
| — | Blocked / unavailable / damaged / scrap | Partial | **scrap** from rental_loc releases the reservation (`_rental_scrapped_qty`); no generic "blocked/damaged" quantity concept beyond repair + scrap (§51) |
| — | Substitutions / routes / component availability | Component-level | sets resolve to leaf components via `_rental_available_qty`; routes affect *which* moves exist, not the formula |
| — | Custom Orentoo reservation logic | Yes | effective pickup/return/reserved-qty overrides above |

---

## Sets, Capacity, Utilisation

### 27. Sets excluded from rows — safe

Set availability is a *derived* `floor(min over leaf components of comp_avail / qty-per-set)`
that itself calls `_rental_available_qty` on the physical leaf products. Showing only
physical/component products and omitting sets does not touch the engine and matches the
operational intent (fix the component → the set is fixed). Confirmed no breakage.

### 28. Capacity — proposed definition (reporting-only, engine-consistent)

Capacity is not in the engine. The definition that reuses existing primitives and makes
Utilisation behave exactly as the brief's examples require:

```
Capacity(product, company, warehouse, [from,to]) =
      _rental_physical_total(warehouse, company)          (owned pool: on-hand + at-customer)
    − transfer_out([from,to]) + transfer_in([from,to])    (the pool this WH will hold for the interval)
```

Then, by construction:

```
Operational Available (signed) = Capacity − reserved_other − in_repair
```

i.e. Capacity is the denominator, "used" = `reserved_other + in_repair`, and Available is what
is left. Product/company/warehouse/interval-specific, built only from engine primitives, never
redefining availability.

### 19. Utilisation

`utilisation = (Capacity − Available) / Capacity × 100`, uncapped; `Capacity = 0 → N/A`.

| Capacity | Available | Utilisation |
|---|---|---|
| 40 | 40 | 0 % |
| 40 | 12 | 70 % |
| 40 | 0 | 100 % |
| 40 | −4 | 110 % |

Requires the **signed** (unclamped) Available to reach 110 % / red — see §30.

---

## Does the engine need changing?

### 30–31. Engine-change assessment

**Strictly required: essentially no.** The report can be built almost entirely as a new
reporting layer calling existing methods. **One minimal, backward-compatible change is strongly
recommended** because the brief mandates negative availability and >100 % utilisation, and
`_rental_available_qty` clamped at `max(…, 0)`. **Implemented:** the `clamp=True` kwarg below now exists (`product_product.py`), with `test_clamp_default_unchanged` guarding the default:

- **Minimum concrete change:** add an optional `clamp=True` kwarg to `_rental_available_qty`
  (and pass it through). `clamp=True` preserves *every* existing caller byte-for-byte; the
  report calls `clamp=False` to obtain the signed value. Keeps the formula in exactly one place
  (no re-summing primitives in the report → no risk of a divergent "second engine").
- **Recommended companion (for Projected, not strictly required):** add an
  `include_projected=False` kwarg threaded into `_rental_transfer_qty(direction='in')` so the
  report can request "count all non-`ignore` incoming supply operationally" and get the
  Projected number **from the same code path**. Without it, Projected would have to re-scan
  moves in the report — a soft violation of the single-engine rule.

Both are additive, default-off, and change no existing semantics. Evidence: `-4 available →
110 %`, red for `<0`, and the Projected drill-down deltas cannot be produced from the current
clamped, projected-excluding method without one of these hooks. No correctness *defect* was
found in Operational Availability that must be fixed before reuse.

---

## Proposed architecture

### 32. Backend reporting layer

A new model, e.g. `rental.availability.report` (TransientModel or a small model in
`rental_set` with an `@api.model` service), exposing one RPC:

```
get_availability_matrix(filters) -> {
    'columns': [{'start': iso, 'label': '08:30'}, ...],          # dynamic
    'rows':    [{'category', 'product_id', 'company_id', 'warehouse_id', ...hierarchy...}],
    'cells':   { 'product-company-warehouse-colIdx': {
                    'available': float (signed),
                    'capacity':  float,
                    'projected': float } },
    'display_meta': {...}
}
```

All business logic server-side. The method loops products × companies × warehouses and, per
(product, company, warehouse), builds the reserved step-function **once** for the full report
window and evaluates every column from it (see §33). It calls
`_rental_available_qty(clamp=False)` (or the batch equivalent) so numbers are guaranteed
identical to the order-line pop-up.

### 33. Batch strategy that reuses the engine (no reimplementation)

The reserved step-function returned by
`_get_active_rental_lines(win_start, win_end)._get_rented_quantities([...])` is
**interval-independent**: build it once per (product, warehouse) over the whole report window,
then compute the peak for each of the 48/24/21 columns by slicing the cumulative sum in Python.
Likewise prefetch: quant sums per product across the relevant locations (one `_read_group`),
open repairs per product (one search), open transfer moves per product (one search). This turns
O(products × wh × cols) queries into O(products × wh) while calling the *same* Orentoo/native
primitives — so semantics are preserved. Provide a batch method (e.g.
`_rental_available_batch(products, warehouse, company, columns)`) inside `rental_set` that
orchestrates this reuse; it must not re-derive the formula, only slice pre-built structures.

### 34. OWL / client action

A new `ir.actions.client` (tag e.g. `rental_availability_matrix`) + `menuitem` under
`sale_renting.menu_rental_reporting` (as shipped). A root OWL
`Component` with `proxy()` state, `useService("orm")`, calling `get_availability_matrix` on load
and on filter/nav change. Matches the project's OWL conventions (the pop-up widget uses
`usePopover`, `useService("orm")` and a `computed` `calcData`; register `.xml` before `.js` in
the manifest). *Odoo 20 / Owl 3: `useState` became `proxy()`, and `onWillRender` — still used by
the two list renderers — is imported from `@web/owl2/utils`.* A dynamic client action is right because the number of time columns is dynamic.
Filters (categories/products/companies/warehouses/start/interval/display-mode) as OWL controls;
category selection expands to products server-side (recursive `child_of` on `categ_id`),
unioned + deduped with explicitly selected products.

### 35. Matrix API / data structure

As in §32: separate `columns`, `rows` (hierarchical: category → product → company → warehouse),
and a flat `cells` dict keyed by `row-key + column-index`, each cell carrying `available`
(signed), `capacity`, `projected`. This lets the client switch **Available / Available-per-
Capacity / Utilisation %** *without a server round-trip*: all three derive from `available` +
`capacity` already in the payload; Projected is shown alongside, never replacing Operational.

### 36. Drill-down

Reuse the existing pop-up data providers as the template. A per-cell dialog calls
`get_cell_detail(product, company, warehouse, from, to)` returning: header
(product/company/warehouse/period/operational/capacity/utilisation/projected), the contributing
rental/sale lines (sorted by rental start then end), repairs, and the projected movements
(§39). Reuse `_rental_stock_partition` for the physical breakdown and mirror
`get_rental_warehouse_availability` for "Availability elsewhere."

### 37. Availability elsewhere

For the drilled cell's product, call the **same** `_rental_available_qty` for every other
warehouse of the same company (internal-transfer candidates) and, for authorised other
companies, per their warehouses (intercompany candidates). No shortcut math — identical engine
call, different `warehouse=`/`company=`. Group and label same-company vs other-company; never
aggregate into the selected cell.

### 38. Operational vs Projected exposure

Both numbers are carried in every cell; the UI shows Operational as the primary figure with its
colour rule, and Projected as a visually distinct secondary (e.g. muted/badged), never merged,
never silently substituted. The distinction already lives in the engine as the policy gate; the
report surfaces it, it does not redefine it.

### 39. Can Projected expose its explaining movements? Yes — and this is the natural definition

Projected − Operational = exactly the set of incoming moves the `projected` policy currently
withholds (plus any non-`ignore` supply not yet counted), arriving within the window.
`_rental_transfer_qty(direction='in')` already iterates those `stock.move`s and knows each
one's `picking_id`, `product_uom_qty`, `date`, source usage, and policy. To expose the itemised
deltas (e.g. `+20 PO00425 exp 10 Sep 08:00`, `−5 Transfer Leuven→Brussels 11 Sep 09:00`) the
engine only needs to *return the move list it already scans* (a reporting hook), not new logic.
Each delta links back to its concrete `stock.move` / picking → its PO / MO / transfer.
Recommend the `include_projected` hook (§30) also optionally return this breakdown.

---

## Transfers & intercompany (for the later balancing phase)

### 40. Existing internal-transfer architecture

Standard `stock.picking` with `picking_type_code='internal'` (or resupply routes); identify
eligible transfers by source WH, dest WH, `state` (draft/confirmed modifiable; **assigned locks
quantities**), `scheduled_date` / `move.date`, `company_id`. Grounding uses `move.date` — the
*same* timestamp the transfer terms already use.

### 41. Which transfers can receive extra quantity

Same company, correct source → dest warehouses, state ∈ {draft, confirmed}, arriving in time to
solve the shortage (`move.date ≤ shortage start`), source has enough **Operational Available**
(same engine), product compatible, user has write access. The eligibility list is satisfiable
from existing data.

### 42–43. Intercompany architecture & correct master document

`sale_purchase_stock_inter_company_rules`: an SO in company A auto-creates a PO in company B
(and vice-versa), linked by `auto_generated` / `auto_purchase_order_id` / `auto_sale_order_id`;
destination company via partner → company lookup, warehouse via `intercompany_warehouse_id`;
goods flow through a transit location.

**Concrete limitation (important for phase 2):** line propagation happens **only at document
creation** — appending a line to an existing intercompany SO/PO does **not** auto-sync the
counterpart. So "Add to Intercompany Order" cannot be a naive line append; phase 2 must either
drive propagation explicitly or, more safely, treat the intercompany case as adding to the
**source Sale Order** and re-running the sync path. The correct *master* is the source SO (which
generates the counterpart PO), but the propagation gap must be designed around. Flag as an open
risk (§51).

### 44. Transfer/intercompany timing reuse

Reuse the existing `move.date`-based grounding; a transfer only "solves" a shortage if its
arrival (`transfer_in` credit condition: `date ≤ from_date` of the shortage column) precedes
the shortage. No separate timing rules.

### 45. Existing line vs new line

Prefer increasing an existing compatible `stock.move.product_uom_qty` on the picking over
adding duplicate moves; never manipulate `stock.move.line` (reservations/serials/packages)
directly when the correct operation is changing demand on `stock.move`. Only draft/confirmed
pickings.

### 46. Recalculation after allocation

After writing to the real transfer/intercompany document, simply **re-call**
`get_availability_matrix` (which re-calls the engine). Do **not** do client-side arithmetic
(`Brussels−5, Leuven+5`); the true result depends on move dates, states, policy and the peak
recomputation, which only the engine knows.

---

## Cross-cutting

### 47. Security / record rules

Company & warehouse are explicit args, so no context-based multi-company ambiguity; restrict the
report to `company_id in user.company_ids` (allowed companies), not broad `sudo()`. The
aggregate number uses a little internal `sudo()` (to read rental-location ids and repairs) which
is acceptable for a computed figure, but **drill-down record lists (SO/PO/repairs/transfers)
must be read as the user** so ACLs/record rules apply — never sudo the documents shown. The
future balancing action must check **write** access on the target transfer/document before
adding quantity.

### 48. Performance risks

Naive per-cell computation is `products × companies × warehouses × 48` × several searches →
unacceptable. Mitigated by the batch strategy (§33): build the interval-independent reserved
step-function and prefetch quants/repairs/transfers once per (product, warehouse), evaluate
columns in Python. Also cap default product scope (require at least a category or product
filter) and log/surface any truncation. Correctness/reuse first; the batch is reuse, not a
rewrite.

### 49. Files likely created/changed

- **New:** `rental_set/models/rental_availability_report.py` (RPC + batch orchestration),
  `rental_set/static/src/js/availability_matrix.{js,xml}` (+ scss),
  `rental_set/views/rental_availability_report_views.xml` (client action + menu), security/ACL
  entries; manifest `data`/`assets` + **version bump**.
- **Minimal edits:** `rental_set/models/product_product.py` — add optional `clamp` (and, for
  Projected, `include_projected` + optional breakdown return) to `_rental_available_qty` /
  `_rental_transfer_qty`. Nothing else in the engine.
- **Phase 2 (later):** balancing wizard/dialog + eligibility service (new files), no engine
  change.

### 50. Conflicts / risks with existing customisations

Low for the report (additive, read-only reuse). Watch:

- (a) the `clamp` default must stay `True` so the pop-up, MCRF, set/component checks are
  untouched;
- (b) don't route the report through the ecommerce `_get_set_availabilities` path;
- (c) the transfer terms already changed availability semantics recently (PRO Receipts/Pick
  policy) — the report will faithfully reflect whatever policies are set, so mis-set policies
  will show up as surprising Projected/Operational values (a feature, but worth a note in the
  drill-down).

### 51. Functional ambiguities that materially affect correctness

- **Projected Availability is undefined in code** — you must approve the derivation (Operational
  + non-`ignore` incoming supply arriving in window). Everything Projected hangs on this.
- **Quotations** (`state='draft/sent'`) are **excluded** from Operational. If the report should
  warn on quoted-but-unconfirmed demand, that's a *new* lens, not the current engine.
- **Confirmed non-rental customer Sales Orders** are only deducted when they leave the view-tree
  to an *internal/transit* location; a direct customer delivery reduces stock at done-time, not
  before. If "committed outgoing should reduce what we can promise" must cover ordinary customer
  deliveries, that is a **scope decision** (and a Projected-side item, not Operational) — needs
  a ruling.
- **Intercompany line-append does not auto-sync** (§43) — phase-2 design decision required.
- **"Blocked/damaged/unavailable" stock** beyond repair + scrap has no representation — confirm
  none is expected.

### 52. Recommended sequence (no implementation yet)

1. Approve the **Projected Availability definition** and the two engine hooks (`clamp`,
   `include_projected`).
2. Add the (tiny) engine hooks + unit tests proving `clamp=True` is byte-identical to today.
3. Build the **batch reporting method** (reuse step-function) + tests asserting per-cell
   equality with `_rental_available_qty`.
4. Build the **read-only matrix** (client action, filters, nav, three display modes, colours,
   Operational + Projected).
5. Add **drill-down** (contributing orders, repairs, projected deltas, availability elsewhere).
6. *(Later, separate approval)* **Stock Balancing**: eligibility service → "Add to Internal
   Transfer" (safe first) → "Add to Intercompany" (after solving the propagation gap) →
   recalc via the engine.

---

## Explicit answers (Q1–Q10)

**Q1. Can Operational Availability be reused directly, unchanged?**
**Yes.** `_rental_available_qty(from, to, warehouse=, company=, ignored_soline_id=)` is the
canonical, warehouse/company-parameterised source of truth already used by orders, sets and
MCRF. The only optional tweak is an additive `clamp=False` flag to expose signed values for
utilisation/overbooking; the default preserves current behaviour exactly.

**Q2. Can Projected Availability be reused directly, unchanged?**
**No — because it does not exist as a calculation.** Only the `rental_incoming_policy` *gate*
exists. Projected must be **derived** by the report (reusing the engine's incoming-move logic
via an `include_projected` hook). It reuses, but it is new assembly, not an existing
calculation.

**Q3. Does the engine support `product + company + warehouse + arbitrary [start,end]`?**
**Yes, natively and cleanly** — those are explicit arguments; no context hacks required.

**Q4. Complete-interval availability while reusing the engine?**
**Yes, in one call per cell.** The reserved term is peak-concurrent and all other terms are
worst-case over the interval, so `_rental_available_qty(col_start, col_end)` already answers
"rentable for the COMPLETE interval." No separate sub-interval min-logic.

**Q5. Multi-company & multi-warehouse reusable without refactor?**
**Yes.** Company isolation (rental_loc + at-customer attribution by `order.warehouse_id`) and
warehouse isolation (view-tree on-hand, warehouse-keyed reservations, location-scoped repairs,
tree-boundary transfers) are already correct and never aggregate across the boundary. No
refactor needed; scope the report to the user's allowed companies (no broad sudo).

**Q6. Can Sets be excluded, acting on components?**
**Yes.** Set availability is a derived min-over-components that itself calls the component
engine; showing components and omitting sets is safe and matches operational intent.

**Q7. What should Capacity mean?**
The interval-specific owned pool:
`_rental_physical_total(warehouse, company) − transfer_out + transfer_in`, so that
`Operational Available (signed) = Capacity − reserved_other − in_repair`.
Product/company/warehouse/interval-specific, built only from existing primitives, never
redefining availability.

**Q8. Can "Availability elsewhere" reuse the exact same calculation?**
**Yes** — same `_rental_available_qty` with a different `warehouse=`/`company=`. No shortcut
logic.

**Q9. Can future balancing update real documents and rely on the existing engines to show the
effect?**
**Yes for internal transfers** (add/increase demand on an eligible draft/confirmed
`stock.move`, then recompute). **Qualified for intercompany** — the engine will correctly
reflect the resulting moves, but the intercompany line-append does **not** auto-sync the
counterpart (propagation happens only at creation), so phase 2 must handle that explicitly. In
both cases, recompute by re-calling the engine — never by client-side arithmetic.

**Q10. Is ANY availability-engine change necessary before the report?**
**Only one, minimal and additive:** an optional `clamp` flag on `_rental_available_qty`
(default `True`, byte-identical to today) so the report can show negative availability and
>100 % utilisation. Evidence: the method currently returns `max(…, 0)`, which cannot express
`-4 available / 110 %` or the red overbooking state. Strongly recommended companion (to avoid a
de-facto second engine when computing Projected): an `include_projected` flag threaded into the
existing incoming-move scan so Projected and its itemised deltas come from the *same* code. No
other change, and no correctness defect requires fixing.
