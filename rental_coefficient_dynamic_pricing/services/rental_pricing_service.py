import math

from odoo import api, models

# Conversion factors: how many minutes in one unit
_UNIT_TO_MINUTES = {
    'minute': 1,
    'hour': 60,
    'day': 60 * 24,
    'week': 60 * 24 * 7,
    'month': 60 * 24 * 30,  # approximate; consistent with ceil rounding
}


class RentalPricingService(models.AbstractModel):
    """Reusable pricing engine helpers.

    [RE01–RE10]

    This abstract model groups all coefficient and dynamic pricing helpers
    so they can be called from any context (sale order line, website,
    reports) without duplicating logic.

    Architecture notes
    ------------------
    * The **base-price adapter** (_get_base_rental_price_for_line) is the
      *only* place where Odoo's native rental/pricelist logic is touched.
      After upgrading to Odoo 19.3, review this single method.
    * The **coefficient engine** and **dynamic pricing engine** are
      completely independent from Odoo's internal rental pricing.
    * The **final-price composer** combines base × coefficient × multiplier.
    """

    _name = 'rental.pricing.service'
    _description = 'Rental Pricing Service'

    # =====================================================================
    # 1. Base-price adapter  [RE01]
    #
    # >>> REVIEW THIS METHOD AFTER EVERY ODOO UPGRADE <<<
    #
    # This is the only coupling point to Odoo's native rental price logic.
    # It retrieves the price that Odoo would normally set on a rental SOL
    # before any coefficient / dynamic pricing is applied.
    # =====================================================================

    @api.model
    def _get_base_rental_price_for_line(self, line):  # RE01
        """Return the Odoo-native base rental price for ONE rental period.

        This calls Odoo's standard ``_get_pricelist_price()``.  If no
        pricelist rule matches, the product falls back to its Sales Price
        (``lst_price``).

        **Odoo 20**: rental pricing moved from ``product.pricing`` records to
        ``product.rent_periodicity`` + the pricelist, and for a rental line
        ``_get_pricelist_price()`` now returns the price of the WHOLE rental
        period (the sum of the price at each periodicity step).  The
        coefficient engine expects the price of a SINGLE period — it applies
        the duration itself, through the coefficient table — so the number of
        periods is divided back out here.  Without that, the duration would
        be counted twice.

        :param sale.order.line line: the rental order line.
        :returns: base rental price (float) for a single rental period.
        """
        # Call the *original* chain – coefficient/dynamic overrides must
        # skip themselves when this flag is set.
        price = line.with_context(
            skip_coefficient_dynamic_pricing=True,
        )._get_pricelist_price()
        return self._to_single_period_price(line, price)

    @api.model
    def _to_single_period_price(self, line, price):  # RE01
        """Scale an Odoo-native rental price back to ONE rental period.

        Odoo 20 prices a rental line for the whole rental period (the sum of
        the price at each periodicity step).  Every base price this engine
        starts from — the pricelist price of a standalone line, the fixed
        price of a set parent, the component sum of a sum-mode parent — is
        therefore duration-scaled already, while the coefficient table
        applies the duration itself.  Dividing the number of periods back out
        keeps the duration counted exactly once.

        Returns ``price`` unchanged for anything that is not a dated rental
        line of a product with a rental periodicity.
        """
        if not price or not line.is_rental:
            return price
        product = line.product_id
        if not product.rent_periodicity or not (line.start_date and line.return_date):
            return price
        periods = product._get_number_of_periods(line.start_date, line.return_date)
        return price / periods if periods > 1 else price

    # =====================================================================
    # 2. Duration calculator  [RE02]
    # =====================================================================

    @api.model
    def _compute_duration_integer(self, start_dt, end_dt, duration_unit):  # RE02
        """Convert a rental period into a rounded-up integer duration.

        For any positive rental duration the result is at least 1.

        Examples (duration_unit = 'day'):
            25 h → 2 days,  47 h → 2 days,  48 h → 2 days,  49 h → 3 days
        Examples (duration_unit = 'hour'):
            90 min → 2 hours,  30 min → 1 hour

        :param datetime start_dt: rental start.
        :param datetime end_dt: rental end.
        :param str duration_unit: one of minute, hour, day, week, month.
        :returns: integer duration (>= 1 for positive periods, 0 otherwise).
        """
        if not start_dt or not end_dt or end_dt <= start_dt:
            return 0
        total_minutes = (end_dt - start_dt).total_seconds() / 60.0
        unit_minutes = _UNIT_TO_MINUTES.get(duration_unit, _UNIT_TO_MINUTES['day'])
        duration = math.ceil(total_minutes / unit_minutes)
        return max(duration, 1)

    # =====================================================================
    # 3. Coefficient engine  [RE03–RE06]
    # =====================================================================

    @api.model
    def _get_applicable_coefficient_table(
        self, product, partner, warehouse, coefficient_type=None,
    ):  # RE03
        """Select the single best coefficient table for the given context.

        Resolution order:
        1. Intersection of product/warehouse tables, customer tables, and
           warehouse company.  If multiple match → lowest sequence wins.
        2. If no intersection → standard table for the company and
           coefficient type.
        3. If no standard table → returns empty recordset (caller uses
           fallback duration).

        :param product.template product: the rental product.
        :param res.partner partner: the customer.
        :param stock.warehouse warehouse: the sale order warehouse.
        :param rental.coefficient.type coefficient_type: optional filter.
        :returns: ``rental.coefficient.table`` singleton or empty recordset.
        """
        CTable = self.env['rental.coefficient.table']
        if not warehouse:
            return CTable

        company = warehouse.company_id

        # Product/warehouse tables
        product_tables = product._get_applicable_coefficient_tables(warehouse)

        # Customer tables (filtered by company)
        customer_tables = (
            partner._get_customer_allowed_coefficient_tables(company)
            if partner else CTable
        )

        # Intersection
        if product_tables and customer_tables:
            candidates = product_tables & customer_tables
        elif product_tables:
            candidates = product_tables
        elif customer_tables:
            candidates = customer_tables
        else:
            candidates = CTable

        # Filter by company
        candidates = candidates.filtered(lambda t: t.company_id == company)

        # Optional type filter
        if coefficient_type:
            candidates = candidates.filtered(
                lambda t: t.coefficient_type_id == coefficient_type
            )

        if candidates:
            return candidates.sorted('sequence')[:1]

        # Fallback: standard table for this company (+ optional type)
        domain = [
            ('is_standard', '=', True),
            ('company_id', '=', company.id),
        ]
        if coefficient_type:
            domain.append(('coefficient_type_id', '=', coefficient_type.id))
        return CTable.search(domain, order='sequence', limit=1)

    @api.model
    def _get_coefficient_for_context(
        self, product, partner, warehouse, start_dt, end_dt,
        coefficient_type=None,
    ):  # RE04
        """Return the coefficient value for a rental context.

        If a matching table and line are found, returns the coefficient
        from the table.  Otherwise falls back to the rounded-up duration
        (minimum 1 for positive periods).

        :returns: float coefficient (>= 1 for positive periods).
        """
        table = self._get_applicable_coefficient_table(
            product, partner, warehouse, coefficient_type,
        )

        if table:
            duration_int = self._compute_duration_integer(
                start_dt, end_dt, table.duration_unit,
            )
            coeff = table.get_coefficient_for_duration(duration_int)
            if coeff:
                return coeff
            # Table exists but no matching line → fallback to duration
            return max(duration_int, 1) if duration_int > 0 else 1.0

        # No table at all → fallback: duration in days (default unit)
        duration_int = self._compute_duration_integer(start_dt, end_dt, 'day')
        return max(duration_int, 1) if duration_int > 0 else 1.0

    # =====================================================================
    # 4. Dynamic pricing engine  [RE05–RE07]
    # =====================================================================

    @api.model
    def _get_dynamic_factor_percentage_for_context(
        self, product, warehouse, start_dt, end_dt,
    ):  # RE05
        """Return the weighted-average factor percentage for a rental context.

        :returns: float percentage (e.g. 120.0 for +20 %).
                  Returns 100.0 when no table or no lines apply.
        """
        if not warehouse:
            return 100.0
        table = product._get_applicable_dynamic_pricing_table(warehouse)
        if not table:
            return 100.0
        return table.get_weighted_factor_percentage(start_dt, end_dt)

    @api.model
    def _get_dynamic_multiplier_for_context(
        self, product, warehouse, start_dt, end_dt,
    ):  # RE06
        """Return the dynamic pricing multiplier (percentage / 100).

        :returns: float multiplier (e.g. 1.20 for +20 %).
        """
        pct = self._get_dynamic_factor_percentage_for_context(
            product, warehouse, start_dt, end_dt,
        )
        return pct / 100.0

    # =====================================================================
    # 5. Final price composer  [RE07]
    # =====================================================================

    @api.model
    def _compute_final_rental_price(
        self, base_price, coefficient, dynamic_multiplier,
    ):  # RE07
        """Combine the three pricing components.

        final_price = base_price × coefficient × dynamic_multiplier

        :param float base_price: Odoo-native base rental price (for one
            time-unit from the pricelist / product.pricing).
        :param float coefficient: from the coefficient engine.
        :param float dynamic_multiplier: from the dynamic pricing engine.
        :returns: float final unit price before quantity.
        """
        return base_price * coefficient * dynamic_multiplier
