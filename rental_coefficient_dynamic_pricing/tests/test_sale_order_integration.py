from datetime import datetime, timedelta

from odoo.tests import TransactionCase


class TestSaleOrderIntegrationBase(TransactionCase):
    """Shared setup for sale order integration tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.svc = cls.env['rental.pricing.service']
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id

        # Coefficient type + table
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'SOL Integration Type',
        })
        cls.coeff_table = cls.env['rental.coefficient.table'].create({
            'name': 'SOL Integration Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 1,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 3, 'coefficient': 2.5}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
                (0, 0, {'as_from_duration': 14, 'coefficient': 9.0}),
            ],
        })

        # Dynamic pricing table
        cls.dp_table = cls.env['rental.dynamic.pricing.table'].create({
            'name': 'SOL Integration DP',
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {
                    'start_datetime': datetime(2026, 7, 1),
                    'end_datetime': datetime(2026, 7, 31),
                    'factor_percentage': 150.0,
                }),
            ],
        })

        # Rental product with pricing config
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Integration Rental Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 20.0,  # Sales Price = base per period
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [(6, 0, [cls.coeff_table.id])],
                    'dynamic_pricing_table_id': cls.dp_table.id,
                }),
            ],
        })
        cls.product = cls.product_tmpl.product_variant_id

        # Customer
        cls.partner = cls.env['res.partner'].create({
            'name': 'Integration Customer',
            'allowed_coefficient_table_ids': [(6, 0, [cls.coeff_table.id])],
        })

        # Pricelist (simple, no special rules)
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )

    def _create_rental_order(self, start_dt, end_dt, qty=1.0):
        """Helper to create a rental SO with one line."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start_dt,
            'rental_return_date': end_dt,
            'is_rental_order': True,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': qty,
            'is_rental': True,
        })
        return order, line


