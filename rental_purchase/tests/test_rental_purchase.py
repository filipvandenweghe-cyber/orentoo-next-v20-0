# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'rental_purchase')
class TestRentalPurchase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.supplier_loc = cls.env.ref('stock.stock_location_suppliers')
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Rental Vendor',
            'property_stock_supplier': cls.supplier_loc.id,
        })
        cls.categ = cls.env['product.category'].create({'name': 'Rental Category'})
        cls.product = cls.env['product.product'].create({
            'name': 'Rented Chair',
            'is_storable': True,
            'purchase_ok': True,
            'rent_periodicity': 'days',
            'type': 'consu',
            'categ_id': cls.categ.id,
        })
        cls.serial_product = cls.env['product.product'].create({
            'name': 'Rented Serial Machine',
            'is_storable': True,
            'purchase_ok': True,
            'type': 'consu',
            'tracking': 'serial',
            'categ_id': cls.categ.id,
        })
        cls.start = datetime(2026, 9, 10, 8, 0, 0)
        cls.ret = datetime(2026, 9, 17, 17, 0, 0)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @classmethod
    def _make_warehouse(cls, code, delivery_steps):
        return cls.env['stock.warehouse'].create({
            'name': 'WH %s' % code,
            'code': code,
            'company_id': cls.company.id,
            'reception_steps': 'one_step',
            'delivery_steps': delivery_steps,
        })

    def _make_po(self, warehouse, product=None, qty=10, rental=True):
        product = product or self.product
        return self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'is_rental_purchase': rental,
            'rental_start_date': self.start if rental else False,
            'rental_return_date': self.ret if rental else False,
            'picking_type_id': warehouse.in_type_id.id,
            'order_line': [Command.create({
                'product_id': product.id,
                'product_qty': qty,
                'price_unit': 5.0,
            })],
        })

    def _receive(self, po, qty_by_product=None, backorder=True):
        """Validate the incoming receipt; optionally receive partial qty."""
        receipt = po.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'incoming'
            and p.state not in ('done', 'cancel'))[:1]
        receipt.action_assign()
        for move in receipt.move_ids:
            qty = move.product_uom_qty
            if qty_by_product and move.product_id in qty_by_product:
                qty = qty_by_product[move.product_id]
            move.quantity = qty
            move.picked = True
        res = receipt.button_validate()
        if isinstance(res, dict) and res.get('res_model') == 'stock.backorder.confirmation':
            wiz = self.env['stock.backorder.confirmation'].with_context(
                res['context']).create({})
            if backorder:
                wiz.process()
            else:
                wiz.process_cancel_backorder()
        return receipt

    def _return_moves(self, po):
        return self.env['stock.move'].search([
            ('rental_purchase_order_id', '=', po.id)])

    def _owned_qty(self, warehouse, product, owner):
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', warehouse.lot_stock_id.id),
            ('owner_id', '=', owner.id),
        ])
        return sum(quants.mapped('quantity'))

    # ------------------------------------------------------------------
    # Test 1 - Normal Purchase is unaffected
    # ------------------------------------------------------------------
    def test_01_normal_purchase(self):
        wh = self._make_warehouse('RPN', 'ship_only')
        po = self._make_po(wh, rental=False)
        po.button_confirm()
        self.assertEqual(po.state, 'purchase')
        self.assertFalse(po.rental_return_picking_ids,
                         "A normal PO must not create any rental return.")
        receipt = po.picking_ids
        self.assertTrue(receipt)
        self.assertFalse(receipt.owner_id,
                         "A normal receipt must not be supplier-owned.")

    # ------------------------------------------------------------------
    # Test 2 - External supplier Rental Purchase
    # ------------------------------------------------------------------
    def test_02_external_rental_purchase(self):
        wh = self._make_warehouse('RPE', 'ship_only')
        po = self._make_po(wh)
        po.button_confirm()
        # normal incoming receipt
        receipt = po.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'incoming')
        self.assertTrue(receipt, "Standard incoming receipt must exist.")
        # supplier remains owner
        self.assertEqual(receipt.owner_id, self.vendor)
        # future supplier return exists
        self.assertTrue(po.rental_return_picking_ids,
                        "A future supplier return must be prepared.")
        return_moves = self._return_moves(po)
        self.assertTrue(return_moves)
        self.assertEqual(return_moves.mapped('restrict_partner_id'), self.vendor)

    # ------------------------------------------------------------------
    # Test 3 / 4 / 5 - outgoing route steps + dates + ownership
    # ------------------------------------------------------------------
    def _assert_return_chain(self, po, wh, expected_locations):
        moves = self._return_moves(po).sorted('id')
        pairs = [(m.location_id, m.location_dest_id) for m in moves]
        self.assertEqual(pairs, expected_locations)
        # final destination is the supplier location, never the customer
        self.assertEqual(moves[-1].location_dest_id, self.supplier_loc)
        # scheduling driven by rental return date
        for m in moves:
            self.assertEqual(m.date_deadline, self.ret)

    def test_03_one_step(self):
        wh = self._make_warehouse('RP1', 'ship_only')
        po = self._make_po(wh)
        po.button_confirm()
        self._assert_return_chain(po, wh, [
            (wh.lot_stock_id, self.supplier_loc),
        ])
        # incoming: Vendor -> Stock, dated rental start
        receipt = po.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')
        self.assertEqual(receipt.move_ids.date_deadline, self.start)

    def test_04_two_step(self):
        wh = self._make_warehouse('RP2', 'pick_ship')
        po = self._make_po(wh)
        po.button_confirm()
        self._assert_return_chain(po, wh, [
            (wh.lot_stock_id, wh.wh_output_stock_loc_id),
            (wh.wh_output_stock_loc_id, self.supplier_loc),
        ])

    def test_05_three_step(self):
        wh = self._make_warehouse('RP3', 'pick_pack_ship')
        po = self._make_po(wh)
        po.button_confirm()
        self._assert_return_chain(po, wh, [
            (wh.lot_stock_id, wh.wh_pack_stock_loc_id),
            (wh.wh_pack_stock_loc_id, wh.wh_output_stock_loc_id),
            (wh.wh_output_stock_loc_id, self.supplier_loc),
        ])

    # ------------------------------------------------------------------
    # Test 6 - existing unrelated stock is never reserved by the return
    # ------------------------------------------------------------------
    def test_06_unrelated_stock_not_reserved(self):
        wh = self._make_warehouse('RP6', 'ship_only')
        # 20 company-owned chairs already on hand
        self.env['stock.quant']._update_available_quantity(
            self.product, wh.lot_stock_id, 20)
        po = self._make_po(wh, qty=10)
        po.button_confirm()
        first_return = self._return_moves(po).filtered(
            lambda m: m.location_id == wh.lot_stock_id)
        # Before any receipt: the return must not grab the company's 20 chairs
        first_return._action_assign()
        self.assertEqual(first_return.quantity, 0,
                         "The return reserved unrelated company-owned stock!")
        # After receiving 10 supplier-owned chairs: only those 10 are eligible
        self._receive(po)
        self.assertEqual(self._owned_qty(wh, self.product, self.vendor), 10)
        first_return._action_assign()
        self.assertEqual(first_return.quantity, 10)
        # company stock untouched
        self.assertEqual(self._owned_qty(wh, self.product, self.company.partner_id), 0)

    # ------------------------------------------------------------------
    # Test 7 - partial receipt: return follows what actually arrived
    # ------------------------------------------------------------------
    def test_07_partial_receipt(self):
        wh = self._make_warehouse('RP7', 'ship_only')
        self.env['stock.quant']._update_available_quantity(
            self.product, wh.lot_stock_id, 20)
        po = self._make_po(wh, qty=10)
        po.button_confirm()
        self._receive(po, qty_by_product={self.product: 8}, backorder=True)
        self.assertEqual(self._owned_qty(wh, self.product, self.vendor), 8)
        first_return = self._return_moves(po).filtered(
            lambda m: m.location_id == wh.lot_stock_id)
        first_return._action_assign()
        self.assertEqual(first_return.quantity, 8,
                         "Return must never pull the 2 missing units from own stock.")

    # ------------------------------------------------------------------
    # Test 8 - serial tracking keeps supplier ownership
    # ------------------------------------------------------------------
    def test_08_serial_ownership(self):
        wh = self._make_warehouse('RP8', 'ship_only')
        po = self._make_po(wh, product=self.serial_product, qty=2)
        po.button_confirm()
        receipt = po.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')[:1]
        move = receipt.move_ids
        move.move_line_ids.unlink()
        for i in range(2):
            lot = self.env['stock.lot'].create({
                'name': 'SERIAL-%s' % i,
                'product_id': self.serial_product.id,
            })
            self.env['stock.move.line'].create({
                'move_id': move.id,
                'picking_id': receipt.id,
                'product_id': self.serial_product.id,
                'quantity': 1,
                'lot_id': lot.id,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            })
        move.picked = True
        receipt.button_validate()
        quants = self.env['stock.quant'].search([
            ('product_id', '=', self.serial_product.id),
            ('location_id', '=', wh.lot_stock_id.id),
            ('quantity', '>', 0),
        ])
        self.assertEqual(len(quants), 2)
        self.assertEqual(quants.mapped('owner_id'), self.vendor,
                         "Every received serial must stay supplier-owned.")

    # ------------------------------------------------------------------
    # Test 16 - cancellation before receipt
    # ------------------------------------------------------------------
    def test_16_cancel_before_receipt(self):
        wh = self._make_warehouse('R16', 'ship_only')
        po = self._make_po(wh)
        po.button_confirm()
        self.assertTrue(po.rental_return_picking_ids)
        po.button_cancel()
        self.assertEqual(po.state, 'cancel')
        self.assertTrue(all(p.state == 'cancel'
                            for p in po.rental_return_picking_ids),
                        "Return pickings must be cancelled with the PO.")

    # ------------------------------------------------------------------
    # Test 17 - cancellation after receipt is blocked
    # ------------------------------------------------------------------
    def test_17_cancel_after_receipt_blocked(self):
        wh = self._make_warehouse('R17', 'ship_only')
        po = self._make_po(wh)
        po.button_confirm()
        self._receive(po)
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            po.button_cancel()

    # ------------------------------------------------------------------
    # Test 18 - partial final return is allowed (no custom reconciliation)
    # ------------------------------------------------------------------
    def test_18_partial_final_return(self):
        wh = self._make_warehouse('R18', 'ship_only')
        po = self._make_po(wh, qty=10)
        po.button_confirm()
        self._receive(po)
        first_return = self._return_moves(po).filtered(
            lambda m: m.location_id == wh.lot_stock_id)
        first_return._action_assign()
        # return only 6 of the 10 supplier-owned units
        first_return.quantity = 6
        first_return.picked = True
        res = first_return.picking_id.button_validate()
        if isinstance(res, dict) and res.get('res_model') == 'stock.backorder.confirmation':
            self.env['stock.backorder.confirmation'].with_context(
                res['context']).create({}).process()
        self.assertEqual(first_return.state, 'done')
        self.assertEqual(first_return.quantity, 6,
                         "Fewer units than received may be returned.")

    # ------------------------------------------------------------------
    # Extra - Rental Purchase expense account on the vendor bill (§8)
    # ------------------------------------------------------------------
    def test_19_expense_account(self):
        wh = self._make_warehouse('R19', 'ship_only')
        rental_expense = self.env['account.account'].create({
            'name': 'Rental Equipment Expense',
            'code': 'RENTEXP',
            'account_type': 'expense',
        })
        self.product.categ_id.rental_purchase_expense_account_id = rental_expense
        po = self._make_po(wh)
        po.button_confirm()
        self._receive(po)
        po.action_create_invoice()
        bill = po.invoice_ids
        self.assertTrue(bill)
        product_line = bill.invoice_line_ids.filtered(
            lambda l: l.product_id == self.product)
        self.assertEqual(product_line.account_id, rental_expense,
                         "Rental Purchase bill line must use the rental expense account.")

    def test_20_normal_bill_account_unchanged(self):
        wh = self._make_warehouse('R20', 'ship_only')
        rental_expense = self.env['account.account'].create({
            'name': 'Rental Equipment Expense 2',
            'code': 'RENTEX2',
            'account_type': 'expense',
        })
        self.product.categ_id.rental_purchase_expense_account_id = rental_expense
        po = self._make_po(wh, rental=False)
        po.button_confirm()
        self._receive(po)
        po.action_create_invoice()
        bill = po.invoice_ids
        product_line = bill.invoice_line_ids.filtered(
            lambda l: l.product_id == self.product)
        self.assertNotEqual(product_line.account_id, rental_expense,
                            "A normal PO bill must keep the standard expense account.")

    # ------------------------------------------------------------------
    # Extra - hired-in units leave the rental availability at the return date
    # (integration with rental_set; skipped if that engine is absent)
    # ------------------------------------------------------------------
    def test_21_return_reduces_rental_availability(self):
        Product = self.env['product.product']
        if not hasattr(Product, '_rental_available_qty'):
            self.skipTest("rental_set availability engine not installed")
        company = self.company
        if not company.rental_loc_id:
            self.skipTest("no rental location configured for company")
        wh = self._make_warehouse('R21', 'ship_only')
        rentable = Product.create({
            'name': 'Hired Printer',
            'is_storable': True,
            'purchase_ok': True,
            'rent_periodicity': 'days',
            'type': 'consu',
            'categ_id': self.categ.id,
        })
        po = self._make_po(wh, product=rentable, qty=10)
        po.button_confirm()
        self._receive(po)  # 10 supplier-owned units now on hand at wh/Stock
        # the chained return should no longer be waiting on the receipt
        ret = self._return_moves(po)
        self.assertNotIn(ret.state, ('draft', 'waiting'))

        during_from = self.start
        during_to = self.ret - timedelta(hours=1)
        after = self.ret + timedelta(days=1)
        avail_during = rentable._rental_available_qty(
            during_from, during_to, warehouse=wh, company=company, clamp=False)
        avail_after = rentable._rental_available_qty(
            after, after, warehouse=wh, company=company, clamp=False)
        self.assertEqual(avail_during, 10.0,
                         "Hired-in units must be available during the rental.")
        self.assertEqual(avail_after, 0.0,
                         "Hired-in units must leave availability after the "
                         "return date (return departure counted).")

    # ------------------------------------------------------------------
    # Return obligation tracks RECEIVED, not ordered (mirror of Option B)
    # ------------------------------------------------------------------
    def test_22_partial_no_backorder_shrinks_return(self):
        wh = self._make_warehouse('R22', 'ship_only')
        # 5 units we genuinely own — must never be hidden by the return.
        self.env['stock.quant']._update_available_quantity(
            self.product, wh.lot_stock_id, 5)
        po = self._make_po(wh, qty=10)
        po.button_confirm()
        # receive 8, decline the back-order (shortfall written off)
        self._receive(po, qty_by_product={self.product: 8}, backorder=False)
        ret = self._return_moves(po)
        self.assertEqual(ret.product_uom_qty, 8.0,
                         "Return obligation must shrink to what was received.")

        if hasattr(self.env['product.product'], '_rental_available_qty') \
                and self.company.rental_loc_id and self.product.rent_periodicity:
            during = self.product._rental_available_qty(
                self.start, self.ret - timedelta(hours=1),
                warehouse=wh, company=self.company, clamp=False)
            after = self.product._rental_available_qty(
                self.ret + timedelta(days=1), self.ret + timedelta(days=1),
                warehouse=wh, company=self.company, clamp=False)
            self.assertEqual(during, 13.0)   # 5 own + 8 hired
            self.assertEqual(after, 5.0)     # only our own 5 remain

    def test_23_backorder_keeps_full_return(self):
        wh = self._make_warehouse('R23', 'ship_only')
        po = self._make_po(wh, qty=10)
        po.button_confirm()
        # receive 8 WITH a back-order for the remaining 2
        self._receive(po, qty_by_product={self.product: 8}, backorder=True)
        ret = self._return_moves(po)
        self.assertEqual(ret.product_uom_qty, 10.0,
                         "A pending back-order keeps the obligation full - the "
                         "remaining units are still coming.")
        # receive the back-order (the remaining 2)
        self._receive(po)
        self.assertEqual(ret.product_uom_qty, 10.0)
        ret._action_assign()
        self.assertEqual(ret.quantity, 10.0,
                         "Return must reserve all received units (chain "
                         "relinked to the back-order receipt).")

    # ------------------------------------------------------------------
    # Operational both-sides: projected bump BEFORE any receipt
    # ------------------------------------------------------------------
    def test_24_operational_projection_before_receipt(self):
        Product = self.env['product.product']
        if not hasattr(Product, '_rental_available_qty'):
            self.skipTest("rental_set availability engine not installed")
        if not self.company.rental_loc_id:
            self.skipTest("no rental location configured for company")
        wh = self._make_warehouse('R24', 'ship_only')
        po = self._make_po(wh, product=self.product, qty=10)
        po.button_confirm()
        # nothing received yet
        receipt = po.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'incoming')
        self.assertNotIn('done', receipt.mapped('state'))

        before = self.product._rental_available_qty(
            self.start - timedelta(days=2), self.start - timedelta(days=1),
            warehouse=wh, company=self.company, clamp=False)
        during = self.product._rental_available_qty(
            self.start, self.ret - timedelta(hours=1),
            warehouse=wh, company=self.company, clamp=False)
        after = self.product._rental_available_qty(
            self.ret + timedelta(days=1), self.ret + timedelta(days=1),
            warehouse=wh, company=self.company, clamp=False)
        self.assertEqual(before, 0.0, "Not available before the rental starts.")
        self.assertEqual(during, 10.0, "Hired units projected available during "
                                       "the rental, before any receipt.")
        self.assertEqual(after, 0.0, "Back to baseline after the return date.")
