from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalScanningCommon(TransactionCase):
    """Acceptance tests for the rental_scanning reconciliation core.

    Fixtures mirror the live dev scenario:
      * set "40 Glazen in Eurobak" (barcode EUROBAKBARCODE) = 1 Eurobak + 40 Glas
      * Eurobak 40 / Glas / Kayak: consu, storable, NOT serial-tracked
      * BAK01 = 1 Eurobak + 40 Glas            (exact match)
      * BAK02 = 1 Eurobak + 35 Glas            (partial)
      * BAK03 = 1 Eurobak + 41 Glas            (glas overflow)
      * BAK04 = 1 Eurobak + 40 Glas @ Output   (wrong source location)
      * BAK05 = 1 Kayak                         (product not on order)
    A separate serial-tracked 'Crate' product covers PPB-13 (serial scan).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.customer_loc = cls.env.ref('stock.stock_location_customers')
        cls.other_loc = cls.env['stock.location'].create({
            'name': 'RS Output', 'usage': 'internal',
            'location_id': cls.warehouse.view_location_id.id,
        })

        # Products (match live data: NOT serial-tracked) ---------------------
        cls.glas = cls.env['product.product'].create({
            'name': 'Glas', 'type': 'consu', 'is_storable': True})
        cls.kayak = cls.env['product.product'].create({
            'name': 'Kayak (1-persoons)', 'type': 'consu', 'is_storable': True})
        cls.eurobak = cls.env['product.product'].create({
            'name': 'Eurobak 40', 'type': 'consu', 'is_storable': True})
        # Serial-tracked crate, only for the serial-scan tests (PPB-13).
        cls.crate = cls.env['product.product'].create({
            'name': 'Crate', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial'})

        # Rental set with barcode EUROBAKBARCODE -----------------------------
        cls.set_tmpl = cls.env['product.template'].create({
            'name': '40 Glazen in Eurobak', 'type': 'consu',
            'is_storable': True, 'is_rental_set': True})
        cls.env['rental.set.component'].create([
            {'set_product_tmpl_id': cls.set_tmpl.id,
             'product_id': cls.eurobak.id, 'quantity': 1},
            {'set_product_tmpl_id': cls.set_tmpl.id,
             'product_id': cls.glas.id, 'quantity': 40},
        ])
        cls.set_product = cls.set_tmpl.product_variant_id
        cls.set_product.barcode = 'EUROBAKBARCODE'

        # Base loose stock so deliveries can reserve --------------------------
        cls._set_stock(cls.glas, cls.stock_loc, 1000)
        cls._set_stock(cls.eurobak, cls.stock_loc, 10)

        # Named prepared packages (BAK01..BAK05) ------------------------------
        cls.bak01 = cls._mk_package('BAK01', [
            (cls.eurobak, 1, None), (cls.glas, 40, None)])
        cls.bak02 = cls._mk_package('BAK02', [
            (cls.eurobak, 1, None), (cls.glas, 35, None)])
        cls.bak03 = cls._mk_package('BAK03', [
            (cls.eurobak, 1, None), (cls.glas, 41, None)])
        cls.bak04 = cls._mk_package('BAK04', [
            (cls.eurobak, 1, None), (cls.glas, 40, None)], location=cls.other_loc)
        cls.bak05 = cls._mk_package('BAK05', [(cls.kayak, 1, None)])

    # ── helpers ────────────────────────────────────────────────────────────

    @classmethod
    def _set_stock(cls, product, location, qty, lot=None, package=None):
        vals = {'product_id': product.id, 'location_id': location.id,
                'inventory_quantity': qty}
        if lot:
            vals['lot_id'] = lot.id
        if package:
            vals['package_id'] = package.id
        quant = cls.env['stock.quant'].with_context(
            inventory_mode=True).create(vals)
        quant.action_apply_inventory()
        return quant

    @classmethod
    def _mk_package(cls, name, contents, location=None):
        location = location or cls.stock_loc
        package = cls.env['stock.package'].create({'name': name})
        for product, qty, lot in contents:
            cls._set_stock(product, location, qty, lot=lot, package=package)
        return package

    def _serial(self, name):
        return self.env['stock.lot'].create({
            'name': name, 'product_id': self.crate.id})

    def _make_delivery(self, demand, reserve=False):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.stock_loc.id,
            'location_dest_id': self.customer_loc.id,
        })
        for product, qty in demand:
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty, 'uom_id': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': self.stock_loc.id,
                'location_dest_id': self.customer_loc.id,
            })
        picking.action_confirm()
        if reserve:
            picking.action_assign()
        else:
            picking.do_unreserve()
        return picking

    def _make_internal(self, demand, dest):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.int_type_id.id,
            'location_id': self.stock_loc.id,
            'location_dest_id': dest.id,
        })
        for product, qty in demand:
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty, 'uom_id': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': self.stock_loc.id, 'location_dest_id': dest.id,
            })
        picking.action_confirm()
        picking.do_unreserve()
        return picking

    def _mv(self, picking, product):
        return picking.move_ids.filtered(lambda m: m.product_id == product)[:1]

    def _set_delivery(self):
        """Delivery with the set's expanded demand: 1 Eurobak + 40 Glas."""
        return self._make_delivery([(self.eurobak, 1), (self.glas, 40)])


