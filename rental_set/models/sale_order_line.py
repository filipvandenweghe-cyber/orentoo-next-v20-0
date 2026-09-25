import math
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


# Hard ceiling on nesting depth -- protects against accidental infinite loops
# even if the circular-reference guard somehow fails.
_MAX_SET_DEPTH = 10


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # -- Set pricing mode (delegated from product template) --------------------

    set_pricing_mode = fields.Selection(
        related='product_id.product_tmpl_id.set_pricing_mode',
        string='Set Pricing Mode',
    )

    # -- Set identity ----------------------------------------------------------

    is_set = fields.Boolean(
        string='Is Rental Set',
        default=False,
        help=(
            'This line is a Rental Set parent.  It may also have '
            'is_set_component=True when it is a nested set inside another set.'
        ),
    )
    is_set_component = fields.Boolean(
        string='Is Set Component',
        default=False,
        help='This line was generated as an internal component of a Rental Set.',
    )

    # -- Parent / child hierarchy (self-referential on sale.order.line) --------

    set_parent_line_id = fields.Many2one(
        comodel_name='sale.order.line',
        string='Set Parent Line',
        ondelete='cascade',
        index=True,
        help='The set line (or nested set line) this component belongs to.',
    )
    set_child_line_ids = fields.One2many(
        comodel_name='sale.order.line',
        inverse_name='set_parent_line_id',
        string='Set Component Lines',
        help='Component lines that expand from this set line.',
    )

    # -- Visibility & ordering -------------------------------------------------

    visible_to_customer = fields.Boolean(
        string='Visible to Customer',
        default=True,
        help=(
            'When unchecked, this component line is hidden on customer-facing '
            'documents (quotations, order confirmations, invoices).'
        ),
    )
    set_level = fields.Integer(
        string='Set Level',
        default=0,
        help=(
            'Hierarchy depth: 0 = top-level set line, '
            '1 = direct component, 2 = nested sub-component, \u2026'
        ),
    )
    set_sequence_path = fields.Char(
        string='Set Sequence Path',
        help=(
            'Dot-separated, zero-padded sequence path for hierarchical ordering, '
            'e.g. "001.002.001".  Empty on the top-level set line.'
        ),
    )

    # Visual hierarchy indicator shown in the sale order internal list view.
    set_indent_label = fields.Char(
        string=' ',
        compute='_compute_set_indent_label',
        help='Box-drawing prefix that visualises set nesting depth in the list.',
    )

    # Stored client-side collapse state.
    set_components_folded = fields.Boolean(
        string='Components Folded',
        default=False,
        help=(
            'When True the JS renderer hides all component lines that descend '
            'from this Rental Set line.  Toggle via the chevron in the list.'
        ),
    )

    # Per-unit base quantity from rental.set.component, stored at expansion
    # time so that child quantities can be rescaled without re-reading the
    # product template definition.
    set_component_qty = fields.Float(
        string='Component Base Qty',
        digits='Product Unit of Measure',
        help=(
            'Base quantity (per 1 set unit) from the Rental Set component '
            'definition.  Used to rescale the child line when the parent '
            'set quantity changes.'
        ),
    )

    # -- Allocated price (internal only, never affects order totals) -----------

    set_allocated_price = fields.Float(
        string='Allocated Price',
        digits='Product Price',
        compute='_compute_set_allocated_price',
        store=True,
        readonly=False,
        help=(
            'Internal allocated price per unit.  For lines that are not set '
            'components this equals the line price.  For components within a '
            'fixed-price Rental Set the parent set price is distributed '
            'proportionally based on normal product prices.'
        ),
    )

    # -- Set availability (how many complete sets can be rented) ---------------

    set_availability = fields.Float(
        string='Set Availability',
        compute='_compute_set_availability',
        digits='Product Unit of Measure',
        help=(
            'How many complete Rental Sets can be fulfilled for the rental '
            'period.  Equals the minimum of '
            'available(component) / required_qty_per_set across all components.'
        ),
    )

    # -- Computed fields -------------------------------------------------------

    @api.depends('price_unit', 'product_uom_qty', 'is_set_component')
    def _compute_set_allocated_price(self):
        """Default allocated price = line total (price × qty).

        For non-component lines (regular lines and set parents) the allocated
        price equals price_unit × product_uom_qty.  For set components the
        value is managed by ``_allocate_fixed_prices`` / ``_allocate_sum_prices``
        and kept unchanged here.
        """
        for line in self:
            if line.is_set_component:
                # Preserve the value set by _allocate_fixed_prices / _allocate_sum_prices
                line.set_allocated_price = line.set_allocated_price
            else:
                line.set_allocated_price = line.price_unit * line.product_uom_qty

    @api.depends('is_set', 'is_set_component', 'set_level')
    def _compute_set_indent_label(self):
        for line in self:
            if not line.is_set and not line.is_set_component:
                line.set_indent_label = ''
            elif line.is_set and not line.is_set_component:
                line.set_indent_label = '\u25b6'
            else:
                indent = '\u00a0\u00a0' * max(line.set_level - 1, 0)
                connector = '\u2514\u2500'
                line.set_indent_label = (
                    f'{indent}{connector}\u25b6' if line.is_set else f'{indent}{connector}'
                )

    # -- Aggregate order demand for stock indicator (RS12) -------------------------

    order_product_demand = fields.Float(
        string='Order Product Demand',
        compute='_compute_order_product_demand',
        digits='Product Unit of Measure',
        help=(
            'Total quantity demanded for the same product across ALL lines '
            'on this order.  Used to determine if aggregate demand exceeds '
            'available stock (red indicator when demand > available).'
        ),
    )

    all_warehouse_available = fields.Float(
        string='Available All Warehouses',
        compute='_compute_all_warehouse_available',
        digits='Product Unit of Measure',
        help=(
            'Total available stock for this product across ALL warehouses, '
            'minus this order\'s own demand if confirmed.  Shown in the '
            'rental popover when the product exists in multiple warehouses.'
        ),
    )
    all_warehouse_count = fields.Integer(
        string='Warehouse Count',
        compute='_compute_all_warehouse_available',
    )

    # ── Rental availability breakdown (pop-up) ──────────────────────────
    # Period-aware, auditable split shown in the rental availability
    # pop-up so the availability number explains itself.  (RAV-05, RAV-13)
    rental_reserved_self = fields.Float(
        string='Reserved by this order',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
    )
    rental_reserved_other = fields.Float(
        string='Reserved by other orders',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
    )
    rental_in_repair = fields.Float(
        string='In Repair',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
    )
    rental_pickable = fields.Float(
        string='Available to this order',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
        help='Net quantity available to this order at the picking warehouse.',
    )
    rental_total_stock = fields.Float(
        string='Total Stock',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
        help='Real units owned right now = current on-hand across the '
             "warehouse's internal locations plus the internal rental "
             '(at customer) location. Conserved and stable.',
    )
    rental_onhand_json = fields.Json(
        string='On-hand by Location',
        compute='_compute_rental_breakdown',
    )
    rental_repair_installed = fields.Boolean(
        string='Repair Module Installed',
        compute='_compute_rental_breakdown',
    )
    rental_show_stock_locations = fields.Boolean(
        related='company_id.rental_show_stock_locations',
        string='Show Physical Stock in Availability Pop-up',
    )
    rental_flag_options = fields.Boolean(
        related='company_id.rental_flag_options',
        string='Warn About Stock On Option',
    )
    rental_on_option_other = fields.Float(
        string='On option by other orders',
        compute='_compute_rental_breakdown',
        digits='Product Unit of Measure',
        help='Quantity held on option by OTHER, unconfirmed orders '
             '(quotations still valid) whose rental period overlaps this one. '
             'A soft hold — informational only.',
    )
    rental_on_option_until = fields.Char(
        string='Options Valid Until',
        compute='_compute_rental_breakdown',
        help='Earliest validity (option) date among the overlapping '
             'unconfirmed orders holding this product on option.',
    )

    def get_rental_warehouse_availability(self):
        """Availability of this line's product for its rental period, per
        warehouse of the order's company.

        Lazily fetched by the availability pop-up (only when it is opened).
        Returns ``[]`` — so the pop-up shows no extra section — for
        non-rental lines, rental sets (sourced per component, not per
        warehouse), non-storable products, and single-warehouse companies.
        Fully-empty warehouses are skipped; the order's own warehouse is
        always included.
        """
        self.ensure_one()
        order = self.order_id
        product = self.product_id
        if not product or not self.is_rental or self.is_set:
            return []
        if not product.is_storable:
            return []
        company = order.company_id or self.env.company
        warehouses = self.env['stock.warehouse'].search(
            [('company_id', '=', company.id)])
        if len(warehouses) < 2:
            return []

        from_date = getattr(order, 'rental_start_date', None) \
            or self.start_date or fields.Datetime.now()
        to_date = getattr(order, 'rental_return_date', None) \
            or self.return_date or from_date
        current_wh = order.warehouse_id

        rows = []
        for wh in warehouses:
            is_current = wh == current_wh
            available = product._rental_available_qty(
                from_date, to_date, warehouse=wh,
                ignored_soline_id=self.id if is_current else False,
                company=company,
            )
            if not is_current:
                onhand = product._rental_warehouse_onhand(wh)
                if available <= 0 and onhand <= 0:
                    continue  # fully empty here — skip to avoid clutter
            rows.append({
                'warehouse_id': wh.id,
                'name': wh.display_name,
                'available': available,
                'is_current': is_current,
            })
        rows.sort(key=lambda r: (not r['is_current'], r['name']))
        return rows

    @api.depends('product_id', 'order_id.state', 'product_uom_qty')
    def _compute_all_warehouse_available(self):
        """Compute total stock across all warehouses for this product."""
        warehouses = self.env['stock.warehouse'].search([])
        # Cache per product to avoid N+1
        product_cache = {}  # product_id -> (total, wh_count)
        for line in self:
            if not line.product_id or not line.product_id.is_storable:
                line.all_warehouse_available = 0
                line.all_warehouse_count = 0
                continue

            pid = line.product_id.id
            if pid not in product_cache:
                total = 0
                wh_count = 0
                for wh in warehouses:
                    qty = line.product_id.with_context(
                        warehouse_id=wh.id,
                    ).qty_available
                    if qty > 0:
                        wh_count += 1
                    total += qty
                product_cache[pid] = (total, wh_count)

            line.all_warehouse_available = product_cache[pid][0]
            line.all_warehouse_count = product_cache[pid][1]

    @api.depends('order_id.order_line.product_id', 'order_id.order_line.product_uom_qty')
    def _compute_order_product_demand(self):
        """Compute total demand for this product across all lines on the order.

        RS12: when multiple lines demand the same product, the stock
        indicator must compare total demand (not per-line demand) against
        available stock.
        """
        # Pre-group by (order, product) in a single pass to avoid O(n²)
        demand_map = {}  # (order_id, product_id) -> total_qty
        for line in self:
            if not line.product_id or not line.order_id:
                continue
            key = (line.order_id.id, line.product_id.id)
            if key not in demand_map:
                demand_map[key] = sum(
                    ol.product_uom_qty
                    for ol in line.order_id.order_line
                    if ol.product_id == line.product_id
                    and not ol.display_type
                )

        for line in self:
            if not line.product_id or not line.order_id:
                line.order_product_demand = line.product_uom_qty
                continue
            line.order_product_demand = demand_map.get(
                (line.order_id.id, line.product_id.id),
                line.product_uom_qty,
            )

    @api.depends('product_id', 'product_uom_qty', 'is_rental',
                 'reservation_begin', 'return_date')
    def _compute_qty_at_date(self):
        """Override: forecast-based availability + equalise.  (RS12)

        Uses the same data as the forecast report to compute availability:
        the minimum forecasted stock level during the rental period.

        For confirmed orders, the forecast already includes the order's
        own outgoing moves (reducing stock).  We add those back to get
        "how much CAN this order use" — matching the forecast report.

        For draft orders, no moves exist, so the forecast is the current
        stock level minus other orders' moves during the period.

        Finally, equalise: when multiple lines on the same order use the
        same product, all show the same availability.  The red/green icon
        uses order_product_demand to check aggregate demand.
        """
        super()._compute_qty_at_date()

        # ── Step 1: Forecast-based availability ──
        # Cache per (order, product) to avoid recomputing for siblings
        cache = {}  # (order_id, product_id, wh_id) -> available
        for line in self:
            if not line.product_id or not line.product_id.is_storable:
                continue
            if not line.is_rental:
                continue
            if not getattr(line.order_id, 'is_rental_order', False):
                continue

            wh_id = line.order_id.warehouse_id.id
            key = (line.order_id.id, line.product_id.id, wh_id)
            if key in cache:
                line.free_qty_today = cache[key]
                line.virtual_available_at_date = cache[key]
                continue

            from_date = getattr(line.order_id, 'rental_start_date', None) or \
                        line.start_date or fields.Datetime.now()
            to_date = getattr(line.order_id, 'rental_return_date', None) or \
                      line.return_date or from_date

            available = self._compute_forecast_availability(
                line.product_id, line.order_id, wh_id, from_date, to_date,
                ignored_soline_id=line.id,
            )
            cache[key] = available
            line.free_qty_today = available
            line.virtual_available_at_date = available

        # ── Step 2: Equalise across sibling lines ──
        groups = {}
        for line in self:
            if not line.product_id or not line.order_id:
                continue
            if not line.product_id.is_storable:
                continue
            key = (line.order_id.id, line.product_id.id)
            groups.setdefault(key, []).append(line)

        for (order_id, product_id), lines_in_self in groups.items():
            if len(lines_in_self) < 2:
                line = lines_in_self[0]
                all_order_lines = line.order_id.order_line.filtered(
                    lambda ol: ol.product_id.id == product_id
                    and not ol.display_type
                )
                if len(all_order_lines) < 2:
                    continue

            max_free = max(l.free_qty_today for l in lines_in_self)
            max_virtual = max(l.virtual_available_at_date for l in lines_in_self)
            for line in lines_in_self:
                line.free_qty_today = max_free
                line.virtual_available_at_date = max_virtual

    def _rental_effective_reserved_qty(self):
        """Quantity this rental line actually commits over its period.

        Normally the ordered quantity.  But once the outbound delivery is
        **closed** (no outgoing move still open) having shipped fewer units
        than ordered — e.g. picked 2 of 4 with no backorder — the un-shipped
        remainder will never be picked up, so only what actually reached the
        customer stays committed.  The remainder is then released to other
        orders on the SAME warehouse (reservations are warehouse-scoped).

        Robust across the flow:

        * multi-step-safe — "what reached the customer" is read from the DONE
          outgoing moves whose destination is the rental location (only the
          final ship leg; pick/pack legs target internal locations), so it is
          never inflated by summing legs;
        * still fully committed while a delivery is in progress — any open
          outgoing move (incl. a backorder) means the rest is still coming,
          so the full ordered quantity is reserved;
        * transfers-OFF-safe — with rental pickings disabled there are no
          moves to reconcile, so it falls back to the ordered quantity
          (native behaviour).
        """
        self.ensure_one()
        ordered = self.product_uom_qty
        if not self.is_rental or not self._are_rental_pickings_enabled():
            return ordered
        outgoing = self.move_ids.filtered(
            lambda m: m.picking_id.picking_type_code == 'outgoing')
        if not outgoing:
            return ordered
        # A delivery still in progress (any non-final/open outgoing move,
        # including a backorder) keeps the full ordered quantity committed.
        if any(m.state not in ('done', 'cancel') for m in outgoing):
            return ordered
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return self.qty_delivered
        # Delivery closed: commit exactly what physically reached the rental
        # location.  Units that have since LEFT it again — returned or
        # scrapped — are released by the ``qty_returned`` timing block in
        # ``_get_rented_quantities``.  Do NOT subtract the scraps here as
        # well: native ``qty_returned`` already counts them (see the custody
        # block comment), so doing both released every written-off unit twice
        # and could drive the commitment negative.
        return sum(
            m.quantity for m in outgoing
            if m.state == 'done' and m.location_dest_id == rental_loc
        )

    # ── custody at the customer (single source of truth) ────────────────────
    #
    # CAREFUL: native ``qty_returned`` is incremented by
    # ``sale_stock_renting.stock_move._action_done`` for **every** done move
    # leaving ``rental_loc`` — which includes the lost/broken **scrap** moves.
    # So ``qty_returned`` already means "left the customer", returns AND
    # write-offs.  Adding ``_rental_scrapped_qty()`` on top of it double-counts
    # the written-off units.  Use ``_rental_custody_outstanding_qty()`` instead
    # of hand-rolling the arithmetic.

    def _rental_custody_out_qty(self):
        """Units of this line that physically reached the at-customer location.

        Multi-step-safe: only the final ship leg targets ``rental_loc``, so
        summing legs cannot inflate it.
        """
        self.ensure_one()
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return 0.0
        return sum(
            m.quantity for m in self.move_ids
            if m.state == 'done' and m.location_dest_id == rental_loc
        )

    def _rental_custody_back_qty(self):
        """Units of this line that have left the at-customer location again —
        returns to the warehouse **and** lost/broken scraps.

        Deliberately the same set of moves native ``qty_returned`` counts.
        """
        self.ensure_one()
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return 0.0
        return sum(
            m.quantity for m in self.move_ids
            if m.state == 'done' and m.location_id == rental_loc
        )

    def _rental_custody_outstanding_qty(self):
        """Units of this line the customer still physically holds."""
        self.ensure_one()
        return max(
            self._rental_custody_out_qty() - self._rental_custody_back_qty(),
            0.0,
        )

    def _rental_scrapped_qty(self):
        """Quantity of this line's units scrapped FROM the rental
        (at-customer) location — the lost/broken write-offs.

        Such units are gone: no longer out on rent and no longer expected
        back.  NOTE: they are also counted by native ``qty_returned`` (see the
        block comment above), so never add the two together.
        """
        self.ensure_one()
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return 0.0
        return sum(
            m.quantity for m in self.move_ids
            # Odoo 20: stock.move.scrap_id was replaced by the is_scrap flag.
            if m.state == 'done' and m.is_scrap
            and m.location_id == rental_loc
        )

    def _rental_effective_return_date(self):
        """Datetime by which this line's units are actually expected back in
        the warehouse — the scheduled date of the **first inbound (return)
        operation** that leaves the customer/rental location, when such a
        pending operation exists; otherwise the order's declared
        ``return_date``.

        Grounding the return on the operation (not the declared date) is what
        keeps availability honest, both ways:

        * a unit out at a customer whose return receipt is scheduled *after*
          the order's stated return_date stays unavailable until that receipt
          actually brings it back (real case S00338: order says 09-05, return
          receipt scheduled 09-11).  An overdue pending return (scheduled in
          the past but not yet done) still holds the units, so the pending date
          is floored at ``now``.
        * a unit whose return has already **completed** is released on the
          ACTUAL return operation date, even when that is *earlier* than the
          declared return_date (real case S00705: returned 09-03 while the
          order still says 09-04, so a later window must not treat it as out).

        Transfers-OFF-safe: with rental pickings disabled there are no return
        moves, so it falls back to the declared ``return_date`` (native
        behaviour).
        """
        self.ensure_one()
        declared = self.return_date
        if not self.is_rental or not self._are_rental_pickings_enabled():
            return declared
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return declared
        # Return legs = moves leaving the rental (at-customer) location back
        # into the warehouse.
        return_moves = self.move_ids.filtered(
            lambda m: m.state != 'cancel' and m.location_id == rental_loc)
        pending = return_moves.filtered(lambda m: m.state != 'done')
        if pending:
            op_date = min(pending.mapped('date'))
            if op_date:
                # Overdue but not done → units still out at least until now.
                now = fields.Datetime.now()
                return op_date if op_date >= now else now
            return declared
        done = return_moves.filtered(lambda m: m.state == 'done')
        outstanding = self._rental_custody_outstanding_qty()
        if done and outstanding <= 0:
            # Everything is settled (returned and/or written off): release on
            # the ACTUAL last return operation date, even when it is earlier
            # than the declared return_date (real case S00705 — returned a day
            # before its declared date, so it must not stay reserved for a
            # later window that the declared date would still overlap).
            op_date = max(done.mapped('date'))
            if op_date:
                return op_date
        if outstanding > 0:
            # A PARTIAL return with no further return operation scheduled.
            # Releasing on the last completed return would hand back units the
            # customer still holds, and — when the declared pickup is in the
            # future — invert the effective window so the line reserves
            # OUTSIDE its rental and nothing DURING it.  The outstanding units
            # stay committed until at least now, and until the declared return
            # date when that is later.
            now = fields.Datetime.now()
            return max(declared, now) if declared else now
        return declared

    def _rental_effective_pickup_date(self):
        """Datetime from which this line's units are actually committed at the
        warehouse — the scheduled date of the **first outbound (pickup /
        delivery) operation** that pulls the goods toward the customer, when it
        is earlier than the order's declared ``reservation_begin``; otherwise
        the declared ``reservation_begin``.

        Grounding the pickup on the operation (not the declared date) keeps
        availability honest when a delivery picking is scheduled before the
        rental formally starts: the units physically leave stock on the picking
        date and are no longer rentable to others from then on — even when the
        order's declared start is later (real case S00708: rental starts 09-08
        but the delivery picking is scheduled 09-07).

        Symmetric to ``_rental_effective_return_date``: the return anchors on
        the first leg that *leaves* the rental location; the pickup anchors on
        the first leg that *heads out* of the warehouse toward it.  We take the
        earlier of the operation date and the declared start, so an
        early-scheduled picking commits the unit sooner while a normal or late
        picking never releases it before the declared start.

        Transfers-OFF-safe: with rental pickings disabled there are no outbound
        moves, so it falls back to the declared ``reservation_begin`` (native
        behaviour).
        """
        self.ensure_one()
        declared = self.reservation_begin
        if not self.is_rental or not self._are_rental_pickings_enabled():
            return declared
        rental_loc = self.company_id.rental_loc_id
        if not rental_loc:
            return declared
        # Outbound (pickup/delivery) legs head OUT of the warehouse toward the
        # customer.  Exclude the return legs — the first leaves the rental
        # location (``location_id == rental_loc``) and the receipt is an
        # incoming-type picking — so only the delivery chain is considered.
        # Anchoring on the earliest such leg matches the return side, which
        # anchors on the first leg leaving the customer (not the final putaway).
        pending_pickup = self.move_ids.filtered(
            lambda m: m.state not in ('done', 'cancel')
            and m.picking_id.picking_type_code != 'incoming'
            and m.location_id != rental_loc)
        if not pending_pickup:
            return declared
        op_date = min(pending_pickup.mapped('date'))
        if not op_date:
            return declared
        if not declared:
            return op_date
        return min(op_date, declared)

    def _get_rented_quantities(self, mandatory_dates):
        """Override of ``sale_stock_renting``: reserve each line's EFFECTIVE
        committed quantity instead of the raw ordered quantity, so a rental
        whose delivery is closed short (no backorder) releases the un-shipped
        remainder to other orders on the same warehouse; commit the units from
        the **pickup operation** date rather than the order's declared
        ``reservation_begin`` (see ``_rental_effective_pickup_date``), so a unit
        whose delivery picking is scheduled before the rental formally starts is
        already unavailable to others; and release the commitment on the
        **return operation** date rather than the order's declared
        ``return_date`` (see ``_rental_effective_return_date``), so a unit still
        out at a customer whose return is scheduled later stays reserved until
        it is physically back.

        Identical to native otherwise — the early pickup / early return
        timing adjustments are unchanged.  (See
        ``_rental_effective_reserved_qty``.)
        """
        if not self:
            return defaultdict(float), sorted(set(mandatory_dates))
        self.product_id.ensure_one()
        rented_quantities = defaultdict(float)
        now = fields.Datetime.now()
        for so_line in self.filtered('is_rental'):
            effective = so_line._rental_effective_reserved_qty()
            eff_pickup = so_line._rental_effective_pickup_date() \
                or so_line.reservation_begin
            eff_return = so_line._rental_effective_return_date() \
                or so_line.return_date
            rented_quantities[eff_pickup] += effective
            rented_quantities[eff_return] -= effective
            # Early pickups: units already out before the expected pickup.
            if eff_pickup > now and so_line.qty_delivered > 0:
                rented_quantities[now] += so_line.qty_delivered
                rented_quantities[eff_pickup] -= \
                    so_line.qty_delivered
            # Early returns: units already back before the expected return.
            if eff_return > now and so_line.qty_returned > 0:
                rented_quantities[now] -= so_line.qty_returned
                rented_quantities[eff_return] += so_line.qty_returned
        key_dates = sorted(
            set(rented_quantities.keys()) | set(mandatory_dates))
        return rented_quantities, key_dates

    def _rental_physical_total(self, product, order, wh):
        """Real units owned right now for this warehouse pool.

        Thin wrapper around the canonical, order-independent
        ``product.product._rental_physical_total`` (single source of truth).
        The order only supplies the company that owns the rental location.
        """
        return product._rental_physical_total(
            warehouse=wh,
            company=order.company_id if order else False,
        )

    def _compute_forecast_availability(self, product, order, wh_id,
                                        from_date, to_date,
                                        ignored_soline_id=False):
        """Availability to THIS order for the rental period (Option A):

            Available = max(Total physical stock
                            − reserved by OTHER orders (period-aware)
                            − in repair, 0)

        Delegates to the canonical ``product.product._rental_available_qty``
        so the sale order, the rental-set component check and the
        multi-channel rental flow all share ONE definition of availability.
        Passing ``ignored_soline_id`` keeps an order from subtracting its own
        reserved/picked units from itself.
        """
        wh = order.warehouse_id if order else self.env['stock.warehouse'].browse(wh_id)
        return product._rental_available_qty(
            from_date, to_date, warehouse=wh,
            ignored_soline_id=ignored_soline_id,
            company=order.company_id if order else False,
        )

    @api.depends('product_id', 'product_uom_qty', 'is_rental',
                 'reservation_begin', 'return_date', 'state',
                 'free_qty_today')
    def _compute_rental_breakdown(self):  # noqa: C901
        """Compute the auditable availability breakdown for the pop-up:
        per-internal-location on-hand, reserved by this order, reserved by
        other orders (period-aware), in-repair, and the net pickable figure.
        (RAV-05, RAV-13)
        """
        repair_installed = 'repair.order' in self.env
        for line in self:
            line.rental_reserved_self = 0.0
            line.rental_reserved_other = 0.0
            line.rental_in_repair = 0.0
            line.rental_pickable = 0.0
            line.rental_total_stock = 0.0
            line.rental_onhand_json = False
            line.rental_repair_installed = repair_installed
            line.rental_on_option_other = 0.0
            line.rental_on_option_until = False

            product = line.product_id
            order = line.order_id
            if not product or not product.is_storable or not line.is_rental:
                continue
            if not getattr(order, 'is_rental_order', False):
                continue

            wh = order.warehouse_id
            wh_id = wh.id if wh else False
            from_date = getattr(order, 'rental_start_date', None) or \
                line.start_date or fields.Datetime.now()
            to_date = getattr(order, 'rental_return_date', None) or \
                line.return_date or from_date

            # Reserved by OTHER orders — period-aware rental commitment,
            # excluding this line (same basis as native availability).
            reserved_other = 0.0
            if product.rent_periodicity and hasattr(product, '_get_unavailable_qty'):
                reserved_other = product._get_unavailable_qty(
                    from_date, to_date,
                    ignored_soline_id=line.id, warehouse_id=wh_id,
                )

            # Reserved by THIS order — its own outgoing rental moves for
            # this product (only meaningful once confirmed / reserved).
            reserved_self = 0.0
            if order.state == 'sale':
                own_moves = order.picking_ids.move_ids.filtered(
                    lambda m: m.product_id == product
                    and m.picking_id.picking_type_code == 'outgoing'
                    and m.state not in ('done', 'cancel')
                )
                reserved_self = sum(own_moves.mapped('product_uom_qty'))

            in_repair = product._get_repair_unavailable_qty(
                from_date, to_date, warehouse_id=wh_id,
            )

            line.rental_reserved_self = reserved_self
            line.rental_reserved_other = reserved_other
            line.rental_in_repair = in_repair

            # On option by OTHER (unconfirmed) orders — a soft hold shown for
            # awareness; it never changes the committed availability above.
            # The whole current order is excluded, so it never counts itself.
            if order.company_id.rental_flag_options and product.rent_periodicity \
                    and hasattr(product, '_get_on_option_lines'):
                opt_lines = product._get_on_option_lines(
                    from_date, to_date, ignored_order_id=order.id,
                    warehouse_id=wh_id)
                if opt_lines:
                    rq, kd = opt_lines._get_rented_quantities(
                        [from_date, to_date])
                    line.rental_on_option_other = product._reserved_peak(
                        rq, kd, from_date, to_date)
                    valids = [d for d in opt_lines.order_id.mapped(
                        'validity_date') if d]
                    line.rental_on_option_until = (
                        min(valids).isoformat() if valids else False)
            # free_qty_today is already repair-aware (see forecast) and is
            # the time-based "available to this order" figure.
            line.rental_pickable = line.free_qty_today

            # Physical stock is a SEPARATE, point-in-time lens (Option A):
            # Total stock = real units owned = current on-hand across all the
            # warehouse's internal locations PLUS the internal rental
            # ("at customer") location.  It is conserved — picking just moves
            # a unit from Stock to At customer, Total unchanged — so it is a
            # stable anchor and the partition always sums to it (no phantom).
            # It is intentionally NOT derived from the time-based forecast.
            partition, total = self._rental_stock_partition(
                product, order, wh, in_repair)
            line.rental_total_stock = total
            line.rental_onhand_json = partition or False

    def _rental_stock_partition(self, product, order, wh, in_repair):
        """Return ``(buckets, total)`` — the current physical distribution of
        this product and its real total owned.

        Buckets cover the warehouse internal locations (Input/QC/Stock…),
        **At customer** (the internal rental location) and **In repair**
        (carved out of the stock it physically sits in).  ``total`` is the sum
        of current on-hand across those locations, so the buckets always sum
        to Total with no reconciling remainder.  (RAV-14, Option A)
        """
        # Odoo 20 removed uom.uom.rounding: quantities are rounded with the
        # 'Product Unit' decimal precision, exposed as uom.compare().
        uom = product.uom_id
        wh_locs = self.env['stock.location']
        if wh and wh.view_location_id:
            wh_locs = self.env['stock.location'].search([
                ('id', 'child_of', wh.view_location_id.id),
                ('usage', 'in', ('internal', 'transit')),
            ])
        rental_loc = order.company_id.rental_loc_id
        loc_ids = list(wh_locs.ids)
        if rental_loc:
            loc_ids.append(rental_loc.id)

        onhand = {}
        for loc, qty in self.env['stock.quant']._read_group(
            [('product_id', '=', product.id),
             ('location_id', 'in', loc_ids)],
            ['location_id'], ['quantity:sum'],
        ):
            onhand[loc.id] = qty

        # Total owned = every unit sitting in one of these internal locations.
        total = sum(onhand.values())

        buckets = []
        # In repair is carved out of the warehouse stock it physically sits in
        # so it shows as its own bucket without inflating the location count.
        repair_left = in_repair
        for loc in wh_locs:
            qty = onhand.get(loc.id, 0.0)
            carve = min(max(qty, 0.0), repair_left)
            qty -= carve
            repair_left -= carve
            if uom.compare(qty, 0.0) > 0:
                buckets.append({'location': loc.display_name, 'qty': qty})

        if uom.compare(in_repair, 0.0) > 0:
            buckets.append({'location': _('In repair'), 'qty': in_repair})

        if rental_loc:
            at_customer = onhand.get(rental_loc.id, 0.0)
            if uom.compare(at_customer, 0.0) > 0:
                buckets.append({'location': _('At customer'),
                                'qty': at_customer})

        return buckets, total

    # -- Set composition permission check ----------------------------------------

    def _check_set_edit_permission(self, operation='edit'):
        """Verify the current user may modify set composition on these lines.

        Rules
        -----
        - System / superuser: always allowed (automated expansion, crons, …).
        - Picker (stock.group_stock_user only): never allowed to modify set
          composition.
        - Sales Manager or Warehouse Manager: allowed **before** any related
          picking is validated (state = 'done').  After validation the set is
          locked.

        :param operation: human-readable label ('edit', 'delete', 'add')
                          used in the error message.
        :raises UserError: when the operation is not permitted.
        """
        if self.env.su:
            return
        if self.env.context.get('rental_set_expanding'):
            return

        user = self.env.user
        is_sale_manager = user.has_group('sales_team.group_sale_manager')
        is_stock_manager = user.has_group('stock.group_stock_manager')

        # Pickers (stock users who are NOT managers) may never touch set
        # composition.
        if not is_sale_manager and not is_stock_manager:
            raise ValidationError(_(
                "You do not have permission to %(op)s Rental Set components. "
                "Only Sales Managers and Warehouse Managers may modify set "
                "composition.",
                op=operation,
            ))

        # Managers may only edit before related pickings are validated.
        set_lines = self.filtered('is_set_component')
        for line in set_lines:
            top = line._get_top_set_parent()
            target = top or line
            # Collect all stock moves for the set parent and its children
            all_lines = target | target.set_child_line_ids
            if hasattr(all_lines, 'move_ids'):
                done_pickings = all_lines.move_ids.picking_id.filtered(
                    lambda p: p.state == 'done'
                )
                if done_pickings:
                    raise ValidationError(_(
                        "Cannot %(op)s components of Rental Set "
                        "'%(set)s': picking %(pick)s has already been "
                        "validated.  A manager must reopen / correct the "
                        "picking before the set can be modified.",
                        op=operation,
                        set=target.product_id.display_name,
                        pick=done_pickings[0].name,
                    ))

    # -- ORM overrides ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if self.env.context.get('rental_set_expanding'):
            return lines
        if self.env.context.get('rental_set_copying'):
            # Duplicating an order: Odoo already copied the set parent
            # AND all its component lines.  Do not expand again.  (RS11)
            return lines
        lines.filtered(
            lambda l: not l.is_set_component
                      and l.product_id.product_tmpl_id.is_rental_set
        )._expand_rental_set()
        return lines

    def _collect_all_descendants(self):
        """Return all descendant component lines (recursively) of the given
        set parent lines, including intermediate nested sets.
        """
        result = self.env['sale.order.line']
        for line in self:
            for child in line.set_child_line_ids:
                result |= child
                if child.is_set and child.set_child_line_ids:
                    result |= child._collect_all_descendants()
        return result

    def unlink(self):
        """Check permissions, cancel moves, reallocate, and sync stock.

        When a set parent is deleted, all its descendant component lines
        (including nested sub-components) are explicitly included in the
        deletion so that their stock moves are properly cancelled.

        For confirmed orders Odoo forbids deleting lines, so we cancel the
        stock moves and zero the quantity instead (effectively removing the
        component without breaking Odoo's tracking constraints).
        """
        # When a set parent is being deleted, include all its descendants
        set_parents_being_deleted = self.filtered(
            lambda l: l.is_set and l.set_child_line_ids
        )
        if set_parents_being_deleted:
            descendants = set_parents_being_deleted._collect_all_descendants()
            # Add descendants that are not already in the deletion set
            self |= descendants

        components_to_delete = self.filtered('is_set_component')
        if components_to_delete:
            components_to_delete._check_set_edit_permission(operation='delete')

        # Collect top-level set parents that survive (for reallocation)
        fixed_parents = self.env['sale.order.line']
        sum_parents = self.env['sale.order.line']
        set_parents_for_sync = self.env['sale.order.line']
        for line in components_to_delete:
            top = line._get_top_set_parent()
            if top and top not in self:
                set_parents_for_sync |= top
                if top.set_pricing_mode == 'fixed':
                    fixed_parents |= top
                elif top.set_pricing_mode == 'sum':
                    sum_parents |= top

        # For confirmed orders, zero-out instead of deleting
        confirmed_components = components_to_delete.filtered(
            lambda l: l.order_id.state == 'sale'
        )
        if confirmed_components:
            for line in confirmed_components:
                stale = line.move_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                )
                if stale:
                    stale._action_cancel()
            confirmed_components.with_context(rental_set_expanding=True).write({
                'product_uom_qty': 0,
                'set_component_qty': 0,
            })
            # Remove from the "actually unlink" set
            self -= confirmed_components

        # Cancel stock moves on remaining lines being deleted
        lines_with_moves = self.filtered(
            lambda l: l.is_set_component or (l.is_set and not l.is_set_component)
        )
        for line in lines_with_moves:
            stale = line.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
            )
            if stale:
                stale._action_cancel()

        result = super().unlink()

        for parent in fixed_parents.exists():
            parent._allocate_fixed_prices()

        for parent in sum_parents.exists():
            parent._recompute_set_price_from_components()
            parent._allocate_sum_prices()

        # Invalidate availability on surviving top-level parents
        (fixed_parents | sum_parents | set_parents_for_sync).exists()._invalidate_set_availability()

        # Re-sync stock for remaining components
        set_parents_for_sync.exists()._sync_set_stock_moves()

        return result

    def write(self, vals):
        if self.env.context.get('rental_set_expanding'):
            return super().write(vals)

        # -- Permission check for set-composition changes ----------------------
        set_composition_fields = {'product_id', 'product_uom_qty', 'is_set_component'}
        if set_composition_fields & set(vals):
            components = self.filtered('is_set_component')
            if components:
                components._check_set_edit_permission(operation='edit')

        # -- Guard: block manual price edits on set component lines ------------
        if 'price_unit' in vals:
            set_components = self.filtered('is_set_component')
            if set_components:
                # Let non-component lines write normally
                other_lines = self - set_components
                if other_lines:
                    other_lines.write(vals)

                # For components, ignore price_unit change and keep it at 0.
                comp_vals = {k: v for k, v in vals.items()
                             if k not in ('price_unit', 'technical_price_unit')}
                comp_vals['price_unit'] = 0.0
                comp_vals['technical_price_unit'] = 0.0
                super(SaleOrderLine, set_components).write(comp_vals)

                # The price change is silently ignored (component lines in a
                # Rental Set never carry a price — the set price lives on the
                # parent line only).  No chatter notification is posted.
                return True

        # Capture set-parent lines before mutation
        set_lines = (
            self.filtered('is_set')
            if 'product_id' in vals or 'product_uom_qty' in vals
            else self.browse()
        )

        # Capture old quantities before the write for ratio-based rescaling
        old_qtys = {line.id: line.product_uom_qty for line in set_lines}

        result = super().write(vals)

        if 'product_id' in vals:
            # Product changed on existing set lines
            for line in set_lines:
                line.set_child_line_ids.unlink()
                if line.product_id.product_tmpl_id.is_rental_set:
                    line._expand_rental_set()
                else:
                    line.with_context(rental_set_expanding=True).write({'is_set': False})

            # Lines that just became sets
            (self - set_lines).filtered(
                lambda l: not l.is_set_component
                          and l.product_id.product_tmpl_id.is_rental_set
            )._expand_rental_set()

            # If a component product was substituted, reallocate
            substituted_components = self.filtered('is_set_component')
            if substituted_components:
                for parent in substituted_components.mapped(lambda l: l._get_top_set_parent()):
                    if parent.set_pricing_mode == 'fixed':
                        parent._allocate_fixed_prices()
                    elif parent.set_pricing_mode == 'sum':
                        parent._recompute_set_price_from_components()
                        parent._allocate_sum_prices()
                        parent._reapply_set_derived_pricing()

            # Sync stock moves for changed lines
            self._sync_set_stock_moves()

            # Invalidate availability after component changes
            self._invalidate_set_availability()

        elif 'product_uom_qty' in vals:
            # Quantity changed -> rescale children by ratio new/old
            set_lines._sync_set_component_quantities(old_qtys)
            # Sync stock moves for quantity changes
            set_lines._sync_set_stock_moves()

            # Re-allocate prices since allocation depends on ordered qty
            for sline in set_lines.filtered('is_set'):
                if sline.set_pricing_mode == 'fixed':
                    sline._allocate_fixed_prices()
                elif sline.set_pricing_mode == 'sum':
                    sline._allocate_sum_prices()

            # If a component's qty was changed directly (not via set parent
            # rescaling), re-allocate prices on the parent set.
            # Business rule: when the user manually adjusts a component qty,
            # the allocated price must follow the actual order content.
            changed_components = self.filtered(
                lambda l: l.is_set_component and not l.is_set
            )
            if changed_components:
                parents_to_reallocate = changed_components.mapped(
                    lambda l: l._get_top_set_parent()
                )
                for parent in parents_to_reallocate:
                    # Keep the per-set base quantity in sync with a direct
                    # component qty edit.  In sum-of-components mode the parent
                    # price is derived from ``set_component_qty`` (the stable
                    # per-set base), while the allocated prices use the live
                    # ``product_uom_qty``.  If the base qty is not refreshed the
                    # two diverge: the parent price keeps reflecting the original
                    # set definition while the allocations follow the new order
                    # content.  Resyncing every component of the affected set
                    # (relative to its immediate set parent) keeps the parent
                    # price equal to the sum of the component allocations.
                    parent._resync_set_component_base_qty()
                    if parent.set_pricing_mode == 'fixed':
                        parent._allocate_fixed_prices()
                    elif parent.set_pricing_mode == 'sum':
                        parent._recompute_set_price_from_components()
                        parent._allocate_sum_prices()
                        parent._reapply_set_derived_pricing()

            # Invalidate availability after qty changes
            set_lines._invalidate_set_availability()
            changed_components._invalidate_set_availability()

        return result

    # -- Expansion entry point -------------------------------------------------

    def _expand_rental_set(self):
        """Mark each line as a Rental Set parent and recursively generate its
        component sub-tree, then apply pricing rules.
        """
        if not self:
            return

        self.with_context(rental_set_expanding=True).write({
            'is_set': True,
            'visible_to_customer': True,
            'set_level': 0,
            'set_sequence_path': False,
        })

        for line in self:
            ancestor_ids = frozenset([line.product_id.product_tmpl_id.id])
            line._expand_set_children(
                parent_qty=line.product_uom_qty,
                depth=0,
                ancestor_ids=ancestor_ids,
                parent_path='',
            )

        # After all children are created, apply pricing rules
        self._apply_set_pricing()

        # Invalidate availability so it recomputes from the new components
        self._invalidate_set_availability()

    # -- Recursive expansion helper --------------------------------------------

    def _expand_set_children(self, parent_qty, depth, ancestor_ids, parent_path):
        """Create child sale.order.lines for every component of this set line,
        then recurse into any component that is itself a Rental Set.
        """
        self.ensure_one()

        if depth >= _MAX_SET_DEPTH:
            raise ValidationError(_(
                "Rental Set '%(name)s' exceeds the maximum nesting depth of "
                "%(max)d levels.  Please simplify your set structure.",
                name=self.product_id.display_name,
                max=_MAX_SET_DEPTH,
            ))

        template = self.product_id.product_tmpl_id
        components = template.set_component_ids.filtered(
            lambda c: not c.parent_component_id
        ).sorted('sequence')

        for idx, component in enumerate(components, start=1):
            comp_tmpl = component.product_id.product_tmpl_id
            is_nested_set = comp_tmpl.is_rental_set

            if is_nested_set and comp_tmpl.id in ancestor_ids:
                raise ValidationError(_(
                    "Circular reference detected in Rental Set '%(set)s': "
                    "product '%(product)s' is already an ancestor in this "
                    "set structure.  Please remove the circular dependency "
                    "from the Rental Set component definition.",
                    set=template.display_name,
                    product=comp_tmpl.display_name,
                ))

            child_qty = component.quantity * parent_qty
            seq_path = f"{parent_path}.{idx:03d}" if parent_path else f"{idx:03d}"

            child = self.env['sale.order.line'].with_context(
                rental_set_expanding=True
            ).create({
                'order_id': self.order_id.id,
                'product_id': component.product_id.id,
                'product_uom_qty': child_qty,
                'sequence': self.sequence,
                'is_set_component': True,
                'is_set': is_nested_set,
                'visible_to_customer': False,
                'set_parent_line_id': self.id,
                'set_level': depth + 1,
                'set_component_qty': component.quantity,
                'set_sequence_path': seq_path,
            })

            if is_nested_set:
                child._expand_set_children(
                    parent_qty=child_qty,
                    depth=depth + 1,
                    ancestor_ids=ancestor_ids | {comp_tmpl.id},
                    parent_path=seq_path,
                )

    # -- eCommerce: hide components from cart/checkout ----------------------------

    def _show_in_cart(self):
        """Hide Rental Set component lines from the website cart.

        Only the parent set line is shown.  This method is defined by
        website_sale and only called when that module is installed.
        """
        if self.is_set_component and not self.visible_to_customer:
            return False
        if hasattr(super(), '_show_in_cart'):
            return super()._show_in_cart()
        return True

    # -- Availability widget override: show for set parents ----------------------

    @api.depends('product_uom_qty', 'qty_delivered', 'state', 'move_ids')
    def _compute_qty_to_deliver(self):
        """Hide the standard stock widget for all Rental Set lines.

        Set lines (both top-level and nested) use a custom availability
        widget instead.  The standard widget would show red because the
        set product has no stock of its own (stock is on components).
        """
        super()._compute_qty_to_deliver()
        for line in self:
            if line.is_set:
                line.display_qty_widget = False

    # -- Stock rule override: skip set parents -----------------------------------

    def _action_launch_stock_rule(self, **kwargs):
        """Exclude all Rental Set lines from procurement.

        Set lines (both top-level and nested) are virtual grouping
        products — only their leaf component lines should generate
        stock moves and be picked/shipped.

        After component moves are created, we insert display-only "header"
        moves for each set parent so the picking form can show them as
        collapsible group headers.  These header moves carry no real
        inventory impact and are auto-completed when validated.
        """
        set_parents = self.filtered(lambda l: l.is_set)
        lines = self - set_parents
        result = super(SaleOrderLine, lines)._action_launch_stock_rule(**kwargs)

        # Create display-only header moves for set parents in each picking
        if set_parents:
            self._create_set_parent_header_moves(set_parents)

        return result

    def _create_set_parent_header_moves(self, set_parents):
        """Create header stock.move records for set parent lines.

        Business requirement:
        On all pickings in the **outbound sale chain** (Pick → Pack → Ship
        in multi-step flows) the order picker must see the set parent
        product as a collapsible header row above its component moves,
        identical to the rental order form.

        Return (incoming) pickings do NOT get header moves because the
        warehouse has no control over how items are returned.

        Outbound chain = picking has a sale_id and is NOT a return
        (return_id is not set).

        These moves are non-storable display entries: they bypass
        reservation, have no inventory impact, and are only created when
        the component moves have a picking (rental pickings enabled).
        """
        StockMove = self.env['stock.move']
        # Process top-level sets first, then nested — ensures parent
        # headers exist before nested headers reference their sequences.
        sorted_parents = set_parents.sorted(lambda l: l.set_level)
        for parent_sol in sorted_parents:
            if parent_sol.order_id.state not in ('sale', 'done'):
                continue

            # Find pickings that hold component moves for this set
            component_sols = parent_sol.set_child_line_ids
            component_moves = StockMove.search([
                ('sale_line_id', 'in', component_sols.ids),
                ('state', 'not in', ('done', 'cancel')),
                ('picking_id', '!=', False),
            ])

            # Group by picking to create one header per outbound-chain
            # picking.  A picking is outbound when it has a sale_id and
            # is NOT a return (return_id is not set).  This covers all
            # steps in multi-step flows (Pick → Pack → Ship).
            pickings_seen = set()
            for move in component_moves:
                picking = move.picking_id
                if picking.id in pickings_seen:
                    continue
                pickings_seen.add(picking.id)

                # Skip return pickings
                if picking.return_id:
                    continue

                # Check if a header move already exists for this parent SOL
                existing = picking.move_ids.filtered(
                    lambda m: m.sale_line_id == parent_sol
                    and m.state not in ('done', 'cancel')
                )
                if existing:
                    continue

                # Place the header above its DIRECT component moves.
                # For nested sets, this means the nested header appears
                # between the parent's direct components and the nested
                # set's own components.
                direct_child_moves = component_moves.filtered(
                    lambda m: m.picking_id == picking
                    and m.sale_line_id in component_sols
                )
                min_sequence = min(
                    (m.sequence for m in direct_child_moves),
                    default=10,
                )
                # Create a zero-demand, pre-picked header move.
                # - product_uom_qty = 0: no demand, so it does not
                #   interfere with backorder splits or reservation.
                # - picked = True: silently finishes on validation.
                # - quantity = 0: no done qty.
                #
                # The zero-qty validation check is bypassed via an
                # override on stock.picking (see stock_picking.py).
                # On backorder creation, a new header move is created
                # automatically (see _create_backorder_header_moves).
                header = StockMove.create({
                    'product_id': parent_sol.product_id.id,
                    'product_uom_qty': 0,
                    'quantity': 0,
                    'uom_id': parent_sol.product_uom_id.id or parent_sol.product_id.uom_id.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'picking_id': picking.id,
                    'sale_line_id': parent_sol.id,
                    'sequence': min_sequence - 1,
                    'picked': False,
                })
                # Force to assigned state so it doesn't pull the picking
                # state down to draft.  Header moves are display-only and
                # should match the component moves' state.
                header.write({'state': 'assigned'})

    # -- Wizard action ---------------------------------------------------------

    def action_add_set_component(self):
        """Open the 'Add Component' wizard for this set line."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Add Component to Set'),
            'res_model': 'rental.set.add.component.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_sale_order_id': self.order_id.id,
                'default_set_line_id': self.id,
            },
        }

    # -- Set availability invalidation -----------------------------------------

    def _invalidate_set_availability(self):
        """Invalidate ``set_availability`` on the top-level set parents of the
        given lines so the field recomputes on the next read.

        Must be called after any operation that changes component composition
        (add, delete, substitute, qty change) because the ``@api.depends``
        only watches one level of ``set_child_line_ids`` and cannot detect
        deeper changes in nested sets.
        """
        top_parents = self.env['sale.order.line']
        for line in self:
            if line.is_set and not line.is_set_component:
                top_parents |= line
            elif line.is_set_component:
                top = line._get_top_set_parent()
                if top:
                    top_parents |= top
        if top_parents:
            # Clear the ORM cache so the value recomputes on next read.
            self.invalidate_model(fnames=['set_availability'])
            # Signal ORM that child quantities changed so that the
            # dependency chain (set_child_line_ids.product_uom_qty →
            # set_availability) is re-evaluated by the onchange engine
            # and the web client receives the updated value.
            for parent in top_parents:
                parent.set_child_line_ids.modified(['product_uom_qty'])

    # -- Set availability computation ------------------------------------------

    @api.depends(
        'is_set', 'is_set_component', 'product_uom_qty',
        'set_child_line_ids.product_id', 'set_child_line_ids.product_uom_qty',
        'set_child_line_ids.set_component_qty',
    )
    def _compute_set_availability(self):
        """Compute how many complete sets can be fulfilled for the rental period.

        Collects **all** leaf components across the entire hierarchy (including
        nested sets) with their cumulative quantity per one unit of the
        top-level set.  Leaves that share the same product are **grouped** so
        that competing demand for the same stock is correctly summed, then:

            min( available(product) / total_cumulative_qty )

        Uses Odoo's standard ``_get_unavailable_qty`` for rental products so
        that existing reservations, preparation time, and date ranges are
        respected.
        """
        for line in self:
            if not line.is_set:
                line.set_availability = 0.0
                continue

            # Collect all leaf components with their cumulative qty per 1 set
            leaves = []
            self._collect_leaf_availability_data(line, 1.0, leaves)

            if not leaves:
                line.set_availability = 0.0
                continue

            # Group by product: sum cumulative quantities and collect line ids
            # so the same product appearing in multiple branches of the tree
            # correctly reflects total demand against shared stock.
            #
            # Non-storable products (is_storable=False) are considered to
            # have limitless stock — they never constrain set availability.
            # Only storable products are included in the demand map.
            # If a set contains ONLY non-storable products, availability
            # is infinite (displayed as the ordered qty — i.e. 100% available).
            product_demand = {}  # product_id -> {qty, line_ids}
            has_storable = False
            for leaf, cumulative_qty in leaves:
                product = leaf.product_id
                if not product:
                    continue
                if not product.is_storable:
                    # Non-storable: limitless stock, skip from demand check
                    continue
                has_storable = True
                pid = product.id
                if pid not in product_demand:
                    product_demand[pid] = {
                        'product': product,
                        'total_qty': 0.0,
                        'line_ids': [],
                    }
                product_demand[pid]['total_qty'] += cumulative_qty
                product_demand[pid]['line_ids'].append(leaf)

            if not has_storable:
                # All components are non-storable: always fully available
                line.set_availability = line.product_uom_qty
                continue

            if not product_demand:
                line.set_availability = 0.0
                continue

            order = line.order_id
            from_date = getattr(order, 'rental_start_date', None) or \
                        line.start_date or \
                        fields.Datetime.now()
            to_date = getattr(order, 'rental_return_date', None) or \
                      line.return_date or \
                      from_date
            warehouse_id = order.warehouse_id.id if order.warehouse_id else False

            # Exclude the current order's own demand from availability.
            #
            # The set availability must answer: "how many complete sets
            # can this order fulfill?"  For confirmed orders this means
            # the stock already reserved by this order's moves counts as
            # available TO this order.
            #
            # Approach: compute the rentable base stock (qty_available or
            # virtual_available), then add back only the actual reserved
            # quantity from this order's stock moves (not the SOL demand
            # which may exceed stock).  Then subtract other orders' rental
            # demand for the overlapping period.

            # Collect all order lines for competing demand calculation.
            # Lines on the same order for the same product that are NOT
            # part of this set's hierarchy compete for the same stock.
            #
            # For nested sets: also exclude the parent set's entire
            # hierarchy.  The parent's availability already accounts for
            # all its descendants (including this nested set).  Without
            # this exclusion, the parent's sibling components would
            # reduce the nested set's availability — double-counting.
            all_order_lines = order.order_line.filtered(
                lambda l: not l.display_type and l.product_uom_qty > 0
            )
            # Identify lines to EXCLUDE from competing demand.
            #
            # Each set shows its own independent availability.  Other
            # sets on the same order are excluded — each set's availability
            # answers "how many of THIS set can be fulfilled" without
            # being reduced by other sets' demand.  Only truly standalone
            # non-set lines (e.g. a standalone Printer line) compete.
            set_line_ids = set()
            for ol in all_order_lines:
                if ol.is_set or ol.is_set_component:
                    set_line_ids.add(ol.id)

            min_sets = float('inf')
            for data in product_demand.values():
                product = data['product']
                total_qty = data['total_qty']

                # For confirmed lines, use Odoo's free_qty_today from the
                # component SOL.  This is the same value the standard stock
                # widget uses and it correctly accounts for this order's own
                # reservations without the double-counting issues of
                # virtual_available + manual add-back.  (RS08)
                #
                # For draft lines, use _get_component_available_qty which
                # computes availability from rental-aware stock methods.
                confirmed_leaves = [
                    l for l in data['line_ids'] if l.state == 'sale'
                ]

                if confirmed_leaves:
                    available = confirmed_leaves[0].free_qty_today
                elif product.rent_periodicity and hasattr(product, '_get_unavailable_qty'):
                    available = self._get_component_available_qty(
                        product, from_date, to_date, warehouse_id,
                        ignored_soline_id=False,
                    )
                else:
                    available = self._get_component_available_qty(
                        product, from_date, to_date, warehouse_id,
                    )

                # Subtract demand from OTHER lines on the same order for
                # the same product that are NOT part of this set.
                # E.g. if the order has a set needing 3 Printers and a
                # standalone line needing 18 Printers, the standalone
                # line's demand reduces what's available for the set.
                competing_demand = sum(
                    ol.product_uom_qty
                    for ol in all_order_lines
                    if ol.product_id == product and ol.id not in set_line_ids
                )
                available = max(available - competing_demand, 0)

                sets_from_product = available / total_qty if total_qty else 0.0
                min_sets = min(min_sets, sets_from_product)

            # Whole sets only: a partial set cannot be fulfilled.  (RAV-08)
            line.set_availability = float(max(
                math.floor(min_sets) if min_sets != float('inf') else 0,
                0,
            ))

    def _collect_leaf_availability_data(self, set_line, parent_multiplier, result):
        """Recursively collect all leaf components under ``set_line``.

        Each entry in ``result`` is a tuple ``(leaf_line, cumulative_qty)``
        where ``cumulative_qty`` is the total quantity of this leaf product
        needed per 1 unit of the **top-level** set (product of all
        ``set_component_qty`` values along the path).

        Nested sets are traversed transparently — only actual leaf products
        end up in the result list.
        """
        children = set_line.set_child_line_ids.filtered(
            lambda l: l.product_uom_qty > 0
        )
        # Per-set quantity: actual order qty divided by parent's ordered qty.
        # product_uom_qty is already scaled by parent (e.g. 3 printers × 2
        # sets = 6), so dividing by the parent's qty gives the per-set value.
        # This reflects manual qty changes while keeping the cascading
        # parent_multiplier logic correct for nested sets.
        parent_ordered = set_line.product_uom_qty or 1.0
        for child in children:
            per_set_qty = child.product_uom_qty / parent_ordered
            cumulative = parent_multiplier * per_set_qty

            if child.is_set and child.set_child_line_ids:
                # Nested set: recurse deeper, don't add the set itself
                self._collect_leaf_availability_data(child, cumulative, result)
            else:
                # Leaf component
                result.append((child, cumulative))

    def _get_component_available_qty(self, product, from_date, to_date,
                                      warehouse_id, ignored_soline_id=False):
        """Available quantity of ``product`` for the rental period, using the
        same Option-A formula as line availability:

            max(Total physical stock − reserved by OTHER orders − in repair, 0)

        (See ``_compute_forecast_availability``.)  Keeps set-component
        availability consistent with standalone-line availability.
        """
        if product.rent_periodicity and hasattr(product, '_get_unavailable_qty'):
            wh = (self.env['stock.warehouse'].browse(warehouse_id)
                  if warehouse_id else self.order_id.warehouse_id)
            return product._rental_available_qty(
                from_date, to_date, warehouse=wh,
                ignored_soline_id=ignored_soline_id,
                company=self.order_id.company_id,
            )
        else:
            # Non-rental product: standard stock availability
            return product.with_context(
                to_date=from_date, warehouse_id=warehouse_id,
            ).virtual_available

    # -- Quantity sync (recursive) ---------------------------------------------

    def _sync_set_component_quantities(self, old_qtys):
        """Rescale child line quantities when a set parent's quantity changes.
        Uses a simple ratio (new_qty / old_qty) applied to the current child
        quantities.
        """
        for line in self.filtered('is_set'):
            old_qty = old_qtys.get(line.id, 0)
            if not old_qty:
                continue
            ratio = line.product_uom_qty / old_qty
            child_old_qtys = {}
            for child in line.set_child_line_ids:
                child_old_qtys[child.id] = child.product_uom_qty
                new_qty = child.product_uom_qty * ratio
                child.with_context(rental_set_expanding=True).write({
                    'product_uom_qty': new_qty,
                })
                if child.is_set:
                    child._sync_set_component_quantities(child_old_qtys)

    # -- Stock / rental reservation sync ---------------------------------------

    def _sync_set_stock_moves(self):
        """Synchronise stock moves and reservations after set composition
        changes (add / remove / substitute / qty change).

        For component lines in a **confirmed** order:
        1.  Cancel existing non-done moves that no longer match the current
            product or whose quantity needs adjustment.
        2.  Trigger standard procurement (``_action_launch_stock_rule``) so
            Odoo creates the correct moves for the delta.
        3.  Attempt reservation on affected pickings.
        4.  Post a non-blocking warning if any component cannot be fully
            reserved.

        Reuses Odoo's standard stock logic so that rental availability,
        push/pull rules, and warehouse configuration are respected.
        """
        # Only act on confirmed (sale) orders — draft orders have no moves.
        confirmed = self.filtered(lambda l: l.order_id.state == 'sale')
        if not confirmed:
            return

        orders_to_check = self.env['sale.order']

        for line in confirmed:
            # Gather all leaf component lines
            if line.is_set and not line.is_set_component:
                component_lines = self._collect_leaf_components(line)
            elif line.is_set_component:
                top = line._get_top_set_parent()
                component_lines = self._collect_leaf_components(top) if top else line
            else:
                continue

            for comp in component_lines:
                active_moves = comp.move_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                )
                # Cancel moves whose product no longer matches
                wrong_product = active_moves.filtered(
                    lambda m: m.product_id != comp.product_id
                )
                if wrong_product:
                    wrong_product._action_cancel()

                # For matching-product moves, cancel excess if qty decreased
                # (_action_launch_stock_rule handles the "need more" case)

            # Re-trigger procurement — it only creates moves for the delta
            component_lines._action_launch_stock_rule()
            orders_to_check |= line.order_id

        # Re-reserve on pickings
        for order in orders_to_check:
            pickings = order.picking_ids.filtered(
                lambda p: p.state in ('confirmed', 'partially_available', 'assigned')
            )
            if pickings:
                pickings.action_assign()
            self._warn_availability(order)

    def _collect_leaf_components(self, set_parent):
        """Return all leaf (non-set) component lines under a set parent,
        recursively through nested sets.
        """
        result = self.env['sale.order.line']
        for child in set_parent.set_child_line_ids:
            if child.is_set and child.set_child_line_ids:
                result |= self._collect_leaf_components(child)
            else:
                result |= child
        return result

    def _warn_availability(self, order):
        """Post a chatter warning if any component line in the order has
        insufficient stock reservation.  The warning is purely informational
        and never blocks the operation.
        """
        insufficient = []
        for line in order.order_line.filtered('is_set_component'):
            if not line.move_ids:
                continue
            reserved = sum(
                m.quantity for m in line.move_ids
                if m.state not in ('done', 'cancel')
            )
            if reserved < line.product_uom_qty:
                shortage = line.product_uom_qty - reserved
                insufficient.append(
                    _("%(product)s: %(short).1f %(uom)s short (need %(need).1f, reserved %(res).1f)",
                      product=line.product_id.display_name,
                      short=shortage,
                      need=line.product_uom_qty,
                      res=reserved,
                      uom=line.product_uom_id.name)
                )
        if insufficient:
            body = _(
                "<b>Rental Set availability warning</b><br/>"
                "The following components could not be fully reserved:<br/>"
            ) + "<br/>".join(insufficient)
            order.message_post(body=body, message_type='notification')

    # -- Set pricing (post-expansion) ------------------------------------------

    def _apply_set_pricing(self):
        """Apply pricing rules after a set has been fully expanded.

        Both modes:  component price_unit = 0 (only the parent line counts
                     in the order total).
        Fixed mode:  additionally compute set_allocated_price on each component
                     for internal reference.
        Sum mode:    parent price_unit = sum of component product prices.
        """
        for line in self:
            pricing_mode = line.product_id.product_tmpl_id.set_pricing_mode
            # Zero all component prices (both modes)
            self._zero_descendant_prices(line)
            if pricing_mode == 'fixed':
                line._allocate_fixed_prices()
            elif pricing_mode == 'sum':
                line._recompute_set_price_from_components()
                line._allocate_sum_prices()

    def _zero_descendant_prices(self, parent_line):
        """Recursively zero out price_unit on all descendants of parent_line."""
        for child in parent_line.set_child_line_ids:
            child.with_context(rental_set_expanding=True).write({
                'price_unit': 0.0,
                'technical_price_unit': 0.0,
            })
            if child.set_child_line_ids:
                self._zero_descendant_prices(child)

    # -- Fixed-price allocation (internal reference only) ----------------------

    def _allocate_fixed_prices(self):
        """Distribute the parent set price over component lines into the
        set_allocated_price field (internal reference only, not price_unit).

        The allocated price reflects the **total** for the ordered quantity,
        not just a single set unit.

        Allocation logic:
        - If total normal value > 0:
              allocated = set_total * (product_price * comp_qty) / total_normal_value
        - If total normal value = 0 (all zero-price):
              allocate evenly by quantity: set_total / total_qty
        """
        self.ensure_one()
        children = self.set_child_line_ids
        if not children:
            return

        set_total = self.price_unit * self.product_uom_qty

        # Gather normal (product) prices for each child
        normal_values = []
        total_normal_value = 0.0
        total_qty = 0.0

        for child in children:
            normal_price = self._get_component_unit_price(child)
            # Use actual order line quantity for allocation, not template
            # base qty.  Manual component qty changes must be reflected.
            comp_qty = child.product_uom_qty or 1.0
            normal_value = normal_price * comp_qty
            normal_values.append((child, normal_price, comp_qty, normal_value))
            total_normal_value += normal_value
            total_qty += comp_qty

        for child, normal_price, comp_qty, normal_value in normal_values:
            if total_normal_value > 0:
                allocated_price = set_total * normal_value / total_normal_value
            elif total_qty > 0:
                allocated_price = set_total / total_qty
            else:
                allocated_price = 0.0

            child.with_context(rental_set_expanding=True).write({
                'set_allocated_price': allocated_price,
            })

            # Recurse into nested sets
            if child.is_set and child.set_child_line_ids:
                child._allocate_fixed_prices()

    # -- Sum-price allocation (internal reference only) -------------------------

    def _allocate_sum_prices(self):
        """Set ``set_allocated_price`` on each component of a sum-mode set.

        In sum-of-components mode, each component's allocated price equals its
        effective price × its per-set quantity × the parent's ordered quantity
        (``product_uom_qty``), giving the **total** allocated amount.

        For nested sets with sum-of-components pricing, the effective price is
        computed recursively from the nested set's own components.  For all
        other components, the pricelist price (``_get_display_price``) is used.
        """
        self.ensure_one()
        children = self.set_child_line_ids
        if not children:
            return

        for child in children:
            price = self._get_component_unit_price(child)
            # Use actual order line quantity for allocation.  product_uom_qty
            # already includes parent scaling (comp_base_qty × parent_qty),
            # so we do NOT multiply by parent_qty again.
            # When the user manually adjusts a component quantity, the
            # allocation reflects the actual order content.
            comp_total_qty = child.product_uom_qty or 1.0
            child.with_context(rental_set_expanding=True).write({
                'set_allocated_price': price * comp_total_qty,
            })

            # Recurse into nested sets
            if child.is_set and child.set_child_line_ids:
                child._allocate_sum_prices()

    # -- Pricing (compute override) --------------------------------------------

    @api.depends('product_id', 'product_uom_id', 'product_uom_qty')
    def _compute_price_unit(self):
        """Override to handle Rental Set pricing modes.

        All component lines: price_unit = 0 (never contributes to order total).
        Sum-mode parents:    price recomputed from component product prices.
        Fixed-mode parents:  keep standard product/pricelist price.
        """
        # Let Odoo compute standard prices for everything first
        super(SaleOrderLine, self)._compute_price_unit()

        # Zero out all set component prices
        for line in self:
            if line.is_set_component:
                line.update({
                    'price_unit': 0.0,
                    'technical_price_unit': 0.0,
                })

        # For sum-mode parents, recompute price from component product prices
        for line in self:
            if (line.is_set and not line.is_set_component
                    and line.set_pricing_mode == 'sum'):
                line._recompute_set_price_from_components()

    def _get_component_unit_price(self, child):
        """Return the per-unit price of a component line, respecting its
        pricing mode.

        - If the child is a nested set with sum-of-components pricing, the
          price is computed recursively from *its* own components.
        - Otherwise, the pricelist price (``_get_display_price``) is used.
        """
        if child.is_set and child.set_child_line_ids and child.set_pricing_mode == 'sum':
            # Recursively sum the nested set's own children
            nested_total = 0.0
            for grandchild in child.set_child_line_ids:
                gc_price = self._get_component_unit_price(grandchild)
                # Per-set quantity: prefer stable set_component_qty
                if grandchild.set_component_qty:
                    gc_per_set = grandchild.set_component_qty
                else:
                    nested_parent_qty = child.product_uom_qty or 1.0
                    gc_per_set = (grandchild.product_uom_qty or 1.0) / nested_parent_qty
                nested_total += gc_price * gc_per_set
            return nested_total
        return child.with_company(child.company_id)._get_display_price()

    def _recompute_set_price_from_components(self):
        """Set the parent line price_unit to the sum of component product prices,
        normalised per 1 unit of the set.

        Formula: price_unit = SUM(child_price * per_set_qty)

        Uses set_component_qty (the base per-set quantity) for the per-unit
        price calculation.  This is stable regardless of when the compute
        fires relative to component quantity sync.

        When the user manually changes a component qty, the write() method
        calls this AFTER updating the component, and at that point
        product_uom_qty / parent_qty gives the correct per-set value.

        For nested sets with sum-of-components pricing, the child price is
        computed recursively from its own components rather than using the
        product's catalog price.
        """
        self.ensure_one()
        if not self.product_uom_qty:
            return
        parent_qty = self.product_uom_qty or 1.0
        total = 0.0
        for child in self.set_child_line_ids:
            price = self._get_component_unit_price(child)
            # Per-set quantity: use set_component_qty when available (stable
            # during compute cycles), fall back to product_uom_qty / parent
            # for manually adjusted components.
            if child.set_component_qty:
                per_set_qty = child.set_component_qty
            else:
                per_set_qty = child.product_uom_qty / parent_qty
            total += price * per_set_qty
        # Write both fields together under the compute context. Using
        # ``.update()`` assigns them one at a time, so technical_price_unit is
        # written on its own and the core sale guard strips it (it only keeps a
        # bare technical_price_unit write when price_unit is present or the
        # sale_write_from_compute context is set). That left technical stale and
        # diverging from price_unit, which pricing extensions misread as a
        # hand-typed price.
        self.with_context(sale_write_from_compute=True).write({
            'price_unit': total,
            'technical_price_unit': total,
        })

    def _reapply_set_derived_pricing(self):
        """Hook: reapply pricing layered on top of the set component sum.

        Called after a sum-mode parent price is recomputed from a component
        change. No-op in base rental_set; pricing extensions (e.g. the
        coefficient / dynamic pricing module) override this to reapply their
        adjustment to the fresh component sum and rescale the allocations.
        """
        return

    def _resync_set_component_base_qty(self):
        """Recompute ``set_component_qty`` (per-set base) from the live order
        quantity for every descendant component of this set.

        ``set_component_qty`` is the quantity of a component per 1 unit of its
        immediate set parent.  In a consistent state it always equals
        ``product_uom_qty / parent_qty``; the two only diverge when a component
        quantity is edited directly.  Because the sum-mode parent price is
        derived from ``set_component_qty`` while the allocations use the live
        ``product_uom_qty``, a stale base qty makes the parent price disagree
        with the sum of its component allocations.  Resyncing here restores the
        invariant ``parent price_unit x parent_qty == SUM(allocations)``.
        """
        self.ensure_one()
        parent_qty = self.product_uom_qty or 1.0
        for child in self.set_child_line_ids:
            new_base_qty = (child.product_uom_qty or 0.0) / parent_qty
            if child.set_component_qty != new_base_qty:
                child.with_context(rental_set_expanding=True).write({
                    'set_component_qty': new_base_qty,
                })
            # Recurse into nested sets (base qty is relative to each level).
            if child.is_set and child.set_child_line_ids:
                child._resync_set_component_base_qty()

    def _get_top_set_parent(self):
        """Walk up the set hierarchy and return the top-level set parent line."""
        node = self.set_parent_line_id
        while node and node.is_set_component:
            node = node.set_parent_line_id
        return node if node and node.is_set else self.env['sale.order.line']
