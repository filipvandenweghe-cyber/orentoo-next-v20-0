# Orentoo — Project Memory (for Claude)

Odoo **20.0** on **Odoo.sh**. Custom rental modules under `/home/odoo/src/user`.
This file is the durable context; the `docs/*_requirements.{md,docx}` files hold the
full rationale per feature. Read this first.

## Workflow rules (important)
- **Push with `odoosh-push` only — never `git push`.** HEAD is detached at `main`.
- **Bump the manifest `version`** whenever schema/views/assets change, or Odoo.sh won't
  pick up the update on rebuild.
- **Commit/push only when the user asks.** Commit footers:
  - `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_015UyktmijNHjiCtQB359rzX`
- **Running tests locally.** The `odoo-bin` wrapper on `$PATH` already injects the
  addons path and the database of the current build, so never pass `-d` or
  `--addons-path` (the DB name changes with every build):
  ```
  odoo-bin -u <module> --test-enable --test-tags /<module> --stop-after-init --no-http
  ```
  Run the whole custom suite by listing the modules and tags comma-separated. Browser
  tests (`HttpCase.browser_js`) cannot run in the AI sandbox — Chrome cannot fork there;
  they are tagged `-standard` and run with an explicit `--test-tags`.
- `odoo shell` works the same way (`odoo-bin shell --no-http`). Shell sessions **roll
  back** unless you call `env.cr.commit()`.
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
- **On option by other orders** (informational; never changes committed Available):
  `product._get_on_option_qty(from,to, ignored_order_id, warehouse_id)` = peak of unconfirmed
  quotations **explicitly flagged `sale.order.rental_on_option`** (opt-in; `state in
  ('draft','sent')`, `validity_date` = option end, not lapsed) overlapping the window, via the
  same step-fn as reserved; **excludes the whole current order** (`order_id != …`).
  Surfaced on `sale.order.line` (`rental_on_option_other`, `rental_on_option_until`); shown as an
  orange pop-up line + "Available if those options confirm". The **availability icon goes red
  risk-based**: `demand > max(Available − OnOption, 0)`. Availability report drill-down
  (`get_cell_detail`) adds `on_option` + `option_orders`. Gated by company
  `rental_flag_options` (default on).
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
  **active** repair (`state == 'confirmed'` — Odoo 20 dropped `under_repair`; read live, no duplicated flag);
  the reusable JS modal (`rental_scanning/.../repair_warning.js`, wired into
  `_processBarcode` → fires on PPB **and** native serial scans) asks **Proceed Anyway**/Cancel;
  proceeding logs a `repair_override` event via `rental.serial.log.rsl_log_repair_override(...)`.
- Docs: `docs/rental_return_serial_requirements.md`.

## Odoo 20 migration notes (from 19.0)
Applied across all modules; each change is commented at the call site.

- **Security**: `ir.model.access.csv` + `ir.rule` are gone — one `security/ir.access.csv`
  per module (`model_id` holds the model *name*; a row with an empty `group_id` is a
  restriction, i.e. the old global rule). Converted with Odoo's own
  `odoo-bin upgrade_code --script 19.4-00-ir-access`.
- **Rental products**: `product.rent_ok` (bool) → **`rent_periodicity`** (selection
  `hours/days/nights/weeks`; falsy = not rentable). Rental pricing moved off
  `product.pricing` onto the pricelist + periodicity.
- **Rental price base** (`rental_coefficient_dynamic_pricing`): `_get_pricelist_price()`
  now returns the price of the **whole rental period**. The coefficient engine wants the
  price of **one** period (it applies the duration itself), so
  `rental.pricing.service._to_single_period_price()` divides the periods back out. It is
  applied to *every* base: standalone line, fixed set parent, component sum.
  The "Update Rental Prices" button and `sale.order.show_update_duration` are gone —
  the partner onchange recomputes softly; force with `order._recompute_rental_prices()`.
- **Rental returns**: the `stock.return.picking` wizard is gone. A return is
  `picking._create_return()` (a copy), per-move values from
  `_prepare_return_move_default_values()`. **The rental return picking is now created with
  the delivery and linked to it via `return_id`** (which also propagates to back-orders),
  and `sale_stock_renting._create_return()` deliberately deletes rental moves so no second
  return can be made. Consequence for `sale_flow`: the return reconciliation now sees
  multi-leg returns, so it subtracts **what already came back** from the expected demand
  (see `_reconcile_return_pickings`), and the lost/broken scrap runs with
  `skip_sale_flow_sync=True` so the wizard stays the only one reducing the demand.
