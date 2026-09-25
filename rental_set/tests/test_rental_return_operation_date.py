from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalReturnOperationDate(TransactionCase):
    """Availability follows the warehouse's real RETURN operation, not the
    order's declared return_date.

    Reproduces order S00338: 6 units are out at a customer, the order says
    they return on day 1, but the physical return receipt is (re)scheduled for
    day 5.  A booking that overlaps days 2-4 must therefore see the 6 units as
    still OUT (unavailable), not freed by the declared date.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Return Client'})
        cls.wh = cls.env['stock.warehouse'].create({
            'name': 'Ret WH', 'code': 'RTW', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        # Real pickup/return pickings.
        cls.env['res.config.settings'].create(
            {'group_rental_stock_picking': True}).set_values()
        cls.env['stock.warehouse'].update_rental_rules()
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])
        cls.prod = cls.env['product.product'].create({
            'name': 'Return Widget', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, qty):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.prod.id,
            'location_id': self.wh.lot_stock_id.id,
            'inventory_quantity': qty,
        }).action_apply_inventory()

    def _order(self, start_offset=0, days=1):
        now = fields.Datetime.now()
        return self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id,
            'warehouse_id': self.wh.id,
            'rental_start_date': now + timedelta(days=start_offset),
            'rental_return_date': now + timedelta(days=start_offset + days),
        })

    def _line(self, order, qty):
        return self.env['sale.order.line'].with_context(
            in_rental_app=True).create({
                'order_id': order.id, 'product_id': self.prod.id,
                'product_uom_qty': qty})

    def _deliver_full(self, order, qty):
        """Validate the outbound rental picking fully (units out at customer)."""
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing'
            and p.state not in ('done', 'cancel'))
        picking.ensure_one()
        picking.action_assign()
        move = picking.move_ids.filtered(lambda m: m.product_id == self.prod)
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        picking.button_validate()
        return picking

    def _return_full(self, order, qty):
        """Validate the inbound (return) rental picking fully — units back."""
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'incoming'
            and p.state not in ('done', 'cancel'))
        picking.ensure_one()
        picking.action_assign()
        move = picking.move_ids.filtered(lambda m: m.product_id == self.prod)
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        picking.button_validate()
        return picking

    def _avail(self, f_off, t_off, ignored=False):
        now = fields.Datetime.now()
        return self.prod._rental_available_qty(
            now + timedelta(days=f_off), now + timedelta(days=t_off),
            warehouse=self.wh, ignored_soline_id=ignored)

    # ── the S00338 reproduction ──────────────────────────────────────────
    def test_return_scheduled_past_window_keeps_units_reserved(self):
        self._set_stock(10)
        order = self._order(start_offset=0, days=1)
        line = self._line(order, 6)
        order.action_confirm()
        self._deliver_full(order, 6)
        self.assertAlmostEqual(line.qty_delivered, 6.0, places=2)

        # The pending return receipt — reschedule it well past the declared
        # return_date (order says day 1; receipt now scheduled day 5).
        ret = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'incoming'
            and p.state not in ('done', 'cancel'))
        ret.ensure_one()
        now = fields.Datetime.now()
        ret.move_ids.write({'date': now + timedelta(days=5)})

        # Sanity: the line now reports the operation date, not the order date.
        self.assertAlmostEqual(
            (line._rental_effective_return_date()
             - (now + timedelta(days=5))).total_seconds(), 0.0, delta=2,
            msg="Effective return must follow the return operation date")

        # Total is conserved either way (the warehouse still owns all 10).
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=self.wh, company=self.company), 10.0, places=2)

        # A booking on days 2-3 (after the declared return, before the real
        # return) must see only 4 — the 6 are still physically out.
        self.assertAlmostEqual(
            self._avail(2, 3), 4.0, places=2,
            msg="Units out until the return operation → 4, not 10 (S00338)")

        # A booking on days 6-7 (after the real return) sees all 10 back.
        self.assertAlmostEqual(
            self._avail(6, 7), 10.0, places=2,
            msg="After the return operation the units are available again")

    # ── the S00708 reproduction (pickup side) ────────────────────────────
    def test_pickup_scheduled_before_window_reserves_units(self):
        """Symmetric to S00338, on the PICKUP side (order S00708).

        A competing order's rental only starts on day 5, but its delivery
        picking is (re)scheduled for day 2.  From day 2 the unit physically
        leaves stock, so a booking on days 3-4 — after the pickup operation
        but before the competing order's declared start — must see only 9,
        not 10.
        """
        self._set_stock(10)
        # Competing order: declared start well in the future (day 5-6).
        comp = self._order(start_offset=5, days=1)
        cline = self._line(comp, 1)
        comp.action_confirm()

        # Its outbound (pickup) picking is scheduled EARLIER than the declared
        # reservation start — the delivery leaves stock on day 2.
        pick = comp.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing'
            and p.state not in ('done', 'cancel'))
        pick.ensure_one()
        now = fields.Datetime.now()
        pick.move_ids.write({'date': now + timedelta(days=2)})

        # Sanity: the effective pickup follows the operation, not the declared
        # reservation_begin (which is ~day 5).
        self.assertAlmostEqual(
            (cline._rental_effective_pickup_date()
             - (now + timedelta(days=2))).total_seconds(), 0.0, delta=2,
            msg="Effective pickup must follow the delivery operation date")
        self.assertGreater(
            cline.reservation_begin, now + timedelta(days=4),
            msg="declared reservation_begin must be after the eval window")

        # A booking on days 3-4 — after the pickup operation, before the
        # competing order's declared start — sees only 9 (the unit is gone).
        self.assertAlmostEqual(
            self._avail(3, 4), 9.0, places=2,
            msg="Pickup scheduled before the window commits the unit (S00708)")

        # A booking on days 0.2-0.5 — before the pickup operation — still 10.
        self.assertAlmostEqual(
            self._avail(0.2, 0.5), 10.0, places=2,
            msg="Before the pickup operation nothing is committed → 10")

    def test_return_at_declared_date_frees_units_after(self):
        """Control: when the return is NOT rescheduled, a window after the
        declared return date sees the units back — no over-holding."""
        self._set_stock(10)
        order = self._order(start_offset=0, days=1)
        self._line(order, 6)
        order.action_confirm()
        self._deliver_full(order, 6)

        # Return receipt sits at the declared date (day 1); a booking on
        # days 2-3 is after it → all 10 available.
        self.assertAlmostEqual(
            self._avail(2, 3), 10.0, places=2,
            msg="Return on schedule → units back → 10 available")

    def test_completed_early_return_frees_units_for_overlapping_window(self):
        """S00705 case: a rental returned EARLY (before its declared return
        date) must free its units immediately — a later window that the
        *declared* return date would still overlap must see everything back."""
        self._set_stock(10)
        order = self._order(start_offset=0, days=1)  # declared return = day 1
        line = self._line(order, 6)
        order.action_confirm()
        self._deliver_full(order, 6)
        # Return the 6 NOW (day ~0), well before the declared day-1 return.
        self._return_full(order, 6)
        self.assertAlmostEqual(line.qty_returned, 6.0, places=2)

        # The effective return is the actual operation (~now), not the declared
        # day-1 date.
        now = fields.Datetime.now()
        self.assertLess(
            line._rental_effective_return_date(), now + timedelta(hours=1),
            msg="completed return must ground on the actual operation date")

        # A window on days 0.5-1.5 — which the DECLARED return (day 1) overlaps
        # — must still see all 10, because the units physically came back.
        self.assertAlmostEqual(
            self._avail(0.5, 1.5), 10.0, places=2,
            msg="early-completed return frees units for the overlapping window")

    def _move_done(self, wh, src, dst, qty, sale_line=False):
        """Create and validate a done stock move src→dst (simulates one leg
        of a multi-step flow), optionally linked to a rental line."""
        vals = {
            'product_id': self.prod.id,
            'uom_id': self.prod.uom_id.id,
            'product_uom_qty': qty,
            'location_id': src.id,
            'location_dest_id': dst.id,
        }
        if sale_line:
            vals['sale_line_id'] = sale_line.id
        move = self.env['stock.move'].create(vals)
        move._action_confirm()
        move._action_assign()
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        move._action_done()
        return move

    def test_multistep_delivery_does_not_inflate_availability(self):
        """As a rental's units move through the internal zones on their way
        out (Stock → Packing → Output → Customer), availability for a
        competing booking must stay flat — never increase step by step.  The
        physical total is conserved because every zone up to Output is an
        internal child of the warehouse, and only the final leg reaches the
        customer (counted there via at-customer, not double-counted)."""
        wh = self.env['stock.warehouse'].create({
            'name': 'MS WH', 'code': 'MSW', 'company_id': self.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'pick_pack_ship'})
        self.env['stock.warehouse'].update_rental_rules()
        stock_loc = wh.lot_stock_id
        pack_loc = wh.wh_pack_stock_loc_id
        out_loc = wh.wh_output_stock_loc_id
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.prod.id, 'location_id': stock_loc.id,
            'inventory_quantity': 10,
        }).action_apply_inventory()

        now = fields.Datetime.now()
        order = self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id, 'warehouse_id': wh.id,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=1)})
        line = self.env['sale.order.line'].with_context(
            in_rental_app=True).create({
                'order_id': order.id, 'product_id': self.prod.id,
                'product_uom_qty': 6})
        order.action_confirm()

        def total():
            return self.prod._rental_physical_total(
                warehouse=wh, company=self.company)

        def avail():
            # Availability seen by a *competing* booking over the same window
            # (the order's own 6 stay committed via _get_unavailable_qty).
            return self.prod._rental_available_qty(
                order.rental_start_date, order.rental_return_date,
                warehouse=wh)

        # Baseline: 10 owned, 6 committed → 4 free.
        self.assertAlmostEqual(total(), 10.0, places=2)
        self.assertAlmostEqual(avail(), 4.0, places=2)

        # Leg 1 — Stock → Packing (internal): still 10 owned, still 4 free.
        self._move_done(wh, stock_loc, pack_loc, 6, sale_line=line)
        self.assertAlmostEqual(total(), 10.0, places=2,
                               msg="Stock→Packing must not change the total")
        self.assertAlmostEqual(avail(), 4.0, places=2,
                               msg="Stock→Packing must not inflate availability")

        # Leg 2 — Packing → Output (internal): unchanged.
        self._move_done(wh, pack_loc, out_loc, 6, sale_line=line)
        self.assertAlmostEqual(total(), 10.0, places=2,
                               msg="Packing→Output must not change the total")
        self.assertAlmostEqual(avail(), 4.0, places=2,
                               msg="Packing→Output must not inflate availability")

        # Leg 3 — Output → Customer (reaches the rental location): the unit is
        # now out at the customer but still owned; total and availability are
        # unchanged (conserved), never inflated by the sequence of legs.
        self._move_done(wh, out_loc, self.company.rental_loc_id, 6,
                        sale_line=line)
        self.assertAlmostEqual(total(), 10.0, places=2,
                               msg="Final ship leg must not change the total")
        self.assertAlmostEqual(avail(), 4.0, places=2,
                               msg="Final ship leg must not inflate availability")

    # ── S02100: a PARTIAL return must not release the units still out ────
    def _scrap_from_rental(self, order, line, qty):
        """Write off `qty` units at the customer (lost/broken), as the
        lost/broken wizard does: a done stock.move flagged is_scrap out of the
        rental location."""
        move = self.env['stock.move'].with_context(
            skip_sale_flow_sync=True).create({
                'product_id': self.prod.id,
                'uom_id': self.prod.uom_id.id,
                'product_uom_qty': qty,
                'quantity': qty,
                'is_scrap': True,
                'location_id': self.company.rental_loc_id.id,
                'location_dest_id': self.company.scrap_location_id.id,
                'company_id': self.company.id,
                'sale_line_id': line.id,
            })
        move._action_scrap()
        return move

    def _deliver_short_no_backorder(self, order, qty):
        """Ship `qty` and cancel the backorder — the delivery is then closed
        at less than ordered, and the remainder will never go out."""
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing'
            and p.state not in ('done', 'cancel'))
        picking.ensure_one()
        picking.action_assign()
        move = picking.move_ids.filtered(lambda m: m.product_id == self.prod)
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        res = picking.with_context(skip_lost_broken_check=True).button_validate()
        if isinstance(res, dict) \
                and res.get('res_model') == 'stock.backorder.confirmation':
            self.env['stock.backorder.confirmation'].with_context(
                res['context']).create({}).process_cancel_backorder()
        return picking

    def _return_short_no_backorder(self, order, qty):
        """Receive `qty` back and cancel the backorder — the return is then
        closed with units still at the customer."""
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'incoming'
            and p.state not in ('done', 'cancel'))
        picking.ensure_one()
        picking.action_assign()
        move = picking.move_ids.filtered(lambda m: m.product_id == self.prod)
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        res = picking.with_context(skip_lost_broken_check=True).button_validate()
        if isinstance(res, dict) \
                and res.get('res_model') == 'stock.backorder.confirmation':
            self.env['stock.backorder.confirmation'].with_context(
                res['context']).create({}).process_cancel_backorder()
        return picking

    def _partially_settled_order(self):
        """9 out, 6 returned, 2 written off -> 1 unit still at the customer,
        with the return picking closed and no backorder.  The rental window
        itself is in the FUTURE (days 9-15), which is what made the effective
        window invert."""
        self._set_stock(10)
        order = self._order(start_offset=9, days=6)
        line = self._line(order, 10)
        order.action_confirm()
        self._deliver_short_no_backorder(order, 9)   # ship 9 of 10
        self._return_short_no_backorder(order, 6)   # only 6 come back
        self._scrap_from_rental(order, line, 2)   # 2 written off
        return order, line

    def test_partial_return_does_not_invert_the_effective_window(self):
        """The line is only released on the return operation date when custody
        is actually settled.  With 1 unit still out, releasing on the last
        completed return put the effective return BEFORE the effective pickup,
        so the line reserved outside its rental and nothing during it."""
        order, line = self._partially_settled_order()
        self.assertAlmostEqual(line._rental_custody_outstanding_qty(), 1.0, places=2)
        self.assertLessEqual(
            line._rental_effective_pickup_date(),
            line._rental_effective_return_date(),
            "effective return must never precede effective pickup")

    def test_partial_return_keeps_the_outstanding_unit_reserved(self):
        """The unit the customer still holds is unavailable to everyone else —
        before, during and up to the declared return of the holding order."""
        order, line = self._partially_settled_order()
        for label, f_off, t_off in (("before the rental", 3, 4),
                                    ("inside the rental", 11, 12),
                                    ("late in the rental", 14, 15)):
            self.assertAlmostEqual(
                self._avail(f_off, t_off), 7.0, places=2,
                msg=f"{label}: 8 owned - 1 still at the customer = 7 available")

    def test_unshipped_remainder_is_released_without_touching_the_order(self):
        """Shipping 9 of 10 with no backorder releases the 10th unit to other
        orders, while the order line still says 10 (no renegotiation)."""
        order, line = self._partially_settled_order()
        self.assertAlmostEqual(
            line.product_uom_qty, 10.0, places=2,
            msg="the order must not be rewritten")
        self.assertAlmostEqual(
            line._rental_effective_reserved_qty(), 9.0, places=2,
            msg="only what actually shipped stays committed")

    def test_scrapped_units_are_released_exactly_once(self):
        """Native qty_returned already counts the lost/broken scraps, so the
        engine must not subtract them a second time (which drove the
        commitment below what is really out)."""
        order, line = self._partially_settled_order()
        self.assertAlmostEqual(
            line.qty_returned, 8.0, places=2,
            msg="6 returned + 2 scrapped, per native accounting")
        self.assertAlmostEqual(line._rental_custody_back_qty(), 8.0, places=2)
        # 9 committed - 8 gone = 1 still out; availability = 8 owned - 1.
        self.assertAlmostEqual(self._avail(11, 12), 7.0, places=2)

    def test_partially_returned_order_is_still_returnable(self):
        """The order must keep offering a return while a unit is out."""
        order, line = self._partially_settled_order()
        order.invalidate_recordset(['has_returnable_lines'])
        self.assertTrue(
            order.has_returnable_lines,
            "1 unit still at the customer must keep the order returnable")

    def test_fully_settled_order_releases_on_the_operation_date(self):
        """Regression guard for S00705: when custody IS settled, the line is
        still released on the actual return date, not the declared one."""
        self._set_stock(10)
        order = self._order(start_offset=9, days=6)
        line = self._line(order, 4)
        order.action_confirm()
        self._deliver_full(order, 4)
        self._return_full(order, 4)
        self.assertAlmostEqual(line._rental_custody_outstanding_qty(), 0.0, places=2)
        self.assertLess(line._rental_effective_return_date(), line.return_date,
                        "settled early -> released on the operation date")
        self.assertAlmostEqual(self._avail(11, 12), 10.0, places=2)
