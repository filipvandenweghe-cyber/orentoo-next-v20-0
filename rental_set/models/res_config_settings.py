from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rental_show_stock_locations = fields.Boolean(
        related='company_id.rental_show_stock_locations',
        readonly=False,
        string="Show physical stock & locations in availability pop-up",
    )
    rental_set_allow_detail = fields.Boolean(
        related='company_id.rental_set_allow_detail',
        readonly=False,
        string="Allow showing rental set contents on customer documents",
    )
    rental_flag_options = fields.Boolean(
        related='company_id.rental_flag_options',
        readonly=False,
        string="Warn about stock on option by other orders",
    )
