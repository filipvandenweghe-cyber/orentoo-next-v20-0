from odoo import api, fields, models, _
from odoo.tools import float_is_zero


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    rental_set_show_grouping = fields.Boolean(
        string='Show Rental Set Grouping',
        compute='_compute_rental_set_show_grouping',
        help=(
            'True for pickings in the outbound sale chain (Pick → Pack → '
            'Ship).  False for return pickings.  Controls whether the '
            'set fold/expand column is visible.'
        ),
    )

    @api.depends('sale_id', 'return_id')
    def _compute_rental_set_show_grouping(self):
        """Show set grouping on outbound sale chain pickings only.

        A picking is outbound when it originates from a sale order
        (sale_id is set) and is NOT a return (return_id is not set).
        This covers all steps in multi-step flows.
        """
        for picking in self:
            picking.rental_set_show_grouping = bool(
                picking.sale_id and not picking.return_id
            )

    def _get_set_grouped_moves(self):
        """Group stock moves by their Rental Set parent for report rendering.

        Returns a list of dicts, each representing either:
        - A set group:  {'type': 'set', 'set_line': SOL, 'moves': recordset}
        - A standalone move: {'type': 'move', 'move': stock.move}

        Moves belonging to the same top-level set are grouped together under
        the set's product name / description.

        Used by the delivery slip report (report_deliveryslip.xml).
        """
        self.ensure_one()
        moves = self.move_ids.filtered(lambda m: m.product_uom_qty or m.quantity)

        result = []
        seen_set_parents = set()

        for move in moves.sorted('sequence'):
            sol = move.sale_line_id
            if sol and sol.is_set_component:
                top_parent = sol._get_top_set_parent()
                if top_parent and top_parent.id not in seen_set_parents:
                    seen_set_parents.add(top_parent.id)
                    # Collect all moves for this set
                    set_moves = moves.filtered(
                        lambda m: m.sale_line_id
                        and m.sale_line_id.is_set_component
                        and m.sale_line_id._get_top_set_parent().id == top_parent.id
                    )
                    result.append({
                        'type': 'set',
                        'set_line': top_parent,
                        'moves': set_moves,
                    })
            elif sol and sol.is_set and not sol.is_set_component:
                # Set parent itself (shouldn't have moves, but just in case)
                continue
            else:
                # Regular non-set move
                if not (sol and sol.is_set_component):
                    result.append({
                        'type': 'move',
                        'move': move,
                    })

        return result

    # ── RS04/RS09: Zero-qty validation bypass for set header moves ───

    def _is_set_header_move(self, move):
        """Check if a move is a set header (display-only).

        Matches both top-level set parents AND nested set headers
        (is_set + is_set_component).  Both are zero-demand display
        entries that must not block validation.
        """
        sol = move.sale_line_id
        return sol and sol.is_set

    def _sanity_check(self, separate_pickings=True):
        """Override to exclude set header moves from the zero-qty check.

        Set parent header moves have product_uom_qty=0 and quantity=0.
        They are display-only grouping entries and must not trigger the
        'zero quantity transfer' error.  (RS04)

        Approach: run the standard check.  If it raises the zero-qty
        error, verify whether the picking has real (non-header) moves
        with quantity > 0.  If yes, suppress the error — the zero-qty
        is only from headers.
        """
        from odoo.exceptions import UserError
        try:
            return super()._sanity_check(separate_pickings=separate_pickings)
        except UserError as e:
            # Check if this is the zero-quantity error
            zero_msg = self._get_without_quantities_error_message()
            if str(e) != str(UserError(zero_msg)):
                raise

            # Re-check: does any picking have real moves with quantity?
            prec = self.env['decimal.precision'].precision_get('Product Unit')
            for picking in self:
                real_moves = picking.move_ids.filtered(
                    lambda m: (
                        m.state not in ('done', 'cancel')
                        and not self._is_set_header_move(m)
                    )
                )
                has_real_qty = any(
                    not float_is_zero(m.quantity, precision_digits=prec)
                    for m in real_moves
                )
                if not has_real_qty and real_moves:
                    # Real moves exist but all have zero qty → genuine error
                    raise
                if not real_moves:
                    # Only header moves remain → suppress (edge case)
                    continue
            # If we get here, all pickings passed the relaxed check

    def button_validate(self):
        """Override to create set header moves on backorder pickings.

        After a picking is validated with a backorder, creates a new
        set header move on the backorder so the visual grouping is
        preserved.  (RS04/RS09)
        """
        result = super().button_validate()

        # After validation, ensure backorders AND chained pickings
        # (next step in multi-step flow) also have set header moves.
        for picking in self:
            if picking.state == 'done':
                self._create_backorder_header_moves(picking)
                self._create_chained_header_moves(picking)

        return result

    def _create_backorder_header_moves(self, validated_picking):
        """Create set header moves on backorder pickings.

        When a picking is validated with a backorder, the component moves
        are split between the validated picking and the backorder.  The
        set header move stays on the validated picking (it was done).
        We create a new header move on the backorder so the set grouping
        is preserved.
        """
        # Find backorders of this picking
        backorders = self.search([
            ('backorder_id', '=', validated_picking.id),
            ('state', 'not in', ('done', 'cancel')),
        ])
        if not backorders:
            return

        for backorder in backorders:
            # Find set parent SOLs from the backorder's component moves
            set_parent_sols = set()
            for move in backorder.move_ids:
                sol = move.sale_line_id
                if sol and sol.is_set_component:
                    parent = sol._get_top_set_parent()
                    if parent:
                        set_parent_sols.add(parent)

            for parent_sol in set_parent_sols:
                # Check if header already exists on the backorder
                existing = backorder.move_ids.filtered(
                    lambda m: m.sale_line_id == parent_sol
                )
                if existing:
                    continue

                # Create header move on backorder
                min_seq = min(
                    (m.sequence for m in backorder.move_ids),
                    default=10,
                )
                header = self.env['stock.move'].create({
                    'product_id': parent_sol.product_id.id,
                    'product_uom_qty': 0,
                    'quantity': 0,
                    'uom_id': (
                        parent_sol.product_uom_id.id
                        or parent_sol.product_id.uom_id.id
                    ),
                    'location_id': backorder.location_id.id,
                    'location_dest_id': backorder.location_dest_id.id,
                    'picking_id': backorder.id,
                    'sale_line_id': parent_sol.id,
                    'sequence': min_seq - 1,
                    'picked': False,
                })
                header.write({'state': 'assigned'})

    def _create_chained_header_moves(self, validated_picking):
        """Create set header moves on the next step in multi-step flows.

        In Pick→Pack→Ship flows, when Pick is validated the component
        moves are chained to Pack via move_dest_ids.  The set header
        does not get chained automatically because it has no stock
        impact.  We find the next-step pickings and create headers
        so the set grouping is preserved across all outbound steps.

        Only applies to outbound pickings (not returns).
        """
        if validated_picking.return_id:
            return

        # Find next-step pickings from chained moves
        next_pickings = self.env['stock.picking']
        for move in validated_picking.move_ids.filtered(
            lambda m: m.state == 'done' and m.move_dest_ids
        ):
            for dest in move.move_dest_ids.filtered(
                lambda d: d.picking_id
                and d.picking_id != validated_picking
                and d.state not in ('done', 'cancel')
                and not d.picking_id.return_id
            ):
                next_pickings |= dest.picking_id

        for next_pick in next_pickings:
            # Find set parent SOLs from this picking's component moves
            set_parent_sols = set()
            for move in next_pick.move_ids:
                sol = move.sale_line_id
                if sol and sol.is_set_component:
                    parent = sol._get_top_set_parent()
                    if parent:
                        set_parent_sols.add(parent)
                # Also handle nested sets
                if sol and sol.is_set and sol.is_set_component:
                    set_parent_sols.add(sol)

            for parent_sol in set_parent_sols:
                existing = next_pick.move_ids.filtered(
                    lambda m: m.sale_line_id == parent_sol
                    and m.state not in ('cancel',)
                )
                if existing:
                    continue

                min_seq = min(
                    (m.sequence for m in next_pick.move_ids
                     if m.sale_line_id
                     and m.sale_line_id.set_parent_line_id == parent_sol),
                    default=min(
                        (m.sequence for m in next_pick.move_ids),
                        default=10,
                    ),
                )
                header = self.env['stock.move'].create({
                    'product_id': parent_sol.product_id.id,
                    'product_uom_qty': 0,
                    'quantity': 0,
                    'uom_id': (
                        parent_sol.product_uom_id.id
                        or parent_sol.product_id.uom_id.id
                    ),
                    'location_id': next_pick.location_id.id,
                    'location_dest_id': next_pick.location_dest_id.id,
                    'picking_id': next_pick.id,
                    'sale_line_id': parent_sol.id,
                    'sequence': min_seq - 1,
                    'picked': False,
                })
                header.write({'state': 'assigned'})

    def _prepare_return_move_default_values(self, move_id):
        """Skip set parent header moves on return pickings.

        Set parent header moves are display-only entries on outbound
        pickings (demand=set qty, picked=True).  They should never carry
        a return demand because:
          * The set parent product carries no stock of its own.
          * Only the actual components need to be returned.
          * Return reconciliation (_reconcile_return_pickings) handles
            component-level return demands correctly.

        Odoo 20 builds return pickings by copying the original moves
        (``stock.picking._create_return``); the legacy
        ``stock.return.picking`` wizard no longer exists.  We therefore
        force the returned quantity to 0 here instead of dropping a
        wizard line.
        """
        vals = super()._prepare_return_move_default_values(move_id)
        if self._is_set_header_move(move_id):
            vals['product_uom_qty'] = 0
        return vals
