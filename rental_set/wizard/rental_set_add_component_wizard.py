from odoo import _, fields, models


class RentalSetAddComponentWizard(models.TransientModel):
    """One-shot wizard that adds an order-specific component line to a Rental Set.

    The product template is never modified.  Only a new ``sale.order.line``
    is created with the appropriate set flags, making the component visible
    only on this particular sale order.

    When the selected product is itself a Rental Set the new line is created
    as a *nested set* (``is_set=True``) and its own sub-components are
    expanded immediately using the same ``_expand_set_children`` helper that
    the automatic expansion uses.
    """
    _name = 'rental.set.add.component.wizard'
    _description = 'Add Component to Rental Set (Order-Specific)'

    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string='Sale Order',
        required=True,
        readonly=True,
    )
    set_line_id = fields.Many2one(
        comodel_name='sale.order.line',
        string='Parent Set Line',
        required=True,
        readonly=True,
    )
    set_name = fields.Char(
        related='set_line_id.product_id.display_name',
        string='Adding to Set',
        readonly=True,
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Component Product',
        required=True,
        domain="['|', '|', ('sale_ok', '=', True), ('rent_periodicity', '!=', False), ('product_tmpl_id.is_rental_set', '=', True)]",
    )
    quantity_per_set = fields.Float(
        string='Quantity per Set Unit',
        default=1.0,
        digits='Product Unit of Measure',
        help=(
            'How many units of this component are needed per 1 unit of the '
            'Rental Set.  The actual line quantity is automatically scaled by '
            'the set quantity on the order and will update if the set qty changes.'
        ),
    )
    visible_to_customer = fields.Boolean(
        string='Visible to Customer',
        default=False,
        help=(
            'When checked, this component line will appear on customer-facing '
            'documents (quotation, order confirmation, invoice).'
        ),
    )

    # ── Action ────────────────────────────────────────────────────────────────

    def action_confirm(self):
        """Create the order-specific component line and close the wizard.

        If the chosen product is itself a Rental Set the new line is marked
        ``is_set=True`` and ``_expand_set_children`` is called on it so that
        its own sub-components are immediately inserted into the order, exactly
        as if the set had been added via the product field.
        """
        self.ensure_one()
        parent = self.set_line_id

        # Check that the user may add components to this set
        parent._check_set_edit_permission(operation='add')
        product_tmpl = self.product_id.product_tmpl_id
        is_rental_set = product_tmpl.is_rental_set

        # ── Compute position for the new line ────────────────────────────────
        new_level = (parent.set_level or 0) + 1
        sibling_count = self.env['sale.order.line'].search_count([
            ('set_parent_line_id', '=', parent.id),
        ])
        next_seq = sibling_count + 1
        parent_path = parent.set_sequence_path or ''
        new_path = (
            f"{parent_path}.{next_seq:03d}" if parent_path else f"{next_seq:03d}"
        )
        new_qty = self.quantity_per_set * parent.product_uom_qty

        # ── Create the component / nested-set line ────────────────────────────
        new_line = self.env['sale.order.line'].with_context(
            rental_set_expanding=True
        ).create({
            'order_id': self.sale_order_id.id,
            'product_id': self.product_id.id,
            'product_uom_qty': new_qty,
            'sequence': parent.sequence,
            'is_set_component': True,
            'is_set': is_rental_set,
            'visible_to_customer': self.visible_to_customer,
            'set_parent_line_id': parent.id,
            'set_level': new_level,
            'set_sequence_path': new_path,
            'set_component_qty': self.quantity_per_set,
        })

        # ── If the added product is a Rental Set, expand its sub-components ──
        if is_rental_set:
            # Build ancestor set from the parent chain to guard against
            # circular references (same logic as the automatic expansion)
            ancestor_ids = frozenset({product_tmpl.id})
            node = parent
            while node:
                if node.product_id and node.is_set:
                    ancestor_ids = ancestor_ids | {
                        node.product_id.product_tmpl_id.id
                    }
                node = node.set_parent_line_id

            new_line._expand_set_children(
                parent_qty=new_qty,
                depth=new_level,
                ancestor_ids=ancestor_ids,
                parent_path=new_path,
            )

        # Recompute pricing on the top-level set parent after adding
        top_parent = parent if not parent.is_set_component else parent._get_top_set_parent()
        if top_parent:
            top_parent._apply_set_pricing()

        # Sync stock moves / reservations for the new component
        new_line._sync_set_stock_moves()

        # Invalidate availability so it recomputes from the updated components
        new_line._invalidate_set_availability()

        return {'type': 'ir.actions.act_window_close'}
