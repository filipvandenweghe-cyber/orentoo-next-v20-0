from odoo import api, models, _
from odoo.exceptions import UserError

# Business requirements: OG01–OG10
# See __init__.py for full index.


class OrderGenerationService(models.AbstractModel):
    """Generates draft sale/rental orders from dossier slots and items.

    Architecture:
    - Each rental slot generates one sale.order with rental dates set
      at order level (Odoo native architecture).
    - Rental items within a slot become order lines with is_rental=True
      (via in_rental_app context).
    - Non-slot items (add-ons/services without a slot) are grouped into
      a separate non-rental sale order.
    - Event ticket items create lines compatible with event_sale when
      that module is installed.
    - Manually linked orders are never touched or removed.
    """

    _name = 'multi.channel.rental.order.service'
    _description = 'Dossier Order Generation Service'

    # =================================================================
    # Public API
    # =================================================================

    @api.model
    def _generate_sale_orders(self, dossier):
        """Generate or update draft sale orders for the entire dossier.

        Creates one order per slot + one for non-slot items.
        Skips items that already have a generated order line.
        """
        dossier.ensure_one()
        dossier._validate_for_confirmation()

        generated_orders = self.env['sale.order']

        # 1. One order per slot
        for slot in dossier.slot_ids.filtered(
            lambda s: s.state not in ('cancelled',)
        ):
            order = self._generate_sale_order_for_slot(slot)
            if order:
                generated_orders |= order

        # 2. Non-slot items
        non_slot_items = dossier.item_ids.filtered(
            lambda i: not i.slot_id
            and i.state not in ('cancelled',)
            and not i.sale_order_line_id
        )
        if non_slot_items:
            order = self._generate_sale_order_for_non_slot_items(
                dossier, non_slot_items,
            )
            if order:
                generated_orders |= order

        # Link all generated orders to dossier M2M
        if generated_orders:
            existing_ids = set(dossier.sale_order_ids.ids)
            new_ids = set(generated_orders.ids) - existing_ids
            if new_ids:
                dossier.write({
                    'sale_order_ids': [(4, oid) for oid in new_ids],
                })

        return generated_orders

    @api.model
    def _generate_sale_order_for_slot(self, slot):
        """Generate or update a draft sale order for a single slot.

        The order carries rental dates at order level. All rental items
        in the slot become is_rental=True order lines.
        """
        dossier = slot.dossier_id
        items = slot.item_ids.filtered(
            lambda i: i.state not in ('cancelled',)
        )
        if not items:
            return self.env['sale.order']

        # Reuse existing draft order if one was already generated
        order = slot.sale_order_id
        if order and order.state == 'cancel':
            order = self.env['sale.order']
            slot.sale_order_id = False

        has_rental = any(
            i.item_role == 'rental' and i.product_id.rent_periodicity
            for i in items
        )

        if not order:
            order_vals = self._prepare_order_vals(dossier, slot, has_rental)
            order = self.env['sale.order'].with_context(
                in_rental_app=has_rental,
            ).create(order_vals)
            slot.sale_order_id = order

        # Generate lines for items not yet linked
        for item in items.filtered(lambda i: not i.sale_order_line_id):
            line = self._create_order_line(order, item, is_rental_order=has_rental)
            item.write({
                'sale_order_line_id': line.id,
                'state': 'generated',
            })

        slot.state = 'order_created'
        return order

    @api.model
    def _generate_sale_order_for_non_slot_items(self, dossier, items):
        """Generate a sale order for dossier items not assigned to any slot.

        These are typically add-ons or services that don't need a rental
        timeslot. A single non-rental order is created.
        """
        # Look for an existing non-slot order on this dossier
        existing = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
            ('mcrf_dossier_slot_id', '=', False),
            ('state', '!=', 'cancel'),
        ], limit=1)

        order = existing
        if not order:
            order_vals = self._prepare_order_vals(
                dossier, slot=None, is_rental=False,
            )
            order = self.env['sale.order'].create(order_vals)

        for item in items.filtered(lambda i: not i.sale_order_line_id):
            line = self._create_order_line(order, item, is_rental_order=False)
            item.write({
                'sale_order_line_id': line.id,
                'state': 'generated',
            })

        return order

    # =================================================================
    # Order preparation
    # =================================================================

    @api.model
    def _prepare_order_vals(self, dossier, slot=None, is_rental=False):
        """Prepare values for creating a sale.order."""
        warehouse = (
            (slot and slot.warehouse_id)
            or dossier.warehouse_id
        )

        vals = {
            'partner_id': dossier.partner_id.id,
            'company_id': dossier.company_id.id,
            'warehouse_id': warehouse.id if warehouse else False,
            'pricelist_id': dossier.pricelist_id.id if dossier.pricelist_id else False,
            'user_id': dossier.user_id.id if dossier.user_id else False,
            'mcrf_dossier_id': dossier.id,
            'mcrf_dossier_slot_id': slot.id if slot else False,
            'mcrf_profile_id': dossier.profile_id.id if dossier.profile_id else False,
            'mcrf_source': dossier.source,
        }

        if is_rental and slot:
            vals.update({
                'is_rental_order': True,
                'rental_start_date': slot.start_datetime,
                'rental_return_date': slot.end_datetime,
            })

        return vals

    # =================================================================
    # Line creation
    # =================================================================

    @api.model
    def _create_order_line(self, order, item, is_rental_order=False):
        """Create a sale.order.line from a dossier item.

        For rental items on a rental order: uses in_rental_app context
        so Odoo marks the line as is_rental=True and applies rental
        pricing (including coefficients via rental_coefficient_dynamic_pricing).

        For event ticket items: sets event_id and event_ticket_id when
        event_sale is installed.
        """
        is_rental_line = (
            is_rental_order
            and item.item_role == 'rental'
            and item.product_id.rent_periodicity
        )

        line_vals = {
            'order_id': order.id,
            'product_id': item.product_id.id,
            'product_uom_qty': item.quantity,
            'mcrf_dossier_item_id': item.id,
        }

        # Event ticket fields — sets event_id/event_ticket_id on the SO
        # line so event_sale's _init_registrations creates registrations
        # when the order is confirmed.
        if (item.item_role == 'event_ticket'
                and item.event_id
                and 'event.event' in self.env):
            line_vals['event_id'] = item.event_id.id
            if item.event_ticket_id:
                line_vals['event_ticket_id'] = item.event_ticket_id.id
            if item.event_slot_id:
                line_vals['event_slot_id'] = item.event_slot_id.id

        # Create line with appropriate context
        ctx = {}
        if is_rental_line:
            ctx['in_rental_app'] = True

        line = self.env['sale.order.line'].with_context(**ctx).create(
            line_vals,
        )

        # Carry the basket's authoritative unit price onto the order line.
        # The dossier item's ``price_unit`` already reflects what the
        # customer saw and agreed to in the kiosk basket (list price with
        # duration coefficient and dynamic multiplier applied). Without this,
        # rental lines get re-priced by Odoo's rental duration coefficients
        # (``in_rental_app``) and the confirmed order total no longer matches
        # the basket the customer paid.
        if item.price_unit and line.price_unit != item.price_unit:
            line.write({'price_unit': item.price_unit})

        return line

    # =================================================================
    # Sync / update
    # =================================================================

    @api.model
    def _sync_dossier_items_to_order_lines(self, dossier):
        """Synchronize dossier item changes to existing generated lines.

        Updates quantity and product on generated lines that still exist
        in draft orders. Does not touch manually created lines.
        """
        for item in dossier.item_ids.filtered(
            lambda i: i.sale_order_line_id and i.state != 'cancelled'
        ):
            line = item.sale_order_line_id
            if not line.exists() or line.order_id.state != 'draft':
                continue
            vals = {}
            if line.product_uom_qty != item.quantity:
                vals['product_uom_qty'] = item.quantity
            if vals:
                line.write(vals)

        # Cancel lines for cancelled items
        for item in dossier.item_ids.filtered(
            lambda i: i.sale_order_line_id and i.state == 'cancelled'
        ):
            line = item.sale_order_line_id
            if line.exists() and line.order_id.state == 'draft':
                line.unlink()
                item.sale_order_line_id = False

    # =================================================================
    # Cancellation
    # =================================================================

    @api.model
    def _cancel_generated_orders(self, dossier):
        """Cancel all generated (non-manually-linked) sale orders.

        Only cancels orders that were generated by the dossier (have
        mcrf_dossier_id set). Manually linked orders are left untouched.
        """
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
            ('state', '!=', 'cancel'),
        ])
        for order in generated:
            if order.state == 'draft':
                order.action_cancel()
            elif order.state in ('sent', 'sale'):
                order.action_cancel()
