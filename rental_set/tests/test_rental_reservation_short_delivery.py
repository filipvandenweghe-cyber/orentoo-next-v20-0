from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalShortDeliveryRelease(TransactionCase):
    """A rental whose delivery is closed short (fewer units picked than
    ordered, no backorder) must release the un-shipped remainder to other
    orders on the same warehouse.

    Reproduces the S00890 / S00891 case: S00890 ordered 4 Half Coupler
    Clamps but its delivery was completed at 2 with no backorder; a second
    order for 2 must then still see 2 available (not red).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Short Client'})
        # A dedicated single-step warehouse for a clean one-move delivery.
        cls.wh = cls.env['stock.warehouse'].create({
            'name': 'Short WH', 'code': 'SHW', 'company_id': cls.company.id,
            'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        # Enable Rental Transfers (real pickup/return pickings) AFTER the
        # warehouse exists so it gets the rental rules.
        cls.env['res.config.settings'].create(
            {'group_rental_stock_picking': True}).set_values()
        cls.env['stock.warehouse'].update_rental_rules()
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])

        cls.prod = cls.env['product.product'].create({
            'name': 'Short Widget', 'type': 'consu', 'is_storable': True,
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

    def _deliver_short_no_backorder(self, order, qty):
        """Validate the outbound rental picking at ``qty`` and cancel the
        backorder — leaving the delivery closed at less than ordered."""
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing'
            and p.state not in ('done', 'cancel'))
        picking.ensure_one()
        picking.action_assign()
        move = picking.move_ids.filtered(lambda m: m.product_id == self.prod)
        move.move_line_ids[:1].quantity = qty
        move.picked = True
        res = picking.button_validate()
        if isinstance(res, dict) \
                and res.get('res_model') == 'stock.backorder.confirmation':
            self.env['stock.backorder.confirmation'].with_context(
                res['context']).create({}).process_cancel_backorder()
        return picking

    # ── the reproduction ─────────────────────────────────────────────────
    def test_short_closed_delivery_releases_remainder(self):
        self._set_stock(4)

        # Order A rents 4 but only 2 are delivered, no backorder.
        order_a = self._order()
        line_a = self._line(order_a, 4)
        order_a.action_confirm()
        self._deliver_short_no_backorder(order_a, 2)

        self.assertEqual(line_a.product_uom_qty, 4.0)
        self.assertAlmostEqual(line_a.qty_delivered, 2.0, places=2)
        # Only 2 are actually committed now (the other 2 will never ship).
        self.assertAlmostEqual(
            line_a._rental_effective_reserved_qty(), 2.0, places=2,
            msg="Closed-short delivery must commit only the delivered qty")

        # Order B rents 2 over an overlapping window.
        order_b = self._order()
        line_b = self._line(order_b, 2)

        reserved = self.prod._get_unavailable_qty(
            order_b.rental_start_date, order_b.rental_return_date,
            ignored_soline_id=line_b.id, warehouse_id=self.wh.id)
        self.assertAlmostEqual(
            reserved, 2.0, places=2,
            msg="Order A must reserve only its delivered 2, not the ordered 4")

        avail = self.prod._rental_available_qty(
            order_b.rental_start_date, order_b.rental_return_date,
            warehouse=self.wh, ignored_soline_id=line_b.id)
        self.assertAlmostEqual(
            avail, 2.0, places=2,
            msg="2 units must remain available (order B not red)")

    def test_delivery_in_progress_keeps_full_commitment(self):
        """While the delivery is still open (nothing shipped yet), the full
        ordered quantity stays reserved — no premature release."""
        self._set_stock(4)
        order_a = self._order()
        line_a = self._line(order_a, 4)
        order_a.action_confirm()  # picking created, not yet validated

        self.assertAlmostEqual(
            line_a._rental_effective_reserved_qty(), 4.0, places=2,
            msg="Open delivery must keep the full ordered quantity committed")

        order_b = self._order()
        line_b = self._line(order_b, 2)
        reserved = self.prod._get_unavailable_qty(
            order_b.rental_start_date, order_b.rental_return_date,
            ignored_soline_id=line_b.id, warehouse_id=self.wh.id)
        self.assertAlmostEqual(reserved, 4.0, places=2)

    def test_scrap_releases_total_and_reservation(self):
        """A lost unit scrapped from the rental location drops BOTH the
        physical total and the reservation together (as the lost/broken
        wizard does), so nothing is left half-counted."""
        self._set_stock(4)
        order = self._order()
        line = self._line(order, 2)
        order.action_confirm()
        self._deliver_short_no_backorder(order, 2)  # full delivery of 2

        # 2 out at customer, 2 in stock → committed 2, total 4.
        self.assertAlmostEqual(
            line._rental_effective_reserved_qty(), 2.0, places=2)
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=self.wh, company=self.company), 4.0, places=2)

        # Scrap 1 from the rental location, linked to the rental line
        # (exactly what the wizard's _scrap_from_rental does).
        # Odoo 20: a scrap is a stock.move flagged is_scrap, processed
        # with _action_scrap() (the stock.scrap model is gone).
        scrap = self.env['stock.move'].create({
            'product_id': self.prod.id,
            'uom_id': self.prod.uom_id.id,
            'product_uom_qty': 1,
            'quantity': 1,
            'is_scrap': True,
            'location_id': self.company.rental_loc_id.id,
            'location_dest_id': self.company.scrap_location_id.id,
            'company_id': self.company.id,
            'sale_line_id': line.id,
        })
        scrap._action_scrap()

        # Total −1 (written off) AND reservation −1 (no longer out): both move.
        #
        # The commitment base stays "what actually went out" (2) — the
        # written-off unit is released through the custody accounting, because
        # native qty_returned already counts a scrap out of the rental
        # location.  Subtracting it from the base as well would release it
        # twice, so assert the number other orders actually see.
        self.assertAlmostEqual(
            line._rental_custody_outstanding_qty(), 1.0, places=2,
            msg="1 of the 2 delivered units is written off")
        # Ask about a point INSIDE the rental (an hour in), not the line's own
        # start_date.  ``_get_unavailable_qty`` returns the PEAK commitment
        # over the window, and the write-off enters the step function at
        # ``now`` (native's early-return adjustment on qty_returned) — a past
        # peak can never be lowered by a release happening later.  Querying
        # from start_date therefore still sees the 2 units that WERE out
        # during the sub-second slice between the order's start and the
        # write-off, so the result depended on whether create → deliver →
        # scrap happened to land inside a single wall-clock second (Odoo
        # datetimes are truncated to the second).  Starting the window past
        # ``now`` puts the release before ``from_date`` unconditionally.
        self.assertAlmostEqual(
            self.prod._get_unavailable_qty(
                line.start_date + timedelta(hours=1), line.return_date,
                warehouse_id=self.wh.id),
            1.0, places=2,
            msg="Scrapped unit must be released from the commitment")
        self.assertAlmostEqual(
            self.prod._rental_physical_total(
                warehouse=self.wh, company=self.company), 3.0, places=2,
            msg="Scrapped unit must be written off the total")

    def test_scrap_flips_status_to_returned(self):
        """An order whose outstanding units are all written off (lost/broken
        scrap) is fully returned — the status must leave 'Picked-up'."""
        self._set_stock(5)
        order = self._order()
        line = self._line(order, 5)
        order.action_confirm()
        self._deliver_short_no_backorder(order, 5)  # full delivery: 5 out

        # Nothing returned yet → still awaiting return.
        self.assertEqual(order.rental_status, 'return')

        # Write off all 5 as lost/broken (scrap from the rental location,
        # linked to the line) — exactly what the wizard does.
        # Odoo 20: a scrap is a stock.move flagged is_scrap, processed
        # with _action_scrap() (the stock.scrap model is gone).
        scrap = self.env['stock.move'].create({
            'product_id': self.prod.id,
            'uom_id': self.prod.uom_id.id,
            'product_uom_qty': 5,
            'quantity': 5,
            'is_scrap': True,
            'location_id': self.company.rental_loc_id.id,
            'location_dest_id': self.company.scrap_location_id.id,
            'company_id': self.company.id,
            'sale_line_id': line.id,
        })
        scrap._action_scrap()

        self.assertAlmostEqual(line._rental_scrapped_qty(), 5.0, places=2)
        self.assertFalse(
            order.has_returnable_lines,
            "scrapped units are no longer expected back")
        self.assertEqual(
            order.rental_status, 'returned',
            "all-written-off rental must be Returned, not stuck on Picked-up")
