from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_move_lines_to_report(self):
        """RS-22: the invoice PDF follows the ORIGINATING ORDER's decision.

        Not ``visible_to_customer`` alone: whether a set's contents are shown
        is decided per order (RS-02) under a company flag (RS-01), and the
        invoice must show exactly what the offer showed.
        """
        lines = super()._get_move_lines_to_report()
        return lines.filtered(lambda l: l._rs_shown_to_customer())


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _rs_shown_to_customer(self):
        """Whether this invoice line appears on the customer invoice.

        RS-23: a line with no ``sale_line_ids`` (manual line, credit-note
        adjustment) is never treated as a rental set component.
        """
        self.ensure_one()
        return not any(
            sl.is_set_component
            and not sl.order_id._rental_set_shows_line(sl)
            for sl in self.sale_line_ids
        )

    def _rs_is_set_component(self):
        """True when this invoice line came from a rental set component line.

        Drives the report presentation (RS-11…RS-13): indented, and with no
        price, discount, tax or subtotal shown.
        """
        self.ensure_one()
        return any(self.sale_line_ids.mapped('is_set_component'))

    def _rs_set_level(self):
        """Nesting depth of the set component behind this invoice line.

        0 when it is not a component.  Capped at 4 by the report (RS-11) so a
        deep set cannot eat the description column.
        """
        self.ensure_one()
        levels = self.sale_line_ids.filtered('is_set_component').mapped('set_level')
        return max(levels) if levels else 0
