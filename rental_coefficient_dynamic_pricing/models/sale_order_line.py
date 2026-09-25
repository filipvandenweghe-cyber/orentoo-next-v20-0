from odoo import api, fields, models


class SaleOrderLine(models.Model):
    """Extend sale.order.line with coefficient & dynamic pricing.  [RI01–RI07]

    Upgrade-proof architecture
    --------------------------
    * All Odoo-native rental price retrieval is isolated in the
      ``rental.pricing.service._get_base_rental_price_for_line()``
      adapter.  THAT IS THE ONLY METHOD TO REVIEW AFTER ODOO 19.3.
    * The coefficient engine, dynamic pricing engine, and final-price
      composer live on the service and are completely independent from
      Odoo's internal rental pricing.

    rental_set compatibility
    ------------------------
    * Set component lines always keep price_unit = 0 (rental_set rule).
      Coefficient/dynamic pricing is never applied to components.
    * Fixed-price set parents: coefficient × dynamic is applied to the
      parent's fixed price (the price that super() already set).
    * Sum-of-components set parents: the parent price from super() is
      the sum of component pricelist prices.  Coefficient × dynamic is
      applied to that sum total.
    * In all cases, coefficient/dynamic is applied exactly once on the
      parent line — never on individual components.
    """

    _inherit = 'sale.order.line'

    # -- Stored pricing detail fields -----------------------------------------

    applied_coefficient_type_id = fields.Many2one(
        'rental.coefficient.type',
        string='Coefficient Type',
        copy=True,
        help='The type of the coefficient table used for this line.',
    )
    applied_coefficient_table_id = fields.Many2one(
        'rental.coefficient.table',
        string='Coefficient Table',
        copy=True,
        help='The coefficient table used for this line.',
    )
    applied_coefficient = fields.Float(
        string='Coefficient',
        digits=(12, 4),
        default=0.0,
        copy=True,
        help=(
            'Charged-duration multiplier applied to the base rental price. '
            'Example: coefficient 5 for a 7-day rental means the customer '
            'pays 5 × the base daily price instead of 7×.'
        ),
    )
    applied_dynamic_pricing_table_id = fields.Many2one(
        'rental.dynamic.pricing.table',
        string='Dynamic Pricing Table',
        copy=True,
        help='The dynamic pricing table used for this line.',
    )
    applied_dynamic_factor_percentage = fields.Float(
        string='Dynamic Factor (%)',
        digits=(12, 4),
        default=0.0,
        copy=True,
        help=(
            'Dynamic pricing factor as a percentage. '
            '100% = no price change, '
            '120% = +20% surcharge (peak season), '
            '80% = -20% discount (low season).'
        ),
    )
    applied_dynamic_multiplier = fields.Float(
        string='Dynamic Multiplier',
        digits=(12, 4),
        default=0.0,
        copy=True,
        help='Internal multiplier = dynamic factor percentage / 100.',
    )
    base_rental_price = fields.Float(
        string='Base Rental Price',
        digits='Product Price',
        help=(
            'The rental price as calculated by Odoo before coefficient '
            'and dynamic pricing adjustments are applied.'
        ),
    )
    manual_coefficient_override = fields.Boolean(
        string='Manual Coefficient',
        default=False,
        copy=False,
        help='Set automatically when the coefficient is manually edited.',
    )
    manual_dynamic_factor_override = fields.Boolean(
        string='Manual Dynamic Factor',
        default=False,
        copy=False,
        help='Set automatically when the dynamic factor is manually edited.',
    )
    manual_price_override = fields.Boolean(
        string='Manual Price',
        default=False,
        copy=False,
        help=(
            'Set automatically when the unit price is edited by hand. '
            'While set, the coefficient/dynamic engine will not overwrite '
            'the unit price on quantity changes. Cleared when the product, '
            'rental period or partner changes, or on "Update Rental Prices".'
        ),
    )

    # =====================================================================
    # rental_set hook: reapply coefficient after set expansion  [RF01]
    #
    # When rental_set expands a set and calls _apply_set_pricing(), it
    # recalculates the parent's price_unit (sum or fixed).  We hook in
    # AFTER to apply coefficient × dynamic on top.
    # >>> If rental_set changes _apply_set_pricing in a future version,
    #     review this override. <<<
    # =====================================================================

    # =====================================================================
    # Helpers — rental_set awareness
    # =====================================================================

    def _is_set_component_line(self):
        """Return True if this line is a rental_set component (price = 0)."""
        return bool(self.is_set_component)

    def _is_set_parent_line(self):
        """Return True if this line is a rental_set parent (carries price)."""
        return bool(self.is_set and not self.is_set_component)

    # =====================================================================
    # Set allocation recalculation  [RF01]
    #
    # After coefficient × dynamic adjusts the parent's price_unit, the
    # set_allocated_price on components must be updated so they sum to
    # the new total.  We delegate to rental_set's own allocation methods
    # which read self.price_unit (now coefficient-adjusted).
    #
    # >>> If rental_set changes _allocate_fixed_prices or
    #     _allocate_sum_prices in a future version, review this. <<<
    # =====================================================================

    def _recalculate_set_allocations(self):
        """Recalculate set_allocated_price on components after coefficient.

        Only acts on set parent lines.  Delegates to rental_set's own
        allocation methods which read self.price_unit to distribute the
        total across components.
        """
        self.ensure_one()
        if not self._is_set_parent_line():
            return
        if not self.set_child_line_ids:
            return
        pricing_mode = self.product_id.product_tmpl_id.set_pricing_mode
        if pricing_mode == 'fixed':
            self._allocate_fixed_prices()
        elif pricing_mode == 'sum':
            self._allocate_sum_prices_adjusted()

    def _allocate_sum_prices_adjusted(self):
        """Distribute the coefficient-adjusted parent total across components.

        rental_set's native _allocate_sum_prices() uses component product
        prices which don't include the coefficient.  This method scales
        the allocations so they sum to the actual parent total
        (price_unit × product_uom_qty).
        """
        self.ensure_one()
        children = self.set_child_line_ids
        if not children:
            return

        # Compute the raw sum of component values (same logic as rental_set)
        raw_total = 0.0
        raw_values = []
        for child in children:
            price = self._get_component_unit_price(child)
            comp_qty = child.product_uom_qty or 1.0
            raw_value = price * comp_qty
            raw_values.append((child, raw_value))
            raw_total += raw_value

        # The actual total that must be distributed
        adjusted_total = self.price_unit * self.product_uom_qty

        # Distribute proportionally
        for child, raw_value in raw_values:
            if raw_total > 0:
                allocated = adjusted_total * raw_value / raw_total
            else:
                allocated = adjusted_total / len(children) if children else 0.0
            child.with_context(rental_set_expanding=True).write({
                'set_allocated_price': allocated,
            })
            # Recurse into nested sets
            if child.is_set and child.set_child_line_ids:
                child._allocate_sum_prices_adjusted()

    # =====================================================================
    # rental_set component-change hooks  [RF03]
    #
    # When a set component qty/product changes, rental_set resets the sum-mode
    # parent price to the RAW component sum (bypassing coefficient/dynamic).
    # We (a) freeze a manually-priced parent so it is not overwritten, and
    # (b) reapply coefficient × dynamic to the fresh sum and rescale the
    # component allocations, so the invariant
    #   price_unit = SUM(component prices) × coefficient × dynamic
    # holds after a component edit — exactly as on create.
    # =====================================================================

    def _recompute_set_price_from_components(self):
        """Freeze a manually-priced set parent; otherwise recompute the sum.

        A set parent whose unit price was set by hand (manual_price_override)
        is effectively fixed: component changes must not overwrite it.  [RF03]
        """
        self.ensure_one()
        if self.manual_price_override:
            return
        return super()._recompute_set_price_from_components()

    def _reapply_set_derived_pricing(self):
        """Reapply coefficient × dynamic to a sum set after a component change.

        rental_set has just reset the parent price to the raw component sum;
        reapply the coefficient/dynamic adjustment to that sum (skipping a
        manually-priced parent) and rescale the component allocations to the
        adjusted total.  Uses the raw sum as the base, so it never compounds
        (RI07).  [RF03]
        """
        super()._reapply_set_derived_pricing()
        for line in self:
            if not (line.is_rental and line._is_set_parent_line()):
                continue
            if line.manual_price_override or not line.price_unit:
                continue
            line._apply_coefficient_dynamic_pricing(
                use_write=True, base_price_override=line.price_unit,
            )

    # =====================================================================
    # Create override — persist coefficient/dynamic values after INSERT
    # =====================================================================

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if not line.is_rental or not line.order_id or not line.price_unit:
                continue
            # Skip set components — they always have price_unit = 0.
            if line._is_set_component_line():
                continue
            # RI06: respect a price the user typed by hand at line creation.
            if line.manual_price_override:
                continue
            # ``price_unit`` after super() already holds the correct *base*
            # price for every line type: the native pricelist price for a
            # standalone line, the fixed set price for a fixed-mode parent,
            # and the component sum for a sum-mode parent (set by
            # rental_set._expand_rental_set during super().create).  Pass it
            # explicitly as the base so sum/fixed set parents are not reset to
            # the parent product's own list price.  Mirrors _compute_price_unit.
            line._apply_coefficient_dynamic_pricing(
                use_write=True, base_price_override=line.price_unit,
            )
        return lines

    # =====================================================================
    # Price computation override  [RI01]
    #
    # After super() computes prices (including rental_set's own override
    # which zeros components and recomputes sum-mode parents), we apply
    # coefficient × dynamic to each rental parent / standalone line.
    #
    # rental_set compatibility:
    # - Components: skipped (price_unit = 0, must stay 0).
    # - Fixed parents: super() already set the fixed pricelist price;
    #   we apply coefficient × dynamic to that.
    # - Sum parents: super() set parent price = sum of component prices;
    #   we apply coefficient × dynamic to that sum.
    # =====================================================================

    @api.depends('product_id', 'product_uom_id', 'product_uom_qty')
    def _compute_price_unit(self):
        force = self.env.context.get('force_price_recomputation')
        skip = self.env.context.get('skip_coefficient_dynamic_pricing')

        # RI06: detect a manual unit-price edit *before* super() runs.  The
        # engine always keeps technical_price_unit == price_unit, so any
        # divergence means the price was set by hand (form edit or external
        # write).  super() preserves that divergence via its own manual-price
        # guard; we capture it here so the engine below refrains from
        # overwriting the hand-typed price on a quantity change.
        if not force and not skip:
            for line in self:
                if line.is_rental and line._is_manual_price_edit():
                    line.manual_price_override = True

        super()._compute_price_unit()

        if skip:
            return

        for line in self:
            if not line.is_rental or not line.order_id:
                continue
            if not line.id or not isinstance(line.id, int):
                continue

            # Skip set components — their price must remain 0.
            if line._is_set_component_line():
                continue

            if force:
                line.manual_coefficient_override = False
                line.manual_dynamic_factor_override = False
                line.manual_price_override = False

            # RI06: a hand-typed price must survive quantity changes.  A
            # forced recomputation (Update Rental Prices) has cleared the
            # flag above and falls through to recompute from the base.
            if line.manual_price_override and not force:
                continue

            # price_unit from super() is the Odoo-native base rental price
            # (or the rental_set sum/fixed price for set parents).
            base_price = line.price_unit
            if not base_price:
                continue
            line._apply_coefficient_dynamic_pricing(
                use_write=False, base_price_override=base_price,
            )

    # =====================================================================
    # Central pricing pipeline — single source of truth
    # =====================================================================

    def _apply_coefficient_dynamic_pricing(
        self, use_write=False, base_price_override=None,
    ):
        """Compute coefficient + dynamic pricing and set the final price.

        :param bool use_write: when True, persist via write() (needed
            after create).  When False, set fields directly (inside compute).
        :param float base_price_override: if given, use this as the base
            price instead of querying the pricelist again.
        """
        self.ensure_one()
        svc = self.env['rental.pricing.service']

        # --- 1. Base price ---
        # >>> After Odoo 19.3 upgrade, review _get_base_rental_price_for_line
        if base_price_override is not None:
            # Odoo 20 prices a rental line for the whole rental period, so
            # every native base (standalone price, fixed set price, component
            # sum) is duration-scaled; the coefficient applies the duration,
            # so scale it back to a single period first.
            base_price = svc._to_single_period_price(self, base_price_override)
        else:
            base_price = svc._get_base_rental_price_for_line(self)
            if not base_price:
                base_price = self.product_id.lst_price or 0.0

        # --- 2. Coefficient ---
        coeff_vals = self._compute_coefficient_values()

        # --- 3. Dynamic pricing ---
        dp_vals = self._compute_dynamic_pricing_values()

        # --- 4. Final price (delegated to service) ---
        coefficient = coeff_vals.get('applied_coefficient') or 1.0
        multiplier = dp_vals.get('applied_dynamic_multiplier') or 1.0
        final_price = svc._compute_final_rental_price(
            base_price, coefficient, multiplier,
        )

        # --- 5. Persist ---
        vals = {
            'base_rental_price': base_price,
            'applied_coefficient_table_id': coeff_vals.get(
                'applied_coefficient_table_id', False,
            ),
            'applied_coefficient_type_id': coeff_vals.get(
                'applied_coefficient_type_id', False,
            ),
            'applied_coefficient': coefficient,
            'applied_dynamic_pricing_table_id': dp_vals.get(
                'applied_dynamic_pricing_table_id', False,
            ),
            'applied_dynamic_factor_percentage': dp_vals.get(
                'applied_dynamic_factor_percentage', 100.0,
            ),
            'applied_dynamic_multiplier': multiplier,
            'price_unit': final_price,
            'technical_price_unit': final_price,
        }

        if use_write:
            self.write(vals)
        else:
            # Assign under the ``sale_write_from_compute`` context.  When a
            # stored field is set on a persisted record, ``setattr`` routes
            # through the standard ``SaleOrderLine.write()``.  That write
            # strips ``technical_price_unit`` whenever it is set on its own
            # without ``price_unit`` and without this context (a guard meant
            # for readonly-view recomputes).  Because each ``setattr`` below
            # is a separate write, ``technical_price_unit`` would otherwise be
            # silently dropped and stay at the base value, desyncing from
            # ``price_unit``.  On the next recompute that desync makes
            # ``super()._compute_price_unit`` treat the line as a manually
            # priced one, skip resetting ``price_unit`` to the base, and let
            # our engine multiply the already-adjusted price by the
            # coefficient again — compounding the price on every quantity or
            # period change.  The context mirrors what super() itself uses in
            # ``_reset_price_unit`` and keeps the two fields in sync.
            line = self.with_context(sale_write_from_compute=True)
            for field_name, value in vals.items():
                setattr(line, field_name, value)

        # --- 6. Recalculate set allocations  [RF01] ---
        # After the parent's price_unit is adjusted by coefficient × dynamic,
        # recalculate set_allocated_price on components so allocations match
        # the final order total.
        self._recalculate_set_allocations()

    # =====================================================================
    # Coefficient computation  [RE03, RE04]
    # =====================================================================

    def _compute_coefficient_values(self):
        """Return a dict of coefficient field values for this line.

        Respects manual override.  Delegates table selection and
        coefficient lookup to the pricing service.
        """
        self.ensure_one()
        if self.manual_coefficient_override:
            return {
                'applied_coefficient_type_id': self.applied_coefficient_type_id.id,
                'applied_coefficient_table_id': self.applied_coefficient_table_id.id,
                'applied_coefficient': self.applied_coefficient,
            }

        svc = self.env['rental.pricing.service']
        product = self.product_id.product_tmpl_id
        partner = self.order_id.partner_id
        warehouse = self.order_id.warehouse_id
        start_dt = self.start_date
        end_dt = self.return_date

        table = svc._get_applicable_coefficient_table(
            product, partner, warehouse,
        )

        vals = {
            'applied_coefficient_table_id': table.id if table else False,
            'applied_coefficient_type_id': (
                table.coefficient_type_id.id if table else False
            ),
        }

        if table:
            duration_int = svc._compute_duration_integer(
                start_dt, end_dt, table.duration_unit,
            )
            coeff = table.get_coefficient_for_duration(duration_int)
            vals['applied_coefficient'] = coeff if coeff else max(duration_int, 1)
        else:
            duration_int = svc._compute_duration_integer(
                start_dt, end_dt, 'day',
            )
            vals['applied_coefficient'] = max(duration_int, 1)

        return vals

    # =====================================================================
    # Dynamic pricing computation  [RE05, RE06]
    # =====================================================================

    def _compute_dynamic_pricing_values(self):
        """Return a dict of dynamic pricing field values for this line.

        Respects manual override and per-customer toggle.  Delegates
        factor computation to the pricing service.
        """
        self.ensure_one()
        if self.manual_dynamic_factor_override:
            return {
                'applied_dynamic_pricing_table_id': (
                    self.applied_dynamic_pricing_table_id.id
                ),
                'applied_dynamic_factor_percentage': (
                    self.applied_dynamic_factor_percentage
                ),
                'applied_dynamic_multiplier': self.applied_dynamic_multiplier,
            }

        # RP10: per-customer dynamic pricing toggle
        partner = self.order_id.partner_id
        if partner and not partner.use_dynamic_pricing:
            return {
                'applied_dynamic_pricing_table_id': False,
                'applied_dynamic_factor_percentage': 100.0,
                'applied_dynamic_multiplier': 1.0,
            }

        svc = self.env['rental.pricing.service']
        product = self.product_id.product_tmpl_id
        warehouse = self.order_id.warehouse_id
        start_dt = self.start_date
        end_dt = self.return_date

        table = product._get_applicable_dynamic_pricing_table(warehouse)
        pct = svc._get_dynamic_factor_percentage_for_context(
            product, warehouse, start_dt, end_dt,
        )
        return {
            'applied_dynamic_pricing_table_id': table.id if table else False,
            'applied_dynamic_factor_percentage': pct,
            'applied_dynamic_multiplier': pct / 100.0,
        }

    # =====================================================================
    # Manual unit-price detection  [RI06]
    # =====================================================================

    def _is_manual_price_edit(self):
        """Return True when price_unit was set by hand.

        The coefficient/dynamic engine always writes
        ``technical_price_unit == price_unit``.  Any difference between the
        two therefore signals a manual unit-price edit that must be
        preserved instead of being overwritten by the engine.  [RI06]
        """
        self.ensure_one()
        currency = (
            self.currency_id
            or self.company_id.currency_id
            or self.env.company.currency_id
        )
        if not currency:
            return self.technical_price_unit != self.price_unit
        return bool(
            currency.compare_amounts(
                self.technical_price_unit, self.price_unit,
            )
        )

    # =====================================================================
    # Onchange — detect manual edits on the form
    # =====================================================================

    @api.onchange('price_unit')
    def _onchange_price_unit_manual(self):
        """Lock a manually edited unit price against engine recompute.  [RI06]

        Fires for every ``price_unit`` change, but only flags the line when
        the new value diverges from ``technical_price_unit`` — i.e. the user
        typed it.  Engine-driven price updates keep the two in sync and are
        therefore not mistaken for a manual edit.
        """
        if self.is_rental and self._is_manual_price_edit():
            self.manual_price_override = True

    @api.onchange('applied_coefficient')
    def _onchange_applied_coefficient(self):
        """Mark coefficient as manually overridden and recalculate price."""
        if self.is_rental and self.applied_coefficient and self.base_rental_price:
            self.manual_coefficient_override = True
            self._recompute_price_from_stored_values()

    @api.onchange('applied_dynamic_factor_percentage')
    def _onchange_applied_dynamic_factor_percentage(self):
        """Mark dynamic factor as manually overridden and recalculate."""
        if self.is_rental and self.applied_dynamic_factor_percentage:
            self.manual_dynamic_factor_override = True
            self.applied_dynamic_multiplier = (
                self.applied_dynamic_factor_percentage / 100.0
            )
            if self.base_rental_price:
                self._recompute_price_from_stored_values()

    def _recompute_price_from_stored_values(self):
        """Recalculate price_unit using the service's final-price composer."""
        self.ensure_one()
        svc = self.env['rental.pricing.service']
        final_price = svc._compute_final_rental_price(
            self.base_rental_price or 0.0,
            self.applied_coefficient or 1.0,
            self.applied_dynamic_multiplier or 1.0,
        )
        # Editing the coefficient/dynamic factor re-establishes engine
        # control over the price, so any prior manual price lock is dropped.
        self.manual_price_override = False
        self.price_unit = final_price
        self.technical_price_unit = final_price

    # =====================================================================
    # Reset manual overrides on significant context changes
    # =====================================================================

    @api.onchange('product_id', 'start_date', 'return_date')
    def _onchange_reset_coefficient_dynamic_overrides(self):
        """Clear manual overrides when key pricing context changes.  [RI06]"""
        if self.is_rental:
            self.manual_coefficient_override = False
            self.manual_dynamic_factor_override = False
            self.manual_price_override = False
