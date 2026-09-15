from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestSerialUniqueness(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Lot = cls.env['stock.lot']
        # Two distinct serial-tracked products (to prove uniqueness is
        # name-only, i.e. instance-wide across products).
        cls.serial_a = cls.env['product.product'].create({
            'name': 'SU Serial A', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial'})
        cls.serial_b = cls.env['product.product'].create({
            'name': 'SU Serial B', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial'})
        cls.batch = cls.env['product.product'].create({
            'name': 'SU Batch', 'type': 'consu', 'is_storable': True,
            'tracking': 'lot'})

    # ── the normalized key ───────────────────────────────────────────────
    def test_key_only_for_serial_and_trimmed(self):
        lot = self.Lot.create({'name': '  SN-1  ', 'product_id': self.serial_a.id})
        self.assertEqual(lot.serial_unique_key, 'SN-1',
                         "key must be whitespace-trimmed")
        batch = self.Lot.create({'name': 'B-1', 'product_id': self.batch.id})
        self.assertFalse(batch.serial_unique_key,
                         "batch/lot must never carry a serial key")

    # ── serial vs serial (instance-wide, across products) ────────────────
    def test_same_product_duplicate_blocked(self):
        self.Lot.create({'name': 'SP-1', 'product_id': self.serial_a.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'SP-1', 'product_id': self.serial_a.id})

    def test_same_serial_two_products_blocked(self):
        self.Lot.create({'name': 'SN-DUP', 'product_id': self.serial_a.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'SN-DUP', 'product_id': self.serial_b.id})

    def test_different_company_duplicate_blocked(self):
        company2 = self.env['res.company'].create({'name': 'SU Co2'})
        p1 = self.env['product.product'].create({
            'name': 'SU C1', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial', 'company_id': self.env.company.id})
        p2 = self.env['product.product'].create({
            'name': 'SU C2', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial', 'company_id': company2.id})
        l1 = self.Lot.create({'name': 'MC-1', 'product_id': p1.id})
        self.assertEqual(l1.company_id, self.env.company)
        # Same serial string in a different company is still a duplicate.
        with self.assertRaises(ValidationError):
            self.Lot.with_company(company2).create(
                {'name': 'MC-1', 'product_id': p2.id})

    def test_trailing_space_collides(self):
        self.Lot.create({'name': 'SN-WS', 'product_id': self.serial_a.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'SN-WS ', 'product_id': self.serial_b.id})

    def test_case_sensitive_distinct(self):
        self.Lot.create({'name': 'sn-case', 'product_id': self.serial_a.id})
        # Different case → a different serial, allowed.
        other = self.Lot.create({'name': 'SN-CASE', 'product_id': self.serial_b.id})
        self.assertTrue(other.id)

    @mute_logger('odoo.sql_db')
    def test_db_index_is_the_source_of_truth(self):
        """Bypass the Python constraint and prove the DB index still blocks."""
        self.Lot.create({'name': 'SN-DB', 'product_id': self.serial_a.id})
        lot2 = self.Lot.create({'name': 'SN-DB-2', 'product_id': self.serial_b.id})
        with self.assertRaises(IntegrityError):
            # Write the raw column directly, skipping recompute/constraints.
            self.env.cr.execute(
                "UPDATE stock_lot SET serial_unique_key = 'SN-DB' WHERE id = %s",
                (lot2.id,))

    # ── import / server-side create cannot bypass ────────────────────────
    def test_server_side_create_cannot_bypass(self):
        """Even an import-style context can't slip a duplicate past create."""
        self.Lot.create({'name': 'IMP-1', 'product_id': self.serial_a.id})
        with self.assertRaises(ValidationError):
            self.Lot.with_context(import_file=True).create(
                {'name': 'IMP-1', 'product_id': self.serial_b.id})

    @mute_logger('odoo.sql_db', 'odoo.models')
    def test_import_load_cannot_bypass(self):
        """`load()` (the CSV import path) reports the error and creates nothing."""
        self.Lot.create({'name': 'IMP-2', 'product_id': self.serial_a.id})
        result = self.env['stock.lot'].load(
            ['name', 'product_id/.id'],
            [['IMP-2', str(self.serial_b.id)]])
        self.assertTrue(result.get('messages'),
                        "import of a duplicate serial must be rejected")
        self.assertEqual(
            self.Lot.search_count([('serial_unique_key', '=', 'IMP-2')]), 1,
            "no second lot may be created by import")

    # ── cross-type (Option B) ────────────────────────────────────────────
    def test_batch_cannot_reuse_serial_string(self):
        self.Lot.create({'name': 'X-1', 'product_id': self.serial_a.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'X-1', 'product_id': self.batch.id})

    def test_serial_cannot_reuse_batch_string(self):
        self.Lot.create({'name': 'Y-1', 'product_id': self.batch.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'Y-1', 'product_id': self.serial_a.id})

    def test_cross_type_trailing_space(self):
        self.Lot.create({'name': '  Z-1  ', 'product_id': self.batch.id})
        with self.assertRaises(ValidationError):
            self.Lot.create({'name': 'Z-1', 'product_id': self.serial_a.id})

    # ── batch semantics unchanged ────────────────────────────────────────
    def test_two_batches_same_name_allowed(self):
        other_batch = self.env['product.product'].create({
            'name': 'SU Batch 2', 'type': 'consu', 'is_storable': True,
            'tracking': 'lot'})
        self.Lot.create({'name': 'LOT-1', 'product_id': self.batch.id})
        # Same lot number on another lot-tracked product is normal → allowed.
        ok = self.Lot.create({'name': 'LOT-1', 'product_id': other_batch.id})
        self.assertTrue(ok.id)

    # ── physical on-hand uniqueness (Inventory Adjustments etc.) ──────────
    def test_serial_cannot_be_on_hand_in_two_locations(self):
        lot = self.Lot.create({'name': 'ONH-1', 'product_id': self.serial_a.id})
        Quant = self.env['stock.quant']
        wh = self.env['stock.warehouse'].search([], limit=1)
        loc1 = wh.lot_stock_id
        loc2 = self.env['stock.location'].create({
            'name': 'ONH loc2', 'usage': 'internal',
            'location_id': wh.view_location_id.id})
        Quant._update_available_quantity(self.serial_a, loc1, 1.0, lot_id=lot)
        self.env.flush_all()
        with self.assertRaises(ValidationError):
            Quant._update_available_quantity(self.serial_a, loc2, 1.0, lot_id=lot)
            self.env.flush_all()

    def test_serial_can_move_between_locations(self):
        """Moving the single unit (source → 0, dest → 1) stays allowed."""
        lot = self.Lot.create({'name': 'ONH-2', 'product_id': self.serial_a.id})
        Quant = self.env['stock.quant']
        wh = self.env['stock.warehouse'].search([], limit=1)
        loc1 = wh.lot_stock_id
        loc2 = self.env['stock.location'].create({
            'name': 'ONH loc3', 'usage': 'internal',
            'location_id': wh.view_location_id.id})
        Quant._update_available_quantity(self.serial_a, loc1, 1.0, lot_id=lot)
        Quant._update_available_quantity(self.serial_a, loc1, -1.0, lot_id=lot)
        Quant._update_available_quantity(self.serial_a, loc2, 1.0, lot_id=lot)
        self.env.flush_all()  # total on-hand across internal locs == 1

    def test_serial_at_customer_plus_stock_allowed(self):
        """Deliver-before-receipt transient stays allowed: a unit sitting at a
        customer location plus one on-hand in stock must NOT be blocked, because
        customer-usage locations are excluded from the single-on-hand sum."""
        lot = self.Lot.create({'name': 'ONH-4', 'product_id': self.serial_a.id})
        Quant = self.env['stock.quant']
        wh = self.env['stock.warehouse'].search([], limit=1)
        customer = self.env.ref('stock.stock_location_customers')
        Quant._update_available_quantity(self.serial_a, customer, 1.0, lot_id=lot)
        Quant._update_available_quantity(
            self.serial_a, wh.lot_stock_id, 1.0, lot_id=lot)
        self.env.flush_all()  # only the internal on-hand counts → allowed

    # ── legacy duplicate detection (install/upgrade hard-stop) ────────────
    def test_detection_reports_without_changing_data(self):
        self.Lot.create({'name': 'DET-OK', 'product_id': self.serial_a.id})
        # Clean so far.
        self.assertFalse(self.Lot._find_serial_duplicates())
        # Force a duplicate past the guards to simulate legacy data.
        lot = self.Lot.create({'name': 'DET-1', 'product_id': self.serial_b.id})
        self.env.cr.execute(
            "UPDATE stock_lot SET name = 'DET-OK', serial_unique_key = NULL "
            "WHERE id = %s", (lot.id,))
        self.Lot.invalidate_model()
        dups = self.Lot._find_serial_duplicates()
        self.assertIn(('DET-OK', 'serial'), dups)
        with self.assertRaises(ValidationError):
            self.Lot._assert_no_serial_duplicates()

    def test_detection_reports_cross_type(self):
        serial = self.Lot.create({'name': 'CT-1', 'product_id': self.serial_a.id})
        batch = self.Lot.create({'name': 'CT-BATCH', 'product_id': self.batch.id})
        # Rename the batch onto the serial's string, bypassing the guards.
        self.env.cr.execute(
            "UPDATE stock_lot SET name = 'CT-1' WHERE id = %s", (batch.id,))
        self.Lot.invalidate_model()
        self.assertIn(('CT-1', 'cross-type'), self.Lot._find_serial_duplicates())
        self.assertTrue(serial.id)

    # ── administrator audit action ───────────────────────────────────────
    def test_audit_lists_conflicting_lots(self):
        a = self.Lot.create({'name': 'AUD-A', 'product_id': self.serial_a.id})
        b = self.Lot.create({'name': 'AUD-B', 'product_id': self.serial_b.id})
        self.assertNotIn(a.id, self.Lot._serial_duplicate_lot_ids())
        # Simulate a legacy duplicate (key NULL so the DB index stays happy).
        self.env.cr.execute(
            "UPDATE stock_lot SET name = 'AUD-A', serial_unique_key = NULL "
            "WHERE id = %s", (b.id,))
        self.Lot.invalidate_model()
        ids = self.Lot._serial_duplicate_lot_ids()
        self.assertIn(a.id, ids)
        self.assertIn(b.id, ids)

    def test_audit_action_clean_vs_dirty(self):
        action = self.env.ref('rental_serial_log.action_serial_duplicate_audit')
        # Clean DB (install passed the hard-stop) → success notification.
        result = action.run()
        self.assertEqual(result.get('tag'), 'display_notification')
        # Introduce a conflict → an act_window listing exactly those lots.
        a = self.Lot.create({'name': 'AUD2-A', 'product_id': self.serial_a.id})
        b = self.Lot.create({'name': 'AUD2-B', 'product_id': self.serial_b.id})
        self.env.cr.execute(
            "UPDATE stock_lot SET name = 'AUD2-A', serial_unique_key = NULL "
            "WHERE id = %s", (b.id,))
        self.Lot.invalidate_model()
        result = action.run()
        self.assertEqual(result.get('res_model'), 'stock.lot')
        self.assertIn(a.id, result['domain'][0][2])
        self.assertIn(b.id, result['domain'][0][2])

    def test_audit_menu_restricted_to_managers(self):
        menu = self.env.ref('rental_serial_log.menu_serial_duplicate_audit')
        mgr = self.env.ref('stock.group_stock_manager')
        self.assertIn(mgr, menu.group_ids,
                      "audit menu must be restricted to stock managers")
        user = self.env['res.users'].create({
            'name': 'SU Plain', 'login': 'su_plain',
            'group_ids': [(6, 0, [self.env.ref('stock.group_stock_user').id])]})
        self.assertNotIn(mgr, user.all_group_ids,
                         "a plain stock user is not a manager")
        visible = menu.with_user(user)._filter_visible_menus()
        self.assertNotIn(menu, visible,
                         "audit menu must be hidden from non-managers")
