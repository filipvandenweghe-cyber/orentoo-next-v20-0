import math
from collections import defaultdict

from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    # The set component listing is no longer appended to the sale
    # description.  Standard Odoo logic applies: the quotation line
    # uses the description_sale defined on the product's Sales tab.

    rental_avail_catalog = fields.Float(
        string="Rental Availability",
        compute="_compute_rental_avail_catalog",
        digits='Product Unit',
        help="Rental units still available for the current order's rental "
             "period (its warehouse & company), shown on the product catalog "
             "card.  Uses the canonical rental availability engine.")

    @api.depends_context('start_date', 'end_date', 'rental_catalog_wh',
                         'rental_catalog_company', 'allowed_company_ids')
    def _compute_rental_avail_catalog(self):
        """Rental availability for the order's period, for the catalog card.

        The rental dates are placed in the catalog context by ``sale_renting``
        (``start_date`` / ``end_date``); the order's warehouse & company are
        added by ``sale.order._get_action_add_from_catalog_extra_context`` in
        this module.  Values arrive JSON-serialised (dates as strings), so they
        are parsed back before hitting the canonical engine.
        """
        ctx = self.env.context
        start = fields.Datetime.to_datetime(ctx.get('start_date'))
        end = fields.Datetime.to_datetime(ctx.get('end_date')) or start
        wh_id = ctx.get('rental_catalog_wh')
        warehouse = self.env['stock.warehouse'].browse(wh_id) if wh_id else False
        comp_id = ctx.get('rental_catalog_company')
        company = self.env['res.company'].browse(comp_id) if comp_id \
            else self.env.company
        for product in self:
            tmpl = product.product_tmpl_id
            if not start:
                product.rental_avail_catalog = 0.0
            elif tmpl.is_rental_set and tmpl.set_component_ids:
                # A set owns no stock — its availability is how many COMPLETE
                # sets the components allow (same rule as set_availability, but
                # period-aware via the canonical engine).
                product.rental_avail_catalog = product._rental_set_avail_for_period(
                    start, end, warehouse, company)
            elif product.rent_periodicity and product.is_storable:
                product.rental_avail_catalog = product._rental_available_qty(
                    start, end, warehouse=warehouse, company=company)
            else:
                product.rental_avail_catalog = 0.0

    def _rental_set_avail_for_period(self, start, end, warehouse, company):
        """How many COMPLETE sets are rentable for ``[start, end]`` =
        ``floor(min over leaf components of component_avail / qty-per-set)``.

        Reuses the set's leaf-flattening
        (``product.template._collect_leaf_components_for_availability``) and the
        canonical ``_rental_available_qty`` per component — no separate
        availability logic.  Non-storable components are limitless and never
        constrain the set.
        """
        self.ensure_one()
        tmpl = self.product_tmpl_id
        leaves = []
        tmpl._collect_leaf_components_for_availability(tmpl, 1.0, leaves)
        demand = {}
        for comp, qty in leaves:
            if not comp.is_storable or not qty:
                continue  # non-storable = limitless
            data = demand.setdefault(comp.id, {'product': comp, 'qty': 0.0})
            data['qty'] += qty
        if not demand:
            return 0.0
        min_sets = float('inf')
        for data in demand.values():
            avail = data['product']._rental_available_qty(
                start, end, warehouse=warehouse, company=company)
            min_sets = min(min_sets, avail / data['qty'])
        return float(max(math.floor(min_sets), 0)) \
            if min_sets != float('inf') else 0.0

    def _rental_physical_total(self, warehouse=False, company=False):
        """Real units this warehouse owns right now — warehouse-local:

            on-hand across the warehouse's OWN internal/transit locations
            + units currently out at a customer that were shipped FROM this
              warehouse (attributed by the order's warehouse).

        The at-customer part is attributed per warehouse instead of counting
        the company-wide rental location, so a company running SEVERAL
        warehouses never sees another warehouse's rented-out stock as
        available here.  For a company with a single warehouse the two are
        identical, so single-warehouse tenants are unaffected.

        Order-independent, canonical implementation.  ``sale.order.line``,
        the rental-set component check and the multi-channel rental flow all
        funnel through this so "total physical stock" has ONE definition.

        The total is conserved and stable across the pick/pack/ship steps
        (a pickup just moves a unit from the warehouse to the at-customer
        location) and multi-step-safe: the at-customer part is measured from
        DONE moves that actually reached the rental location — never the
        intermediate pick/pack legs (see ``_rental_at_customer_qty``).

        :param warehouse: stock.warehouse to scope stock/attribution to
        :param company: res.company owning the rental ("at customer")
            location; when falsy the at-customer part is 0 (callers that want
            it must pass the company explicitly, e.g. the order's)
        """
        self.ensure_one()
        return self._rental_warehouse_onhand(warehouse) \
            + self._rental_at_customer_qty(warehouse, company)

    def _rental_warehouse_onhand(self, warehouse):
        """Current on-hand across the warehouse's OWN internal/transit
        locations only — excludes the company-wide at-customer location, so
        stock never leaks between warehouses of one company.  In a multi-step
        warehouse the pick/pack/output zones are internal children of the
        warehouse view, so units in transit are still counted here (and only
        here).  Falls back to ``qty_available`` when no warehouse is given.
        """
        self.ensure_one()
        if not (warehouse and warehouse.view_location_id):
            return self.qty_available
        locs = self.env['stock.location'].search([
            ('id', 'child_of', warehouse.view_location_id.id),
            ('usage', 'in', ('internal', 'transit')),
        ])
        if not locs:
            return self.qty_available
        total = 0.0
        for dummy_loc, qty in self.env['stock.quant']._read_group(
            [('product_id', '=', self.id), ('location_id', 'in', locs.ids)],
            ['location_id'], ['quantity:sum'],
        ):
            total += qty
        return total

    def _rental_at_customer_qty(self, warehouse, company):
        """Units of this product currently out at a customer that were
        shipped FROM ``warehouse`` — i.e. that reached the rental
        ("at customer") location through an order placed on this warehouse.

        Measured from DONE stock moves, which makes it:

        * multi-step-safe — filtering on ``location_dest_id == rental_loc``
          matches only the final customer-facing leg; the intermediate
          pick/pack legs target internal locations and are ignored, so a unit
          in transit is counted once (by ``_rental_warehouse_onhand``) and
          never double-counted here;
        * setting-agnostic — with Rental Transfers OFF no such moves exist,
          so this is 0 and the base collapses to warehouse on-hand;
        * attributed by ``order.warehouse_id`` — the SAME key
          ``_get_unavailable_qty`` uses for reservations, so the base and the
          reservation term always agree.

        net = Σ done moves INTO rental_loc − Σ done moves OUT of rental_loc,
        for moves whose order is on this warehouse.
        """
        self.ensure_one()
        rental_loc = company.rental_loc_id if company else False
        if not (rental_loc and warehouse):
            return 0.0
        Move = self.env['stock.move']
        base = [
            ('product_id', '=', self.id),
            ('state', '=', 'done'),
            ('sale_line_id.order_id.warehouse_id', '=', warehouse.id),
        ]

        def _summed(extra):
            rows = Move._read_group(base + extra, [], ['quantity:sum'])
            return (rows[0][0] or 0.0) if rows else 0.0

        delivered = _summed([('location_dest_id', '=', rental_loc.id)])
        returned = _summed([('location_id', '=', rental_loc.id)])
        return delivered - returned

    def _rental_available_qty(self, from_date, to_date=False, warehouse=False,
                              ignored_soline_id=False, company=False,
                              clamp=True):
        """Canonical rental availability for a period:

            Available = max(Total physical stock
                            - reserved by OTHER orders (period-aware)
                            - in repair, 0)

        This is the SINGLE definition of "how many additional units of this
        product can be rented for ``[from_date, to_date]``".  It is shared by
        the rental order lines (``sale.order.line._compute_forecast_availability``
        / ``_get_component_available_qty``) and the multi-channel rental flow
        (kiosk / website / POS via ``mcrf_service``), so operational
        availability and any downstream check always agree.

        **Total physical stock** is the conserved quantity the warehouse owns
        right now (on-hand in its own locations + units still out at a customer
        that shipped from it).  It is stable across the pick/pack/ship steps, so
        multi-step staging never inflates availability.

        **Reserved by others** follows the warehouse's real **operations**, not
        the order's declared dates: a rental commits its units from its first
        outbound (pickup) date until its first **inbound (return) operation**
        date.  So a unit still out at a customer whose return operation is
        scheduled *after* the requested window stays reserved — even when the
        order's declared ``return_date`` has already passed (see the regression
        test / order S00338, where the return receipt is scheduled 09-11 while
        the order says 09-05).  This is what native Odoo Rental's warehouse
        forecast expresses via the pickings' move dates; here we express the
        same thing over the conserved total via ``_get_unavailable_qty``, whose
        return timing is grounded on the operation (see
        ``sale.order.line._rental_effective_return_date`` /
        ``_get_active_rental_lines`` / ``_get_rented_quantities``).

        :param from_date: start of the rental period (Datetime)
        :param to_date: end of the rental period (Datetime); defaults to
            ``from_date`` (a single instant)
        :param warehouse: stock.warehouse to scope stock/reservations to
        :param ignored_soline_id: sale.order.line to exclude so an order
            never subtracts its own units from itself; ``False`` counts
            every order (the right choice for a fresh availability question)
        :param company: res.company owning the rental location; defaults to
            the warehouse company, then the current company
        :param clamp: when ``True`` (default, and what every existing caller
            gets) the result is floored at 0 via ``max(..., 0)`` — the
            operational "you cannot rent negative units" figure.  When
            ``False`` the SAME canonical calculation is returned *before* that
            floor, so a reporting layer can see a signed (possibly negative)
            over-/under-commitment.  ``clamp`` changes nothing but the final
            floor — the terms above are untouched.
        """
        self.ensure_one()
        to_date = to_date or from_date
        if not company:
            company = (warehouse.company_id if warehouse else False) \
                or self.env.company
        wh_id = warehouse.id if warehouse else False

        total = self._rental_physical_total(warehouse=warehouse, company=company)

        reserved_other = 0.0
        if self.rent_periodicity and hasattr(self, '_get_unavailable_qty'):
            reserved_other = self._get_unavailable_qty(
                from_date, to_date,
                ignored_soline_id=ignored_soline_id, warehouse_id=wh_id,
            )

        in_repair = self._get_repair_unavailable_qty(
            from_date, to_date, warehouse_id=wh_id,
        )
        # Ground confirmed (not-yet-done) interwarehouse / intercompany
        # transfers on their scheduled operation dates.  A relocation OUT of
        # this warehouse reduces it from the departure operation; a relocation
        # IN raises it once the units are guaranteed present for the whole
        # interval.  Both follow the real stock moves (route-based resupply or
        # a manual internal transfer alike), never the order's headers.
        transfer_out = self._get_transfer_out_qty(
            from_date, to_date, warehouse=warehouse,
        )
        transfer_in = self._get_transfer_in_qty(
            from_date, to_date, warehouse=warehouse,
        )
        signed = total - reserved_other - in_repair - transfer_out + transfer_in
        return max(signed, 0.0) if clamp else signed

    def _get_active_rental_lines(self, from_date, to_date,
                                 ignored_soline_id=False, warehouse_id=False,
                                 **kwargs):
        """Extend native to also catch rentals whose committed interval
        overlaps ``[from_date, to_date]`` when measured by the warehouse's real
        **operations** rather than the order's declared dates.

        Native ``_get_active_rental_lines`` keys on the declared
        ``reservation_begin`` / ``return_date``, so it drops two symmetric
        cases:

        * a unit still physically OUT whose **return operation** is scheduled
          after ``from_date`` even though the declared ``return_date`` is
          earlier — the return receipt was rescheduled (real case S00338);
        * a unit whose **pickup operation** is scheduled before ``to_date``
          even though the declared ``reservation_begin`` is later — the
          delivery picking is scheduled 09-07 for a rental that only starts
          09-08 (real case S00708).

        Both are units the warehouse has really committed over the window, so
        they must keep counting as reserved.  We add back any confirmed rental
        line whose effective interval ``[eff_pickup, eff_return]`` overlaps the
        window and let ``_get_rented_quantities`` place the +/- on the
        operation dates (see ``sale.order.line._rental_effective_pickup_date``
        / ``_rental_effective_return_date``).
        """
        lines = super()._get_active_rental_lines(
            from_date, to_date, ignored_soline_id=ignored_soline_id,
            warehouse_id=warehouse_id, **kwargs)
        to_date = to_date or from_date
        domain = [
            ('is_rental', '=', True),
            ('product_id', '=', self.id),
            ('state', '=', 'sale'),
        ]
        if ignored_soline_id:
            domain.append(('id', '!=', ignored_soline_id))
        if warehouse_id:
            domain.append(('order_id.warehouse_id', '=', warehouse_id))
        candidates = self.env['sale.order.line'].search(domain) - lines

        def _overlaps(line):
            eff_pickup = line._rental_effective_pickup_date() \
                or line.reservation_begin
            eff_return = line._rental_effective_return_date() \
                or line.return_date
            if not eff_pickup or not eff_return:
                return False
            return eff_return > from_date and eff_pickup <= to_date

        return lines | candidates.filtered(_overlaps)

    # ── "on option" by other orders (unconfirmed quotations) ────────────────
    def _get_on_option_lines(self, from_date, to_date=False,
                             ignored_order_id=False, warehouse_id=False):
        """Rental lines held **on option** by OTHER orders over the window.

        "On option" = a quotation (``state in ('draft', 'sent')``) explicitly
        flagged ``rental_on_option`` that is not confirmed yet, kept alive until
        its ``validity_date`` ("on option until").  These are a **soft** hold:
        they do not reduce the committed availability, but the salesperson
        should be warned they may firm up.  Opt-in only — an unconfirmed
        quotation that is NOT flagged never counts.

        Excludes the whole ``ignored_order_id`` (not just one line), so an order
        never counts itself — even a draft order does not count its own option.
        Same period-overlap and warehouse scoping as the confirmed reserved
        term, on effective (or declared) pickup/return dates.
        """
        self.ensure_one()
        to_date = to_date or from_date
        domain = [
            ('is_rental', '=', True),
            ('product_id', '=', self.id),
            ('order_id.state', 'in', ('draft', 'sent')),
            ('order_id.rental_on_option', '=', True),  # explicit opt-in
        ]
        if ignored_order_id:
            domain.append(('order_id', '!=', ignored_order_id))
        if warehouse_id:
            domain.append(('order_id.warehouse_id', '=', warehouse_id))
        today = fields.Date.context_today(self)

        def _live_and_overlaps(line):
            order = line.order_id
            # Option lapsed → no longer holds anything.
            if order.validity_date and order.validity_date < today:
                return False
            eff_pickup = line._rental_effective_pickup_date() \
                or line.reservation_begin
            eff_return = line._rental_effective_return_date() \
                or line.return_date
            if not eff_pickup or not eff_return:
                return False
            return eff_return > from_date and eff_pickup <= to_date

        return self.env['sale.order.line'].search(domain).filtered(
            _live_and_overlaps)

    def _get_on_option_qty(self, from_date, to_date=False,
                           ignored_order_id=False, warehouse_id=False):
        """Peak concurrent quantity held on option by OTHER orders over the
        window — computed with the SAME step-function as the confirmed reserved
        term (so overlapping options are counted at their peak, never
        double-counted across time).  ``0`` when nothing is on option."""
        self.ensure_one()
        if not self.rent_periodicity:
            return 0.0
        to_date = to_date or from_date
        lines = self._get_on_option_lines(
            from_date, to_date, ignored_order_id=ignored_order_id,
            warehouse_id=warehouse_id)
        if not lines:
            return 0.0
        rented_quantities, key_dates = lines._get_rented_quantities(
            [from_date, to_date])
        return self._reserved_peak(
            rented_quantities, key_dates, from_date, to_date)

    def _get_repair_unavailable_qty(self, from_date, to_date=False,
                                    warehouse_id=False, lot_id=False):
        """Quantity of this product tied up in **open** repairs whose window
        overlaps ``[from_date, to_date]``.

        A unit under repair is physically present but **not rentable**, and
        standard Odoo does not deduct it from availability (the repair stock
        move is only created at ``action_repair_done``).  We therefore expose
        an explicit, period-aware deduction.

        The ``repair`` module is an **optional** dependency: if it is not
        installed this returns ``0.0`` and never raises.

        :param from_date: start of the rental period (Datetime)
        :param to_date: end of the rental period (Datetime); defaults to from_date
        :param warehouse_id: restrict to repairs located inside this warehouse
        :param lot_id: restrict to a specific serial/lot
        """
        self.ensure_one()
        # Optional dependency — soft check, never import repair directly.
        if 'repair.order' not in self.env:
            return 0.0
        if not from_date:
            return 0.0
        repairs = self._rental_open_repairs(warehouse_id, lot_id)
        return self._rental_repair_sum(repairs, from_date, to_date)

    def _rental_open_repairs(self, warehouse_id=False, lot_id=False):
        """Interval-INDEPENDENT search of this product's **open** repairs
        (``state ∉ done/cancel``), optionally scoped to a warehouse's locations
        or a lot.  Split out from ``_get_repair_unavailable_qty`` so the scalar
        engine and the batch reporting layer share ONE search + ONE overlap
        summation (``_rental_repair_sum``) — no divergent repair logic.
        Returns an empty recordset when ``repair`` is not installed.
        """
        self.ensure_one()
        if 'repair.order' not in self.env:
            return self.env['stock.move'].browse()  # empty, iterable
        domain = [
            ('product_id', '=', self.id),
            ('state', 'not in', ('done', 'cancel')),
        ]
        if lot_id:
            domain.append(('lot_id', '=', lot_id))
        if warehouse_id:
            wh = self.env['stock.warehouse'].browse(warehouse_id)
            if wh.view_location_id:
                domain.append(
                    ('location_id', 'child_of', wh.view_location_id.id)
                )
        return self.env['repair.order'].sudo().search(domain)

    def _rental_repair_sum(self, repairs, from_date, to_date=False):
        """Sum ``product_qty`` of the given open repairs whose unavailability
        window ``[create_date, schedule_date]`` (floored at ``now`` when the
        schedule date is overdue) overlaps ``[from_date, to_date]``.  Identical
        arithmetic to the original inline loop, so scalar and batch agree."""
        if not from_date:
            return 0.0
        to_date = to_date or from_date
        now = fields.Datetime.now()
        total = 0.0
        for repair in repairs:
            # Window the unit is unavailable: from creation until the
            # scheduled repair date — but an overdue repair still holds the
            # unit, so extend to "now" when the schedule date is in the past.
            start = repair.create_date or from_date
            end = repair.schedule_date or start
            if end < now:
                end = now
            # Overlap with the requested rental period.
            if start <= to_date and end >= from_date:
                total += repair.product_qty or 0.0
        return total

    def _get_transfer_out_qty(self, from_date, to_date=False, warehouse=False):
        """Units committed to LEAVE this warehouse before/within the window via
        a confirmed (not-yet-done) relocation to another warehouse/company —
        grounded on the transfer move's scheduled date.

        A relocation is any stock move that crosses this warehouse's view-tree
        boundary into another INTERNAL/TRANSIT location (same-company
        interwarehouse, or — through a transit location — intercompany).  It is
        deducted when it departs at or before ``to_date``: a unit leaving at
        any point inside the window is not available for the COMPLETE interval
        (worst case at the end of the window).

        The units are still physically on-hand right now (the move is not done,
        so the conserved physical total still counts them here); this term is
        what makes availability follow the scheduled OUTBOUND operation instead
        of waiting for the move to complete.  It is move-based, so it fires the
        same for a route-driven resupply and a manual internal transfer.

        Excludes:
        * done/cancelled moves — already reflected in the physical total;
        * moves whose destination stays INSIDE this warehouse — internal
          staging, not a departure;
        * moves to a customer / supplier location — sales and vendor returns,
          not relocations of owned stock;
        * moves touching a rental (at-customer) location — rental pickups are
          handled by the reserved term.  The exclusion is by LOCATION (not by
          ``sale_line.is_rental``), so a resupply transfer that merely FEEDS a
          rental still counts as the relocation it is.
        """
        return self._rental_transfer_qty(
            from_date, to_date, warehouse, direction='out')

    def _get_transfer_in_qty(self, from_date, to_date=False, warehouse=False):
        """Units scheduled to ARRIVE into this warehouse via a confirmed
        (not-yet-done) relocation from another warehouse/company — grounded on
        the transfer move's scheduled date.

        Credited only when the arrival is at or before ``from_date``: a unit
        must be present for the WHOLE interval to be rentable for the complete
        column, so an arrival mid-window does not yet raise the operational
        figure (conservative).

        What actually counts is governed per operation type by
        ``stock.picking.type.rental_incoming_policy``:

        * **relocations of OWNED stock** (source internal/transit — an
          interwarehouse / intercompany transfer) count operationally by
          default, because the group already owns the units; set the type to
          ``ignore`` to opt out;
        * **external / produced supply** (a Purchase Order from a supplier, a
          Manufacturing Order, intercompany supply) counts **only** when its
          operation type is set to ``operational`` — the safe default keeps
          uncertain incoming supply out of the booking gate.
        """
        return self._rental_transfer_qty(
            from_date, to_date, warehouse, direction='in')

    def _rental_transfer_qty(self, from_date, to_date, warehouse, direction):
        """Shared engine for the transfer OUT / IN terms.  ``direction`` is
        ``'out'`` (relocations leaving this warehouse, dated by ``to_date``) or
        ``'in'`` (relocations arriving, dated by ``from_date``).

        Split into an interval-INDEPENDENT search (``_rental_transfer_moves``)
        and an interval-DEPENDENT summation (``_rental_transfer_sum``) so the
        scalar engine and the batch reporting layer share ONE implementation —
        the batch caches the moveset once and re-sums it per column, and the
        two can never diverge because they run the same summation."""
        self.ensure_one()
        if not (warehouse and warehouse.view_location_id) or not from_date:
            return 0.0
        moves = self._rental_transfer_moves(warehouse, direction)
        if not moves:
            return 0.0
        # Rental (at-customer) locations across every company — their moves are
        # rental pickups/returns, handled by the reserved term, never here.
        rental_loc_ids = self.env['res.company'].sudo().search(
            []).rental_loc_id.ids
        return self._rental_transfer_sum(
            moves, from_date, to_date, direction, rental_loc_ids)

    def _rental_transfer_moves(self, warehouse, direction):
        """Interval-INDEPENDENT search of confirmed (not done/cancel)
        relocations crossing this warehouse's view-tree boundary in
        ``direction`` — everything EXCEPT the move-date bound, which is applied
        per interval by ``_rental_transfer_sum``.  Cache this once per
        ``(product, warehouse, direction)`` to batch many intervals cheaply."""
        self.ensure_one()
        Move = self.env['stock.move']
        if not (warehouse and warehouse.view_location_id):
            return Move.browse()
        inside = self.env['stock.location'].search([
            ('id', 'child_of', warehouse.view_location_id.id),
            ('usage', 'in', ('internal', 'transit')),
        ])
        if not inside:
            return Move.browse()
        if direction == 'out':
            # Relocations LEAVING this warehouse toward another internal/transit
            # location (a departure of owned stock) — plus a Rental Purchase
            # return to the vendor: hired-in (supplier-owned) units physically
            # leave this warehouse at the scheduled return date, so the
            # availability must drop then even though the destination is a
            # supplier location.  ``rental_purchase_order_id`` only exists when
            # the optional ``rental_purchase`` module is installed (soft dep).
            dest_clause = [('location_dest_id.usage', 'in', ('internal', 'transit'))]
            if 'rental_purchase_order_id' in Move._fields:
                dest_clause = ['|'] + dest_clause \
                    + [('rental_purchase_order_id', '!=', False)]
            return Move.search([
                ('product_id', '=', self.id),
                ('state', 'not in', ('done', 'cancel')),
                ('location_id', 'in', inside.ids),
                ('location_dest_id', 'not in', inside.ids),
            ] + dest_clause)
        # direction == 'in': everything arriving into this warehouse from
        # OUTSIDE.
        return Move.search([
            ('product_id', '=', self.id),
            ('state', 'not in', ('done', 'cancel')),
            ('location_dest_id', 'in', inside.ids),
            ('location_id', 'not in', inside.ids),
        ])

    def _rental_transfer_sum(self, moves, from_date, to_date, direction,
                             rental_loc_ids):
        """Apply the interval date bound + rental-location / policy exclusions
        to a prefetched ``moves`` recordset.  Same arithmetic the original
        per-call loop used, so ``_rental_transfer_qty`` and the batch produce
        identical numbers.

        * ``out`` — deducted when it departs at or before ``to_date`` (a unit
          leaving anywhere inside the window is unavailable for the complete
          interval); rental pickups (dest = rental location) are excluded.
        * ``in`` — credited only when guaranteed present before ``from_date``;
          rental returns excluded; the operation type's
          ``rental_incoming_policy`` decides whether external/produced supply
          counts (relocations of owned stock count by default unless
          ``ignore``).
        """
        to_date = to_date or from_date
        total = 0.0
        if direction == 'out':
            for move in moves:
                if not (move.date and move.date <= to_date):
                    continue
                if move.location_dest_id.id in rental_loc_ids:
                    continue
                # A Rental Purchase return (dest = supplier) is counted from its
                # scheduled date even while still 'waiting' on the receipt: the
                # paired hired-in supply is credited operationally on the IN
                # side (see below), so departure and arrival net out and we
                # never hide our own stock.
                total += move.product_uom_qty
            return total
        # direction == 'in'
        for move in moves:
            if not (move.date and move.date <= from_date):
                continue
            if move.location_id.id in rental_loc_ids:
                continue  # rental return — reserved term handles it
            policy = move.picking_type_id.rental_incoming_policy or 'projected'
            if policy == 'ignore':
                continue
            is_relocation = move.location_id.usage in ('internal', 'transit')
            # Hired-in (Rental Purchase) supply is trusted operationally by
            # nature — it is paired with a scheduled supplier return, so it is
            # credited from its arrival date regardless of the picking type's
            # incoming policy.  Soft check: the fields only exist when the
            # optional purchase / rental_purchase modules are installed.
            rp_supply = False
            if 'purchase_line_id' in move._fields and move.purchase_line_id:
                order = move.purchase_line_id.order_id
                if 'is_rental_purchase' in order._fields:
                    rp_supply = bool(order.is_rental_purchase)
            if policy == 'operational' or is_relocation or rp_supply:
                total += move.product_uom_qty
        return total

    # ── Reservation step-function (batch reuse of the native peak) ──────────
    def _rental_reserved_stepfn(self, from_date, to_date, warehouse_id=False,
                                ignored_soline_id=False):
        """Build the interval-INDEPENDENT reservation step-function ONCE for the
        whole report window ``[from_date, to_date]``.

        Returns ``(rented_quantities, key_dates)`` exactly as native
        ``sale.order.line._get_rented_quantities`` does — a ``defaultdict`` of
        date→signed-delta (pickup +, return −) and the sorted key dates.  Any
        sub-column's peak is then read with ``_reserved_peak`` and equals the
        native ``_get_unavailable_qty`` for that sub-column (the window's active
        lines are a superset of any sub-column's, and the extra lines net to
        zero inside the sub-column — see the equality tests).
        """
        self.ensure_one()
        lines = self._get_active_rental_lines(
            from_date, to_date,
            ignored_soline_id=ignored_soline_id, warehouse_id=warehouse_id)
        return lines._get_rented_quantities([from_date, to_date])

    def _reserved_peak(self, rented_quantities, base_key_dates,
                       from_date, to_date):
        """Peak concurrent reserved quantity over ``[from_date, to_date]`` —
        a faithful mirror of native ``product.product._get_unavailable_qty``'s
        cumulative-max loop, with ``from_date``/``to_date`` added as mandatory
        boundary dates (the same role native's ``mandatory_dates`` play)."""
        to_date = to_date or from_date
        key_dates = sorted(set(base_key_dates) | {from_date, to_date})
        cumulative = 0.0
        max_unavailable = 0.0
        for key_date in key_dates:
            if key_date > to_date:
                break
            cumulative += rented_quantities.get(key_date, 0.0)
            if key_date >= from_date:
                max_unavailable = max(cumulative, max_unavailable)
        return max_unavailable

    # ── Batch availability (reuses every scalar primitive; never a 2nd engine)
    def _rental_available_batch(self, columns, warehouse=False, company=False,
                                ignored_soline_id=False, clamp=True):
        """Batch of ``_rental_available_qty`` over ``self`` (many products) and
        many time ``columns`` for ONE ``(warehouse, company)``.

        ``columns`` is a list of ``(from_date, to_date)`` datetime pairs.
        Returns ``{product_id: [{'available', 'capacity'}, ... per column]}``
        where ``available`` respects ``clamp`` (signed when ``clamp=False``)
        and ``capacity = physical_total − transfer_out + transfer_in``.

        Reuses the SAME primitives the scalar engine uses — physical total,
        the reservation step-function, and the repair / transfer search+sum
        helpers — so each cell is provably equal to
        ``_rental_available_qty(from, to, warehouse, company, clamp=…)``.  No
        second availability formula lives here; this is purely a batching /
        caching orchestration.  (See ``test_rental_availability_batch``.)

        Everything that does NOT vary per product (the warehouse's internal
        location set, the on-hand quants, the at-customer moves, the transfer
        moves, the open repairs) is fetched ONCE for the whole product set with
        a single batched query per data type and then bucketed by product — so
        the query count is O(warehouses) rather than O(products × warehouses).
        The per-column arithmetic still runs through the identical sum helpers,
        so the numbers are unchanged (equality tests guard this).
        """
        if not columns:
            return {p.id: [] for p in self}
        win_start = min(c[0] for c in columns)
        win_end = max((c[1] or c[0]) for c in columns)
        wh_id = warehouse.id if warehouse else False
        rental_loc_ids = self.env['res.company'].sudo().search(
            []).rental_loc_id.ids
        repair_installed = 'repair.order' in self.env
        Move = self.env['stock.move']
        ids = self.ids

        # ── per-WAREHOUSE prefetch (once for all products) ──────────────────
        inside = self.env['stock.location']
        if warehouse and warehouse.view_location_id:
            inside = self.env['stock.location'].search([
                ('id', 'child_of', warehouse.view_location_id.id),
                ('usage', 'in', ('internal', 'transit'))])
        inside_ids = inside.ids

        # On-hand across the warehouse's own locations — one read_group.
        onhand = {}
        if inside_ids:
            for prod, qty in self.env['stock.quant']._read_group(
                    [('product_id', 'in', ids),
                     ('location_id', 'in', inside_ids)],
                    ['product_id'], ['quantity:sum']):
                onhand[prod.id] = qty

        # Net at-customer (done moves to/from the rental location, this wh) —
        # two read_groups for the whole product set.
        at_customer = defaultdict(float)
        rental_loc = company.rental_loc_id if company else False
        if rental_loc and warehouse:
            base = [('product_id', 'in', ids), ('state', '=', 'done'),
                    ('sale_line_id.order_id.warehouse_id', '=', warehouse.id)]
            for prod, qty in Move._read_group(
                    base + [('location_dest_id', '=', rental_loc.id)],
                    ['product_id'], ['quantity:sum']):
                at_customer[prod.id] += qty
            for prod, qty in Move._read_group(
                    base + [('location_id', '=', rental_loc.id)],
                    ['product_id'], ['quantity:sum']):
                at_customer[prod.id] -= qty

        # Transfer moves (out / in) — one search each, bucketed by product.
        out_ids, in_ids = defaultdict(list), defaultdict(list)
        if inside_ids:
            out_dest_clause = [('location_dest_id.usage', 'in', ('internal', 'transit'))]
            if 'rental_purchase_order_id' in Move._fields:
                # Rental Purchase returns leave to the vendor location — count
                # them as departures too (see _rental_transfer_moves).
                out_dest_clause = ['|'] + out_dest_clause \
                    + [('rental_purchase_order_id', '!=', False)]
            for m in Move.search([
                    ('product_id', 'in', ids),
                    ('state', 'not in', ('done', 'cancel')),
                    ('location_id', 'in', inside_ids),
                    ('location_dest_id', 'not in', inside_ids)] + out_dest_clause):
                out_ids[m.product_id.id].append(m.id)
            for m in Move.search([
                    ('product_id', 'in', ids),
                    ('state', 'not in', ('done', 'cancel')),
                    ('location_dest_id', 'in', inside_ids),
                    ('location_id', 'not in', inside_ids)]):
                in_ids[m.product_id.id].append(m.id)

        # Open repairs — one search, bucketed by product.
        repair_ids = defaultdict(list)
        if repair_installed and warehouse and warehouse.view_location_id:
            for r in self.env['repair.order'].sudo().search([
                    ('product_id', 'in', ids),
                    ('state', 'not in', ('done', 'cancel')),
                    ('location_id', 'child_of', warehouse.view_location_id.id)]):
                repair_ids[r.product_id.id].append(r.id)
        RepairSudo = (self.env['repair.order'].sudo()
                      if repair_installed else None)

        result = {}
        for product in self:
            # Interval-independent, from the prefetched buckets.
            if inside_ids:
                total = onhand.get(product.id, 0.0) + at_customer.get(product.id, 0.0)
            else:
                total = product._rental_physical_total(
                    warehouse=warehouse, company=company)
            rentable = product.rent_periodicity and hasattr(product, '_get_unavailable_qty')
            if rentable:
                rented, base_keys = product._rental_reserved_stepfn(
                    win_start, win_end, warehouse_id=wh_id,
                    ignored_soline_id=ignored_soline_id)
            else:
                rented, base_keys = {}, []
            out_moves = Move.browse(out_ids.get(product.id))
            in_moves = Move.browse(in_ids.get(product.id))
            repairs = (RepairSudo.browse(repair_ids.get(product.id))
                       if RepairSudo is not None else Move.browse())

            cells = []
            for (from_date, to_date) in columns:
                to_date = to_date or from_date
                reserved = (product._reserved_peak(
                    rented, base_keys, from_date, to_date) if rentable else 0.0)
                in_repair = product._rental_repair_sum(
                    repairs, from_date, to_date)
                t_out = product._rental_transfer_sum(
                    out_moves, from_date, to_date, 'out', rental_loc_ids)
                t_in = product._rental_transfer_sum(
                    in_moves, from_date, to_date, 'in', rental_loc_ids)
                capacity = total - t_out + t_in
                signed = capacity - reserved - in_repair
                cells.append({
                    'available': signed if not clamp else max(signed, 0.0),
                    'capacity': capacity,
                })
            result[product.id] = cells
        return result