class TestRentalScanning(TestRentalScanningCommon):

    # ── BAK01 : exact match (T-01) ──────────────────────────────────────────
    def test_bak01_exact_match(self):
        picking = self._set_delivery()
        res = picking.rental_scanning_scan('BAK01')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        self.assertIn(self.bak01, picking.rental_scanning_package_ids)
        self.assertEqual(
            self._mv(picking, self.glas).move_line_ids[:1].package_id, self.bak01)

    # ── BAK01 on a RESERVED picking (T-15 regression) ───────────────────────
    def test_bak01_on_reserved_picking(self):
        picking = self._make_delivery(
            [(self.eurobak, 1), (self.glas, 40)], reserve=True)
        self.assertTrue(all(not l.picked for l in picking.move_line_ids))
        res = picking.rental_scanning_scan('BAK01')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)

    # ── BAK02 : partial (T-05) ──────────────────────────────────────────────
    def test_bak02_partial(self):
        picking = self._set_delivery()
        res = picking.rental_scanning_scan('BAK02')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.glas).quantity, 35)
        self.assertEqual(picking._rs_remaining(self.glas.id, False), 5)

    # ── BAK03 : overflow -> split prompt (T-04) ─────────────────────────────
    def test_bak03_overflow_prompts_split(self):
        picking = self._set_delivery()
        res = picking.rental_scanning_scan('BAK03')
        self.assertEqual(res['status'], 'need_split')
        self.assertIn('Glas', res['message'])
        # warning mentions the package will be lost
        self.assertIn('BAK03', res['message'])
        # nothing applied yet
        self.assertEqual(self._mv(picking, self.glas).quantity, 0)
        # confirm split -> capped at demand, and the package is dissolved
        res2 = picking.rental_scanning_scan('BAK03', allow_split=True)
        self.assertEqual(res2['status'], 'partial')
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)
        self.assertFalse(self.bak03.quant_ids,
                         "a split dissolves (unpacks) the whole package")

    # ── BAK04 : wrong source location (T-06) ────────────────────────────────
    def test_bak04_wrong_location(self):
        picking = self._set_delivery()
        with self.assertRaises(UserError):
            picking.rental_scanning_scan('BAK04')

    # ── BAK05 : product not on order (T-03) ─────────────────────────────────
    def test_bak05_product_not_on_order(self):
        picking = self._set_delivery()
        with self.assertRaises(UserError):
            picking.rental_scanning_scan('BAK05')
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 0)

    # ── EUROBAKBARCODE : set scan (T-09, PPB-12) ────────────────────────────
    def test_eurobakbarcode_set_scan(self):
        picking = self._set_delivery()
        res = picking.rental_scanning_scan('EUROBAKBARCODE')
        self.assertEqual(res['kind'], 'set')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)

    # ── Unassign / remove (PPB-15, T-17) ────────────────────────────────────
    def test_unassign_package(self):
        picking = self._set_delivery()
        picking.rental_scanning_scan('BAK01')
        self.assertIn(self.bak01, picking.rental_scanning_package_ids)
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)

        picking.rental_scanning_remove_package('BAK01')
        self.assertNotIn(self.bak01, picking.rental_scanning_package_ids)
        self.assertEqual(self._mv(picking, self.glas).quantity, 0)
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 0)

    def test_unassign_unknown_raises(self):
        picking = self._set_delivery()
        with self.assertRaises(UserError):
            picking.rental_scanning_remove_package('BAK01')  # not applied

    # ── Swap partial -> full via remove (Q2, T-18) ──────────────────────────
    def test_swap_partial_for_full(self):
        picking = self._set_delivery()
        picking.rental_scanning_scan('BAK02')            # 35
        self.assertEqual(self._mv(picking, self.glas).quantity, 35)
        picking.rental_scanning_remove_package('BAK02')
        picking.rental_scanning_scan('BAK01')            # 40
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        line = self._mv(picking, self.glas).move_line_ids.filtered('quantity')[:1]
        self.assertEqual(line.package_id, self.bak01)

    # ── Idempotent re-scan (T-16) ───────────────────────────────────────────
    def test_rescan_idempotent(self):
        picking = self._set_delivery()
        picking.rental_scanning_scan('BAK01')
        res = picking.rental_scanning_scan('BAK01')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)

    # ── Serial in a package -> resolves to container (T-10, PPB-13) ─────────
    def test_serial_scan_resolves_to_container(self):
        serial = self._serial('CR-0001')
        pkg = self._mk_package('CRATEPKG', [
            (self.crate, 1, serial), (self.glas, 40, None)])
        picking = self._make_delivery([(self.crate, 1), (self.glas, 40)])
        res = picking.rental_scanning_scan('CR-0001')
        self.assertEqual(res['kind'], 'package')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        crate_line = self._mv(picking, self.crate).move_line_ids[:1]
        self.assertEqual(crate_line.package_id, pkg)
        self.assertEqual(crate_line.lot_id, serial)

    # ── Loose serial -> only that product (T-13, PPB-13 fallback) ───────────
    def test_loose_serial_adds_only_that_product(self):
        serial = self._serial('CR-0002')
        self._set_stock(self.crate, self.stock_loc, 1, lot=serial)
        picking = self._make_delivery([(self.crate, 1), (self.glas, 40)])
        res = picking.rental_scanning_scan('CR-0002')
        self.assertEqual(res['kind'], 'product')
        self.assertEqual(self._mv(picking, self.crate).quantity, 1)
        self.assertEqual(self._mv(picking, self.crate).move_line_ids[:1].lot_id,
                         serial)
        self.assertEqual(self._mv(picking, self.glas).quantity, 0)

    # ── Partial then backorder (T-14) ───────────────────────────────────────
    def test_partial_then_backorder(self):
        picking = self._set_delivery()
        picking.rental_scanning_scan('BAK02')  # 1 Eurobak + 35 Glas
        self.assertEqual(self._mv(picking, self.glas).quantity, 35)

        action = picking.button_validate()
        if isinstance(action, dict) and action.get('res_model') == \
                'stock.backorder.confirmation':
            self.env['stock.backorder.confirmation'].with_context(
                action['context']).create({}).process()

        self.assertEqual(picking.state, 'done')
        backorder = self.env['stock.picking'].search(
            [('backorder_id', '=', picking.id)])
        self.assertTrue(backorder)
        self.assertEqual(
            sum(backorder.move_ids.filtered(
                lambda m: m.product_id == self.glas).mapped('product_uom_qty')),
            5)

    # ── Nested / general pack (T-19, Path A) ────────────────────────────────
    def test_nested_general_pack(self):
        general = self.env['stock.package'].create({'name': 'GENERAL'})
        self.bak01.parent_package_id = general.id
        picking = self._set_delivery()
        res = picking.rental_scanning_scan('GENERAL')
        self.assertEqual(res['status'], 'applied')
        self.assertEqual(self._mv(picking, self.eurobak).quantity, 1)
        self.assertEqual(self._mv(picking, self.glas).quantity, 40)
        # Source stamped as the ACTUAL inner package (BAK01), not the outer one.
        line = self._mv(picking, self.glas).move_line_ids.filtered('quantity')[:1]
        self.assertEqual(line.package_id, self.bak01)

    # ── PPB-17: retain package on internal destination ─────────────────────
    def test_ppb17_internal_dest_retains_package(self):
        picking = self._make_internal(
            [(self.eurobak, 1), (self.glas, 40)], self.other_loc)
        picking.rental_scanning_scan('BAK01')
        lines = picking.move_line_ids.filtered('quantity')
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(line.result_package_id, self.bak01,
                             "internal step must retain the scanned package")

    # ── PPB-17: dissolve at customer destination (Option iii) ──────────────
    def test_ppb17_customer_dest_dissolves(self):
        picking = self._set_delivery()  # destination = customer
        picking.rental_scanning_scan('BAK01')
        lines = picking.move_line_ids.filtered('quantity')
        self.assertTrue(lines)
        for line in lines:
            self.assertFalse(line.result_package_id,
                             "customer delivery must dissolve the pack")

    # ── PPB-17: rental/customer-outside-warehouse dissolves ────────────────
    def test_ppb17_rental_location_dissolves(self):
        # sale_renting sends rented goods to an INTERNAL-usage location that
        # lives OUTSIDE the warehouse ('Customers/Rental').  That is the final
        # delivery and must dissolve — not retain (regression: BAK03 -> BAK03).
        rental_loc = self.env['stock.location'].create({
            'name': 'Rental', 'usage': 'internal',
            'location_id': self.customer_loc.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.stock_loc.id,
            'location_dest_id': rental_loc.id,
        })
        for product, qty in [(self.eurobak, 1), (self.glas, 40)]:
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty, 'uom_id': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': self.stock_loc.id,
                'location_dest_id': rental_loc.id,
            })
        picking.action_confirm()
        picking.do_unreserve()
        picking.rental_scanning_scan('BAK01')
        for line in picking.move_line_ids.filtered('quantity'):
            self.assertFalse(line.result_package_id,
                             "rental delivery (outside WH) must dissolve")

    # ── PPB-17: a split must NOT retain the package ────────────────────────
    def test_ppb17_split_does_not_retain(self):
        # Internal step, but BAK03 overflows (41 Glas) -> split -> must NOT
        # retain (a package cannot be split across two locations).
        picking = self._make_internal(
            [(self.eurobak, 1), (self.glas, 40)], self.other_loc)
        self.assertEqual(
            picking.rental_scanning_scan('BAK03')['status'], 'need_split')
        res = picking.rental_scanning_scan('BAK03', allow_split=True)
        self.assertEqual(res['status'], 'partial')
        for line in picking.move_line_ids.filtered('quantity'):
            self.assertFalse(line.result_package_id,
                             "a split must not retain the package")

    # ── Unknown barcode ─────────────────────────────────────────────────────
    def test_unknown_barcode_raises(self):
        picking = self._set_delivery()
        with self.assertRaises(UserError):
            picking.rental_scanning_scan('NOPE-XYZ')
