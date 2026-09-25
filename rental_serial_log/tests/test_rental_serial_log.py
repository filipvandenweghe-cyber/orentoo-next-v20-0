from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalSerialLog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.wh = cls.env['stock.warehouse'].search([], limit=1)
        cls.stock = cls.wh.lot_stock_id
        cls.customer = cls.env.ref('stock.stock_location_customers')
        cls.partner = cls.env['res.partner'].create({'name': 'RSL Client'})
        cls.crate = cls.env['product.product'].create({
            'name': 'RSL Crate', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial', 'rent_periodicity': 'days'})
        cls.glas = cls.env['product.product'].create({
            'name': 'RSL Glas', 'type': 'consu', 'is_storable': True})
        cls.serial = cls.env['stock.lot'].create({
            'name': 'RSL-0001', 'product_id': cls.crate.id})
        cls.Log = cls.env['rental.serial.log']

    # ── helpers ──────────────────────────────────────────────────────────
    def _order(self, rental=True):
        # In Odoo 19 ``stock.picking.sale_id`` is *computed* from
        # ``move_ids.sale_line_id.order_id`` — it can no longer be written
        # directly on the picking.  The order therefore needs a real order
        # line for the serial-tracked crate so that the moves created in
        # ``_line`` can point at it and the picking resolves its ``sale_id``.
        now = fields.Datetime.now()
        vals = {
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.crate.id, 'product_uom_qty': 1})],
        }
        if rental:
            vals.update({
                'is_rental_order': True,
                'rental_start_date': now,
                'rental_return_date': now + timedelta(days=1),
            })
        return self.env['sale.order'].create(vals)

    def _rental_order(self):
        return self._order(rental=True)

    def _picking(self, order, code, return_of=False):
        if code == 'outgoing':
            pt, src, dst = self.wh.out_type_id, self.stock, self.customer
        else:
            pt, src, dst = self.wh.in_type_id, self.customer, self.stock
        vals = {
            'picking_type_id': pt.id,
            'location_id': src.id, 'location_dest_id': dst.id,
        }
        if return_of:
            vals['return_id'] = return_of.id
        return self.env['stock.picking'].create(vals)

    def _line(self, picking, product, qty, lot=False, package=False,
              order=None):
        move_vals = {
            'product_id': product.id,
            'product_uom_qty': qty, 'uom_id': product.uom_id.id,
            'picking_id': picking.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        }
        # ``sale_id`` on the picking is computed from its moves' sale lines
        # (Odoo 19), so wire the move to the matching order line when given.
        if order is not None:
            sol = order.order_line.filtered(
                lambda l: l.product_id == product)[:1]
            if sol:
                move_vals['sale_line_id'] = sol.id
        move = self.env['stock.move'].create(move_vals)
        return self.env['stock.move.line'].create({
            'move_id': move.id, 'picking_id': picking.id,
            'product_id': product.id, 'quantity': qty,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'lot_id': lot.id if lot else False,
            'package_id': package.id if package else False,
        })

    # ── tests ────────────────────────────────────────────────────────────
    def test_delivered_with_package_and_contents(self):
        order = self._rental_order()
        self.assertTrue(order.is_rental_order)
        pkg = self.env['stock.package'].create({'name': 'RSLPKG'})
        pick = self._picking(order, 'outgoing')
        self._line(pick, self.crate, 1, lot=self.serial, package=pkg, order=order)
        self._line(pick, self.glas, 40, package=pkg)

        pick._rental_serial_log_record()

        log = self.Log.search([('lot_id', '=', self.serial.id),
                               ('event_type', '=', 'delivered')])
        self.assertEqual(len(log), 1)
        self.assertEqual(log.sale_order_id, order)
        self.assertEqual(log.partner_id, self.partner)
        self.assertEqual(log.package_id, pkg)
        self.assertIn('RSL Glas', log.package_contents)
        self.assertIn('RSL-0001', log.package_contents)

    def test_returned(self):
        order = self._rental_order()
        deliv = self._picking(order, 'outgoing')
        ret = self._picking(order, 'incoming', return_of=deliv)
        self._line(ret, self.crate, 1, lot=self.serial, order=order)

        ret._rental_serial_log_record()

        log = self.Log.search([('lot_id', '=', self.serial.id),
                               ('event_type', '=', 'returned')])
        self.assertEqual(len(log), 1)
        self.assertEqual(log.sale_order_id, order)

    def test_idempotent(self):
        order = self._rental_order()
        pick = self._picking(order, 'outgoing')
        self._line(pick, self.crate, 1, lot=self.serial, order=order)
        pick._rental_serial_log_record()
        pick._rental_serial_log_record()
        self.assertEqual(self.Log.search_count([
            ('lot_id', '=', self.serial.id),
            ('event_type', '=', 'delivered')]), 1)

    def test_open_transaction(self):
        order = self._rental_order()
        pick = self._picking(order, 'outgoing')
        log = self.Log.create({
            'lot_id': self.serial.id, 'event_type': 'delivered',
            'picking_id': pick.id, 'sale_order_id': order.id})
        act = log.action_open_transaction()
        self.assertEqual(act['res_model'], 'stock.picking')
        self.assertEqual(act['res_id'], pick.id)

    def test_non_rental_not_logged(self):
        order = self._order(rental=False)
        self.assertFalse(order.is_rental_order)
        pick = self._picking(order, 'outgoing')
        self._line(pick, self.crate, 1, lot=self.serial, order=order)
        pick._rental_serial_log_record()
        self.assertFalse(self.Log.search([('lot_id', '=', self.serial.id)]))

    def test_repair_events(self):
        vals = {'product_id': self.crate.id, 'lot_id': self.serial.id}
        rt = self.env['stock.picking.type'].search(
            [('code', '=', 'repair')], limit=1)
        if rt:
            vals['picking_type_id'] = rt.id
        ro = self.env['repair.order'].create(vals)

        ro.write({'state': 'confirmed'})
        self.assertEqual(self.Log.search_count([
            ('lot_id', '=', self.serial.id),
            ('event_type', '=', 'repair_start')]), 1)

        ro.write({'state': 'done'})
        self.assertEqual(self.Log.search_count([
            ('lot_id', '=', self.serial.id),
            ('event_type', '=', 'repair_done')]), 1)