class TestCoefficientOnLine(TestSaleOrderIntegrationBase):
    """Tests for coefficient application on sale order lines  [RI01]"""

    def test_ri01_coefficient_applied(self):
        """7-day rental → coefficient 5.0 from table."""
        start = datetime(2026, 6, 1, 10, 0)  # outside DP range
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_coefficient, 5.0)
        self.assertTrue(line.applied_coefficient_table_id)

    def test_ri01_coefficient_between_ranges(self):
        """5-day rental → uses 'as from 3' → coefficient 2.5."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=5)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_coefficient, 2.5)

    def test_ri01_coefficient_1_day(self):
        """1-day rental → coefficient 1.0."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=1)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_coefficient, 1.0)

    def test_ri01_base_price_stored(self):
        """Base rental price is stored on the line."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertTrue(line.base_rental_price > 0)


class TestDynamicFactorOnLine(TestSaleOrderIntegrationBase):
    """Tests for dynamic pricing factor on sale order lines  [RI01]"""

    def test_ri01_dynamic_factor_applied(self):
        """Rental fully in July → factor 150%."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_dynamic_factor_percentage, 150.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.5)

    def test_ri01_no_dynamic_factor(self):
        """Rental outside DP range → factor 100%."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_dynamic_factor_percentage, 100.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.0)


class TestCombinedPricing(TestSaleOrderIntegrationBase):
    """Tests for coefficient + dynamic factor combined  [RI01]"""

    def test_ri01_both_applied(self):
        """7-day rental in July: coeff 5.0, factor 150%, multiplier 1.5.
        price_unit should reflect base × 5 × 1.5."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_coefficient, 5.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.5)
        # Base price × coeff × multiplier should be reflected in price_unit
        expected = line.base_rental_price * 5.0 * 1.5
        self.assertAlmostEqual(line.price_unit, expected, places=2)

    def test_ri01_only_coefficient_no_dynamic(self):
        """Rental outside DP range: only coefficient applied."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        expected = line.base_rental_price * 5.0 * 1.0
        self.assertAlmostEqual(line.price_unit, expected, places=2)


class TestQuantityChanges(TestSaleOrderIntegrationBase):
    """Tests for quantity changes  [RI05, RI07]"""

    def test_ri05_qty_change_keeps_coefficient(self):
        """Changing quantity keeps the same coefficient and factor."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)
        coeff_before = line.applied_coefficient
        factor_before = line.applied_dynamic_factor_percentage

        line.product_uom_qty = 5
        self.assertEqual(line.applied_coefficient, coeff_before)
        self.assertEqual(
            line.applied_dynamic_factor_percentage, factor_before,
        )

    def test_ri07_no_price_compounding_on_qty_change(self):
        """Repeated quantity changes must not compound the coefficient.

        Regression: the engine previously read the already-adjusted
        price_unit as the base (because technical_price_unit desynced and
        super() skipped the base reset), multiplying by the coefficient
        again on every quantity change.  [RI07]
        """
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)  # coefficient 5.0
        order, line = self._create_rental_order(start, end, qty=1)

        base = line.base_rental_price
        price = line.price_unit
        # Sanity: price is base × coefficient (dynamic 100% outside DP range).
        self.assertAlmostEqual(price, base * line.applied_coefficient, places=2)

        for qty in (2, 3, 5, 2, 1):
            line.product_uom_qty = qty
            self.assertAlmostEqual(
                line.base_rental_price, base, places=2,
                msg=f"base drifted at qty={qty}",
            )
            self.assertAlmostEqual(
                line.price_unit, price, places=2,
                msg=f"price compounded at qty={qty}",
            )
            # technical_price_unit must stay in sync with price_unit so the
            # standard manual-price guard never misfires.
            self.assertAlmostEqual(
                line.technical_price_unit, line.price_unit, places=2,
                msg=f"technical desynced at qty={qty}",
            )

    def test_ri07_no_price_compounding_on_period_change(self):
        """Shrinking/growing the rental period recomputes from the base. [RI07]

        The rental "Update Rental Prices" flow (triggered whenever the
        duration changes) recomputes via ``_recompute_rental_prices``.
        Each recompute must derive from the native base, never compound the
        previous period's adjusted price.
        """
        start = datetime(2026, 6, 1, 10, 0)
        order, line = self._create_rental_order(
            start, start + timedelta(days=7),
        )
        base = line.base_rental_price

        # 3-day period → coefficient 2.5, price = base × 2.5.
        line.return_date = start + timedelta(days=3)
        order._recompute_rental_prices()
        self.assertAlmostEqual(line.base_rental_price, base, places=2)
        self.assertAlmostEqual(line.price_unit, base * 2.5, places=2)

        # Back to 7 days → coefficient 5.0, no compounding from the 3-day price.
        line.return_date = start + timedelta(days=7)
        order._recompute_rental_prices()
        self.assertAlmostEqual(line.base_rental_price, base, places=2)
        self.assertAlmostEqual(line.price_unit, base * 5.0, places=2)


class TestManualOverrides(TestSaleOrderIntegrationBase):
    """Tests for manual coefficient and dynamic factor overrides."""

    def test_manual_coefficient_override(self):
        """Writing applied_coefficient sets manual flag."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertFalse(line.manual_coefficient_override)

        # Simulate manual override
        line.write({
            'applied_coefficient': 99.0,
            'manual_coefficient_override': True,
        })
        self.assertTrue(line.manual_coefficient_override)
        self.assertEqual(line.applied_coefficient, 99.0)

    def test_manual_dynamic_factor_override(self):
        """Writing applied_dynamic_factor_percentage sets manual flag."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertFalse(line.manual_dynamic_factor_override)

        # Simulate manual override
        line.write({
            'applied_dynamic_factor_percentage': 200.0,
            'applied_dynamic_multiplier': 2.0,
            'manual_dynamic_factor_override': True,
        })
        self.assertTrue(line.manual_dynamic_factor_override)
        self.assertEqual(line.applied_dynamic_factor_percentage, 200.0)


