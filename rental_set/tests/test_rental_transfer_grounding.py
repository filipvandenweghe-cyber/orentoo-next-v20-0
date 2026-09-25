from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalTransferGrounding(TransactionCase):
    """Same-company interwarehouse transfers ground rental availability on the
    transfer's scheduled operation date.

    A confirmed (not-yet-done) relocation OUT reduces the source warehouse from
    its departure; a relocation IN raises the destination once the units are
    guaranteed present for the whole interval.  The conserved total is
    preserved, in-transit units belong to neither for a spanning interval, and
    rental pickups / external purchases are never mistaken for a relocation.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Xfer Client'})
        # Two warehouses in the SAME company (like PRO + PD2).
        cls.wha = cls.env['stock.warehouse'].create({
            'name': 'Xfer A', 'code': 'XFA', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        cls.whb = cls.env['stock.warehouse'].create({
            'name': 'Xfer B', 'code': 'XFB', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        cls.env['res.config.settings'].create(
            {'group_rental_stock_picking': True}).set_values()
        cls.env['stock.warehouse'].update_rental_rules()
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])
        cls.prod = cls.env['product.product'].create({
            'name': 'Xfer Widget', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, wh, qty):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.prod.id, 'location_id': wh.lot_stock_id.id,
            'inventory_quantity': qty}).action_apply_inventory()

    def _avail(self, wh, f_off, t_off):
        now = fields.Datetime.now()
        return self.prod._rental_available_qty(
            now + timedelta(days=f_off), now + timedelta(days=t_off),
            warehouse=wh, company=self.company)

    def _transfer(self, src_wh, dst_wh, qty, day_off):
        """Confirmed (not-done) 1-step internal transfer scheduled day_off from
        now."""
        now = fields.Datetime.now()
        move = self.env['stock.move'].create({
            'product_id': self.prod.id,
            'uom_id': self.prod.uom_id.id,
            'product_uom_qty': qty,
            'location_id': src_wh.lot_stock_id.id,
            'location_dest_id': dst_wh.lot_stock_id.id,
            'date': now + timedelta(days=day_off),
        })
        move._action_confirm()
        move._action_assign()
        # Ensure the scheduled operation date sticks (confirm/assign can touch it).
        move.date = now + timedelta(days=day_off)
        return move

    # ── tests ────────────────────────────────────────────────────────────
    def test_transfer_grounds_source_and_destination(self):
        self._set_stock(self.wha, 10)
        # A confirmed transfer of 4 A->B, scheduled day 3, not yet done.
        self._transfer(self.wha, self.whb, 4, day_off=3)

        # Before the transfer (days 1-2): nothing has moved.
        self.assertAlmostEqual(
            self._avail(self.wha, 1, 2), 10.0, places=2,
            msg="source unchanged before the transfer departs")
        self.assertAlmostEqual(
            self._avail(self.whb, 1, 2), 0.0, places=2,
            msg="destination has nothing before the transfer arrives")

        # After the transfer (days 4-5): 4 have relocated A -> B.
        self.assertAlmostEqual(
            self._avail(self.wha, 4, 5), 6.0, places=2,
            msg="source drops by the departed qty (transfer OUT)")
        self.assertAlmostEqual(
            self._avail(self.whb, 4, 5), 4.0, places=2,
            msg="destination rises by the arrived qty (operational IN)")

        # Conservation: the company still owns 10 across the two warehouses.
        self.assertAlmostEqual(
            self._avail(self.wha, 4, 5) + self._avail(self.whb, 4, 5),
            10.0, places=2, msg="total conserved across the transfer")

    def test_spanning_interval_units_belong_to_neither(self):
        """A window straddling the transfer date must not credit the moving
        units to either warehouse — they are not guaranteed present for the
        COMPLETE interval on either side (asymmetric date bounds)."""
        self._set_stock(self.wha, 10)
        self._transfer(self.wha, self.whb, 4, day_off=3)  # departs mid-window

        # Window days 2-4 straddles the day-3 transfer.
        self.assertAlmostEqual(
            self._avail(self.wha, 2, 4), 6.0, places=2,
            msg="source: moving units leave within the window -> deducted")
        self.assertAlmostEqual(
            self._avail(self.whb, 2, 4), 0.0, places=2,
            msg="dest: moving units arrive mid-window -> not yet credited")

    def test_rental_pickup_not_counted_as_transfer(self):
        """A rental pickup leg (Stock -> rental location) must NOT be counted as
        an interwarehouse transfer out — the reserved term handles it."""
        self._set_stock(self.wha, 10)
        now = fields.Datetime.now()
        order = self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id, 'warehouse_id': self.wha.id,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=1)})
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id, 'product_id': self.prod.id,
            'product_uom_qty': 3})
        order.action_confirm()

        out = self.prod._get_transfer_out_qty(
            now, now + timedelta(days=1), warehouse=self.wha)
        self.assertAlmostEqual(
            out, 0.0, places=2,
            msg="rental pickup (-> rental_loc) is not an interwarehouse transfer")

    def test_external_supply_projected_by_default(self):
        """A receipt from a supplier location whose operation type keeps the
        default 'projected' policy must NOT raise operational availability."""
        self._set_stock(self.wha, 5)
        now = fields.Datetime.now()
        supplier = self.env.ref('stock.stock_location_suppliers')
        self.assertEqual(self.wha.in_type_id.rental_incoming_policy, 'projected',
                         "receipts default to projected (safe) policy")
        move = self.env['stock.move'].create({
            'product_id': self.prod.id, 'uom_id': self.prod.uom_id.id,
            'product_uom_qty': 7,
            'location_id': supplier.id,
            'location_dest_id': self.wha.lot_stock_id.id,
            'picking_type_id': self.wha.in_type_id.id,
            'date': now - timedelta(days=1),  # already "arrived" date-wise
        })
        move._action_confirm()

        in_qty = self.prod._get_transfer_in_qty(
            now, now + timedelta(days=1), warehouse=self.wha)
        self.assertAlmostEqual(
            in_qty, 0.0, places=2,
            msg="projected-policy supply must not raise operational availability")
        self.assertAlmostEqual(
            self._avail(self.wha, 0, 1), 5.0, places=2,
            msg="operational availability unchanged by a projected PO")

    def test_supply_counts_when_operation_type_is_operational(self):
        """When the receipt operation type is set to 'operational', its
        confirmed incoming supply (a PO) raises operational availability, still
        grounded on the scheduled arrival date (present for the whole
        interval)."""
        self._set_stock(self.wha, 5)
        now = fields.Datetime.now()
        supplier = self.env.ref('stock.stock_location_suppliers')
        self.wha.in_type_id.rental_incoming_policy = 'operational'
        move = self.env['stock.move'].create({
            'product_id': self.prod.id, 'uom_id': self.prod.uom_id.id,
            'product_uom_qty': 2,
            'location_id': supplier.id,
            'location_dest_id': self.wha.lot_stock_id.id,
            'picking_type_id': self.wha.in_type_id.id,
            'date': now + timedelta(days=2),  # arrives on day 2
        })
        move._action_confirm()
        move.date = now + timedelta(days=2)

        # Before the arrival (days 0-1): only current stock is bookable.
        self.assertAlmostEqual(
            self._avail(self.wha, 0, 1), 5.0, places=2,
            msg="supply not yet arrived -> not counted for that interval")
        # After the arrival (days 3-4): flagged supply raises it 5 + 2 = 7.
        self.assertAlmostEqual(
            self._avail(self.wha, 3, 4), 7.0, places=2,
            msg="'operational' operation type raises availability by the supply")
