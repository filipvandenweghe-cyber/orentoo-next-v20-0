from odoo import models


class RepairOrder(models.Model):
    _inherit = 'repair.order'

    def write(self, vals):
        res = super().write(vals)
        if 'state' not in vals or self.env.context.get('skip_rental_serial_log'):
            return res
        state = vals['state']
        # Odoo 20 removed the 'under_repair' state (draft -> confirmed ->
        # done/cancel), so 'confirmed' is now the point the repair starts.
        if state not in ('confirmed', 'done'):
            return res
        Log = self.env['rental.serial.log']
        event = 'repair_start' if state == 'confirmed' else 'repair_done'
        for repair in self:
            lot = repair.lot_id
            if not lot or repair.product_id.tracking != 'serial':
                continue
            note = False
            if state == 'done' and repair.recycle_location_id:
                # A recycle destination hints the unit was scrapped/recycled
                # rather than returned to usable stock.
                note = 'Recycled/scrapped'
            Log._rsl_log({
                'lot_id': lot.id,
                'event_type': event,
                'repair_order_id': repair.id,
                'note': note,
            })
        return res