class TestManualPriceOverride(TestSaleOrderIntegrationBase):
    """Manual unit-price override survives quantity changes.  [RI06]"""

    def test_ri06_manual_price_survives_qty_change(self):
        """A hand-typed unit price is not overwritten on a quantity change."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)
        engine_price = line.price_unit
        self.assertFalse(line.manual_price_override)

        # User types a manual unit price (diverges from technical_price_unit).
        manual_price = engine_price + 13.0
        line.price_unit = manual_price

        # Changing the quantity must keep the manual price and lock the line.
        # Reading price_unit triggers the recompute that both preserves the
        # hand-typed price and flags the line.
        line.product_uom_qty = 4
        self.assertAlmostEqual(line.price_unit, manual_price, places=2)
        self.assertTrue(line.manual_price_override)

        # A further quantity change must still not touch it.
        line.product_uom_qty = 7
        self.assertAlmostEqual(line.price_unit, manual_price, places=2)

    def test_ri06_engine_priced_line_not_locked(self):
        """A normally engine-priced line is never treated as manual."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)
        line.product_uom_qty = 3
        self.assertFalse(line.manual_price_override)

    def test_ri06_onchange_flags_manual_edit(self):
        """The price_unit onchange flags a divergent (hand-typed) price."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)

        # Simulate the form: user edits the price, onchange fires.
        line.price_unit = line.price_unit + 5.0
        line._onchange_price_unit_manual()
        self.assertTrue(line.manual_price_override)

    def test_ri06_force_recompute_clears_manual_price(self):
        """Update Rental Prices (force) drops the lock and restores engine price."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)
        engine_price = line.price_unit

        line.price_unit = engine_price + 13.0
        line.product_uom_qty = 2  # locks the manual price
        self.assertAlmostEqual(line.price_unit, engine_price + 13.0, places=2)
        self.assertTrue(line.manual_price_override)

        line.with_context(force_price_recomputation=True)._compute_price_unit()
        self.assertFalse(line.manual_price_override)
        self.assertAlmostEqual(line.price_unit, engine_price, places=2)

    def test_ri06_editing_coefficient_releases_manual_price(self):
        """Editing the coefficient hands price control back to the engine."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=1)
        base = line.base_rental_price
        engine_price = line.price_unit

        line.price_unit = engine_price + 13.0
        line.product_uom_qty = 2  # locks the manual price
        self.assertAlmostEqual(line.price_unit, engine_price + 13.0, places=2)
        self.assertTrue(line.manual_price_override)

        # User now edits the coefficient → engine reasserts base × coefficient.
        line.applied_coefficient = 3.0
        line._onchange_applied_coefficient()
        self.assertFalse(line.manual_price_override)
        self.assertAlmostEqual(line.price_unit, base * 3.0, places=2)


class TestAutoRecomputeOnSave(TestSaleOrderIntegrationBase):
    """Saving a quotation auto-refreshes rental prices without the button. [RI08]"""

    def _save_period(self, order, line, new_return):
        """Simulate a form save that changes the line's rental period."""
        order.write({'order_line': [(1, line.id, {'return_date': new_return})]})

    def test_ri08_period_change_recomputes_on_save(self):
        """Changing the period and saving recomputes the coefficient price."""
        start = datetime(2026, 6, 1, 10, 0)
        order, line = self._create_rental_order(
            start, start + timedelta(days=7),  # coefficient 5.0
        )
        base = line.base_rental_price
        self.assertAlmostEqual(line.price_unit, base * 5.0, places=2)

        # Shrink to 3 days → coefficient 2.5, applied on save (no button).
        self._save_period(order, line, start + timedelta(days=3))
        self.assertAlmostEqual(line.base_rental_price, base, places=2)
        self.assertAlmostEqual(line.price_unit, base * 2.5, places=2)

        # Grow back to 7 days → coefficient 5.0 again, no compounding.
        self._save_period(order, line, start + timedelta(days=7))
        self.assertAlmostEqual(line.price_unit, base * 5.0, places=2)

    def test_ri08_manual_price_preserved_on_save(self):
        """A hand-typed price is NOT recomputed by the save-refresh."""
        start = datetime(2026, 6, 1, 10, 0)
        order, line = self._create_rental_order(
            start, start + timedelta(days=7),
        )
        manual_price = line.price_unit + 21.0
        line.price_unit = manual_price  # diverges from technical → manual

        # Saving a period change must leave the hand-typed price untouched.
        self._save_period(order, line, start + timedelta(days=3))
        self.assertAlmostEqual(line.price_unit, manual_price, places=2)

    def test_ri08_no_recompute_on_confirmed_order(self):
        """Confirmed orders keep their agreed prices on save."""
        start = datetime(2026, 6, 1, 10, 0)
        order, line = self._create_rental_order(
            start, start + timedelta(days=7),
        )
        order.action_confirm()
        price_before = line.price_unit

        # Even a period edit must not re-price a confirmed order.
        self._save_period(order, line, start + timedelta(days=3))
        self.assertAlmostEqual(line.price_unit, price_before, places=2)

    def test_ri08_non_price_field_save_is_noop(self):
        """Saving an unrelated field does not recompute prices."""
        start = datetime(2026, 6, 1, 10, 0)
        order, line = self._create_rental_order(
            start, start + timedelta(days=7),
        )
        price_before = line.price_unit
        order.write({'note': 'just a note'})
        self.assertAlmostEqual(line.price_unit, price_before, places=2)


