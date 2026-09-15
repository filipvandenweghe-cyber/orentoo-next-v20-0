from odoo import _, api, models
from odoo.exceptions import ValidationError


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.constrains('quantity', 'lot_id', 'location_id')
    def _check_serial_single_on_hand(self):
        """Hard-block a unique serial being on-hand in more than one place.

        Standard Odoo only *warns* when a serial number is counted into a
        second location/company (e.g. via Inventory Adjustments), and its hard
        ``check_quantity`` check groups per location tree, so the same serial
        can end up on-hand in two warehouses or two companies at once.

        A serial-tracked lot represents exactly one physical unit, so we
        enforce that its total positive on-hand across all company-owned
        (internal / transit) locations — **instance-wide, every company** — is
        at most one, and lives in a single location.  Customer locations are
        deliberately excluded so the standard "delivery validated before its
        receipt" transient stays allowed.

        The check reads with ``sudo`` (cross-company) but only reports location
        names the write already touches, so it never leaks unrelated data.
        """
        Quant = self.env['stock.quant'].sudo()
        checked = set()
        for quant in self:
            lot = quant.lot_id
            if not lot or quant.product_id.tracking != 'serial':
                continue
            if lot.id in checked:
                continue
            checked.add(lot.id)
            groups = Quant._read_group(
                [('lot_id', '=', lot.id),
                 ('location_id.usage', 'in', ('internal', 'transit')),
                 ('quantity', '>', 0)],
                ['location_id'],
                ['quantity:sum'])
            total = sum(qty for _loc, qty in groups)
            if quant.product_id.uom_id.compare(total, 1) <= 0:
                continue
            locations = self.env['stock.location'].browse(
                [loc.id for loc, _qty in groups])
            raise ValidationError(_(
                "Serial number %(sn)s is a unique serial and cannot be on "
                "hand in more than one place at the same time.\n"
                "It is currently on hand in: %(locs)s.\n"
                "Correct the quantities so the serial exists in a single "
                "location.",
                sn=lot.name,
                locs=", ".join(locations.mapped('display_name'))))