- **Stock**: `stock.move.product_uom` → `uom_id`; `stock.move.scrap_id` → `is_scrap`;
  `stock.scrap` model removed (a scrap is a `stock.move` with `is_scrap=True` +
  `_action_scrap()`); `uom.uom.rounding` removed (use `uom.compare/round/is_zero`, which
  use the *Product Unit* decimal precision).
- **Repair**: `repair.order.state` lost `under_repair` (draft → confirmed → done/cancel);
  "committed but unfinished" is now `confirmed`.
- **Resource/planning**: `resource.calendar.tz` removed (the calendar follows
  `res.company.tz`); `resource.calendar.attendance` lost `name` and computes `day_period`;
  `resource.calendar.leaves.time_type` → `count_as` (`absence`/`working_time`);
  `_work_intervals_batch(resources=…)` → `resources_per_tz=resource._get_resources_per_tz()`;
  **`planning.slot.resource_id`/`employee_id` → many2many `resource_ids`/`employee_ids`**
  (a shift may staff several resources — `crew.work.declaration` mirrors the first one).
- **Portal**: the home page is data-driven — a card is a `portal.entry` record, visibility
  comes from `PortalEntry._filter_visible_portal_cards()` (crew_portal overrides it) and
  counters from `_prepare_portal_counter_values(counter)` returning
  `(model, domain, access)`. `_prepare_home_portal_values(counters)` is gone. Cards are
  always rendered and hidden with a CSS class, so never assert on their absence in HTML.
- **Payment**: `payment.provider.state` → `active` (archived = disabled) + `is_live`;
  `pos.printer.epson_printer_ip` → `printer_ip` and `proxy_ip` is gone. Writing on
  `payment.transaction` needs `payment_safe_write=True` in the context, and
  `_post_process()` must be reached through `_post_process_with_lock()`.
- **Misc**: `ir.config_parameter.get_param/set_param` → typed `get_str/get_int/get_bool/
  get_float`; `ir.actions.report.report_file` removed; QWeb `t-call` takes named
  arguments (`title.translate="…"`, `url.f="…"`) instead of nested `t-set`.
- **Views**: list fields can sit inside `<column>` wrappers, so prefer `//field[...]` over
  `/field[...]`; `sale_stock`'s inherited SO form has priority 20 (rental_set's must be
  higher to see `qty_at_date_widget`); `categ_id` is no longer on the product variant list.
- **Owl 3**: templates reference the component explicitly (`this.x`), `t-esc` → `t-out`,
  `useState` → `proxy`, `useService("action")` → `usePlugin(ActionPlugin)`, and
  `onWillRender` comes from `@web/owl2/utils`. Migrated with
  `odoo-bin upgrade_code --script owl3-migration` — but the codemod does **not**
  cover these, which only blow up at render time:
  - a **`static props` / `defaultProps` throws**; declare the schema as an instance
    field: `props = useProps({ ...standardFieldProps, x: t.string().optional() })`.
  - `QtyAtDateWidget.calcData` is a **computed signal** fed by the RETURN value of
    `initCalcData()` — an override must `return calcData`, never mutate
    `this.calcData`; templates read it as `this.calcData()`.
  - `ListRenderer.sortDrop(dataRowId, {element, previous})` — the old
    `(dataRowId, dataGroupId, params)` triple is gone.
  - `StaticList.resequence([movedId], targetId)` — the moved id is an **array** and
    the options argument (`{handleField}`) no longer exists.
  Nothing in Python catches these: `rental_set/tests/test_rental_set_ui.py` opens a
  real quotation in headless Chrome, but it needs a browser the AI sandbox lacks, so
  it is tagged `-standard` — run it with `--test-tags rental_set_ui`.

**Still open (needs a business decision):** a user clicking *Return* on a rental delivery
in Odoo 20 gets an empty return picking (rental moves are stripped by design). Decide
whether the rental round-trip return picking is the only supported return path, and drop
or re-target the `sale_flow` "return of the delivery" handling accordingly.

## Requirement docs
- `docs/rental_availability_requirements.{md,docx}`
- `docs/sale_flow_return_demand_requirements.{md,docx}`
- `docs/rental_serial_log_requirements.{md,docx}`
- `docs/rental_scanning_requirements.{md,docx}`
- `docs/serial_uniqueness_requirements.md`
- `docs/rental_return_serial_requirements.md`