class TestFallbackPricing(TestSaleOrderIntegrationBase):
    """Tests for fallback when no config is set."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Product without any pricing config
        cls.bare_product_tmpl = cls.env['product.template'].create({
            'name': 'Bare Rental Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 30.0,
        })
        cls.bare_product = cls.bare_product_tmpl.product_variant_id
        # Partner without any allowed coefficient tables
        cls.bare_partner = cls.env['res.partner'].create({
            'name': 'Bare Fallback Customer',
        })

    def test_fallback_coefficient_equals_duration(self):
        """No coefficient table → coefficient = duration in days.

        We must ensure no standard table exists for the company +
        coefficient type combination.  Since other test classes may
        create standard tables, we delete any that match first.
        """
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        # Remove any standard tables that could act as fallback
        self.env['rental.coefficient.table'].search([
            ('is_standard', '=', True),
            ('company_id', '=', self.warehouse.company_id.id),
        ]).write({'is_standard': False})

        order = self.env['sale.order'].create({
            'partner_id': self.bare_partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.bare_product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })
        self.assertEqual(line.applied_coefficient, 7)
        self.assertFalse(line.applied_coefficient_table_id)

    def test_fallback_dynamic_factor_100(self):
        """No dynamic pricing table → factor 100%."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order = self.env['sale.order'].create({
            'partner_id': self.bare_partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.bare_product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })
        self.assertEqual(line.applied_dynamic_factor_percentage, 100.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.0)


class TestTaxesAndDiscounts(TestSaleOrderIntegrationBase):
    """Tests verifying standard Odoo pricing mechanics still work."""

    def test_subtotal_computed(self):
        """price_subtotal is calculated from price_unit × qty."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end, qty=2)
        # price_subtotal should be price_unit * qty (before tax)
        self.assertAlmostEqual(
            line.price_subtotal,
            line.price_unit * 2,
            places=2,
        )

    def test_discount_field_default_zero(self):
        """Default discount is 0 — not broken by coefficient logic."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=1)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.discount, 0.0)


class TestPartnerChangeRecomputesPrices(TestSaleOrderIntegrationBase):
    """Changing the customer refreshes rental prices.  [RI05]

    Odoo 20 removed the rental "Update Rental Prices" button and its
    ``show_update_duration`` flag, so the onchange now recomputes the rental
    line prices directly instead of flagging a button.
    """

    def test_ri05_partner_change_recomputes_rental_prices(self):
        """Changing partner on a rental order refreshes the rental price."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        expected = line.price_unit
        # Make the price stale WITHOUT looking like a hand-typed one (RI06
        # keeps a manual price): the engine holds technical_price_unit ==
        # price_unit, so move them together.
        line.write({
            'price_unit': expected + 123.0,
            'technical_price_unit': expected + 123.0,
        })

        new_partner = self.env['res.partner'].create({
            'name': 'New Partner For RI05',
        })
        # Simulate onchange
        order.partner_id = new_partner
        order._onchange_partner_recompute_rental_prices()
        self.assertAlmostEqual(line.price_unit, expected, places=2)

    def test_ri05_no_lines_no_recompute(self):
        """Changing partner on an empty order is a harmless no-op."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        order._onchange_partner_recompute_rental_prices()
        self.assertFalse(order.order_line)


