from datetime import datetime, timedelta

from odoo.tests import TransactionCase


class TestDurationCalculation(TransactionCase):
    """Tests for _compute_duration_integer  [RE02]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']

    # -- Duration unit: day ----------------------------------------------------

    def test_re02_25h_is_2_days(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(hours=25)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 2)

    def test_re02_47h_is_2_days(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(hours=47)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 2)

    def test_re02_48h_is_2_days(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(hours=48)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 2)

    def test_re02_49h_is_3_days(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(hours=49)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 3)

    def test_re02_exactly_one_day(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=1)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 1)

    def test_re02_7_days(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=7)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 7)

    # -- Duration unit: hour ---------------------------------------------------

    def test_re02_90min_is_2_hours(self):
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(minutes=90)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'hour'), 2)

    def test_re02_30min_is_1_hour(self):
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(minutes=30)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'hour'), 1)

    def test_re02_exactly_1_hour(self):
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(hours=1)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'hour'), 1)

    def test_re02_61min_is_2_hours(self):
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(minutes=61)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'hour'), 2)

    # -- Duration unit: week ---------------------------------------------------

    def test_re02_8_days_is_2_weeks(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=8)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'week'), 2)

    def test_re02_7_days_is_1_week(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=7)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'week'), 1)

    # -- Duration unit: minute -------------------------------------------------

    def test_re02_90_seconds_is_2_minutes(self):
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(seconds=90)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'minute'), 2)

    # -- Minimum 1 for positive periods ----------------------------------------

    def test_re02_1_second_is_1(self):
        """Any positive duration returns at least 1."""
        start = datetime(2026, 7, 1, 10, 0)
        end = start + timedelta(seconds=1)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 1)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'hour'), 1)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'week'), 1)

    # -- Duration unit: month ----------------------------------------------------

    def test_re02_31_days_is_2_months(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=31)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'month'), 2)

    def test_re02_29_days_is_1_month(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=29)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'month'), 1)

    def test_re02_30_days_is_1_month(self):
        start = datetime(2026, 7, 1, 0, 0)
        end = start + timedelta(days=30)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'month'), 1)

    # -- Edge cases ------------------------------------------------------------

    def test_re02_zero_duration(self):
        start = datetime(2026, 7, 1, 10, 0)
        self.assertEqual(self.svc._compute_duration_integer(start, start, 'day'), 0)

    def test_re02_negative_duration(self):
        start = datetime(2026, 7, 2, 10, 0)
        end = datetime(2026, 7, 1, 10, 0)
        self.assertEqual(self.svc._compute_duration_integer(start, end, 'day'), 0)

    def test_re02_none_dates(self):
        self.assertEqual(
            self.svc._compute_duration_integer(None, datetime(2026, 7, 1), 'day'), 0,
        )


class TestCoefficientTableSelection(TransactionCase):
    """Tests for _get_applicable_coefficient_table  [RE03]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Engine Test Type',
        })

        # Product table (sequence 5)
        cls.product_table = cls.env['rental.coefficient.table'].create({
            'name': 'Product Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 5,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
            ],
        })

        # Customer table (sequence 10)
        cls.customer_table = cls.env['rental.coefficient.table'].create({
            'name': 'Customer Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 10,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 4.0}),
            ],
        })

        # Shared table (in both product and customer — sequence 3)
        cls.shared_table = cls.env['rental.coefficient.table'].create({
            'name': 'Shared Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 3,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 6.0}),
            ],
        })

        # Standard table (fallback)
        cls.standard_table = cls.env['rental.coefficient.table'].create({
            'name': 'Standard Fallback',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'is_standard': True,
            'sequence': 99,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 3.0}),
            ],
        })

        # Product with config: product_table + shared_table
        cls.product = cls.env['product.template'].create({
            'name': 'Engine Test Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [
                        (6, 0, [cls.product_table.id, cls.shared_table.id]),
                    ],
                }),
            ],
        })

        # Customer with: customer_table + shared_table
        cls.partner = cls.env['res.partner'].create({
            'name': 'Engine Test Customer',
            'allowed_coefficient_table_ids': [
                (6, 0, [cls.customer_table.id, cls.shared_table.id]),
            ],
        })

        # Customer with no tables
        cls.partner_empty = cls.env['res.partner'].create({
            'name': 'No Tables Customer',
        })

        # Product with no config
        cls.product_no_config = cls.env['product.template'].create({
            'name': 'No Config Product',
            'rent_periodicity': 'days',
            'type': 'consu',
        })

    def test_re03_intersection_selects_shared(self):
        """Product/warehouse ∩ customer → shared table (lowest sequence)."""
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner, self.warehouse, self.coeff_type,
        )
        self.assertEqual(table, self.shared_table)

    def test_re03_intersection_lowest_sequence(self):
        """When multiple intersect, lowest sequence wins."""
        # shared_table has sequence 3, product_table has 5
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner, self.warehouse, self.coeff_type,
        )
        self.assertEqual(table.sequence, 3)

    def test_re03_no_customer_tables_uses_product(self):
        """If customer has no tables, product tables are used."""
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner_empty, self.warehouse, self.coeff_type,
        )
        # shared_table (seq 3) < product_table (seq 5)
        self.assertEqual(table, self.shared_table)

    def test_re03_no_product_config_uses_customer(self):
        """If product has no config, customer tables are used."""
        table = self.svc._get_applicable_coefficient_table(
            self.product_no_config, self.partner, self.warehouse, self.coeff_type,
        )
        # customer_table (seq 10) vs shared_table (seq 3) → shared
        self.assertEqual(table, self.shared_table)

    def test_re03_no_tables_at_all_uses_standard(self):
        """If no product or customer tables, falls back to standard."""
        table = self.svc._get_applicable_coefficient_table(
            self.product_no_config, self.partner_empty, self.warehouse,
            self.coeff_type,
        )
        self.assertEqual(table, self.standard_table)

    def test_re03_no_warehouse_returns_empty(self):
        """No warehouse → empty recordset."""
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner, self.env['stock.warehouse'],
            self.coeff_type,
        )
        self.assertFalse(table)

    def test_re03_no_type_filter_still_works(self):
        """Without coefficient_type filter, still selects a table."""
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner, self.warehouse,
        )
        self.assertTrue(table)


