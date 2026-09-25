from odoo import api, models


class SaleOrder(models.Model):
    """Extend sale.order for coefficient & dynamic pricing triggers.  [RI05, RI08]"""

    _inherit = 'sale.order'

    # RI08: order-level fields whose change means the rental unit prices may
    # need a refresh — the rental period, the customer, the pricelist, or any
    # order-line edit (a per-line date/qty/product change arrives here as an
    # ``order_line`` write command).
    _RENTAL_PRICE_TRIGGER_FIELDS = frozenset({
        'order_line',
        'rental_start_date',
        'rental_return_date',
        'partner_id',
        'pricelist_id',
    })

    @api.onchange('partner_id')
    def _onchange_partner_recompute_rental_prices(self):  # RI05
        """Refresh rental prices when the customer changes.

        Changing the customer may affect which coefficient table is
        selected (customer-allowed tables) and whether dynamic pricing
        is active for this customer.

        Odoo 20 removed the rental "Update Rental Prices" button and its
        ``show_update_duration`` flag — prices are recomputed straight from
        the onchange, the way the standard pricelist onchange does.  Like the
        save-time refresh below this is a *soft* recompute, so a hand-typed
        unit price is preserved.
        """
        lines = self.order_line.filtered(
            lambda line: line.is_rental and not line.display_type
        )
        if lines:
            lines.with_context(rental_save_recompute=True)._compute_price_unit()

    def write(self, vals):
        """Auto-refresh rental prices on save, like the button — but softly.

        Saving a rental order recomputes the coefficient/duration/dynamic
        driven unit price of its rental lines, so the user no longer has to
        press "Update Rental Prices" after changing the period, customer,
        pricelist or a line.  [RI08]

        Unlike a forced recomputation (``_recompute_rental_prices`` →
        ``force_price_recomputation``), this runs *without* forcing: any line
        whose unit price was typed by hand is left untouched.  Both guards
        below cooperate to preserve it — super()._compute_price_unit skips a
        line when ``technical_price_unit`` diverges from ``price_unit`` (a
        hand-typed price), and the engine additionally skips
        ``manual_price_override`` lines.  An explicit
        ``_recompute_rental_prices()`` still force-resets everything, manual
        prices included.
        """
        res = super().write(vals)
        self._auto_recompute_rental_prices_on_save(vals.keys())
        return res

    def _auto_recompute_rental_prices_on_save(self, changed_fields):
        """Recompute non-manual rental line prices after a save.  [RI08]

        Only runs when a price-relevant field changed and the order is still a
        quotation (draft/sent) — confirmed/invoiced orders keep their agreed
        prices.  A context flag makes the nested line recompute re-entrancy
        safe, and a forced recomputation already in progress (the button) is
        skipped so the two paths never fight.
        """
        ctx = self.env.context
        if ctx.get('rental_save_recompute') or ctx.get('force_price_recomputation'):
            return
        if not (self._RENTAL_PRICE_TRIGGER_FIELDS & set(changed_fields)):
            return
        for order in self:
            if order.state not in ('draft', 'sent'):
                continue
            lines = order.order_line.filtered(
                lambda l: l.is_rental and not l.display_type
            )
            if not lines:
                continue
            lines.with_context(
                rental_save_recompute=True,
            )._compute_price_unit()
