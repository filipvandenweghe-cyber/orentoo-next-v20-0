from odoo import api, fields, models, _
from odoo.tools import float_compare


class SaleFlowReturnService(models.AbstractModel):
    """Service for handling return-related flow logic.

    Implements: R07, R09.

    Business rules:
      * Expected rental return qty = delivered_qty - returned_qty
        - lost_qty - broken_qty.  (R07)
      * When validating a return with no backorder, missing quantities
        become lost and the lost/broken wizard is opened.  (R09)
      * Sale product returns reduce invoiceable quantity (pre-invoicing).
    """

    _name = 'sale.flow.return.service'
    _description = 'Sale Flow Return Service'

    def _check_missing_returns(self, picking):
        """Check for missing rental returns after return picking validation.

        The wizard only opens when **nothing more is coming back** — i.e. the
        return has no open back-order left.  Two situations reach that state:

          * the return was validated WITHOUT a back-order;
          * a return back-order was later CANCELLED (R22).

        While an open back-order exists the missing units are simply on their
        way — the customer may still bring them — so the wizard stays out of
        the way and the units keep their return demand.

        Once it does open, the classification is **closing**: every missing
        unit must be assigned to one of the three buckets (see the wizard),
        so nothing is left silently "expected back" from a customer that no
        operation will ever collect from.
        """
        if not picking.return_id:
            return
        if self._has_open_return_backorder(picking):
            return

        order = picking.sale_id
        if not order:
            return

        missing_lines = []
        prec = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        for flow_line in order.flow_line_ids:
            if not flow_line.is_rental:
                continue
            if flow_line.state in ('cancelled', 'invoiced'):
                continue

            expected = self._get_expected_return_qty(flow_line)
            if float_compare(expected, 0, precision_digits=prec) > 0:
                missing_lines.append({
                    'flow_line': flow_line,
                    'missing_qty': expected,
                })

        if missing_lines:
            return self._open_lost_broken_wizard(picking, missing_lines)

    def _has_open_return_backorder(self, picking):
        """True while another return picking is still due to bring units back.

        Covers the back-order chain of this return (and of its siblings), so a
        partially received return that left a back-order open does not trigger
        the closing wizard.
        """
        return bool(self.env['stock.picking'].search_count([
            ('id', '!=', picking.id),
            ('return_id', '=', picking.return_id.id),
            ('state', 'not in', ('done', 'cancel')),
        ], limit=1))

    def _get_expected_return_qty(self, flow_line):
        """Compute expected return quantity for a rental flow line.

        Formula: delivered_qty - returned_qty - lost_qty - broken_qty
        """
        return max(
            flow_line.delivered_qty
            - flow_line.returned_qty
            - flow_line.lost_qty
            - flow_line.broken_qty,
            0,
        )

    def _open_lost_broken_wizard(self, picking, missing_lines):
        """Open the lost/broken wizard pre-populated with missing quantities."""
        wizard = self.env['sale.flow.lost.broken.wizard'].create({
            'picking_id': picking.id,
            'sale_order_id': picking.sale_id.id,
        })

        for item in missing_lines:
            fl = item['flow_line']
            price = fl.product_id.sales_price_broken_lost or 0.0
            self.env['sale.flow.lost.broken.wizard.line'].create({
                'wizard_id': wizard.id,
                'flow_line_id': fl.id,
                'product_id': fl.product_id.id,
                'delivered_qty': fl.delivered_qty,
                'returned_qty': fl.returned_qty,
                'missing_qty': item['missing_qty'],
                # All buckets default to 0 — the user classifies what is
                # fully broken / lost (charged) / lost (not charged).
                'broken_lost_unit_price': price,
            })

        # Provide an explicit ``views`` (not just ``view_mode``): the Barcode
        # app validates via its own ``doAction`` path, whose ``_preprocessAction``
        # maps over ``action.views`` and crashes on a bare ``view_mode`` action.
        # An explicit form view makes the wizard open in both the backend and
        # the Barcode client.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Missing Rental Items'),
            'res_model': 'sale.flow.lost.broken.wizard',
            'res_id': wizard.id,
            'views': [
                (self.env.ref(
                    'sale_flow.sale_flow_lost_broken_wizard_view_form').id,
                 'form')],
            'view_mode': 'form',
            'target': 'new',
        }

    def _process_sale_return(self, flow_line, return_qty):
        """Process a sale product return (pre-invoicing).

        Business rule: sale products may be returned before invoicing.
        The return reduces the invoiceable quantity.
        """
        prec = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        if float_compare(return_qty, 0, precision_digits=prec) <= 0:
            return

        new_returned = flow_line.returned_qty + return_qty
        flow_line.with_context(skip_sale_flow_sync=True).write({
            'returned_qty': new_returned,
            'was_changed_after_confirmation': True,
            'change_origin': 'return',
            'change_note': _(
                'Sale return: %(qty)s units returned',
                qty=return_qty,
            ),
            'last_flow_update_at': fields.Datetime.now(),
            'last_flow_update_by_id': self.env.uid,
        })
        flow_line._compute_warning_level()
        flow_line._update_state()
