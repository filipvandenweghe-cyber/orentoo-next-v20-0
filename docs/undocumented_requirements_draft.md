# Requirements Backfill — DRAFT for review

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Status** | **DRAFT — nothing here is merged into the requirement docs yet** |
| **Purpose** | Behaviour that is implemented and tested but was never written down |
| **Date** | 2026-09-25 |

## How to use this document
Each entry below is a **proposed requirement**, written in the ID style of the document it
belongs to, with the code that implements it. Review each one and mark it:

- **KEEP** — the behaviour is intended; I merge the text into the target doc.
- **CHANGE** — the wording or the intent is wrong; tell me what it should say.
- **BUG** — the code does this but it should not; it becomes a fix, not a requirement.

I inferred every entry from the code, so the *behaviour* is verified but the *intent*
is not. Entries marked **⚠ decision** are ones where I think the code may not match what
you actually want — please look at those first.

Nothing was invented: each item cites the file and method that implements it.

---

# 1. rental_scanning
*Target: `docs/rental_scanning_requirements.md`*

- **PPB-18 Rental-location dissolve is a warehouse-tree test, not a "customer" test.**
  Whether a scanned package is kept as a result package depends on `_rs_is_internal_step`:
  the destination must be internal **and inside the operation's own warehouse view tree**.
  The rental at-customer location has `usage='internal'` but lives *outside* the warehouse
  tree, so a delivery to it dissolves the package like a customer delivery would.
  → `models/stock_picking.py::_rs_is_internal_step`; test `test_ppb17_rental_location_dissolves`
- **PPB-19 A split never retains the package.** When a scan overflows and the operator
  confirms the split, the package is dissolved (`retain = bool(package) and not overflow`) —
  the remainder is left loose, not in a residual package.
  → `models/stock_picking.py`; test `test_ppb17_split_does_not_retain`
- **PPB-20 A plain product barcode adds a single unit.** Step 3 of the resolver accepts any
  product barcode (not only packages, serials and set barcodes) and adds one unit.
  → `models/stock_picking.py::_rs_resolve_barcode`
- **PPB-21 An unrecognised barcode is rejected with a named error**
  (`Barcode '%(bc)s' was not recognised…`) rather than silently ignored.
  → `models/stock_picking.py`; test `test_unknown_barcode_raises`
- **PPB-22 An empty package or set is rejected** (`Nothing to add: the scanned %(kind)s is
  empty.`) instead of validating a no-op scan.
  → `models/stock_picking.py`
- **PPB-23 Scan-to-remove wizard.** `rental.scanning.remove` is a transient whose
  `package_id` is constrained to the packages actually applied to that transfer
  (`available_package_ids`); `rental_scanning_remove_package` also accepts a raw barcode
  string as well as a record.
  → `wizard/rental_scanning_assign.py`, `models/stock_picking.py`
- **PPB-24 Access rights.** Both scanning wizards are CRUD for `stock.group_stock_user`.
  → `security/ir.access.csv`
- **⚠ decision — PPB-25 The repair warning is scoped to transfers only.** The modal fires
  only when the Barcode client is on `stock.picking`; scanning a serial under repair in an
  **inventory adjustment** or a **batch transfer** shows no warning.
  → `static/src/js/rental_scanning_barcode.js`
  *Is that the intent, or should the warning follow the serial everywhere it is scanned?*
- **⚠ decision — T-02/T-08/T-11/T-12 have no automated test.** §9 reads as if the acceptance
  table is the implemented suite, but multi-step Pick→Pack→Ship, inbound regroup, serial
  delivery/return and the multilingual check are not covered by `tests/`.
  *Either write them or mark them "manual" in the table.*
