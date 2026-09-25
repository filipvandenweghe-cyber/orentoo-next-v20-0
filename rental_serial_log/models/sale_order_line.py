from odoo import _, api, models
from odoo.exceptions import ValidationError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _rsl_returnable_lot_ids(self):
        """Serials that MAY be returned against this rental line.

        **P3**: the serials picked up on this line, PLUS every serial of the
        same product the client currently holds (on hand at the at-customer
        rental location).  This is the single override point the
        return-validation hooks defer to — the constraint below and the
        picking pre-check — so the rule widened without touching either.

        Why widen (P1 was "picked up on this line" only):

        * a unit the rental company delivered **without** adding it to the
          order has no ``sale.order.line`` at all when
          ``sale_flow_skip_invoice_logistics`` is on, yet it must still come
          back — and be validated when it does;
        * a swap between two orders of the same customer would otherwise be
          rejected even though the serial is genuinely at the client.

        The invariants are unchanged: the serial must already exist (a return
        never creates one) and must genuinely be out at the client.
        """
        self.ensure_one()
        return self.pickedup_lot_ids | self.env['stock.lot']._rsl_lots_at_client(
            self.product_id, self.company_id)

    @api.constrains('returned_lot_ids')
    def _rsl_check_returned_lots(self):
        """A rental return may only record *returnable* serials.

        Flow-agnostic backstop: fires for the picking path (lots linked at
        ``stock.move._action_done``), the rental return wizard, imports and
        manual edits alike.  A brand-new serial can never be returnable, so
        this also blocks "a return created a new serial".
        """
        for line in self:
            if not line.is_rental or line.product_id.tracking != 'serial':
                continue
            if not line.returned_lot_ids:
                continue
            bad = line.returned_lot_ids - line._rsl_returnable_lot_ids()
            if bad:
                raise ValidationError(_(
                    "These serial numbers were not delivered on this rental "
                    "and cannot be returned against it: %(sns)s.",
                    sns=", ".join(bad.mapped('name'))))
