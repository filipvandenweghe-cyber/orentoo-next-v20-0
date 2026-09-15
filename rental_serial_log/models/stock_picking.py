from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        # Enforce the rental-return serial rule BEFORE validation, so no new
        # lot is ever created and only returnable serials are accepted.  The
        # sale.order.line constraint remains the flow-agnostic backstop.
        self._rsl_check_return_serials()
        res = super().button_validate()
        if self.env.context.get('skip_rental_serial_log'):
            return res
        for picking in self:
            # Only after the picking is actually done (super may return a
            # backorder/immediate-transfer wizard before that).
            if picking.state == 'done':
                picking._rental_serial_log_record()
        return res

    # ── rental-return serial validation (H2) ─────────────────────────────────

    def _rsl_is_rental_return(self):
        """True for the customer-facing return leg (out of the at-customer
        rental location) of a rental order."""
        self.ensure_one()
        order = self.sale_id
        if not order or not getattr(order, 'is_rental_order', False):
            return False
        rloc = self.company_id.rental_loc_id
        if not rloc:
            return False
        return any(
            m.location_id == rloc for m in self.move_ids
            if m.sale_line_id and m.sale_line_id.is_rental)

    def _rsl_resolve_serial(self, name):
        """Resolve an existing serial by its normalized name (P1 guarantees
        serial names are unique instance-wide)."""
        name = (name or '').strip()
        if not name:
            return self.env['stock.lot']
        return self.env['stock.lot'].search(
            [('serial_unique_key', '=', name)], limit=1)

    def _rsl_check_return_serials(self):
        """Reject a rental return that would create a new serial or return a
        serial that was not delivered on the order (``_rsl_returnable_lot_ids``)."""
        if self.env.context.get('skip_rental_serial_log'):
            return
        for picking in self:
            if not picking._rsl_is_rental_return():
                continue
            rloc = picking.company_id.rental_loc_id
            not_existing, not_returnable = [], []
            for line in picking.move_line_ids:
                sol = line.move_id.sale_line_id
                if not sol or not sol.is_rental:
                    continue
                if line.product_id.tracking != 'serial' or line.quantity <= 0:
                    continue
                # Only the leg leaving the at-customer rental location.
                if line.location_id != rloc:
                    continue
                lot = line.lot_id
                if not lot:
                    name = (line.lot_name or '').strip()
                    if not name:
                        continue
                    lot = picking._rsl_resolve_serial(name)
                    if not lot:
                        not_existing.append(name)  # would create → forbidden
                        continue
                if lot not in sol._rsl_returnable_lot_ids():
                    not_returnable.append(lot.name)
            if not_existing or not_returnable:
                parts = []
                if not_existing:
                    parts.append(_(
                        "Unknown serial number(s) — a return may not create a "
                        "new serial: %(sns)s", sns=", ".join(not_existing)))
                if not_returnable:
                    parts.append(_(
                        "Serial number(s) not delivered on this rental: "
                        "%(sns)s", sns=", ".join(not_returnable)))
                raise UserError("\n".join(parts))

    # ── rental serial logging (delivered / returned) ─────────────────────────

    def _rental_serial_log_record(self):
        """Log serial delivered/returned events for a rental transfer.

        Delivered is logged only on the customer-facing OUTGOING delivery
        (not on internal Pick/Pack), with the package the serial was in and a
        contents snapshot (Option A).  Returned is logged on the return
        receipt (return_id set).
        """
        self.ensure_one()
        order = self.sale_id
        if not order or not getattr(order, 'is_rental_order', False):
            return
        is_return = bool(self.return_id)
        is_delivery = self.picking_type_code == 'outgoing' and not is_return
        if not (is_return or is_delivery):
            return

        Log = self.env['rental.serial.log']
        for line in self.move_line_ids:
            lot = line.lot_id
            if not lot or line.product_id.tracking != 'serial':
                continue
            if line.quantity <= 0:
                continue
            if is_return:
                Log._rsl_log({
                    'lot_id': lot.id,
                    'event_type': 'returned',
                    'sale_order_id': order.id,
                    'partner_id': order.partner_id.id,
                    'picking_id': self.id,
                })
            else:  # delivery
                pkg = line.package_id or line.result_package_id
                Log._rsl_log({
                    'lot_id': lot.id,
                    'event_type': 'delivered',
                    'sale_order_id': order.id,
                    'partner_id': order.partner_id.id,
                    'picking_id': self.id,
                    'package_id': pkg.id if pkg else False,
                    'package_contents':
                        self._rental_serial_pkg_contents(pkg) if pkg else '',
                })

    def _rental_serial_pkg_contents(self, package):
        """Readable snapshot of what a package held on this transfer."""
        self.ensure_one()
        parts = []
        for line in self.move_line_ids:
            if line.quantity <= 0:
                continue
            if line.package_id != package and line.result_package_id != package:
                continue
            lot = ' [%s]' % line.lot_id.name if line.lot_id else ''
            parts.append('%s× %s%s' % (
                ('%g' % line.quantity), line.product_id.display_name, lot))
        return ', '.join(parts)