class TestNonSetAllocationNoOp(TestSaleOrderIntegrationBase):
    """Tests that _recalculate_set_allocations is a no-op for non-set lines."""

    def test_non_set_line_allocation_noop(self):
        """_recalculate_set_allocations on a non-set line does nothing."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        # Should not raise or change anything
        line._recalculate_set_allocations()
        self.assertTrue(line.price_unit > 0)


class TestCustomerTableIntersection(TransactionCase):
    """Tests for customer ∩ product table selection and Update Prices.

    Reproduces the scenario where a product has both a Standard table and
    a VIP table configured, and the customer only has VIP.  The VIP table
    must be selected — not the Standard table.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )

        # Two coefficient types
        cls.type_standard = cls.env['rental.coefficient.type'].create({
            'name': 'Intersect Standard',
        })
        cls.type_vip = cls.env['rental.coefficient.type'].create({
            'name': 'Intersect VIP',
        })

        # Standard table (type Standard, sequence 10)
        cls.table_standard = cls.env['rental.coefficient.table'].create({
            'name': 'Intersect Standard Table',
            'coefficient_type_id': cls.type_standard.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 10,
            'is_standard': True,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 3, 'coefficient': 2.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
            ],
        })

        # VIP table (type VIP, sequence 10)
        cls.table_vip = cls.env['rental.coefficient.table'].create({
            'name': 'Intersect VIP Table',
            'coefficient_type_id': cls.type_vip.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 10,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 0.5}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 3.0}),
            ],
        })

        # Product has BOTH tables
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Intersect Test Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 20.0,
            'rental_pricing_config_ids': [
                (0, 0, {
                    'warehouse_id': cls.warehouse.id,
                    'coefficient_table_ids': [
                        (6, 0, [cls.table_standard.id, cls.table_vip.id]),
                    ],
                }),
            ],
        })
        cls.product = cls.product_tmpl.product_variant_id

        # VIP customer only has the VIP table
        cls.partner_vip = cls.env['res.partner'].create({
            'name': 'VIP Customer',
            'allowed_coefficient_table_ids': [
                (6, 0, [cls.table_vip.id]),
            ],
        })

        # Regular customer has no tables (fallback to standard)
        cls.partner_regular = cls.env['res.partner'].create({
            'name': 'Regular Customer',
        })

    def _create_order(self, partner, start, end, qty=1):
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': qty,
            'is_rental': True,
        })
        return order, line

    def test_vip_customer_gets_vip_table(self):
        """VIP customer → intersection selects VIP table, coeff = 0.5."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=4)
        order, line = self._create_order(self.partner_vip, start, end)

        self.assertEqual(line.applied_coefficient_table_id, self.table_vip)
        self.assertEqual(line.applied_coefficient, 0.5)
        self.assertEqual(line.applied_coefficient_type_id, self.type_vip)

    def test_vip_customer_7_days_gets_vip_coeff(self):
        """VIP customer, 7 days → VIP table, coeff = 3.0."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_order(self.partner_vip, start, end)

        self.assertEqual(line.applied_coefficient_table_id, self.table_vip)
        self.assertEqual(line.applied_coefficient, 3.0)

    def test_regular_customer_gets_standard_table(self):
        """Regular customer (no tables) → fallback to Standard table."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=4)
        order, line = self._create_order(self.partner_regular, start, end)

        self.assertEqual(line.applied_coefficient_table_id, self.table_standard)
        self.assertEqual(line.applied_coefficient, 2.0)  # as from 3 → 2.0

    def test_update_prices_recalculates_with_correct_table(self):
        """After config changes, Update Prices picks the correct table.

        Scenario: line initially created without VIP config, then config is
        added and Update Prices is clicked.  The VIP table must be selected.
        """
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=4)

        # Create order for VIP customer but WITHOUT product config yet
        bare_product_tmpl = self.env['product.template'].create({
            'name': 'Late Config Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 10.0,
        })
        bare_product = bare_product_tmpl.product_variant_id

        order = self.env['sale.order'].create({
            'partner_id': self.partner_vip.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': bare_product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })

        # Initially: no config → customer has VIP → VIP is used
        # (customer tables are used when product has no config)
        self.assertEqual(line.applied_coefficient_table_id, self.table_vip)
        old_coeff = line.applied_coefficient

        # Now add product config with BOTH tables
        self.env['rental.product.warehouse.pricing.config'].create({
            'product_tmpl_id': bare_product_tmpl.id,
            'warehouse_id': self.warehouse.id,
            'coefficient_table_ids': [
                (6, 0, [self.table_standard.id, self.table_vip.id]),
            ],
        })

        # Click "Update Prices"
        order._recompute_rental_prices()
        line.invalidate_recordset()

        # After update: intersection {standard, vip} ∩ {vip} = {vip}
        self.assertEqual(line.applied_coefficient_table_id, self.table_vip)
        self.assertEqual(line.applied_coefficient, 0.5)

    def test_update_prices_clears_stale_type(self):
        """Update Prices must not use a stale coefficient type as filter.

        Scenario: line was computed with Standard table (type=Standard),
        then customer gets VIP table.  Update Prices must not filter by
        the old "Standard" type — it must find VIP instead.
        """
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=4)

        # Create order for regular customer (no VIP) → gets Standard table
        order, line = self._create_order(self.partner_regular, start, end)
        self.assertEqual(line.applied_coefficient_table_id, self.table_standard)
        self.assertEqual(line.applied_coefficient_type_id, self.type_standard)

        # Now give the customer VIP access
        self.partner_regular.write({
            'allowed_coefficient_table_ids': [(6, 0, [self.table_vip.id])],
        })

        # Click "Update Prices"
        order._recompute_rental_prices()
        line.invalidate_recordset()

        # Intersection: product has {standard, vip}, customer now has {vip}
        # → intersection = {vip}
        self.assertEqual(line.applied_coefficient_table_id, self.table_vip)
        self.assertEqual(line.applied_coefficient_type_id, self.type_vip)
        self.assertEqual(line.applied_coefficient, 0.5)

    def test_vip_price_calculation(self):
        """Full price check: base 20 × VIP coeff 0.5 × no dynamic = 10."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=4)
        order, line = self._create_order(self.partner_vip, start, end)

        expected = line.base_rental_price * 0.5 * 1.0
        self.assertAlmostEqual(line.price_unit, expected, places=2)


