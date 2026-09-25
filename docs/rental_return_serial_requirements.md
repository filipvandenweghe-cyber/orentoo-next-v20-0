# Rental Return Serial Control + Repair Scan Warning — Requirements & Design

Modules: **rental_serial_log** (server, from `20.0.1.0.10`) and **rental_scanning**
(client, from `20.0.1.7.0`, now depends on `rental_serial_log`).

## 1. Goals (P1)
1. A rental **return accepts only the correct existing serial** that was
   delivered on that rental/order, and **never creates a new serial**.
2. A **reusable Repair warning** modal fires on any serial scan: non-blocking
   in business terms, but requires an explicit **Proceed Anyway** and writes an
   **audit** entry when overridden.

## 2. How rentals link serials (native, `sale_stock_renting`)
- The order line (`sale.order.line`) holds the serial links: `reserved_lot_ids`,
  **`pickedup_lot_ids`** (delivered), `returned_lot_ids`.
- With rental pickings enabled, returns are **real pickings** (the leg *out of*
  `company.rental_loc_id`). `stock.move._action_done` records `move.lot_ids`
  into `returned_lot_ids` **with no check** that they were delivered — the gap.
- A brand-new serial can be minted on a return in `stock.move.line._action_done`
  when the picking type has `use_create_lots` and a line carries an unknown
  `lot_name`.

## 3. Design

### 3.1 The returnable-serial resolver (single override point)
`sale.order.line._rsl_returnable_lot_ids()` returns the serials that may be
returned against the line. **P1 = `pickedup_lot_ids`** (exactly what was
delivered). This is the one method later phases override:
- **P3** will widen it to "every serial currently at that client" (on-hand in
  `rental_loc` for the partner) **without touching any hook**.
Two invariants hold in every phase: the serial must **already exist** and must
be in the returnable set.

### 3.2 Return enforcement (two hooks, both defer to the resolver)
- **H1 — `sale.order.line` `@api.constrains('returned_lot_ids')`**: every
  returned serial must be in `_rsl_returnable_lot_ids()`. Flow-agnostic
  backstop — covers the picking path (lots linked at `_action_done`, same
  transaction → the whole validation rolls back), the rental return wizard,
  imports and manual edits. A new serial can never be in the set, so this also
  blocks "created on return".
- **H2 — `stock.picking.button_validate` pre-check** (`_rsl_check_return_serials`)
  for the customer-facing return leg (`_rsl_is_rental_return`, i.e. a move with
  `location_id == rental_loc`): before `super()`, reject (a) a `lot_name` with
  no existing lot (**never create**, resolved via `serial_unique_key`), and
  (b) a lot not in the resolver set. Gives an early, itemised, friendly error
  and prevents server-side lot creation. H1 remains the ultimate guarantee.

### 3.3 Repair status + override audit
- **H3 — `stock.lot.rsl_repair_warning(serial_name)`** (RPC-callable): resolves
  the serial by `serial_unique_key`, and if it has an **active** repair
  (`state == 'confirmed'` — Odoo 20 dropped `under_repair`) returns `{has_repair, repair_id,
  reference, state, message}`. Repair state is read **live** (no duplicated
  flag). Never raises.
- **H4 — `rental.serial.log.rsl_log_repair_override(serial, picking, repair)`**
  (RPC-callable): records an idempotent `repair_override` event (new event type)
  when an operator proceeds despite an active repair.

### 3.4 Reusable Repair warning modal (client)
`rental_scanning/static/src/js/repair_warning.js` exports
`checkAndWarnRepair(model, barcode)`. It is invoked at the top of the patched
`BarcodePickingModel._processBarcode`, so it fires on **any** serial scan — the
PPB/set/package flow *and* native serial entry. If `rsl_repair_warning` reports
an active repair it shows a `ConfirmationDialog` (**Proceed Anyway** / Cancel);
Proceed logs the override (H4) then continues, Cancel aborts the scan. Any error
in the check degrades to "no-repair" so it can never break scanning.

## 4. Files
**rental_serial_log**
- `models/sale_order_line.py` *(new)* — resolver + H1 constraint.
- `models/stock_picking.py` *(modify)* — H2 pre-check in `button_validate`.
- `models/stock_lot.py` *(modify)* — `_rsl_open_repairs` + `rsl_repair_warning` (H3).
- `models/rental_serial_log.py` *(modify)* — `repair_override` event + `rsl_log_repair_override` (H4).
- `models/__init__.py`, `__manifest__.py` — wire + version bump `→20.0.1.0.10`.
- `tests/test_rental_return_serial.py` *(new)*.

**rental_scanning**
- `__manifest__.py` — depend on `rental_serial_log`, register asset, version `→20.0.1.7.0`.
- `static/src/js/repair_warning.js` *(new)* — reusable helper.
- `static/src/js/rental_scanning_barcode.js` *(modify)* — invoke on serial scans.

## 5. Tests (all green)
`rental_serial_log` 35/35, `rental_scanning` 20/20; `rental_set`+`sale_flow`
green on Odoo 20 (no regression from the `button_validate` pre-check). The combined
`rental_set` + `sale_flow` suites now hold 128 test methods; the `rental_set_ui`
browser test is tagged `-standard` and runs only where headless Chrome is available.
- H1: delivered serial returnable; non-delivered blocked; resolver = pickedup.
- H2: delivered ok; wrong serial blocked; unknown serial blocked **and no lot created**.
- H3: draft repair not active; confirmed active; clean/unknown never warn; done not active.
- H4: override audit logged once (idempotent).

## 6. Risks / notes
- **Repair check on every scan**: `_processBarcode` calls `rsl_repair_warning`
  for every barcode (one indexed `serial_unique_key` lookup). Cheap, but it is
  an extra RPC per scan; can be narrowed later if needed.
- **Client-eager lot creation**: if the Barcode client created a lot in a prior
  RPC, H1/H2 still block the validation, but the stray lot could persist until
  cleaned up. The server never creates a lot on a return.
- **P3**: only `_rsl_returnable_lot_ids` changes; hooks, modal and tests keep working.
- **JS modal glue** is covered indirectly by the server-contract tests
  (H3/H4); an end-to-end tour is a possible follow-up (documented gap).
