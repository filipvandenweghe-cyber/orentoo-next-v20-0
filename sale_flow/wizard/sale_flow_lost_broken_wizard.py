from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class SaleFlowLostBrokenWizard(models.TransientModel):
    """Wizard for classifying missing rental items.

    Opened when a return picking is validated and some delivered rental
    quantities are still unaccounted for.  Every classified unit is
    **scrapped** from the rental (at-customer) location; the only difference
    between the buckets is whether the customer is charged:

      * Fully Broken (charged)   -> fee line + scrap
      * Lost (charged)           -> fee line + scrap
      * Lost (not charged)       -> scrap only

    Anything left unclassified stays on the backorder for a later return.

    Repairable-broken items are handled by the standard Odoo repair flow
    (they keep their quantity) and are intentionally NOT part of this wizard.
    """

    _name = 'sale.flow.lost.broken.wizard'
    _description = 'Lost/Broken Items Wizard'

    picking_id = fields.Many2one(
        'stock.picking',
        string='Return Picking',
        readonly=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        readonly=True,
    )
    line_ids = fields.One2many(
        'sale.flow.lost.broken.wizard.line',
        'wizard_id',
        string='Lines',
    )

    def action_confirm(self):
        """Validate the classification, then scrap and charge accordingly.

        The classification is **closing**: the wizard only opens when nothing
        more is coming back (no open return back-order), so every missing unit
        must be assigned to one of the three buckets.  A partial
        classification used to be accepted and the remainder was left silently
        "expected back" — with no operation that would ever collect it, that
        unit stayed reserved against the warehouse for ever.
        """
        self.ensure_one()

        prec = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        unallocated = []
        for wiz_line in self.line_ids:
            total = wiz_line._classified_qty()
            cmp_missing = float_compare(
                total, wiz_line.missing_qty, precision_digits=prec)
            if cmp_missing > 0:
                raise UserError(_(
                    'Classified quantity (%(total)s) cannot exceed the missing '
                    'quantity (%(missing)s) for product %(product)s.',
                    total=total,
                    missing=wiz_line.missing_qty,
                    product=wiz_line.product_id.display_name,
                ))
            if cmp_missing < 0:
                unallocated.append(wiz_line)

        if unallocated:
            raise UserError(_(
                'Every missing unit must be accounted for — nothing is coming '
                'back on this return.\n\n%(details)s\n\n'
                'Split each remainder over "Fully Broken (charged)", '
                '"Lost (charged)" or "Lost (not charged)". Use '
                '"Lost (not charged)" when the unit is written off without '
                'billing the customer.',
                details='\n'.join(
                    ' • %s: %g of %g still unassigned' % (
                        wl.product_id.display_name,
                        wl.missing_qty - wl._classified_qty(),
                        wl.missing_qty,
                    )
                    for wl in unallocated
                ),
            ))

        has_classified = any(
            float_compare(wl._classified_qty(), 0, precision_digits=prec) > 0
            for wl in self.line_ids
        )

        if has_classified:
            self.env['sale.flow.lost.broken.service']._process_lost_broken(self)
            self._reduce_backorder_demand()

        return self._return_to_picking()

    def action_cancel(self):
        """Close the wizard and go back to the return picking.

        The picking has already been validated by this point (the wizard is
        shown *after* validation), so cancelling only skips the loss
        classification — the user is returned to the (done) warehouse
        operation rather than left on an empty screen.
        """
        self.ensure_one()
        return self._return_to_picking()

    def _return_to_picking(self):
        """Navigate back to the return picking form.

        When this wizard is reached through the backorder confirmation
        (which re-calls ``button_validate``), closing it does not reload the
        picking on its own, so we return an explicit action to it.
        """
        self.ensure_one()
        if not self.picking_id:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'main',
        }

    def _reduce_backorder_demand(self):
        """Reduce backorder moves for items classified as lost or broken.

        Classified items are gone (scrapped), so they should no longer be
        expected on any open backorder for this order.  Fully-consumed moves
        are cancelled.
        """
        if not self.picking_id or not self.sale_order_id:
            return

        prec = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')

        backorder_pickings = self.sale_order_id.picking_ids.filtered(
            lambda p: (
                p.return_id
                and p.state not in ('done', 'cancel')
                and p.id != self.picking_id.id
            )
        )
        if not backorder_pickings:
            return

        for wiz_line in self.line_ids:
            remaining_to_reduce = wiz_line._classified_qty()
            if float_compare(remaining_to_reduce, 0,
                             precision_digits=prec) <= 0:
                continue

            for picking in backorder_pickings:
                if float_compare(remaining_to_reduce, 0,
                                 precision_digits=prec) <= 0:
                    break

                for move in picking.move_ids.filtered(
                    lambda m: (
                        m.product_id == wiz_line.product_id
                        and m.state not in ('done', 'cancel')
                    )
                ):
                    current_demand = move.product_uom_qty
                    reduce_by = min(remaining_to_reduce, current_demand)

                    new_demand = current_demand - reduce_by
                    if float_compare(new_demand, 0,
                                     precision_digits=prec) <= 0:
                        move.with_context(
                            skip_sale_flow_sync=True)._action_cancel()
                    else:
                        move.with_context(skip_sale_flow_sync=True).write({
                            'product_uom_qty': new_demand,
                        })

                    remaining_to_reduce -= reduce_by