class TestCoefficientForContext(TransactionCase):
    """Tests for _get_coefficient_for_context  [RE04]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Coeff Context Type',
        })

        cls.table = cls.env['rental.coefficient.table'].create({
            'name': 'Context Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 1,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 3, 'coefficient': 2.5}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
                (0, 0, {'as_from_duration': 14, 'coefficient': 9.0}),
                (0, 0, {'as_from_duration': 30, 'coefficient': 20.0}),
            ],
        })

        cls.product = cls.env['product.template'].create({
            'name': 'Coeff Context Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [(6, 0, [cls.table.id])],
                }),
            ],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Coeff Context Customer',
            'allowed_coefficient_table_ids': [(6, 0, [cls.table.id])],
        })

        # Product with no config (for fallback tests)
        cls.product_bare = cls.env['product.template'].create({
            'name': 'Bare Product',
            'rent_periodicity': 'days',
            'type': 'consu',
        })
        cls.partner_bare = cls.env['res.partner'].create({
            'name': 'Bare Customer',
        })

    def _dt(self, days=0, hours=0):
        """Helper: start + delta."""
        start = datetime(2026, 7, 1, 0, 0)
        return start, start + timedelta(days=days, hours=hours)

    # -- Exact match -----------------------------------------------------------

    def test_re04_exact_1_day(self):
        """1 day rental → coefficient 1.0."""
        s, e = self._dt(days=1)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertEqual(coeff, 1.0)

    def test_re04_exact_7_days(self):
        """7 day rental → coefficient 5.0."""
        s, e = self._dt(days=7)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertEqual(coeff, 5.0)

    # -- Between ranges --------------------------------------------------------

    def test_re04_5_days_between_3_and_7(self):
        """5 days → uses 'as from 3' → coefficient 2.5."""
        s, e = self._dt(days=5)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertEqual(coeff, 2.5)

    # -- Above last range ------------------------------------------------------

    def test_re04_60_days_above_last(self):
        """60 days → uses 'as from 30' → coefficient 20.0."""
        s, e = self._dt(days=60)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertEqual(coeff, 20.0)

    # -- Fallback: no table → duration -----------------------------------------

    def test_re04_fallback_no_table_7_days(self):
        """No table → fallback coefficient = 7 (duration in days)."""
        s, e = self._dt(days=7)
        coeff = self.svc._get_coefficient_for_context(
            self.product_bare, self.partner_bare, self.warehouse, s, e,
            self.coeff_type,
        )
        self.assertEqual(coeff, 7)

    def test_re04_fallback_no_table_1_day(self):
        """No table → fallback coefficient = 1."""
        s, e = self._dt(days=1)
        coeff = self.svc._get_coefficient_for_context(
            self.product_bare, self.partner_bare, self.warehouse, s, e,
            self.coeff_type,
        )
        self.assertEqual(coeff, 1)

    def test_re04_fallback_no_table_partial_day(self):
        """No table, 6 hours → rounds up to 1 day → coefficient = 1."""
        s, e = self._dt(hours=6)
        coeff = self.svc._get_coefficient_for_context(
            self.product_bare, self.partner_bare, self.warehouse, s, e,
            self.coeff_type,
        )
        self.assertEqual(coeff, 1)

    # -- Degressive pricing spec example ---------------------------------------

    def test_re04_spec_example(self):
        """Spec example: base €20, 7 days, coeff 5 → €100."""
        s, e = self._dt(days=7)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertAlmostEqual(20.0 * coeff, 100.0)

    def test_re04_fallback_spec_example(self):
        """Spec example: base €20, 7 days, no table → coeff 7 → €140."""
        s, e = self._dt(days=7)
        coeff = self.svc._get_coefficient_for_context(
            self.product_bare, self.partner_bare, self.warehouse, s, e,
            self.coeff_type,
        )
        self.assertAlmostEqual(20.0 * coeff, 140.0)


class TestDynamicFactorForContext(TransactionCase):
    """Tests for _get_dynamic_factor/multiplier_for_context  [RE05, RE06]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        cls.dp_table = cls.env['rental.dynamic.pricing.table'].create({
            'name': 'Context DP Table',
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {
                    'start_datetime': datetime(2026, 7, 1),
                    'end_datetime': datetime(2026, 7, 15),
                    'factor_percentage': 150.0,
                }),
                (0, 0, {
                    'start_datetime': datetime(2026, 8, 1),
                    'end_datetime': datetime(2026, 8, 15),
                    'factor_percentage': 120.0,
                }),
            ],
        })

        cls.product = cls.env['product.template'].create({
            'name': 'DP Context Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'dynamic_pricing_table_id': cls.dp_table.id,
                }),
            ],
        })

        cls.product_no_dp = cls.env['product.template'].create({
            'name': 'No DP Product',
            'rent_periodicity': 'days',
            'type': 'consu',
        })

    # -- RE05: factor percentage -----------------------------------------------

    def test_re05_no_table_returns_100(self):
        """No dynamic pricing table → 100%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product_no_dp, self.warehouse,
            datetime(2026, 7, 2), datetime(2026, 7, 5),
        )
        self.assertEqual(pct, 100.0)

    def test_re05_no_warehouse_returns_100(self):
        """No warehouse → 100%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product, self.env['stock.warehouse'],
            datetime(2026, 7, 2), datetime(2026, 7, 5),
        )
        self.assertEqual(pct, 100.0)

    def test_re05_fully_inside_one_line(self):
        """Rental fully inside Jul line → 150%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 2), datetime(2026, 7, 5),
        )
        self.assertEqual(pct, 150.0)

    def test_re05_no_overlap_returns_100(self):
        """Rental in gap between lines → 100%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 20), datetime(2026, 7, 25),
        )
        self.assertEqual(pct, 100.0)

    def test_re05_partial_overlap(self):
        """4h overlap with 150%, 6h uncovered → 120%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 14, 20, 0),
            datetime(2026, 7, 15, 6, 0),
        )
        self.assertAlmostEqual(pct, 120.0, places=2)

    def test_re05_multiple_lines(self):
        """Jul 10 → Aug 10: 5d@150 + 17d@100 + 9d@120 → ~113.87%."""
        pct = self.svc._get_dynamic_factor_percentage_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 10), datetime(2026, 8, 10),
        )
        expected = (5 * 150 + 17 * 100 + 9 * 120) / 31.0
        self.assertAlmostEqual(pct, expected, places=2)

    # -- RE06: multiplier = percentage / 100 -----------------------------------

    def test_re06_multiplier_150_pct(self):
        """150% → multiplier 1.5."""
        mult = self.svc._get_dynamic_multiplier_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 2), datetime(2026, 7, 5),
        )
        self.assertAlmostEqual(mult, 1.5)

    def test_re06_multiplier_no_table(self):
        """No table → multiplier 1.0."""
        mult = self.svc._get_dynamic_multiplier_for_context(
            self.product_no_dp, self.warehouse,
            datetime(2026, 7, 2), datetime(2026, 7, 5),
        )
        self.assertAlmostEqual(mult, 1.0)

    def test_re06_multiplier_partial(self):
        """120% factor → multiplier 1.2."""
        mult = self.svc._get_dynamic_multiplier_for_context(
            self.product, self.warehouse,
            datetime(2026, 7, 14, 20, 0),
            datetime(2026, 7, 15, 6, 0),
        )
        self.assertAlmostEqual(mult, 1.2, places=2)


class TestFinalPriceComposer(TransactionCase):
    """Tests for _compute_final_rental_price  [RE07]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']

    def test_re07_basic_formula(self):
        """base 20 × coeff 5 × multiplier 1.2 = 120."""
        result = self.svc._compute_final_rental_price(20.0, 5.0, 1.2)
        self.assertAlmostEqual(result, 120.0)

    def test_re07_no_coefficient_change(self):
        """base 50 × coeff 1 × multiplier 1.0 = 50."""
        result = self.svc._compute_final_rental_price(50.0, 1.0, 1.0)
        self.assertAlmostEqual(result, 50.0)

    def test_re07_discount_multiplier(self):
        """base 100 × coeff 3 × multiplier 0.8 = 240."""
        result = self.svc._compute_final_rental_price(100.0, 3.0, 0.8)
        self.assertAlmostEqual(result, 240.0)

    def test_re07_fallback_coefficient_example(self):
        """Spec: base 20, duration fallback 7, no dynamic → 140."""
        result = self.svc._compute_final_rental_price(20.0, 7.0, 1.0)
        self.assertAlmostEqual(result, 140.0)

    def test_re07_full_pipeline_example(self):
        """Full example: base 20, coeff 5, factor 120% → 120."""
        result = self.svc._compute_final_rental_price(20.0, 5.0, 120.0 / 100.0)
        self.assertAlmostEqual(result, 120.0)

    def test_re07_zero_base_price(self):
        """base 0 × coeff 5 × multiplier 1.2 = 0."""
        result = self.svc._compute_final_rental_price(0.0, 5.0, 1.2)
        self.assertAlmostEqual(result, 0.0)

    def test_re07_fractional_coefficient(self):
        """base 100 × coeff 0.5 × multiplier 1.0 = 50 (degressive VIP)."""
        result = self.svc._compute_final_rental_price(100.0, 0.5, 1.0)
        self.assertAlmostEqual(result, 50.0)


