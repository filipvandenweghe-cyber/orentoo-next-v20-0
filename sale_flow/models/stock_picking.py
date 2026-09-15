"""Stock picking extensions for sale_flow.

Implements: R09, R19, R22.
See sale_flow_line.py module docstring for the full requirements index.
"""

from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_cancel(self):
        """After cancelling a picking, reconcile return demands and — when a
        rental *return* is cancelled — open the lost/broken wizard.

        When a backorder is cancelled, the pending outgoing demand is
        gone.  The return picking must be adjusted to only expect back
        what was actually delivered (not the cancelled backorder qty).
        (R22)

        Cancelling a *return* picking means the delivered items will not come
        back, so they must be classified as lost/broken for invoicing — the
        same wizard shown on validation (R09).
        """
        # Capture sale orders before cancel (state changes after)
        orders = self.filtered(
            lambda p: p.sale_id and not p.return_id
        ).mapped('sale_id')
        # Return pickings being cancelled → their expected items won't return.
        return_pickings = self.filtered(lambda p: p.return_id and p.sale_id)

        res = super().action_cancel()

        if self.env.context.get('skip_sale_flow_sync'):
            return res

        for order in orders:
            self.env['sale.flow.sync.service']._reconcile_return_pickings(order)

        if not self.env.context.get('skip_lost_broken_check'):
            for picking in return_pickings:
                wizard_action = self.env[
                    'sale.flow.return.service']._check_missing_returns(picking)
                if wizard_action:
                    return wizard_action

        return res

    def button_validate(self):
        """After picking validation, handle post-logistics actions.

        1. Check for missing rental returns (no-backorder scenario).  (R09)
           If items are missing, open the lost/broken wizard so the user
           can classify them as lost or broken.
        2. Auto-reconcile ordered qty for flagged products when all
           logistics are complete.  (R19)

        IMPORTANT: super().button_validate() may return a wizard action
        (e.g. backorder wizard) before the picking is actually validated.
        We must only run our post-validation logic when the picking has
        reached 'done' state.  Otherwise returned_qty on the flow line
        is still 0, and the lost/broken wizard would show wrong values.
        """
        res = super().button_validate()

        if self.env.context.get('skip_sale_flow_sync'):
            return res

        for picking in self:
            # Only run post-validation logic if the picking is actually done.
            # If super() returned a wizard (backorder, immediate transfer,
            # etc.), the picking is NOT yet done — do not interfere.
            if picking.state != 'done':
                continue

            # Check for missing rental returns (no-backorder scenario).
            if picking.return_id and not self.env.context.get('skip_lost_broken_check'):
                wizard_action = self.env['sale.flow.return.service']._check_missing_returns(picking)
                if wizard_action:
                    return wizard_action

            # Auto-reconcile ordered qty when all logistics are complete.
            if picking.sale_id:
                picking.sale_id._auto_reconcile_delivered_qty()

        return res
