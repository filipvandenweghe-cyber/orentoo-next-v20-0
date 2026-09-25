# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    is_rental_purchase = fields.Boolean(
        string="To be returned",
        copy=True,
        help="This purchase order represents equipment hired from the supplier "
             "rather than bought. Received goods remain owned by the supplier "
             "and must be returned to them at the end of the rental period.",
    )
    rental_start_date = fields.Datetime(
        string="Rental Start Date",
        copy=True,
        help="Planned start of the hired period. Drives the scheduled date of "
             "the incoming receipt.",
    )
    rental_return_date = fields.Datetime(
        string="Rental Return Date",
        copy=True,
        help="Date on which the equipment must leave our warehouse and be "
             "returned to the supplier. Drives the outbound return logistics.",
    )

    rental_return_picking_ids = fields.One2many(
        'stock.picking',
        'rental_purchase_return_order_id',
        string="Rental Returns",
    )
    rental_return_picking_count = fields.Integer(
        compute='_compute_rental_return_picking_count',
    )

    @api.depends('rental_return_picking_ids')
    def _compute_rental_return_picking_count(self):
        for order in self:
            order.rental_return_picking_count = len(order.rental_return_picking_ids)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('is_rental_purchase', 'rental_start_date', 'rental_return_date')
    def _check_rental_dates(self):
        for order in self:
            if not order.is_rental_purchase:
                continue
            if not order.rental_start_date or not order.rental_return_date:
                raise ValidationError(_(
                    "A Rental Purchase requires both a Rental Start Date and a "
                    "Rental Return Date."))
            if order.rental_return_date <= order.rental_start_date:
                raise ValidationError(_(
                    "The Rental Return Date must be later than the Rental Start "
                    "Date."))

    # ------------------------------------------------------------------
    # Scheduling propagation (§2, §15)
    # ------------------------------------------------------------------
    def _rental_purchase_sync_start_date(self):
        """Align the incoming receipt scheduling with the rental start date by
        driving the standard ``purchase.order.line.date_planned`` field."""
        for order in self:
            if not order.is_rental_purchase or not order.rental_start_date:
                continue
            lines = order.order_line.filtered(lambda l: not l.display_type)
            # Writing date_planned triggers the standard propagation to open
            # incoming moves (purchase_stock._update_date_planned).
            lines.filtered(
                lambda l: l.date_planned != order.rental_start_date
            ).date_planned = order.rental_start_date

    def _rental_purchase_sync_return_date(self):
        """Re-schedule still-open return moves to the rental return date."""
        for order in self:
            if not order.is_rental_purchase or not order.rental_return_date:
                continue
            open_moves = self.env['stock.move'].search([
                ('rental_purchase_order_id', '=', order.id),
                ('state', 'not in', ('done', 'cancel')),
            ])
            open_moves.write({
                'date': order.rental_return_date,
                'date_deadline': order.rental_return_date,
            })

    def write(self, vals):
        res = super().write(vals)
        if 'rental_start_date' in vals:
            self._rental_purchase_sync_start_date()
        if 'rental_return_date' in vals:
            self._rental_purchase_sync_return_date()
        return res

    # ------------------------------------------------------------------
    # Confirmation (§3, §4, §5, §6)
    # ------------------------------------------------------------------
    def button_confirm(self):
        # Push the rental start date onto the lines before the standard flow
        # generates the incoming pickings, so the receipt is scheduled for it.
        self._rental_purchase_sync_start_date()
        res = super().button_confirm()
        for order in self:
            if order.is_rental_purchase and order.state in ('purchase', 'done'):
                order._rental_purchase_assign_owner()
                order._rental_purchase_create_returns()
        return res

    def _rental_purchase_assign_owner(self):
        """Flag the incoming receipt as supplier-owned (§4). Standard Odoo then
        stamps the owner on the moves/lines/quants at validation and it
        propagates through internal transfers automatically."""
        self.ensure_one()
        incoming = self.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'incoming'
            and p.state not in ('done', 'cancel')
            and not p.owner_id
        )
        incoming.write({'owner_id': self.partner_id.id})

    # ------------------------------------------------------------------
    # Supplier-return logistics (§5, §6)
    # ------------------------------------------------------------------
    def _rental_purchase_return_reference(self):
        self.ensure_one()
        return self.env['stock.reference'].create({
            'name': _("Rental Return %s", self.name),
        })

    def _rental_purchase_return_hops(self, warehouse, supplier_location):
        """Ordered list of (src, dest, picking_type) tuples describing the
        outbound return chain. Honours the warehouse's configured delivery
        steps; the final destination is always the supplier location (§5)."""
        self.ensure_one()
        stock = warehouse.lot_stock_id
        steps = warehouse.delivery_steps
        if steps == 'pick_ship':
            output = warehouse.wh_output_stock_loc_id
            return [
                (stock, output, warehouse.pick_type_id),
                (output, supplier_location, warehouse.out_type_id),
            ]
        if steps == 'pick_pack_ship':
            pack = warehouse.wh_pack_stock_loc_id
            output = warehouse.wh_output_stock_loc_id
            return [
                (stock, pack, warehouse.pick_type_id),
                (pack, output, warehouse.pack_type_id),
                (output, supplier_location, warehouse.out_type_id),
            ]
        # ship_only (default)
        return [(stock, supplier_location, warehouse.out_type_id)]

    def _rental_purchase_create_returns(self):
        """Prepare the future return-to-supplier stock move chain.

        The first hop is chained (``move_orig_ids`` / make_to_order) onto the
        incoming receipt moves, so reservation can only ever consume the
        supplier-owned stock that was actually received - never unrelated
        company-owned stock (§6, §7). This is the only reservation path in
        Odoo 19 that preserves the stock owner. Later hops chain onto their
        predecessor so a multi-step warehouse builds the correct
        Stock -> Output -> Supplier flow (§5).
        """
        self.ensure_one()
        if self.rental_return_picking_ids:
            return  # idempotent - already created
        supplier_location = self.partner_id.property_stock_supplier
        if not supplier_location:
            raise UserError(_(
                "The vendor %s has no vendor location set (Inventory tab). It "
                "is required to prepare the rental return.",
                self.partner_id.display_name))
        reference = self._rental_purchase_return_reference()
        StockMove = self.env['stock.move']
        incoming_moves = self.picking_ids.filtered(
            lambda p: p.picking_type_id.code == 'incoming'
        ).move_ids.filtered(lambda m: m.state != 'cancel')
        created = StockMove
        for line in self.order_line:
            if line.display_type or line.product_id.type != 'consu':
                continue
            warehouse = line.order_id.picking_type_id.warehouse_id
            if not warehouse:
                continue
            receipt_moves = incoming_moves.filtered(
                lambda m: m.product_id == line.product_id)
            # Prefer the receipt move(s) that land in the main stock location.
            origin_moves = receipt_moves.filtered(
                lambda m: m.location_dest_id == warehouse.lot_stock_id
            ) or receipt_moves
            hops = self._rental_purchase_return_hops(warehouse, supplier_location)
            prev_move = False
            for src, dest, pick_type in hops:
                if prev_move:
                    move_orig = [(4, prev_move.id)]
                    procure_method = 'make_to_order'
                elif origin_moves:
                    move_orig = [(6, 0, origin_moves.ids)]
                    procure_method = 'make_to_order'
                else:
                    move_orig = False
                    procure_method = 'make_to_stock'
                move = StockMove.create({
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.product_qty,
                    'uom_id': line.uom_id.id,
                    'location_id': src.id,
                    'location_dest_id': dest.id,
                    'picking_type_id': pick_type.id,
                    'reference_ids': [(4, reference.id)],
                    'origin': self.name,
                    'company_id': self.company_id.id,
                    'date': self.rental_return_date,
                    'date_deadline': self.rental_return_date,
                    'restrict_partner_id': self.partner_id.id,
                    'rental_purchase_order_id': self.id,
                    'procure_method': procure_method,
                    'move_orig_ids': move_orig,
                })
                created |= move
                prev_move = move
        # Confirm the chain: assigns pickings and puts moves in waiting/confirmed
        # without running any procurement rule (moves are fully specified).
        created._action_confirm(merge=False)

    def _rental_purchase_reconcile_returns(self):
        """Size the supplier-return obligation to what will ULTIMATELY be
        received.

        Mirror of the delivery-side return-demand reconciliation ("until it is
        out, the client is not expected to return it"), applied to the receipt
        leg: *we owe back exactly what comes in*.  The open return demand tracks
        ``expected inbound - already returned`` per product, where expected =
        already received (done) + still-pending incoming (open moves), never the
        raw ordered quantity:

        * receive less with **no back-order** -> the shortfall is written off
          (no open move remains) so the obligation drops to what arrived;
        * a pending **back-order** keeps its units in ``expected`` -> the
          obligation stays full because those units are still coming;
        * over-receipt -> the obligation grows to what actually arrived.

        We do NOT scrap or financially reconcile a shortfall - the equipment is
        supplier-owned, so the supplier settles differences (spec §7).  This is
        purely logistical right-sizing.
        """
        Move = self.env['stock.move']
        for order in self.filtered('is_rental_purchase'):
            warehouse = order.picking_type_id.warehouse_id
            receipt_moves = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'incoming'
            ).move_ids.filtered(
                lambda m: m.location_id.usage == 'supplier'
                and m.state != 'cancel')
            products = order.order_line.filtered(
                lambda l: not l.display_type).product_id
            for product in products:
                prod_receipts = receipt_moves.filtered(
                    lambda m: m.product_id == product)
                received = sum(prod_receipts.filtered(
                    lambda m: m.state == 'done').mapped('quantity'))
                pending = sum(prod_receipts.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                ).mapped('product_uom_qty'))
                returned = sum(Move.search([
                    ('rental_purchase_order_id', '=', order.id),
                    ('product_id', '=', product.id),
                    ('location_dest_id.usage', '=', 'supplier'),
                    ('state', '=', 'done'),
                ]).mapped('quantity'))
                target = max(received + pending - returned, 0.0)
                open_returns = Move.search([
                    ('rental_purchase_order_id', '=', order.id),
                    ('product_id', '=', product.id),
                    ('state', 'not in', ('done', 'cancel')),
                ])
                if not open_returns:
                    continue
                # Keep the chain root chained to ALL current receipt moves so a
                # back-order's later units can still feed the return through the
                # owner-preserving MTO chain.
                if warehouse and prod_receipts:
                    root = open_returns.filtered(
                        lambda m: m.location_id == warehouse.lot_stock_id)
                    if root:
                        root.move_orig_ids = [(6, 0, prod_receipts.ids)]
                if open_returns.mapped('product_uom_qty') != [target] * len(open_returns):
                    open_returns.product_uom_qty = target
                open_returns._action_assign()

    # ------------------------------------------------------------------
    # Cancellation (§21) - user chose: block when goods already received
    # ------------------------------------------------------------------
    def button_cancel(self):
        for order in self.filtered('is_rental_purchase'):
            received = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'incoming'
                and p.state == 'done')
            if received:
                raise UserError(_(
                    "Rental goods for '%s' have already been received and are "
                    "physically present, still owned by the supplier. Process "
                    "the rental return to the supplier before cancelling this "
                    "order so the return obligation is never lost.",
                    order.name))
        res = super().button_cancel()
        # Nothing received yet: cancel the prepared future return chain too.
        for order in self.filtered('is_rental_purchase'):
            order.rental_return_picking_ids.filtered(
                lambda p: p.state not in ('done', 'cancel')
            ).action_cancel()
        return res

    # ------------------------------------------------------------------
    # Navigation (§24)
    # ------------------------------------------------------------------
    def action_view_rental_returns(self):
        self.ensure_one()
        pickings = self.rental_return_picking_ids
        action = self.env['ir.actions.actions']._for_xml_id(
            'stock.action_picking_tree_all')
        if len(pickings) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = pickings.id
        else:
            action['domain'] = [('id', 'in', pickings.ids)]
        action['context'] = {'create': False}
        return action
