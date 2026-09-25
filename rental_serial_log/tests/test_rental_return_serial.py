from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalReturnSerial(TransactionCase):
    """P1 — rental returns accept only serials delivered on the order and never
    create a new serial; plus the serial repair-status / override-audit API
    that backs the scan warning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        # Rental pickings must be on so writing pickedup/returned lots stays a
        # plain m2m write (no direct-move side effects) — mirrors production.
        if not cls.env['res.groups']._is_feature_enabled(
                'sale_stock_renting.group_rental_stock_picking'):
            cls.env['res.config.settings'].create(
                {'group_rental_stock_picking': True}).set_values()
        cls.company = cls.env.company
        if not cls.company.rental_loc_id:
            cls.company.sudo()._create_rental_location()
        cls.rloc = cls.company.rental_loc_id
        cls.wh = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.stock = cls.wh.lot_stock_id
        cls.partner = cls.env['res.partner'].create({'name': 'RRS Client'})
        cls.crate = cls.env['product.product'].create({
            'name': 'RRS Crate', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial', 'rent_periodicity': 'days'})
        cls.lotA = cls.env['stock.lot'].create(
            {'name': 'RRS-A', 'product_id': cls.crate.id})
        cls.lotB = cls.env['stock.lot'].create(
            {'name': 'RRS-B', 'product_id': cls.crate.id})
        cls.Log = cls.env['rental.serial.log']

    # ── helpers ──────────────────────────────────────────────────────────
    def _rental_order(self, qty=1):
        # ``is_rental`` on the line = is_product_rentable AND context
        # ``in_rental_app`` (evaluated at line creation), so build the order
        # through the rental-app context.  No confirmation needed — the return
        # hooks only need ``is_rental`` + the picked-up/returned m2m fields.
        now = fields.Datetime.now()
        order = self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id,
            'is_rental_order': True,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=1),
            'order_line': [(0, 0, {
                'product_id': self.crate.id, 'product_uom_qty': qty})],
        })
        return order

    def _return_picking(self, order, lot=False, lot_name=False, qty=1):
        sol = order.order_line[:1]
        pick = self.env['stock.picking'].create({
            'picking_type_id': self.wh.in_type_id.id,
            'location_id': self.rloc.id,
            'location_dest_id': self.stock.id,
        })
        move = self.env['stock.move'].create({
            'product_id': self.crate.id, 'product_uom_qty': qty,
            'uom_id': self.crate.uom_id.id, 'picking_id': pick.id,
            'location_id': self.rloc.id, 'location_dest_id': self.stock.id,
            'sale_line_id': sol.id,
        })
        self.env['stock.move.line'].create({
            'move_id': move.id, 'picking_id': pick.id,
            'product_id': self.crate.id, 'quantity': qty,
            'location_id': self.rloc.id, 'location_dest_id': self.stock.id,
            'lot_id': lot.id if lot else False,
            'lot_name': lot_name or False,
        })
        return pick

    # ── H1: sale.order.line constraint (flow-agnostic backstop) ───────────
    def test_h1_delivered_serial_returnable(self):
        order = self._rental_order()
        sol = order.order_line
        sol.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        sol.returned_lot_ids = [(6, 0, [self.lotA.id])]  # must not raise
        self.assertIn(self.lotA, sol.returned_lot_ids)

    def test_h1_non_delivered_serial_blocked(self):
        order = self._rental_order()
        sol = order.order_line
        sol.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        with self.assertRaises(ValidationError):
            sol.returned_lot_ids = [(6, 0, [self.lotB.id])]

    def test_h1_resolver_is_override_point(self):
        order = self._rental_order()
        sol = order.order_line
        sol.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        # The narrow P1 resolver = the picked-up serials.
        self.assertEqual(sol._rsl_returnable_lot_ids(), self.lotA)

    # ── H2: picking pre-check (never create / must be delivered) ──────────
    def test_h2_return_delivered_ok(self):
        order = self._rental_order()
        order.order_line.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        pick = self._return_picking(order, lot=self.lotA)
        self.assertTrue(pick._rsl_is_rental_return())
        pick._rsl_check_return_serials()  # must not raise

    def test_h2_return_wrong_serial_blocked(self):
        order = self._rental_order()
        order.order_line.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        pick = self._return_picking(order, lot=self.lotB)
        with self.assertRaises(UserError):
            pick._rsl_check_return_serials()

    def test_h2_return_unknown_serial_never_creates(self):
        order = self._rental_order()
        order.order_line.pickedup_lot_ids = [(6, 0, [self.lotA.id])]
        pick = self._return_picking(order, lot_name='RRS-NOPE-9999')
        with self.assertRaises(UserError):
            pick._rsl_check_return_serials()
        # The rejected name must not have been turned into a lot.
        self.assertFalse(self.env['stock.lot'].search(
            [('name', '=', 'RRS-NOPE-9999')]))

    # ── H3/H4: repair status API + override audit (backs the scan modal) ──
    def test_repair_warning_states(self):
        Lot = self.env['stock.lot']
        rt = self.env['stock.picking.type'].search(
            [('code', '=', 'repair')], limit=1)
        vals = {'product_id': self.crate.id, 'lot_id': self.lotA.id}
        if rt:
            vals['picking_type_id'] = rt.id
        ro = self.env['repair.order'].create(vals)
        # draft → not active
        self.assertFalse(Lot.rsl_repair_warning('RRS-A')['has_repair'])
        ro.write({'state': 'confirmed'})
        info = Lot.rsl_repair_warning('RRS-A')
        self.assertTrue(info['has_repair'])
        self.assertEqual(info['repair_id'], ro.id)
        # a clean / unknown serial never warns
        self.assertFalse(Lot.rsl_repair_warning('RRS-B')['has_repair'])
        self.assertFalse(Lot.rsl_repair_warning('NOSUCH')['has_repair'])
        # done → no longer active
        ro.write({'state': 'done'})
        self.assertFalse(Lot.rsl_repair_warning('RRS-A')['has_repair'])

    def test_repair_override_audit(self):
        rt = self.env['stock.picking.type'].search(
            [('code', '=', 'repair')], limit=1)
        vals = {'product_id': self.crate.id, 'lot_id': self.lotA.id}
        if rt:
            vals['picking_type_id'] = rt.id
        ro = self.env['repair.order'].create(vals)
        ro.write({'state': 'confirmed'})
        self.Log.rsl_log_repair_override('RRS-A', False, ro.id)
        self.assertEqual(self.Log.search_count([
            ('lot_id', '=', self.lotA.id),
            ('event_type', '=', 'repair_override')]), 1)
        # idempotent per (lot, picking, repair)
        self.Log.rsl_log_repair_override('RRS-A', False, ro.id)
        self.assertEqual(self.Log.search_count([
            ('lot_id', '=', self.lotA.id),
            ('event_type', '=', 'repair_override')]), 1)
