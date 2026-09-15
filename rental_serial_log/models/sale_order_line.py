from odoo import _, api, models
from odoo.exceptions import ValidationError


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _rsl_returnable_lot_ids(self):
        """Serials that MAY be returned against this rental line.

        P1: the serials actually **picked up** on this line.  This is the
        single override point for later phases (P3 will widen it to, e.g.,
        every serial currently at the client) — the return-validation hooks
        (the constraint below and the picking pre-check) both defer to it, so
        broadening the rule never touches the hooks.
        """
        self.ensure_one()
        return self.pickedup_lot_ids

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
