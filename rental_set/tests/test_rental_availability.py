from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalAvailability(TransactionCase):
    """Repair-aware rental availability, own-demand transparency, set formula
    and the pop-up breakdown (RAV-01…13)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.partner = cls.env['res.partner'].create({'name': 'Avail Client'})
        cls.wh = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.stock_loc = cls.wh.lot_stock_id
        cls.has_repair = 'repair.order' in cls.env

        cls.prod = cls.env['product.product'].create({
            'name': 'Avail Widget', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})
        cls.serial_prod = cls.env['product.product'].create({
            'name': 'Avail Serial', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial', 'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, product, qty, lot=None):
        vals = {
            'product_id': product.id,
            'location_id': self.stock_loc.id,
            'inventory_quantity': qty,
        }
        if lot:
            vals['lot_id'] = lot.id
        self.env['stock.quant'].with_context(inventory_mode=True).create(
            vals).action_apply_inventory()

    def _rental_order(self, start_offset=0, days=1):
        now = fields.Datetime.now()
        # in_rental_app makes it a real rental order (is_rental_order / the
        # lines' is_rental), which is what our availability override keys on.
        return self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=start_offset),
            'rental_return_date': now + timedelta(days=start_offset + days),
        })

    def _line(self, order, product, qty):
        return self.env['sale.order.line'].with_context(
            in_rental_app=True).create({
                'order_id': order.id, 'product_id': product.id,
                'product_uom_qty': qty})

    def _repair(self, product, qty=1, state='confirmed', lot=None,
                schedule_offset=1):
        now = fields.Datetime.now()
        vals = {
            'product_id': product.id,
            'product_qty': qty,
            'schedule_date': now + timedelta(days=schedule_offset),
            'location_id': self.stock_loc.id,
        }
        if lot:
            vals['lot_id'] = lot.id
        ro = self.env['repair.order'].create(vals)
        if state and state != 'draft':
            ro.write({'state': state})
        return ro

    def _avail(self, line):
        line.invalidate_recordset(['free_qty_today', 'virtual_available_at_date'])
        return line.free_qty_today

    def _enable_rental_pickings(self):
        """Turn on 'Rental pickings' so confirming a rental order creates the
        real pickup/return transfers. `rental_reserved_self` is derived from
        those outgoing moves, so the availability feature (and the CLAUDE
        design note) assumes this feature is enabled. Fresh build DBs ship
        with it OFF, so tests that check reservation splits must enable it."""
        if self.env['res.groups']._is_feature_enabled(
                'sale_stock_renting.group_rental_stock_picking'):
            return
        cfg = self.env['res.config.settings'].create(
            {'group_rental_stock_picking': True})
        cfg.set_values()

    # ── T-01 ─────────────────────────────────────────────────────────────
    def test_01_open_repair_reduces_availability(self):
        if not self.has_repair:
            self.skipTest('repair not installed')
        self._set_stock(self.prod, 10)
        line = self._line(self._rental_order(), self.prod, 1)
        base = self._avail(line)
        self._repair(self.prod, qty=3)
        self.assertAlmostEqual(self._avail(line), base - 3, places=2)

    # ── T-02 ─────────────────────────────────────────────────────────────
    def test_02_done_repair_does_not_reduce(self):
        if not self.has_repair:
            self.skipTest('repair not installed')
        self._set_stock(self.prod, 10)
        line = self._line(self._rental_order(), self.prod, 1)
        base = self._avail(line)
        self._repair(self.prod, qty=3, state='done')
        self.assertAlmostEqual(self._avail(line), base, places=2)

    # ── T-03 ─────────────────────────────────────────────────────────────
    def test_03_repair_outside_period_ignored(self):
        if not self.has_repair:
            self.skipTest('repair not installed')
        self._set_stock(self.prod, 10)
        # Rental far in the future; an open repair created now (overdue,
        # window ends ~now) does not overlap it.
        line = self._line(self._rental_order(start_offset=30), self.prod, 1)
        base = self._avail(line)
        self._repair(self.prod, qty=3, schedule_offset=-1)
        self.assertAlmostEqual(self._avail(line), base, places=2)

    # ── T-04 ─────────────────────────────────────────────────────────────
    def test_04_serial_repair_removes_one_unit(self):
        if not self.has_repair:
            self.skipTest('repair not installed')
        lot = self.env['stock.lot'].create({
            'name': 'AV-SER-1', 'product_id': self.serial_prod.id})
        self._set_stock(self.serial_prod, 1, lot=lot)
        line = self._line(self._rental_order(), self.serial_prod, 1)
        base = self._avail(line)
        self._repair(self.serial_prod, qty=1, lot=lot)
        self.assertAlmostEqual(self._avail(line), base - 1, places=2)

    # ── T-05 ─────────────────────────────────────────────────────────────
    def test_05_confirmed_own_demand_not_double_counted(self):
        self._set_stock(self.prod, 10)
        order = self._rental_order()
        line = self._line(order, self.prod, 4)
        draft = self._avail(line)
        order.action_confirm()
        confirmed = self._avail(line)
        # Confirming must not reduce this order's own availability: its own
        # reserved units remain available to itself (RAV-10).  With the cap
        # fix the confirmed figure equals the draft figure.
        self.assertGreaterEqual(confirmed, draft - 0.01)
        self.assertAlmostEqual(confirmed, draft, places=2)

    # ── T-06 ─────────────────────────────────────────────────────────────
    def test_06_set_availability_limiting_component(self):
        set_tmpl = self.env['product.template'].create({
            'name': 'Avail Set', 'type': 'consu', 'rent_periodicity': 'days',
            'is_rental_set': True, 'set_pricing_mode': 'sum'})
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id, 'product_id': self.prod.id,
            'quantity': 3, 'sequence': 10})
        self._set_stock(self.prod, 10)
        order = self._rental_order()
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1})
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component)
        set_parent.invalidate_recordset(['set_availability'])
        # 10 / 3 = 3.33 → floor 3 whole sets (RAV-08)
        self.assertEqual(set_parent.set_availability, 3.0)

    # ── T-07 ─────────────────────────────────────────────────────────────
    def test_07_set_non_storable_limitless(self):
        non_storable = self.env['product.product'].create({
            'name': 'Avail Service', 'type': 'consu', 'is_storable': False,
            'rent_periodicity': 'days'})
        set_tmpl = self.env['product.template'].create({
            'name': 'Service Set', 'type': 'consu', 'rent_periodicity': 'days',
            'is_rental_set': True, 'set_pricing_mode': 'sum'})
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id, 'product_id': non_storable.id,
            'quantity': 2, 'sequence': 10})
        order = self._rental_order()
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 5})
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component)
        set_parent.invalidate_recordset(['set_availability'])
        self.assertEqual(set_parent.set_availability, 5.0)

    # ── T-08 ─────────────────────────────────────────────────────────────
    def test_08_breakdown_is_consistent(self):
        self._enable_rental_pickings()
        self._set_stock(self.prod, 10)
        order = self._rental_order()
        line = self._line(order, self.prod, 4)
        order.action_confirm()
        line.invalidate_recordset([
            'free_qty_today', 'rental_pickable', 'rental_reserved_self',
            'rental_reserved_other', 'rental_in_repair', 'rental_total_stock'])
        # Available to this order equals the headline (time-based) figure.
        self.assertAlmostEqual(line.rental_pickable, line.free_qty_today,
                               places=2)
        # This order reserved its 4 units.
        self.assertAlmostEqual(line.rental_reserved_self, 4.0, places=2)
        # Option A: Total stock is the real physical count = current owned.
        # 10 units, none out yet (moves reserve but don't leave) → Total 10.
        self.assertAlmostEqual(
            line.rental_total_stock, 10.0, places=2,
            msg="Total stock must equal real physical on-hand (Option A)")
        # The location partition is a full partition of that physical Total.
        line.invalidate_recordset(['rental_onhand_json'])
        partition = line.rental_onhand_json or []
        self.assertAlmostEqual(
            sum(b['qty'] for b in partition),
            line.rental_total_stock, places=2,
            msg=f"partition {partition} must sum to Total "
                f"{line.rental_total_stock}")

    # ── T-09 ─────────────────────────────────────────────────────────────
    def test_09_repair_helper_graceful(self):
        # No open repair → no deduction (same code path taken when the
        # repair module is absent, where the helper returns 0.0).
        self._set_stock(self.prod, 7)
        line = self._line(self._rental_order(), self.prod, 1)
        val = self.prod._get_repair_unavailable_qty(
            fields.Datetime.now(), warehouse_id=self.wh.id)
        self.assertEqual(val, 0.0)
        self.assertAlmostEqual(self._avail(line), 7, places=2)

    # ── T-10 ─────────────────────────────────────────────────────────────
    def test_10_non_rental_untouched(self):
        self._set_stock(self.prod, 5)
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        line = self.env['sale.order.line'].create({
            'order_id': order.id, 'product_id': self.prod.id,
            'product_uom_qty': 1})
        line.invalidate_recordset(['rental_pickable', 'rental_reserved_self'])
        self.assertEqual(line.rental_pickable, 0.0)
        self.assertEqual(line.rental_reserved_self, 0.0)

    # ── T-12 ─────────────────────────────────────────────────────────────
    def test_12_reservation_split_shown(self):
        self._enable_rental_pickings()
        self._set_stock(self.prod, 20)
        # Competing confirmed order over the same period.
        other = self._rental_order()
        self._line(other, self.prod, 5)
        other.action_confirm()
        # Our order, overlapping period.
        order = self._rental_order()
        line = self._line(order, self.prod, 3)
        order.action_confirm()
        line.invalidate_recordset(['rental_reserved_self',
                                   'rental_reserved_other'])
        self.assertAlmostEqual(line.rental_reserved_self, 3.0, places=2)
        self.assertGreaterEqual(line.rental_reserved_other, 5.0 - 0.01)

    # ── T-14 ─────────────────────────────────────────────────────────────
    def test_14_competing_orders_share_shortfall(self):
        # 7 units, two confirmed orders each want 4 over overlapping windows.
        # Each can only get 7 − 4(other) = 3 → both short by 1 (Option A).
        self._set_stock(self.prod, 7)
        a = self._rental_order(start_offset=0, days=2)
        la = self._line(a, self.prod, 4)
        a.action_confirm()
        b = self._rental_order(start_offset=0, days=2)
        lb = self._line(b, self.prod, 4)
        b.action_confirm()
        self.assertAlmostEqual(self._avail(la), 3, places=2)
        self.assertAlmostEqual(self._avail(lb), 3, places=2)
        # An order's own demand is never subtracted from itself: with no
        # competitor it would see all 7.
        c = self._rental_order(start_offset=100, days=2)
        lc = self._line(c, self.prod, 4)
        c.action_confirm()
        self.assertAlmostEqual(self._avail(lc), 7, places=2)

    # ── T-13 ─────────────────────────────────────────────────────────────
    def test_13_other_order_returning_in_time_not_counted(self):
        self._set_stock(self.prod, 10)
        # Other order occupies stock NOW and returns before our future period.
        other = self._rental_order(start_offset=0, days=1)
        self._line(other, self.prod, 6)
        other.action_confirm()
        # Our order starts well after the other has returned.
        future = self._rental_order(start_offset=30, days=1)
        line = self._line(future, self.prod, 1)
        line.invalidate_recordset(['rental_reserved_other', 'free_qty_today'])
        # The confirmed competing order registered BOTH its pickup and its
        # return transfer, so for a future window it returns before, it holds
        # nothing — and the full stock is available again.
        self.assertAlmostEqual(line.rental_reserved_other, 0.0, places=2)
        self.assertAlmostEqual(self._avail(line), 10, places=2)