class SaleFlowLostBrokenWizardLine(models.TransientModel):
    _name = 'sale.flow.lost.broken.wizard.line'
    _description = 'Lost/Broken Wizard Line'

    wizard_id = fields.Many2one(
        'sale.flow.lost.broken.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    flow_line_id = fields.Many2one(
        'sale.flow.line',
        string='Flow Line',
        required=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        readonly=True,
    )
    delivered_qty = fields.Float(
        string='Delivered',
        digits='Product Unit of Measure',
        readonly=True,
    )
    returned_qty = fields.Float(
        string='Returned',
        digits='Product Unit of Measure',
        readonly=True,
    )
    missing_qty = fields.Float(
        string='Missing',
        digits='Product Unit of Measure',
        readonly=True,
    )
    fully_broken_qty = fields.Float(
        string='Fully Broken (charged)',
        digits='Product Unit of Measure',
        help='Returned damaged beyond repair — charged to the customer and '
             'scrapped.',
    )
    lost_charged_qty = fields.Float(
        string='Lost (charged)',
        digits='Product Unit of Measure',
        help='Not returned — charged to the customer and scrapped.',
    )
    lost_uncharged_qty = fields.Float(
        string='Lost (not charged)',
        digits='Product Unit of Measure',
        help='Not returned — scrapped without charging the customer.',
    )
    still_expected_qty = fields.Float(
        string='Still Expected',
        digits='Product Unit of Measure',
        compute='_compute_still_expected',
        help='Remaining missing quantity, left on the backorder for a later '
             'return.',
    )
    broken_lost_unit_price = fields.Float(
        string='Fee / Unit',
        digits='Product Price',
        help='Price charged per unit for the charged buckets.',
    )

    def _classified_qty(self):
        self.ensure_one()
        return (self.fully_broken_qty or 0.0) \
            + (self.lost_charged_qty or 0.0) \
            + (self.lost_uncharged_qty or 0.0)

    @api.depends('missing_qty', 'fully_broken_qty', 'lost_charged_qty',
                 'lost_uncharged_qty')
    def _compute_still_expected(self):
        for line in self:
            line.still_expected_qty = max(
                line.missing_qty - line._classified_qty(), 0.0)

    @api.onchange('fully_broken_qty', 'lost_charged_qty', 'lost_uncharged_qty')
    def _onchange_quantities(self):
        for line in self:
            if line._classified_qty() > line.missing_qty:
                return {
                    'warning': {
                        'title': _('Quantity Warning'),
                        'message': _(
                            'Classified quantity (%(total)s) cannot exceed the '
                            'missing quantity (%(missing)s).',
                            total=line._classified_qty(),
                            missing=line.missing_qty,
                        ),
                    }
                }
