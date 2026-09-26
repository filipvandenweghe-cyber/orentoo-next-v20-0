from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    rental_show_stock_locations = fields.Boolean(
        string="Show physical stock & locations in availability pop-up",
        default=False,
        help="Show the 'Physical stock (right now)' section — total stock and "
             "its locations — in the rental availability pop-up.  Off by "
             "default to keep the pop-up light; handy for troubleshooting.",
    )
    # RS-01: gates whether a rental set's contents may ever be shown on a
    # customer document.  Default False == the behaviour shipped until now
    # (contents never shown), so enabling the module changes nothing.
    rental_set_allow_detail = fields.Boolean(
        string="Allow showing rental set contents on customer documents",
        default=False,
        help="Allow salespeople to print the contents of a rental set on the "
             "quotation, the order confirmation, the portal and the invoice.  "
             "Off by default: only the set line is shown, carrying the whole "
             "price.  When on, each rental order decides for itself (field "
             "'Show set contents' on the order).",
    )
    rental_flag_options = fields.Boolean(
        string="Warn about stock on option by other orders",
        default=True,
        help="Show 'on option by other orders' in the availability pop-up and "
             "turn the availability icon red when unconfirmed quotations "
             "('on option' until their validity date) could make this line "
             "unfulfillable.  Informational only — it never changes the "
             "committed availability figure.",
    )
