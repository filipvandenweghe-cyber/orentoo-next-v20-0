# Orentoo — Project Memory (for Claude)

Odoo **19.0** on **Odoo.sh**. Custom rental modules under `/home/odoo/src/user`.
This file is the durable context; the `docs/*_requirements.{md,docx}` files hold the
full rationale per feature. Read this first.

## Workflow rules (important)
- **Push with `odoosh-push` only — never `git push`.** HEAD is detached at `main`.
- **Bump the manifest `version`** whenever schema/views/assets change, or Odoo.sh won't
  pick up the update on rebuild.
- **Commit/push only when the user asks.** Commit footers:
  - `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_015UyktmijNHjiCtQB359rzX`
- **Running tests locally** (plain `odoo-bin -i` marks user modules "not installable" —
  you must pass the Enterprise addons path):
  ```
  /home/odoo/src/odoo/odoo-bin \
    --addons-path=/home/odoo/src/odoo/addons,/home/odoo/src/enterprise,/home/odoo/src/themes,/home/odoo/src/user \
    -d filipvandenweghe-cyber-orentoo-demo-abinbev-main-36827463 \
    -u <module> --test-enable --test-tags /<module> --stop-after-init --no-http
  ```
- `odoo shell` works (same addons path). Shell sessions **roll back** unless you call
  `env.cr.commit()`. The dev DB is `filipvandenweghe-cyber-orentoo-demo-abinbev-main-36827463`.
- An Odoo.sh **rebuild** rebuilds container+DB from git; it does not touch git history.

## Warehouses / locations (dev)
- **PRO** ("Pro-Designed.com", company Pro-Designed.com): **1-step delivery** (`ship_only`) +
  **3-step reception** (`three_steps`, Rental→Input→QC→Stock). Input/QC/Stock are the internal
  children of the PRO view — there is **no Packing/Output** location, so `pick_pack_ship`
  delivery is not valid here (setting it produces a broken chain). Rental pickup =
  Stock→Rental; return = Rental→Input→QC→Stock.
- **WH** ("My Company (San Francisco)"): 1-step (`ship_only`).
- **Rental "at customer" location** = `company.rental_loc_id` (displayed "Customers/Rental"),
  `usage='internal'` (so rented goods still count as company stock) but **outside** the
  warehouse tree; its parent "Customers" has `usage='customer'`.
- Real rental round-trips (pickup + return) are created at confirmation only for orders
  made **`with_context(in_rental_app=True)`** (the Rental app path). `_create_rental_order`
  in tests does NOT set it → no return picking.

## Modules
- **rental_set** — rental sets (a product expands to hidden component lines) + **rental
  availability** + the availability pop-up widget. `repair` is an *optional* dep (soft
  `'repair.order' in env` check; never in `depends`).
- **sale_flow** — commercial/logistic flow tracking (`sale.flow.line`) + **return
  (receipt) demand reconciliation**. Hooks every `stock.move._action_done`.
- **rental_scanning** — barcode prepared-package / set-barcode picking (backend + barcode app).
  Also shows the reusable **Repair warning modal** on serial scans (depends on `rental_serial_log`).
- **rental_serial_log** — per-serial rental history (delivered/returned/repair) tab on the lot form.
  Also enforces **instance-wide serial uniqueness** and **rental-return serial control** (see below).

## Key design decisions (do not regress)
### Availability (rental_set) — "Option A"
- `Available to this order = max(Total physical stock − Reserved by other orders − In Repair, 0)`.
  - **Total physical stock** = current on-hand across the warehouse internal/transit
    locations **+** the rental (at-customer) location (conserved; stable across steps).
  - **Reserved by other orders** = native `product._get_unavailable_qty(from,to,
    ignored_soline_id=line.id, warehouse_id=…)` (period-aware; SOL-based; excludes this line
    so an order never subtracts its own units from itself).
  - **In Repair** = `product._get_repair_unavailable_qty(...)` (open repairs over their
    window; 0 if `repair` not installed).
- **Set availability** = `floor(min over leaf components of component_avail / qty-per-set)`.
- Pop-up = two sections: *For this rental* (time-based) + *Physical stock (right now)*
  (a partition that sums to Total). No forecast rewrite; padding stays standard config.
