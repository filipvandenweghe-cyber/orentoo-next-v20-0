from odoo import _, api, fields, models


class RentalSerialLog(models.Model):
    """One entry per serial event in the rental cycle.

    Events:
      * delivered       — serial went out on a rental (client, order, and the
                          package it was in + a contents snapshot).
      * returned        — serial came back.
      * repair_start    — serial entered Repair.
      * repair_done     — serial left Repair.
      * repair_override — an operator proceeded with a serial scan despite an
                          active repair (audit of the non-blocking warning).
    """

    _name = 'rental.serial.log'
    _description = 'Rental Serial Usage Log'
    _order = 'date desc, id desc'

    lot_id = fields.Many2one(
        'stock.lot', string='Serial/Lot', required=True, index=True,
        ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string='Product',
        related='lot_id.product_id', store=True)
    event_type = fields.Selection([
        ('delivered', 'Delivered'),
        ('returned', 'Returned'),
        ('repair_start', 'Repair started'),
        ('repair_done', 'Repair done'),
        ('repair_override', 'Repair override'),
    ], string='Event', required=True, index=True)
    date = fields.Datetime(string='Date', required=True,
                           default=fields.Datetime.now, index=True)

    # Rental context (delivered / returned)
    sale_order_id = fields.Many2one('sale.order', string='Sales Order',
                                    index=True)
    partner_id = fields.Many2one('res.partner', string='Client', index=True)
    picking_id = fields.Many2one('stock.picking', string='Transfer')

    # Packaging context (captured at delivery — Option A)
    package_id = fields.Many2one('stock.package', string='Package')
    package_contents = fields.Char(
        string='Package Contents',
        help='Snapshot of what the package held at delivery time.')

    # Repair context
    repair_order_id = fields.Many2one('repair.order', string='Repair Order')

    note = fields.Char(string='Note')

    @api.depends('event_type', 'lot_id.name')
    def _compute_display_name(self):
        labels = dict(self._fields['event_type']._description_selection(self.env))
        for log in self:
            log.display_name = '%s — %s' % (
                labels.get(log.event_type, log.event_type),
                log.lot_id.name or '')

    def action_open_transaction(self):
        """Open the underlying transaction for this log entry — the repair
        order for repair events, otherwise the picking, else the order."""
        self.ensure_one()
        if self.repair_order_id:
            model, rec = 'repair.order', self.repair_order_id
        elif self.picking_id:
            model, rec = 'stock.picking', self.picking_id
        elif self.sale_order_id:
            model, rec = 'sale.order', self.sale_order_id
        else:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'res_id': rec.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def _rsl_log(self, vals):
        """Create a log entry unless an identical one already exists
        (idempotent against picking re-validation)."""
        domain = [
            ('lot_id', '=', vals['lot_id']),
            ('event_type', '=', vals['event_type']),
        ]
        if vals.get('picking_id'):
            domain.append(('picking_id', '=', vals['picking_id']))
        if vals.get('repair_order_id'):
            domain.append(('repair_order_id', '=', vals['repair_order_id']))
        if self.sudo().search_count(domain):
            return self.browse()
        return self.sudo().create(vals)

    @api.model
    def rsl_log_repair_override(self, serial_name, picking_id=False,
                               repair_id=False):
        """RPC-callable audit entry: an operator proceeded with a serial scan
        despite an active repair.  Resolves the serial by its unique name and
        records one (idempotent) ``repair_override`` event per picking."""
        name = (serial_name or '').strip()
        if not name:
            return False
        lot = self.env['stock.lot'].search(
            [('serial_unique_key', '=', name)], limit=1)
        if not lot:
            return False
        rec = self._rsl_log({
            'lot_id': lot.id,
            'event_type': 'repair_override',
            'picking_id': picking_id or False,
            'repair_order_id': repair_id or False,
            'note': _("Operator proceeded despite an active repair."),
        })
        return bool(rec)
