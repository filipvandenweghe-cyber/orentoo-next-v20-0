from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    rental_on_option = fields.Boolean(
        string='On Option',
        copy=False,
        help="Mark this quotation as holding its rental items 'on option' "
             "until its Expiration date.  While on option (and not yet "
             "confirmed), the held quantity is surfaced as 'on option by other "
             "orders' in the availability pop-up / report of OTHER orders.  "
             "The option's end date is the quotation Expiration "
             "(validity_date).",
    )

    @api.depends('partner_id', 'rental_start_date', 'rental_return_date')
    @api.depends_context('rental_avail_order_label', 'sale_show_partner_name')
    def _compute_display_name(self):
        """Enrich the order label with customer + date(s) when the Availability
        Report's Orders search asks for it (``rental_avail_order_label`` in the
        context).  Every other context falls back to standard behaviour, so the
        order name is unchanged everywhere else.
        """
        if not self.env.context.get('rental_avail_order_label'):
            return super()._compute_display_name()
        for order in self:
            parts = [order.name or '']
            partner = order.partner_id.name or order.partner_id.display_name
            if partner:
                parts.append(partner)
            dates = order._rental_avail_label_dates()
            if dates:
                parts.append(dates)
            order.display_name = ' · '.join(p for p in parts if p)

    def _rental_avail_label_dates(self):
        """Human date span for the order label — rental start→return for rental
        orders (in the user timezone), else the order date."""
        self.ensure_one()
        if getattr(self, 'is_rental_order', False) and self.rental_start_date:
            start = fields.Datetime.context_timestamp(
                self, self.rental_start_date)
            span = start.strftime('%d %b %Y')
            if self.rental_return_date:
                end = fields.Datetime.context_timestamp(
                    self, self.rental_return_date)
                span = '%s → %s' % (span, end.strftime('%d %b %Y'))
            return span
        if self.date_order:
            return fields.Datetime.context_timestamp(
                self, self.date_order).strftime('%d %b %Y')
        return ''

    @api.depends(
        'is_rental_order', 'state',
        'order_line.is_rental', 'order_line.product_uom_qty',
        'order_line.qty_delivered', 'order_line.qty_returned',
        'order_line.is_set',
        'order_line.move_ids', 'order_line.move_ids.state',
        'picking_ids.state', 'picking_ids.picking_type_code',
    )
    def _compute_has_action_lines(self):
        """Refine the rental pickup/return status.

        1) Exclude rental-set HEADER lines: a set parent (and nested set
           headers) carry no stock moves, so their qty_delivered stays 0 and
           would make the order look permanently 'to pick up'.  The real
           status comes from the set's component lines.

        2) The Pickup button is driven by OPEN OUTGOING warehouse operations,
           not by delivered-vs-ordered qty.  Once every outgoing picking is
           done/cancelled (e.g. a no-backorder short delivery) there is
           nothing left to pick up — WITHOUT changing the ordered quantity
           (we must keep what the client asked for, and the warehouse may
           still adjust set contents).

        3) Units scrapped as lost/broken are no longer expected back, so they
           count towards "returned" — an order whose outstanding units were
           all returned OR written off is fully returned.
        """
        super()._compute_has_action_lines()
        for order in self:
            if order.state != 'sale' or not order.is_rental_order:
                continue

            # (1) Ignore set-header lines in the per-line status.
            lines = order.order_line.filtered(
                lambda l: l.is_rental and l.product_type != 'combo'
                and not l.is_set
            )
            order.has_pickable_lines = any(
                sol.qty_delivered < sol.product_uom_qty for sol in lines)
            # (3) Units genuinely still out at the customer.  Read it from
            #     custody (what reached rental_loc minus what left it again);
            #     native qty_returned ALREADY counts the lost/broken scraps,
            #     so adding _rental_scrapped_qty() on top double-counted them
            #     and hid a partially-returned line.
            order.has_returnable_lines = any(
                sol._rental_custody_outstanding_qty() > 0 for sol in lines)

            # (2) Pickup is driven by the customer-facing OUTGOING delivery
            #     only.  Internal steps are ignored — both outbound Pick/Pack
            #     (done before Ship anyway) and return-side QC/Storage — so a
            #     multi-step receipt/return never keeps the button visible.
            #     Once no outgoing delivery is open, there is nothing left to
            #     pick up.  Backorders create an open outgoing delivery, so
            #     partial-with-backorder still shows Pickup.
            outgoing = order.picking_ids.filtered(
                lambda p: p.picking_type_code == 'outgoing')
            if outgoing and not outgoing.filtered(
                    lambda p: p.state not in ('done', 'cancel')):
                order.has_pickable_lines = False

    @api.depends(
        'rental_start_date', 'rental_return_date', 'state',
        'order_line.is_rental', 'order_line.product_uom_qty',
        'order_line.qty_delivered', 'order_line.qty_returned',
        'order_line.move_ids', 'order_line.move_ids.state',
    )
    def _compute_rental_status(self):
        """Extend the native dependencies so the STORED rental status
        refreshes when units are scrapped as lost/broken.

        Native ``_compute_rental_status`` only depends on delivered/returned
        quantities, but a lost/broken scrap changes neither — it adds a scrap
        move.  Without this, an order whose outstanding units were all written
        off would stay stuck on "Picked-up".  The scrap-aware
        ``has_returnable_lines`` (see above) then flips it to "Returned".
        """
        super()._compute_rental_status()

    def copy(self, default=None):
        """Skip set expansion when duplicating an order.

        When an order with a rental set is duplicated, Odoo copies both
        the set parent line and all its component lines.  Without the
        rental_set_copying flag, create() would call _expand_rental_set()
        on the copied parent, creating a second set of components.

        After copying, we remap set_parent_line_id on the new order's
        component lines so they reference the new parent (not the
        original order's parent line).  (RS11)
        """
        self = self.with_context(rental_set_copying=True)
        new_order = super(SaleOrder, self).copy(default=default)

        # Remap set_parent_line_id: find new parents by matching product
        # and is_set flag, then link children to them.
        old_lines = self.order_line
        new_lines = new_order.order_line

        # Build mapping: old_parent_id → new_parent_line
        parent_map = {}
        for old_line in old_lines.filtered(lambda l: l.is_set and not l.is_set_component):
            # Find matching new parent by product and sequence
            new_parent = new_lines.filtered(
                lambda l: (
                    l.is_set and not l.is_set_component
                    and l.product_id == old_line.product_id
                )
            )[:1]
            if new_parent:
                parent_map[old_line.id] = new_parent

        # Remap children
        for new_comp in new_lines.filtered('is_set_component'):
            old_parent_id = new_comp.set_parent_line_id.id
            if old_parent_id in parent_map:
                new_comp.with_context(
                    rental_set_copying=True,
                ).write({
                    'set_parent_line_id': parent_map[old_parent_id].id,
                })

        return new_order

    def _get_order_lines_to_report(self):
        """Exclude hidden Rental Set component lines from customer-facing
        documents (quotation PDF, sales order PDF, portal).

        Only lines where visible_to_customer=True (or non-component lines)
        are shown.  The parent set line carries the customer-facing
        description and price.
        """
        lines = super()._get_order_lines_to_report()
        return lines.filtered(
            lambda l: not l.is_set_component or l.visible_to_customer
        )

    def _get_invoiceable_lines(self, final=False):
        """Exclude hidden Rental Set components from invoice creation.

        Components with visible_to_customer=False should never appear on
        customer invoices.  The parent set line is the only invoiceable line.
        """
        lines = super()._get_invoiceable_lines(final=final)
        return lines.filtered(
            lambda l: not l.is_set_component or l.visible_to_customer
        )

    def _get_action_add_from_catalog_extra_context(self):
        """Expose this order's warehouse & company to the product catalog so the
        card can show rental availability for the order's period (rental dates
        are already added to the context by ``sale_renting``).  Read by
        ``product.product._compute_rental_avail_catalog``.
        """
        ctx = super()._get_action_add_from_catalog_extra_context()
        if getattr(self, 'is_rental_order', False):
            ctx.update(
                rental_catalog_wh=self.warehouse_id.id,
                rental_catalog_company=self.company_id.id,
            )
        return ctx
