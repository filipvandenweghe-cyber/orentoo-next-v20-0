# Instance-wide Serial Number Uniqueness — Requirements & Design

Module: **rental_serial_log** (from version `19.0.1.0.7`).

## 1. Goal

Enforce **instance-wide uniqueness of serial numbers** for products tracked by
*unique serial number* (`tracking = 'serial'`), while leaving **lot/batch**
(`tracking = 'lot'`) semantics unchanged. A scanned serial number must identify
exactly one physical item anywhere in the database.

## 2. Scope decisions (locked with the user)

- **Uniqueness domain = name-only, instance-wide.** A serial *string* is unique
  across **all products and all companies**. Two different serial-tracked
  products may **not** share the same serial string. This is what makes a bare
  serial scan unambiguous.
- **Normalization = whitespace-trimmed, case-sensitive.** `"  SN-1  "` and
  `"SN-1"` are the *same* serial; `"sn-1"` and `"SN-1"` are *different*.
- **Only serial-tracked products** get a uniqueness key. Lots/batches never do.
- **Option B — cross-type block.** A lot/batch may **not** use a name equal to
  an existing serial string, and a serial may not reuse an existing batch name.
  This is the one narrow, deliberate constraint on batch naming; batch-vs-batch
  duplicates remain fully allowed (a lot number may repeat across products).
- **Hard-stop on install/upgrade.** If legacy data already violates the rule,
  the install/upgrade **aborts with a full report and changes no data**. The
  user resolves the conflicts and re-runs.

## 3. Implementation

### 3.1 Normalized key (`stock.lot.serial_unique_key`)
Stored computed `Char`, `copy=False`:
```
key = name.strip()
serial_unique_key = key if (product.tracking == 'serial' and key) else False
```
NULL for everything that is not serial-tracked, so lots/batches are untouched.

### 3.2 Enforcement — two layers
1. **Partial unique DB index** (built in `stock.lot.init()`):
   `CREATE UNIQUE INDEX ... ON stock_lot (serial_unique_key) WHERE serial_unique_key IS NOT NULL`.
   DB-level ⇒ race-safe and inherently cross-company. Excludes NULLs, so only
   serial-tracked lots participate. This is the source of truth for the
   serial-vs-serial case.
2. **Python `@api.constrains('name', 'product_id')`** for a friendly, non-leaking
   message *and* the cross-type (Option B) rule that a single index cannot
   express. All lookups use **raw SQL** (not the ORM): an ORM `search` would
   flush the record under validation and trip the DB index with a cryptic
   `IntegrityError` before the friendly `ValidationError` is raised. Raw SQL is
   instance-wide (bypasses multi-company record rules) but returns existence
   only, so it never leaks another company's data.

`@api.constrains` cannot list the dotted path `product_id.tracking` in Odoo 19,
so a *product tracking change* is not caught by the Python constraint — but the
stored key recomputes (its `@api.depends` includes `product_id.tracking`) and
the DB index still blocks any resulting collision at flush.

### 3.3 Legacy duplicate detection (hard-stop)
Two conflict kinds, both computed on `btrim(name)`:
- serial-vs-serial: >1 serial-tracked lot sharing a normalized name;
- cross-type: a serial normalized name equal to a lot/batch normalized name.