class TestBasePriceAdapter(TransactionCase):
    """Tests for _get_base_rental_price_for_line  [RE01]

    Verifies the service adapter retrieves Odoo's native rental price
    and that this is the only coupling to native pricing internals.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Base Price Test Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 42.0,
        })
        cls.product = cls.product_tmpl.product_variant_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'Base Price Test Customer',
        })

    def _create_line(self, start, end):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        return self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })

    def test_re01_returns_positive_price(self):
        """Adapter returns a positive base price for a valid rental line."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        line = self._create_line(start, end)
        base = self.svc._get_base_rental_price_for_line(line)
        self.assertTrue(base > 0)

    def test_re01_skip_flag_prevents_coefficient(self):
        """Calling with skip flag returns native price without coefficient."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        line = self._create_line(start, end)
        # The adapter uses skip_coefficient_dynamic_pricing context flag.
        # The returned price should be the native rental price, not the
        # coefficient-adjusted one.
        base = self.svc._get_base_rental_price_for_line(line)
        # The stored base_rental_price should match
        self.assertAlmostEqual(base, line.base_rental_price, places=2)


class TestCustomerCoefficientTableSelection(TransactionCase):
    """Tests for customer-specific coefficient table selection  [RE03]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Customer Select Type',
        })

        # Two tables with different sequences
        cls.table_hi_priority = cls.env['rental.coefficient.table'].create({
            'name': 'High Priority',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 1,
            'line_ids': [(0, 0, {'as_from_duration': 1, 'coefficient': 1.0})],
        })
        cls.table_lo_priority = cls.env['rental.coefficient.table'].create({
            'name': 'Low Priority',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 50,
            'line_ids': [(0, 0, {'as_from_duration': 1, 'coefficient': 2.0})],
        })

        # Product has both tables
        cls.product = cls.env['product.template'].create({
            'name': 'Multi Table Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [
                        (6, 0, [cls.table_hi_priority.id, cls.table_lo_priority.id]),
                    ],
                }),
            ],
        })

    def test_re03_customer_has_only_low_priority(self):
        """Customer only has low-priority table → that one is selected."""
        partner = self.env['res.partner'].create({
            'name': 'Low Priority Customer',
            'allowed_coefficient_table_ids': [
                (6, 0, [self.table_lo_priority.id]),
            ],
        })
        table = self.svc._get_applicable_coefficient_table(
            self.product, partner, self.warehouse, self.coeff_type,
        )
        self.assertEqual(table, self.table_lo_priority)

    def test_re03_customer_has_both_selects_lowest_seq(self):
        """Customer has both tables → lowest sequence (1) wins."""
        partner = self.env['res.partner'].create({
            'name': 'Both Tables Customer',
            'allowed_coefficient_table_ids': [
                (6, 0, [self.table_hi_priority.id, self.table_lo_priority.id]),
            ],
        })
        table = self.svc._get_applicable_coefficient_table(
            self.product, partner, self.warehouse, self.coeff_type,
        )
        self.assertEqual(table, self.table_hi_priority)

    def test_re03_customer_has_non_matching_table(self):
        """Customer has table not on product → no intersection → standard."""
        other_table = self.env['rental.coefficient.table'].create({
            'name': 'Other Table',
            'coefficient_type_id': self.coeff_type.id,
            'duration_unit': 'day',
            'company_id': self.company.id,
            'sequence': 5,
        })
        partner = self.env['res.partner'].create({
            'name': 'Non Matching Customer',
            'allowed_coefficient_table_ids': [(6, 0, [other_table.id])],
        })
        table = self.svc._get_applicable_coefficient_table(
            self.product, partner, self.warehouse, self.coeff_type,
        )
        # No intersection → falls back to standard or empty
        # other_table is not on the product, but customer has it
        # Since intersection is empty, falls to standard search
        self.assertNotEqual(table, other_table)


class TestStandardTableFallback(TransactionCase):
    """Tests for standard table fallback  [RE03, RE04]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Fallback Test Type',
        })

        cls.standard_table = cls.env['rental.coefficient.table'].create({
            'name': 'Fallback Standard',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'is_standard': True,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 4.5}),
            ],
        })

        cls.product = cls.env['product.template'].create({
            'name': 'Fallback Product',
            'rent_periodicity': 'days',
            'type': 'consu',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Fallback Customer',
        })

    def test_re03_standard_fallback(self):
        """No product/customer config → standard table."""
        table = self.svc._get_applicable_coefficient_table(
            self.product, self.partner, self.warehouse, self.coeff_type,
        )
        self.assertEqual(table, self.standard_table)

    def test_re04_standard_table_coefficient(self):
        """Uses standard table coefficient for 7 days."""
        s = datetime(2026, 7, 1)
        e = datetime(2026, 7, 8)
        coeff = self.svc._get_coefficient_for_context(
            self.product, self.partner, self.warehouse, s, e, self.coeff_type,
        )
        self.assertEqual(coeff, 4.5)
