from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalAvailabilityWarehouse(TransactionCase):
    """Per-warehouse at-customer attribution (Option A base, warehouse-local).

    The physical total for a warehouse is its own on-hand plus the units out
    at a customer that were shipped FROM this warehouse — attributed by the
    order's warehouse from DONE moves reaching the rental location.  This:

    * keeps single-warehouse tenants byte-for-byte unchanged;
    * stops a company's OTHER warehouses seeing rented-out stock as available;
    * counts multi-step transit at the warehouse (never double-counted).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'WH Client'})
        # Ensure the company has a rental (at-customer) location.
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])
        cls.rental_loc = cls.company.rental_loc_id

        cls.wh_a = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.wh_b = cls.env['stock.warehouse'].create({
            'name': 'WH Scoped B', 'code': 'WSB',
            'company_id': cls.company.id})

        cls.prod = cls.env['product.product'].create({
            'name': 'WH Widget', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, product, qty, location):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id,
            'location_id': location.id,
            'inventory_quantity': qty,
        }).action_apply_inventory()

    def _rental_order(self, warehouse, start_offset=0, days=1):
        now = fields.Datetime.now()
        return self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id,
            'warehouse_id': warehouse.id,
            'rental_start_date': now + timedelta(days=start_offset),
            'rental_return_date': now + timedelta(days=start_offset + days),
        })

    def _line(self, order, product, qty):
        return self.env['sale.order.line'].with_context(
            in_rental_app=True).create({
                'order_id': order.id, 'product_id': product.id,
                'product_uom_qty': qty})

    def _pickup(self, line, qty, warehouse):
        """Simulate a completed rental pickup: a DONE move from the warehouse
        stock to the at-customer (rental) location, linked to the line."""
        move = self.env['stock.move'].create({
            'product_id': line.product_id.id,
            'uom_id': line.product_id.uom_id.id,
            'product_uom_qty': qty,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': self.rental_loc.id,
            'sale_line_id': line.id,
        })
        move._action_confirm()
        move._action_assign()
        move.move_line_ids.quantity = qty
        move.picked = True
        move._action_done()
        return move

    # ── single-warehouse: conserved across pickup, unchanged number ──────
    def test_01_single_warehouse_conserved_after_pickup(self):
        self._set_stock(self.prod, 10, self.wh_a.lot_stock_id)
        order = self._rental_order(self.wh_a)
        line = self._line(order, self.prod, 6)
        order.action_confirm()
        self._pickup(line, 6, self.wh_a)

        # 6 physically at customer, 4 left in the warehouse.
        self.assertAlmostEqual(
            self.prod._rental_warehouse_onhand(self.wh_a), 4.0, places=2)
        self.assertAlmostEqual(
            self.prod._rental_at_customer_qty(self.wh_a, self.company),
            6.0, places=2)
        # Physical total is conserved (10) across the pickup.
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=self.wh_a, company=self.company),
            10.0, places=2)
        # Availability for a NEW booking over the same period = 10 − 6 = 4.
        avail = self.prod._rental_available_qty(
            order.rental_start_date, order.rental_return_date,
            warehouse=self.wh_a)
        self.assertAlmostEqual(avail, 4.0, places=2)

    # ── multi-warehouse: no cross-warehouse leak ─────────────────────────
    def test_02_no_cross_warehouse_leak(self):
        self._set_stock(self.prod, 10, self.wh_a.lot_stock_id)
        order = self._rental_order(self.wh_a)
        line = self._line(order, self.prod, 6)
        order.action_confirm()
        self._pickup(line, 6, self.wh_a)

        # WH B never shipped these units, so it owns none of them.
        self.assertAlmostEqual(
            self.prod._rental_at_customer_qty(self.wh_b, self.company),
            0.0, places=2)
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=self.wh_b, company=self.company),
            0.0, places=2)
        avail_b = self.prod._rental_available_qty(
            order.rental_start_date, order.rental_return_date,
            warehouse=self.wh_b)
        self.assertAlmostEqual(
            avail_b, 0.0, places=2,
            msg="Rented-out WH A stock must not be available at WH B")

        # WH A itself still correctly shows 4.
        avail_a = self.prod._rental_available_qty(
            order.rental_start_date, order.rental_return_date,
            warehouse=self.wh_a)
        self.assertAlmostEqual(avail_a, 4.0, places=2)

    # ── multi-step: transit is counted at the warehouse, once ────────────
    def test_03_multistep_transit_counted_at_warehouse(self):
        wh_m = self.env['stock.warehouse'].create({
            'name': 'WH Multi', 'code': 'WMU', 'company_id': self.company.id,
            'reception_steps': 'three_steps',
            'delivery_steps': 'pick_pack_ship'})
        output_loc = wh_m.wh_output_stock_loc_id
        self.assertTrue(output_loc, "3-step warehouse must have an Output zone")

        # 4 in stock, 6 mid-transit in the Output zone (both internal to WH M).
        self._set_stock(self.prod, 4, wh_m.lot_stock_id)
        self._set_stock(self.prod, 6, output_loc)

        # On-hand counts BOTH internal zones = 10; nothing has reached the
        # customer yet, so at-customer is 0 and the base is 10 (not 16).
        self.assertAlmostEqual(
            self.prod._rental_warehouse_onhand(wh_m), 10.0, places=2)
        self.assertAlmostEqual(
            self.prod._rental_at_customer_qty(wh_m, self.company),
            0.0, places=2)
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=wh_m, company=self.company),
            10.0, places=2,
            msg="Transit units counted once at the warehouse, not doubled")

    # ── popup: per-warehouse availability (lazy method) ──────────────────
    def test_04_warehouse_availability_popup(self):
        """get_rental_warehouse_availability lists per-warehouse availability,
        always includes the order's warehouse, and hides fully-empty ones."""
        self._set_stock(self.prod, 6, self.wh_a.lot_stock_id)  # WH B empty
        order = self._rental_order(self.wh_a)
        line = self._line(order, self.prod, 2)
        order.action_confirm()

        rows = line.get_rental_warehouse_availability()
        by_wh = {r['warehouse_id']: r for r in rows}
        # WH A (the order's warehouse) is always shown, with its availability.
        self.assertIn(self.wh_a.id, by_wh)
        self.assertTrue(by_wh[self.wh_a.id]['is_current'])
        self.assertAlmostEqual(by_wh[self.wh_a.id]['available'], 6.0, places=2)
        # WH B is fully empty → hidden.
        self.assertNotIn(self.wh_b.id, by_wh)

        # Give WH B stock → it now appears with its own availability.
        self._set_stock(self.prod, 3, self.wh_b.lot_stock_id)
        by_wh2 = {r['warehouse_id']: r
                  for r in line.get_rental_warehouse_availability()}
        self.assertIn(self.wh_b.id, by_wh2)
        self.assertFalse(by_wh2[self.wh_b.id]['is_current'])
        self.assertAlmostEqual(by_wh2[self.wh_b.id]['available'], 3.0, places=2)

    def test_05_warehouse_availability_empty_for_sets_and_single_wh(self):
        """No per-warehouse section for set lines (sourced per component)."""
        set_tmpl = self.env['product.template'].create({
            'name': 'WH Set', 'type': 'consu', 'rent_periodicity': 'days',
            'is_rental_set': True, 'set_pricing_mode': 'sum'})
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id, 'product_id': self.prod.id,
            'quantity': 1, 'sequence': 10})
        self._set_stock(self.prod, 5, self.wh_a.lot_stock_id)
        order = self._rental_order(self.wh_a)
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1})
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component)
        self.assertEqual(set_parent.get_rental_warehouse_availability(), [])