Detection runs in three places, all raising a clear report **without mutating
data**:
- `migrations/19.0.1.0.7/pre-migration.py` — before the new model code loads
  (SQL inlined, since model helpers don't exist on the old registry yet) and
  before `init()` builds the index.
- `post_init_hook = _check_serial_duplicates` — fresh-install / seed data guard.
- a defensive re-check inside `init()` before creating the index.

### 3.4 Physical on-hand uniqueness (`stock.quant`)
Name uniqueness alone doesn't stop the *same* serial being counted on-hand in two
places. Standard Odoo only **warns** when a serial is assigned to a second
location/company (e.g. Inventory Adjustments → *Physical Inventory*), and its hard
`check_quantity` groups per location tree, so a unique serial could sit on-hand in
two warehouses or two companies at once (this is how `PRINT001` ended up +1 in both
`WH/Stock` and `PRO/Stock`).

`_check_serial_single_on_hand` is an `@api.constrains('quantity','lot_id','location_id')`
that fires on any quant write (adjustments and move-created quants alike). For a
serial-tracked lot it sums positive on-hand across all **internal/transit** locations
**instance-wide (sudo, every company)** and raises if the total exceeds one. It:
- **blocks** counting a serial into a second warehouse/company (the reported case);
- **allows** moving the single unit (source→0, dest→1) — net stays one;
- **allows** the deliver-before-receipt transient — **customer** locations are
  excluded from the sum.

It only guards new/changed quants; pre-existing anomalies (like the current
`PRINT001`) persist until touched and can be cleaned up separately.

### 3.5 Administrator audit action
For finding conflicts *without* triggering an install/upgrade, an admin-facing
action **Inventory → Reporting → "Serial Number Duplicate Audit"**
(`ir.actions.server`, restricted to `stock.group_stock_manager`) runs
`stock.lot._serial_duplicate_lot_ids()` and either opens a filtered list of the
exact offending lots (serial-vs-serial *and* both sides of a cross-type clash),
or shows a "No duplicate serial numbers found" notification when clean. It is
read-only reconnaissance — it changes no data.

## 4. Interactions

- **Product tracking change** `lot→serial`: keys recompute; real collisions
  surface (via the index) at that point. `serial→lot/none`: keys blank → safe.
- **Imports**: validated through create/write like any record; an in-batch or
  against-existing duplicate fails with the friendly message. Trailing-space
  variants collide by design.
- **Multi-company**: uniqueness is deliberately cross-company; message stays
  generic. Company-less (`company_id = NULL`) lots are covered by the index.
- **PPB serial-to-package scan** (`rental_scanning._rs_resolve_barcode`): with
  uniqueness guaranteed, a scanned serial resolves to exactly one lot. The scan
  code is left unchanged for now; the benefit is realized wherever
  `rental_serial_log` is installed alongside `rental_scanning`.

## 5. Residual risks / notes

- The cross-type check and the single-record friendly-message path have a small
  write-race window (Python, not DB-enforced); the DB index remains the
  ultimate guarantee for serial-vs-serial, and batch-create of duplicates in one
  ORM call falls back to the index's `IntegrityError`.
- Option B narrowly constrains batch naming (a batch cannot reuse a serial
  string) — a conscious, documented deviation from "batches fully free".
- Full unification of the scan lookup with `serial_unique_key` is deferred to a
  later `rental_serial_log` ↔ `rental_scanning` merge.

## 6. Files

- `models/stock_lot.py` — key field + compute + `init()` index + constraint +
  raw-SQL detection helpers + `_serial_duplicate_lot_ids` (audit).
- `models/stock_quant.py` — physical on-hand single-location constraint.
- `data/serial_duplicate_audit.xml` — admin audit server action + menu.
- `hooks.py` + `__manifest__.py` (`post_init_hook`) — install guard. Module at
  `19.0.1.0.9` (name uniqueness `…0.7`, on-hand block `…0.8`, audit `…0.9`).
- `migrations/19.0.1.0.7/pre-migration.py` — upgrade hard-stop.
- `tests/test_serial_uniqueness.py` — see §7.

## 7. Test coverage

Suites (all green): **`rental_serial_log` 27/27**, **`rental_scanning` (PPB
serial/package) 20/20**.

Covered by `tests/test_serial_uniqueness.py`:
- **Key normalization / serial-only** — `test_key_only_for_serial_and_trimmed`.
- **Same-product duplicate** — `test_same_product_duplicate_blocked`.
- **Different-product duplicate** — `test_same_serial_two_products_blocked`.
- **Different-company duplicate** (real 2nd company) —
  `test_different_company_duplicate_blocked`.
- **Whitespace collisions** — `test_trailing_space_collides`,
  `test_cross_type_trailing_space`, `test_key_only_for_serial_and_trimmed`.
- **Case-sensitive non-collision** — `test_case_sensitive_distinct`.
- **Lot/batch unaffected** — `test_two_batches_same_name_allowed`.
- **Import / server-side create can't bypass** —
  `test_server_side_create_cannot_bypass`, `test_import_load_cannot_bypass`
  (real `load()`).
- **DB index is the source of truth** — `test_db_index_is_the_source_of_truth`.
- **Cross-type (Option B) both directions** —
  `test_batch_cannot_reuse_serial_string`, `test_serial_cannot_reuse_batch_string`.
- **Legacy-conflict detection / controlled failure** —
  `test_detection_reports_without_changing_data`,
  `test_detection_reports_cross_type` (both assert the report *and* that no data
  changed).
- **Physical on-hand uniqueness** — `test_serial_cannot_be_on_hand_in_two_locations`,
  `test_serial_can_move_between_locations`, and the deliver-before-receipt
  transient `test_serial_at_customer_plus_stock_allowed` (customer usage excluded).
- **Admin audit** — `test_audit_lists_conflicting_lots`,
  `test_audit_action_clean_vs_dirty` (notification vs. act_window),
  `test_audit_menu_restricted_to_managers` (menu group ACL).

Deliberately **not** covered (documented, low value):
1. **`migrate()` entrypoint not executed in-suite.** The detection logic it calls
   (`_find_serial_duplicates` / `_assert_no_serial_duplicates`) is unit-tested and
   the migration was verified live during an actual upgrade. There is no standard
   in-suite harness to replay a pre-migration.
2. **`post_init_hook` not unit-tested** (fresh-install only). It is a thin wrapper
   over `_assert_no_serial_duplicates`, which is covered.
3. **True cross-session concurrency race** cannot be simulated in a
   `TransactionCase`. The guarantee is the Postgres partial unique index, whose
   rejection is proven by `test_db_index_is_the_source_of_truth`.
