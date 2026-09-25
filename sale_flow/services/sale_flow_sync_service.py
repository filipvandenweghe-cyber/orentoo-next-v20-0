import logging

from odoo import api, fields, models, _
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class SaleFlowSyncService(models.AbstractModel):
    """Service for synchronizing stock moves with sale.flow.line.

    Implements: R07, R08, R16.

    Business rules:
      * Let Odoo stock behavior run first, then reconcile into flow lines.
      * One flow line aggregates all moves for a logical product/order line.
      * Outgoing moves update delivered_qty.
      * Return moves update returned_qty.
      * Context flag skip_sale_flow_sync prevents recursive loops.  (R16)
      * Return picking demands are reconciled to match actual delivered
        quantities after each outgoing delivery.  (R08)
    """

    _name = 'sale.flow.sync.service'
    _description = 'Sale Flow Sync Service'

    def _sync_moves_to_flow(self, moves):
        """Synchronize completed stock moves to their sale.flow.line.

        Called after moves reach 'done' state.  Links moves to flow lines,
        updates operational quantities, and reconciles return pickings so
        their demands match what was actually delivered.
        """
        if self.env.context.get('skip_sale_flow_sync'):
            return

        affected_orders = self.env['sale.order']

        for move in moves.filtered(lambda m: m.state == 'done'):
            flow_line = move.sale_flow_line_id
            sale_line = move.sale_line_id

            if not flow_line and sale_line:
                # Try to find flow line via sale line
                flow_line = sale_line.flow_line_ids.filtered(
                    lambda fl: fl.state not in ('cancelled',)
                )[:1]
                if flow_line:
                    move.with_context(skip_sale_flow_sync=True).write({
                        'sale_flow_line_id': flow_line.id,
                    })

            if not flow_line:
                # No flow line found via direct link or sale line.
                # Before creating a new one, search for an existing flow
                # line on the same order for the same product.  This
                # handles return moves for products that were added during
                # delivery: the return move may have no sale_line_id, but
                # the original outgoing flow line already exists.
                # A return should REDUCE the original line, not create a
                # duplicate.  (R08)
                order = (
                    move.picking_id.sale_id
                    if move.picking_id else False
                )
                if order:
                    flow_line = order.flow_line_ids.filtered(
                        lambda fl: (
                            fl.product_id == move.product_id
                            and fl.state not in ('cancelled',)
                        )
                    )[:1]
                    if flow_line:
                        move.with_context(skip_sale_flow_sync=True).write({
                            'sale_flow_line_id': flow_line.id,
                        })

            if not flow_line:
                # Truly new product — create flow line (and SOL if needed)
                flow_line = self.env['sale.flow.service']._create_flow_line_from_delivery(move)
                if flow_line:
                    move.with_context(skip_sale_flow_sync=True).write({
                        'sale_flow_line_id': flow_line.id,
                    })

            if not flow_line:
                continue

            # Determine direction: outgoing or return
            is_return = self._is_return_move(move)

            if is_return:
                # Link as return move
                flow_line.with_context(skip_sale_flow_sync=True).write({
                    'return_move_ids': [(4, move.id)],
                })
                self._update_return_quantities(flow_line)
            else:
                # Link as outgoing move
                flow_line.with_context(skip_sale_flow_sync=True).write({
                    'outgoing_move_ids': [(4, move.id)],
                })
                self._update_outgoing_quantities(flow_line)

                # Track affected orders for return reconciliation
                if flow_line.sale_order_id:
                    affected_orders |= flow_line.sale_order_id

            flow_line._update_state()
            flow_line._compute_warning_level()
            flow_line._stamp_audit(
                origin='delivery' if not is_return else 'return',
            )

        # After all outgoing moves are synced, reconcile return pickings
        # so their demands match what was actually delivered.
        for order in affected_orders:
            self._reconcile_return_pickings(order)

    def _is_return_move(self, move):
        """Determine if a stock move is a return.

        A move is a return if:
          - Its picking has a return_id (it's a return picking), OR
          - It has an origin_returned_move_id (it reverses another move)
        """
        if move.picking_id and move.picking_id.return_id:
            return True
        if move.origin_returned_move_id:
            return True
        return False

    def _update_outgoing_quantities(self, flow_line):
        """Recompute delivered/prepared quantities from linked outgoing moves.

        In multi-step delivery (Pick→Pack→Ship), each step creates a
        move linked to the same sale line.  Counting all steps would
        inflate delivered_qty.  We only count moves from the FINAL
        delivery step — the one whose destination is NOT an internal
        warehouse location (i.e. customer, rental, or transit).
        Intermediate moves (to packing zone, output area) go to
        internal locations and are excluded.
        """
        done_moves = flow_line.outgoing_move_ids.filtered(
            lambda m: m.state == 'done'
        )
        # Deduplicate for multi-step delivery (Pick→Pack→Ship).
        # Each step has a move linked to the same sale line.  Only
        # count the final step to avoid inflating delivered_qty.
        # Detection: multiple done moves from different picking types.
        if len(done_moves) > 1:
            picking_types = set(done_moves.mapped('picking_type_id.id'))
            if len(picking_types) > 1:
                final_done = done_moves.filtered(
                    lambda m: (
                        m.location_dest_id.usage in ('customer', 'transit')
                        or m.location_dest_id.location_id.usage == 'customer'
                    )
                )
                if final_done:
                    done_moves = final_done

        assigned_moves = flow_line.outgoing_move_ids.filtered(
            lambda m: m.state == 'assigned'
        )

        delivered = sum(done_moves.mapped('quantity'))
        prepared = sum(assigned_moves.mapped('product_uom_qty'))

        vals = {
            'delivered_qty': delivered,
            'prepared_qty': prepared,
        }

        # If delivered exceeds confirmed, mark as changed
        prec = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        if float_compare(delivered, flow_line.confirmed_qty, precision_digits=prec) != 0:
            vals['was_changed_after_confirmation'] = True

        flow_line.with_context(skip_sale_flow_sync=True).write(vals)

    def _update_return_quantities(self, flow_line):
        """Recompute returned quantities from linked return moves.

        For sale products (not rental) with qty_delivered_method='manual',
        also reduce qty_delivered on the sale order line so the invoiceable
        quantity reflects the net delivered amount.

        Business rule: returning a sale product reduces the delivered qty
        on the original line — it does NOT create a new line.  The
        invoiceable quantity = delivered - returned.
        """
        done_returns = flow_line.return_move_ids.filtered(
            lambda m: m.state == 'done'
        )
        returned = sum(done_returns.mapped('quantity'))

        flow_line.with_context(skip_sale_flow_sync=True).write({
            'returned_qty': returned,
            'was_changed_after_confirmation': True,
        })

        # For sale products with manual qty_delivered, adjust the SOL
        # so invoicing sees the correct net quantity.
        sale_line = flow_line.sale_line_id
        if sale_line and not flow_line.is_rental:
            if sale_line.qty_delivered_method == 'manual':
                net_delivered = flow_line.delivered_qty - returned
                sale_line.with_context(skip_sale_flow_sync=True).write({
                    'qty_delivered': max(net_delivered, 0),
                })

    def _link_stock_moves_to_flow(self, order):
        """Link existing stock moves to their flow lines after confirmation.

        Called after order confirmation when moves may have already been
        created by _action_launch_stock_rule.
        """
        for flow_line in order.flow_line_ids:
            sale_line = flow_line.sale_line_id
            if not sale_line:
                continue

            # Find all moves linked to this sale line
            moves = self.env['stock.move'].search([
                ('sale_line_id', '=', sale_line.id),
                ('sale_flow_line_id', '=', False),
            ])
            for move in moves:
                move.with_context(skip_sale_flow_sync=True).write({
                    'sale_flow_line_id': flow_line.id,
                })
                is_return = self._is_return_move(move)
                if is_return:
                    flow_line.with_context(skip_sale_flow_sync=True).write({
                        'return_move_ids': [(4, move.id)],
                    })
                else:
                    flow_line.with_context(skip_sale_flow_sync=True).write({
                        'outgoing_move_ids': [(4, move.id)],
                    })

    # ── Return picking reconciliation ────────────────────────────────

    def _reconcile_return_pickings(self, order):
        """Reconcile return picking demands with actual + pending deliveries.

        Business rule: the return picking must expect back everything that
        was delivered OR will be delivered (pending backorders).  Only
        reduce return demand when items are truly NOT going to be delivered
        (no backorder = cancelled).

        After an outgoing delivery is validated:
          * For each rental product: ensure the return demand matches
            delivered + pending backorder demand.
          * Do NOT reduce demand when a backorder exists for the remaining.
          * Add return moves for rental products added during delivery.
          * Do not touch non-rental (sale) products.
        """
        if not getattr(order, 'is_rental_order', False):
            return

        # Find return pickings (incoming pickings that are returns of outgoing ones)
        return_pickings = order.picking_ids.filtered(
            lambda p: p.return_id and p.state not in ('done', 'cancel')
        )
        if not return_pickings:
            return

        prec = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        # Expected return demand = what has actually gone OUT to the customer
        # (Option B).  "Until it is out, the client is not expected to return
        # it."  So we count only DONE outbound moves that reached the customer
        # / rental location.  This means:
        #   * multi-step (Pick->Pack->Ship): the intermediate legs never reach
        #     the customer, so the return is never inflated across the legs —
        #     it grows only when goods actually ship;
        #   * over-delivery: whatever was shipped is expected back (7 on a
        #     5-line -> 7);
        #   * over-pick then put-back-before-shipping: the excess never ships,
        #     so it is never expected;
        #   * back-orders: the pending part is not expected until it ships.
        rental_loc = order.company_id.rental_loc_id

        def _reached_customer(move):
            dest = move.location_dest_id
            return (
                dest == rental_loc
                or dest.usage in ('customer', 'transit')
                or (dest.location_id and dest.location_id.usage == 'customer')
            )

        def _left_customer(move):
            src = move.location_id
            return (
                src == rental_loc
                or src.usage in ('customer', 'transit')
                or (src.location_id and src.location_id.usage == 'customer')
            )

        outbound = order.picking_ids.filtered(lambda p: not p.return_id)
        expected_map = {}
        pending_products = set()   # products with a delivery still in progress
        for m in outbound.move_ids:
            if m.state == 'cancel' or not m.product_id.rent_periodicity:
                continue
            if m.state == 'done':
                if _reached_customer(m):
                    expected_map[m.product_id.id] = \
                        expected_map.get(m.product_id.id, 0) + m.quantity
            else:
                pending_products.add(m.product_id.id)

        # ...minus what already came back.  Odoo 20 creates the rental return
        # picking together with the delivery and links it to it (``return_id``,
        # which also propagates to its back-orders), so a return is routinely
        # validated in several legs (partial return -> back-order).  What is
        # still expected back is therefore "what went out" MINUS what has
        # already been received; without this the open leg would keep
        # re-demanding the full delivered quantity.
        returned_map = {}
        for picking in order.picking_ids.filtered(lambda p: p.return_id):
            for m in picking.move_ids:
                if m.state != 'done' or not m.product_id.rent_periodicity:
                    continue
                if _left_customer(m):
                    returned_map[m.product_id.id] = \
                        returned_map.get(m.product_id.id, 0) + m.quantity
        for pid, returned in returned_map.items():
            if pid in expected_map:
                expected_map[pid] = max(expected_map[pid] - returned, 0)

        for picking in return_pickings:
            active_moves = picking.move_ids.filtered(lambda m: m.state != 'cancel')
            existing_products = set(active_moves.mapped('product_id.id'))

            # Compare TOTAL existing demand per product against expected.
            # Multiple moves for the same product (e.g. from two sets)
            # must be summed before comparing.
            product_moves = {}  # product_id -> list of moves
            for move in active_moves:
                product_moves.setdefault(move.product_id.id, []).append(move)

            for pid, moves in product_moves.items():
                expected = expected_map.get(pid, 0)
                current_total = sum(m.product_uom_qty for m in moves)

                # Delivery still in progress and nothing has reached the
                # customer yet: leave the return as-is (do NOT reduce/cancel).
                # It will be set to the delivered qty once the goods ship.
                # This keeps the return alive across multi-step legs and only
                # cancels it when a line is genuinely not being delivered.
                if float_compare(expected, 0, precision_digits=prec) <= 0 \
                        and pid in pending_products:
                    continue

                if float_compare(expected, current_total, precision_digits=prec) == 0:
                    continue  # Already correct

                if float_compare(expected, 0, precision_digits=prec) <= 0:
                    for move in moves:
                        move.with_context(skip_sale_flow_sync=True)._action_cancel()
                    _logger.info(
                        'Sale flow: cancelled return moves for %s on %s '
                        '(nothing delivered or pending)',
                        moves[0].product_id.display_name, picking.name,
                    )
                elif float_compare(expected, current_total, precision_digits=prec) != 0:
                    # Distribute the expected qty proportionally across
                    # existing moves, or adjust the first move if simpler.
                    if len(moves) == 1:
                        moves[0].with_context(skip_sale_flow_sync=True).write({
                            'product_uom_qty': expected,
                        })
                    else:
                        # Multiple moves: scale proportionally
                        ratio = expected / current_total if current_total else 1
                        for move in moves:
                            new_qty = move.product_uom_qty * ratio
                            move.with_context(skip_sale_flow_sync=True).write({
                                'product_uom_qty': new_qty,
                            })
                    _logger.info(
                        'Sale flow: adjusted return demand for %s on %s '
                        'from %s to %s (delivered + pending backorder)',
                        moves[0].product_id.display_name, picking.name,
                        current_total, expected,
                    )

            # Add return moves for rental products delivered/pending but
            # not yet in the return picking (e.g. products added during delivery).
            for product_id, expected_qty in expected_map.items():
                if product_id in existing_products:
                    continue
                if float_compare(expected_qty, 0, precision_digits=prec) <= 0:
                    continue

                product = self.env['product.product'].browse(product_id)

                # Create a new return move on the first available return picking
                self.env['stock.move'].with_context(skip_sale_flow_sync=True).create({
                    'product_id': product.id,
                    'product_uom_qty': expected_qty,
                    'uom_id': product.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'sale_line_id': self._find_sale_line_for_product(order, product),
                })
                _logger.info(
                    'Sale flow: added return move for %s (qty=%s) on %s '
                    '(delivered during picking, not in original return)',
                    product.display_name, expected_qty, picking.name,
                )

                # Only add to the first return picking, not all of them
                existing_products.add(product_id)

            # Re-check availability after adjustments — but only when there is
            # actually something to reserve.  Native ``action_assign`` raises
            # ``UserError("Nothing to check the availability for.")`` when the
            # picking has no assignable moves; calling it unconditionally here
            # would abort the whole ``button_validate`` (e.g. when a delivery's
            # return picking has nothing left to reserve), so we guard it.
            assignable = picking.move_ids.filtered(
                lambda m: m.state not in ('draft', 'cancel', 'done'))
            if assignable:
                picking.with_context(skip_sale_flow_sync=True).action_assign()

    def _find_sale_line_for_product(self, order, product):
        """Find or return False for a sale line matching a product on the order."""
        sale_line = order.order_line.filtered(
            lambda l: l.product_id == product
        )[:1]
        return sale_line.id if sale_line else False