- Docs: `docs/rental_availability_requirements.{md,docx}`.

### Return (receipt) demand (sale_flow) — "Option B"
- **Receipt = what has gone OUT to the customer** = Σ of DONE outbound moves that reached
  the customer/rental location. "Until it is out, the client is not expected to return it."
- Multi-step legs (to Packing/Output) never reach the customer → no inflation. Over-delivery
  → expect what shipped. Over-pick not shipped → not expected (put it back, no special undo).
  Pending back-order → not expected until it ships.
- A guard leaves the return untouched while a delivery is in progress (nothing shipped +
  outbound pending) so the return picking isn't cancelled mid-multi-step.
- Root cause fixed: the old code summed pending outbound moves across every multi-step leg
  (4→8→12). Docs: `docs/sale_flow_return_demand_requirements.{md,docx}`.

### Serial uniqueness (rental_serial_log)
- `stock.lot.serial_unique_key` = normalized (`.strip()`, **case-sensitive**) serial name,
  populated **only** for `tracking='serial'` (NULL otherwise → lots/batches untouched).
- Enforced by a **partial unique DB index** `WHERE serial_unique_key IS NOT NULL` (built in
  `init()`; race-safe, cross-company) + an `@api.constrains` (raw-SQL lookups → friendly
  message, no ORM flush) that also implements **Option B**: a batch may not reuse a serial
  string and vice-versa. Batch-vs-batch duplicates stay allowed.
- Install/upgrade **hard-stops** on legacy duplicates (pre-migration + `post_init_hook` +
  `init()` guard), reporting conflicts and **changing no data**.
- **Physical on-hand uniqueness** (`stock.quant._check_serial_single_on_hand`): a
  serial-tracked lot may be on hand in **at most one** internal/transit location
  **instance-wide (all companies)**. Standard Odoo only *warns* here (Inventory
  Adjustments) and its `check_quantity` groups per location tree, so the same serial
  could be counted into two warehouses/companies. Customer locations are excluded so
  the deliver-before-receipt transient stays allowed; moving the single unit (src→0,
  dest→1) is fine.
- **Admin audit**: Inventory → Reporting → *Serial Number Duplicate Audit*
  (`ir.actions.server`, stock managers) → lists the offending lots or a "clean"
  notification via `stock.lot._serial_duplicate_lot_ids()`; changes no data.
- Docs: `docs/serial_uniqueness_requirements.md`.

### Rental return serial control + repair scan warning (rental_serial_log / rental_scanning)
- Returns accept **only serials delivered on the order** and **never create a new serial**.
  Both hooks defer to one override point `sale.order.line._rsl_returnable_lot_ids()`
  (**P1 = `pickedup_lot_ids`**; **P3** will widen to "all serials at the client" —
  hooks/modal/tests untouched). Invariants always: serial must already exist and be returnable.
  - **H1** `sale.order.line` `@api.constrains('returned_lot_ids')` = flow-agnostic backstop
    (picking `_action_done`, wizard, import, manual) — same transaction, so a bad return rolls back.
  - **H2** `stock.picking.button_validate` pre-check (`_rsl_check_return_serials`) on the leg
    leaving `rental_loc`: rejects unknown `lot_name` (resolved via `serial_unique_key`) and
    non-returnable lots *before* super → prevents server-side lot creation + friendly message.
- **Repair scan warning** (non-blocking): `stock.lot.rsl_repair_warning(serial)` reports an
  **active** repair (`state in ('confirmed','under_repair')`, read live — no duplicated flag);
  the reusable JS modal (`rental_scanning/.../repair_warning.js`, wired into
  `_processBarcode` → fires on PPB **and** native serial scans) asks **Proceed Anyway**/Cancel;
  proceeding logs a `repair_override` event via `rental.serial.log.rsl_log_repair_override(...)`.
- Docs: `docs/rental_return_serial_requirements.md`.

## Requirement docs
- `docs/rental_availability_requirements.{md,docx}`
- `docs/sale_flow_return_demand_requirements.{md,docx}`
- `docs/rental_serial_log_requirements.{md,docx}`
- `docs/rental_scanning_requirements.{md,docx}`
- `docs/serial_uniqueness_requirements.md`
- `docs/rental_return_serial_requirements.md`
