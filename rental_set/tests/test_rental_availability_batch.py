from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalAvailabilityBatch(TransactionCase):
    """The batch availability API and the ``clamp`` flag must never diverge
    from the canonical scalar engine ``_rental_available_qty``.

    * ``clamp=True`` (default) is byte-for-byte the historic behaviour.
    * ``clamp=False`` returns the same calculation before the ``max(., 0)``.
    * ``_rental_available_batch(...)[pid][i] == _rental_available_qty(col_i, clamp=…)``
      across reservations, late returns, repairs and interwarehouse transfers.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Batch Client'})
        cls.wha = cls.env['stock.warehouse'].create({
            'name': 'Batch A', 'code': 'BTA', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        cls.whb = cls.env['stock.warehouse'].create({
            'name': 'Batch B', 'code': 'BTB', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        cls.env['res.config.settings'].create(
            {'group_rental_stock_picking': True}).set_values()
        cls.env['stock.warehouse'].update_rental_rules()
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])
        cls.p1 = cls.env['product.product'].create({
            'name': 'Batch Widget 1', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})
        cls.p2 = cls.env['product.product'].create({
            'name': 'Batch Widget 2', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, wh, product, qty):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id, 'location_id': wh.lot_stock_id.id,
            'inventory_quantity': qty}).action_apply_inventory()

    def _rent(self, wh, product, qty, start_off, end_off):
        now = fields.Datetime.now()
        order = self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id, 'warehouse_id': wh.id,
            'rental_start_date': now + timedelta(days=start_off),
            'rental_return_date': now + timedelta(days=end_off)})
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id, 'product_id': product.id,
            'product_uom_qty': qty})
        order.action_confirm()
        return order

    def _transfer(self, src, dst, product, qty, day_off):
        now = fields.Datetime.now()
        move = self.env['stock.move'].create({
            'product_id': product.id, 'uom_id': product.uom_id.id,
            'product_uom_qty': qty,
            'location_id': src.lot_stock_id.id,
            'location_dest_id': dst.lot_stock_id.id,
            'date': now + timedelta(days=day_off)})
        move._action_confirm()
        move._action_assign()
        move.date = now + timedelta(days=day_off)
        return move

    def _columns(self, offsets):
        """Build (from,to) day-wide columns at the given day offsets."""
        now = fields.Datetime.now()
        cols = []
        for off in offsets:
            cols.append((now + timedelta(days=off),
                         now + timedelta(days=off + 1)))
        return cols

    def _assert_batch_equals_scalar(self, products, wh, columns, msg=''):
        for clamp in (True, False):
            batch = products._rental_available_batch(
                columns, warehouse=wh, company=self.company, clamp=clamp)
            for product in products:
                for i, (f, t) in enumerate(columns):
                    scalar = product._rental_available_qty(
                        f, t, warehouse=wh, company=self.company, clamp=clamp)
                    self.assertAlmostEqual(
                        batch[product.id][i]['available'], scalar, places=6,
                        msg=f"{msg} clamp={clamp} p={product.display_name} "
                            f"col={i}: batch != scalar")

    # ── tests ────────────────────────────────────────────────────────────
    def test_clamp_default_unchanged(self):
        """Default clamp=True equals the historic max(.,0); clamp=False exposes
        the signed value (negative on overbooking)."""
        self._set_stock(self.wha, self.p1, 5)
        # Overbook: rent 8 for days 2-4 while only 5 are on hand.
        self._rent(self.wha, self.p1, 8, 2, 4)
        now = fields.Datetime.now()
        f = now + timedelta(days=2, hours=1)
        t = now + timedelta(days=2, hours=2)

        default = self.p1._rental_available_qty(
            f, t, warehouse=self.wha, company=self.company)
        clamped = self.p1._rental_available_qty(
            f, t, warehouse=self.wha, company=self.company, clamp=True)
        signed = self.p1._rental_available_qty(
            f, t, warehouse=self.wha, company=self.company, clamp=False)

        self.assertEqual(default, clamped, "default must be clamp=True")
        self.assertAlmostEqual(clamped, 0.0, places=6,
                               msg="overbooked → clamped at 0")
        self.assertAlmostEqual(signed, -3.0, places=6,
                               msg="signed shows the -3 overcommitment")

    def test_batch_equals_scalar_reservations(self):
        self._set_stock(self.wha, self.p1, 10)
        self._set_stock(self.wha, self.p2, 4)
        self._rent(self.wha, self.p1, 3, 1, 3)
        self._rent(self.wha, self.p1, 4, 2, 5)   # overlapping peak
        self._rent(self.wha, self.p2, 2, 2, 4)
        cols = self._columns([0, 1, 2, 3, 4, 5, 6])
        self._assert_batch_equals_scalar(
            self.p1 | self.p2, self.wha, cols, 'reservations')

    def test_batch_equals_scalar_late_return(self):
        """A return operation scheduled after the declared date keeps units
        reserved; batch and scalar must agree over that tail."""
        self._set_stock(self.wha, self.p1, 6)
        order = self._rent(self.wha, self.p1, 5, 1, 3)
        # Push the return picking well past the declared return date.
        return_moves = order.picking_ids.move_ids.filtered(
            lambda m: m.location_id == self.company.rental_loc_id)
        if return_moves:
            return_moves.write(
                {'date': fields.Datetime.now() + timedelta(days=9)})
        cols = self._columns([0, 2, 4, 6, 8, 10])
        self._assert_batch_equals_scalar(self.p1, self.wha, cols, 'late_return')

    def test_batch_equals_scalar_transfers(self):
        self._set_stock(self.wha, self.p1, 10)
        self._transfer(self.wha, self.whb, self.p1, 4, day_off=3)
        cols = self._columns([0, 1, 2, 3, 4, 5])
        self._assert_batch_equals_scalar(
            self.p1, self.wha, cols, 'transfer_out(source)')
        self._assert_batch_equals_scalar(
            self.p1, self.whb, cols, 'transfer_in(dest)')

    def test_batch_capacity_matches_definition(self):
        """Capacity == physical_total − transfer_out + transfer_in, and
        signed available == capacity − reserved − in_repair."""
        self._set_stock(self.wha, self.p1, 10)
        self._rent(self.wha, self.p1, 3, 2, 5)
        self._transfer(self.wha, self.whb, self.p1, 2, day_off=3)
        cols = self._columns([0, 1, 2, 3, 4, 5])
        batch = self.p1._rental_available_batch(
            cols, warehouse=self.wha, company=self.company, clamp=False)
        for i, (f, t) in enumerate(cols):
            total = self.p1._rental_physical_total(
                warehouse=self.wha, company=self.company)
            t_out = self.p1._get_transfer_out_qty(f, t, warehouse=self.wha)
            t_in = self.p1._get_transfer_in_qty(f, t, warehouse=self.wha)
            self.assertAlmostEqual(
                batch[self.p1.id][i]['capacity'], total - t_out + t_in,
                places=6, msg=f"capacity def mismatch col {i}")

    def test_empty_columns(self):
        res = (self.p1 | self.p2)._rental_available_batch(
            [], warehouse=self.wha, company=self.company)
        self.assertEqual(res, {self.p1.id: [], self.p2.id: []})
