from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    # ── Rental Set fields (derived from the linked sale order line) ────────
    #
    # These fields project the set hierarchy from sale.order.line onto
    # stock.move so the picking form can display collapse/expand and
    # indent visuals identical to the rental order.

    rental_set_is_set = fields.Boolean(
        string='Is Set Parent Move',
        compute='_compute_rental_set_fields',
    )
    rental_set_is_component = fields.Boolean(
        string='Is Set Component Move',
        compute='_compute_rental_set_fields',
    )
    rental_set_parent_move_id = fields.Many2one(
        'stock.move',
        string='Set Parent Move',
        compute='_compute_rental_set_fields',
    )
    rental_set_level = fields.Integer(
        string='Set Nesting Level',
        compute='_compute_rental_set_fields',
    )
    rental_set_folded = fields.Boolean(
        string='Set Components Folded',
        default=False,
    )

    @api.depends('sale_line_id.is_set', 'sale_line_id.is_set_component',
                 'sale_line_id.set_parent_line_id', 'sale_line_id.set_level')
    def _compute_rental_set_fields(self):
        """Derive set hierarchy fields from the linked sale order line.

        The parent move is found by looking for the move in the same picking
        whose sale_line_id matches the parent SOL.
        """
        for move in self:
            sol = move.sale_line_id
            if not sol:
                move.rental_set_is_set = False
                move.rental_set_is_component = False
                move.rental_set_parent_move_id = False
                move.rental_set_level = 0
                continue

            move.rental_set_is_set = sol.is_set and sol.set_child_line_ids
            move.rental_set_is_component = sol.is_set_component
            move.rental_set_level = sol.set_level

            # Find the parent move in the same picking
            parent_sol = sol.set_parent_line_id
            if parent_sol and move.picking_id:
                parent_move = move.picking_id.move_ids.filtered(
                    lambda m: m.sale_line_id == parent_sol
                )[:1]
                move.rental_set_parent_move_id = parent_move
            else:
                move.rental_set_parent_move_id = False

    def _compute_forecast_information(self):
        """Fix forecast badge for done moves.

        Standard Odoo sets forecast_availability=0 for done moves,
        causing the picking form to show 'Not Available' on completed
        moves.  Done moves are fulfilled by definition.
        """
        super()._compute_forecast_information()
        for move in self:
            if move.state == 'done':
                move.forecast_availability = move.product_uom_qty

    def _action_cancel(self):
        """Prevent set parent header moves from being cancelled.

        During picking validation (especially no-backorder), Odoo cancels
        moves with quantity=0.  Set header moves are display-only and
        must not be cancelled — they should silently transition to done
        with the rest of the picking.  (RS04)

        We use SQL to force the state to 'done' to avoid ORM side
        effects (action_assign, availability checks, etc.) that would
        fail on a zero-qty display-only move.
        """
        header_moves = self.filtered(
            lambda m: (
                m.sale_line_id
                and m.sale_line_id.is_set
                and m.picking_id
                and m.state not in ('done', 'cancel')
            )
        )
        if header_moves:
            # Force headers to done via SQL — bypasses ORM triggers
            # that would fail on zero-qty moves (e.g. action_assign,
            # availability checks).
            self.env.cr.execute(
                "UPDATE stock_move SET state = 'done' WHERE id IN %s",
                [tuple(header_moves.ids)],
            )
            header_moves.invalidate_recordset(['state'])
            # Only cancel the remaining (real) moves
            remaining = self - header_moves
            if remaining:
                return super(StockMove, remaining)._action_cancel()
            return True
        return super()._action_cancel()

    @api.depends('sale_line_id')
    def _compute_description_picking(self):
        """Prefix set component moves with their parent set name.

        Business requirement:
        The order picker must be able to see which rental set a component
        belongs to.  The set name prefix is shown on all pickings in the
        **outbound sale chain** (Pick → Pack → Ship in multi-step flows).
        On **return** pickings the prefix is NOT shown because the
        warehouse has no control over how items are returned.

        Outbound chain detection:
        A picking is part of the outbound chain when it originates from
        a sale order (``sale_id`` is set) and is NOT a return picking
        (``return_id`` is not set).

        Warehouse pickers see e.g.:
            [Front Light Set] LED Par 64
            [Front Light Set] DMX Cable 5m

        This works on all views including barcode scanning screens.
        """
        super()._compute_description_picking()
        for move in self:
            sol = move.sale_line_id
            if not sol or not sol.is_set_component or move.description_picking_manual:
                continue
            # Only prefix on outbound sale chain pickings, not returns
            picking = move.picking_id
            if picking and (not picking.sale_id or picking.return_id):
                continue
            top_parent = sol._get_top_set_parent()
            if not top_parent:
                continue
            set_name = top_parent.product_id.display_name
            prefix = f'[{set_name}]'
            current = move.description_picking or ''
            if not current.startswith(prefix):
                move.description_picking = f'{prefix} {current}'.strip()