- **⚠ decision — the nl/fr translations have drifted.** `i18n/nl.po` and `fr.po` hold 10
  msgids each; several no longer match the source string (e.g. the split prompt still says
  "leave the remainder in the package" while the code warns the package "will be dissolved
  and will no longer exist"), and the repair-warning strings are untranslated.
  *D-11 / PPB-14 / T-12 claim full nl+fr coverage — either refresh the catalogues or
  narrow the requirement.*

---

# 2. rental_serial_log
*Target: `docs/rental_serial_log_requirements.md`*

- **RSL-13 Repair-override event.** Proceeding past the repair warning writes a
  `repair_override` log entry through the RPC-callable
  `rental.serial.log.rsl_log_repair_override()`. (The event value is already in the field;
  the requirement and the §6.1/§7 tables never mention it.)
  → `models/rental_serial_log.py`
- **RSL-14 Logging can be suppressed by context.** `skip_rental_serial_log` bypasses both
  the picking hook and the repair hook, for data loads and migrations.
  → `models/stock_picking.py`, `models/repair_order.py`
- **RSL-15 `button_validate` also runs the return-serial pre-check** (`_rsl_check_return_serials`)
  *before* `super()`. The doc still describes this file as delivered/returned logging only.
  → `models/stock_picking.py`
- **RSL-16 Idempotency key degrades gracefully.** `_rsl_log` adds `picking_id` /
  `repair_order_id` to the dedup domain only when present, so an event with neither
  deduplicates on `(lot, event_type)` alone.
  → `models/rental_serial_log.py::_rsl_log`
- **RSL-17 Delivered events fall back to `result_package_id`** when `package_id` is empty —
  relevant under PPB-17, where the source package is dissolved on the way out.
  → `models/stock_picking.py`
- *(Editorial)* §8 says the tests live in one file; the module ships three (35 tests).

---

# 3. serial_uniqueness
*Target: `docs/serial_uniqueness_requirements.md`*

- **SU-09 `serial_unique_key` carries no ordinary index** (`index=False`) — the partial
  unique index is deliberately the only one, to keep writes cheap.
  → `models/stock_lot.py`
- **SU-10 The on-hand check rounds with the Product Unit precision.** `uom.compare(total, 1)`
  replaced `float_compare(..., precision_rounding=uom.rounding)` (Odoo 20 removed
  `uom.uom.rounding`).
  → `models/stock_quant.py::_check_serial_single_on_hand`

---

# 4. rental_return_serial
*Target: `docs/rental_return_serial_requirements.md`*

- **RRS-05 H2 honours the bypass.** The `button_validate` pre-check is skipped entirely
  under `skip_rental_serial_log`.
  → `models/stock_picking.py`
- **RRS-06 H1 is serial-only.** The constraint skips non-rental lines and non-serial
  products, so the backstop never fires on lot-tracked or untracked goods.
  → `models/sale_order_line.py`
- **RRS-07 "A rental return" is narrower than "a move out of `rental_loc`".**
  `_rsl_is_rental_return` additionally requires `order.is_rental_order` **and** a move whose
  `sale_line_id.is_rental` is set.
  → `models/stock_picking.py`

---

# 5. rental_set — availability
*Target: `docs/rental_availability_requirements.md`*

- **RAV-15 Transfer terms.** The formula subtracts `transfer_out` and adds `transfer_in`:
  stock moving to another internal/transit location, and incoming supply gated per
  operation type by `stock.picking.type.rental_incoming_policy`
  (`projected` / `operational` / `ignore`).
  → `models/product_product.py` (`_get_transfer_out_qty`, `_get_transfer_in_qty`,
  `_rental_transfer_moves`, `_rental_transfer_sum`); tests `test_rental_transfer_grounding.py`
- **RAV-16 Signed availability for reporting.** `_rental_available_qty(..., clamp=False)`
  returns the negative figure so the report can show shortfall and >100% utilisation;
  `clamp=True` (the default) preserves the order-line behaviour.
  → `models/product_product.py`; test `test_clamp_default_unchanged`
- **RAV-17 Batch engine and Capacity.** `_rental_available_batch(columns, …)` returns
  `{'available', 'capacity'}` per column, where
  `capacity = physical_total − transfer_out + transfer_in`, and
  `utilisation = (capacity − available) / capacity`, uncapped, N/A at zero capacity. The
  batch engine re-uses the scalar sum helpers so there is only ever one formula.
  → `models/product_product.py`, `models/rental_availability_report.py`
- **RAV-18 Operation-date grounding of the reserved term.** Reservation is measured against
  the dates the goods actually move, not the declared rental dates:
  `_rental_effective_pickup_date` / `_rental_effective_return_date`, with
  `_rental_effective_reserved_qty` and `_rental_scrapped_qty`. This is the largest single
  behavioural block in the engine and it is undocumented in the requirements doc.
  → `models/sale_order_line.py`, `models/product_product.py::_get_active_rental_lines`;
  tests `test_rental_return_operation_date.py`, `test_rental_reservation_short_delivery.py`
- **RAV-19 Availability elsewhere.** The pop-up can show the same product's availability in
  other warehouses (`get_rental_warehouse_availability`), suppressed for sets and for
  single-warehouse companies.
  → `models/sale_order_line.py`; tests `test_rental_availability_warehouse.py`
- **RAV-20 Product-catalog availability.** `product.product.rental_avail_catalog` exposes a
  set-aware availability for the catalog/kiosk, via `_rental_set_avail_for_period`.
  → `models/product_product.py`
- **RAV-21 Report surface.** The matrix supports an `only_unavailable` filter, three display
  modes, 30-minute / hour / day column modes and timezone-aware column alignment.
  → `models/rental_availability_report.py`, `static/src/js/availability_matrix.js`
- **RAV-22 The physical-stock section is opt-in.** Company flag
  `rental_show_stock_locations`, **default off**. *(Now noted in RAV-14; promote to its own
  requirement if you want it stated positively.)*
  → `models/res_company.py`

---

# 6. rental_purchase
*Target: `docs/rental_purchase_requirements.md`*

- **RP-23 Return moves carry `restrict_partner_id`** — a second, independent owner guard
  beyond the MTO chain.
  → `models/purchase_order.py`
- **RP-24 Confirmation is blocked without a vendor location.** A `UserError` is raised when
  the vendor has no `property_stock_supplier`, rather than building a broken chain.
  → `models/purchase_order.py`
- **RP-25 Return creation is idempotent.** Re-confirming a PO that already has
  `rental_return_picking_ids` is a no-op.
  → `models/purchase_order.py`
- **⚠ decision — RP-26 Only `type == 'consu'` lines get a return chain.** Services and
  display lines are skipped. Note that in Odoo 20 `consu` covers both storable and
  **non-storable** consumables, so a non-storable consumable still gets a return chain.
  *Is that wanted?*
  → `models/purchase_order.py`
- **RP-27 Chain root prefers the stock-landing receipt move**, falling back to all receipt
  moves — the mechanism behind the multi-step reception caveat in §16.
  → `models/purchase_order.py`
- **RP-28 The expense account is user-configurable** from Accounting Settings
  (`res.config.settings.rental_purchase_expense_account_id`), not only via the company field.
  → `models/res_config_settings.py`, `views/res_config_settings_views.xml`
- **RP-29 `rental_return_picking_count`** backs the smart button (missing from the §3 field
  table).
- **RP-33 Duplicating a PO preserves the hire setup** — `is_rental_purchase`,
  `rental_start_date` and `rental_return_date` are `copy=True`.
- **RP-34 Owner stamping is conditional**: only incoming pickings not in `done`/`cancel`
  **and with no `owner_id` yet** are stamped. RP-20 reads as unconditional.
  → `models/purchase_order.py::_rental_purchase_assign_owner`
- **RP-35 `_compute_account_id` is narrower than RP-50 states** — it also requires
  `display_type == 'product'` and `move_id.is_purchase_document(include_receipts=True)`.
  → `models/account_move_line.py`
- **RP-36 Reconciliation resizes every open hop** of a multi-step return chain and re-runs
  `_action_assign()`, not just the root.
  → `models/purchase_order.py::_rental_purchase_reconcile_returns`
- **⚠ decision — RP-60 vs `rental_incoming_policy = 'ignore'`.** Already flagged in the doc:
  with `ignore`, hired-in supply is not credited while its paired return is still counted as
  a departure — a net phantom subtraction. *Confirm the intended rule and I will state it.*

---

# 7. crew_planning
*Target: `docs/crew_planning_requirements.md`*

- **R10.1 The `crew.availability` window model is undocumented.** It is the engine's compile
  source, the target of the *Availability Windows* menu (gantt / calendar / list / form) and
  the list the portal reads and removes from — but no requirement describes it.
  → `models/crew_availability.py`, `views/crew_availability_views.xml`
- **R10.2 One-click portal provisioning.** `hr.employee.action_crew_grant_portal` creates the
  portal user and triggers `action_reset_password`.
  → `models/hr_employee.py`; test `test_23_grant_portal_access`
- **R10.3 The card-hiding fields live in `crew_planning`.** `crew_portal_hide_*` and
  `_crew_portal_hidden_counters()` are declared here but documented only in the portal doc.
  → `models/hr_employee.py`
- **R10.4 Horizon settings and their invariant.** The two horizons are editable in Settings
  and an `@api.constrains` enforces *entry horizon ≤ coverage horizon*.
  → `models/res_config_settings.py`
- **R10.5 Engine clamping and housekeeping.** `apply_availability` clamps `start` to now and
  `end` to the coverage horizon; `_recompile` deletes fully-past windows to keep the table
  bounded while keeping past leaves as history.
  → `models/crew_availability_engine.py`
- **R10.6 Manual cross-channel sends.** `action_send_email_now` / `action_send_whatsapp_now`
  on an invitation, beyond the channel-aware dispatch of R4.5.
  → `models/crew_availability_invitation.py`; test `test_17_send_whatsapp_after_email`
- **⚠ decision — R6.3's planner notification is conditional.** The chatter message is posted
  **only if the shift is linked to a request** (`crew_request_id`). A shift not linked to a
  request notifies nobody when the crew member reports they cannot work.
  *(Now noted in R6.3.) Is silence correct, or should it fall back to the shift's own
  followers?*
  → `models/planning_slot.py`
- **⚠ decision — the invitation `expired` state is unreachable.** It is declared but there is
  no cron and no transition that sets it.
  *Either add the expiry job or drop the state.*
- **⚠ decision — a shift can now staff several people.** `crew.work.declaration` mirrors the
  **first** resource only, so a multi-resource shift yields one declaration for one person.
  *Decide: one declaration per shift (current), or one per assigned crew member?*
  → `models/crew_work_declaration.py::_compute_slot_resource`

---

# 8. crew_portal
*Target: `docs/crew_portal_requirements.md`*

- **R7.1 Card visibility is a model hook, not a template condition.**
  `PortalEntry._filter_visible_portal_cards()` decides; the cards themselves are
  `portal.entry` records. *(Now covered in R1.1 — keep here only if you want it as its own
  requirement.)*
- **R7.2 Counters are (model, domain, access) triples.** The `crew_hours_count` domain is the
  declarative equivalent of `_crew_hours_slots()['to_declare']`: started, not reported
  unavailable, and either no declaration or one in an editable state.
  → `controllers/portal.py::_prepare_portal_counter_values`
- **⚠ decision — a counter is a 0/1 flag, not a total.** Odoo 20 counts with `limit=1` for
  non-alert categories, so the badge cannot show "3 shifts to declare".
  *If you want real numbers the entries need `category = 'alert'`, which also changes where
  the card renders.*

---

# 9. Modules with no requirements at all
These three have neither a `.md` doc nor an inline register. Nothing is proposed here yet —
tell me which deserve one and I will draft it from the code.

| Module | What it does (from the manifest + code) |
|---|---|
| `pro_designed_setup` | Post-install hook building the Belgian company: EUR, PCMN chart, VAT, fiscal positions, warehouse opening hours, and replication of the coefficient/pricing demo records |
| `weather_forecast` | Weather locations + forecast lines, OpenWeather API key/units/language in system parameters, manual and cron refresh |
| `website_sale_stock_renting_set` | eCommerce bridge: component-based availability for rental sets on the shop (`_get_set_availabilities`, `_get_set_free_qty`) |

---

# 10. Notes on scope
- Everything above is **already implemented and (except where flagged) covered by tests**.
  Writing it down changes no code.
- Items marked **⚠ decision** are where I suspect the code and the intent may diverge. Those
  are the ones worth reading first.
- Items that are purely editorial (test counts, file lists) were fixed directly in the docs
  and are not repeated here.