class TestCustomerDynamicPricingToggle(TestSaleOrderIntegrationBase):
    """Tests for the per-customer Dynamic Pricing toggle.  [RP10]"""

    def test_rp10_dynamic_pricing_enabled_by_default(self):
        """New partners have dynamic pricing enabled."""
        partner = self.env['res.partner'].create({'name': 'New Customer'})
        self.assertTrue(partner.use_dynamic_pricing)

    def test_rp10_disabled_returns_100_pct(self):
        """Customer with dynamic pricing disabled → factor 100%, multiplier 1."""
        self.partner.use_dynamic_pricing = False
        start = datetime(2026, 7, 2, 10, 0)  # inside DP range (150%)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)

        self.assertEqual(line.applied_dynamic_factor_percentage, 100.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.0)
        self.assertFalse(line.applied_dynamic_pricing_table_id)

    def test_rp10_enabled_applies_factor(self):
        """Customer with dynamic pricing enabled → factor from table."""
        self.partner.use_dynamic_pricing = True
        start = datetime(2026, 7, 2, 10, 0)  # inside DP range (150%)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)

        self.assertEqual(line.applied_dynamic_factor_percentage, 150.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.5)

    def test_rp10_toggle_and_update_prices(self):
        """Disable dynamic pricing, then Update Prices → factor resets to 100%."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, line = self._create_rental_order(start, end)
        self.assertEqual(line.applied_dynamic_factor_percentage, 150.0)

        # Disable dynamic pricing on the customer
        self.partner.use_dynamic_pricing = False

        # Update Prices
        order._recompute_rental_prices()
        line.invalidate_recordset()

        self.assertEqual(line.applied_dynamic_factor_percentage, 100.0)
        self.assertAlmostEqual(line.applied_dynamic_multiplier, 1.0)
