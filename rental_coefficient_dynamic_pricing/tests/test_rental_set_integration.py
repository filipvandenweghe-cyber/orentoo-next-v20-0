from datetime import datetime, timedelta

from odoo.tests import TransactionCase


class TestRentalSetIntegration(TransactionCase):
    """Tests for rental_set compatibility.  [RF01]

    Verifies that coefficient × dynamic pricing is applied exactly once
    on the set parent line and never on component lines.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )

        # Coefficient table
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'Set Integration Type',
        })
        cls.coeff_table = cls.env['rental.coefficient.table'].create({
            'name': 'Set Integration Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'day',
            'company_id': cls.company.id,
            'sequence': 1,
            'line_ids': [
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 7, 'coefficient': 5.0}),
            ],
        })

        # Dynamic pricing table (July = 150%)
        cls.dp_table = cls.env['rental.dynamic.pricing.table'].create({
            'name': 'Set Integration DP',
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {
                    'start_datetime': datetime(2026, 7, 1),
                    'end_datetime': datetime(2026, 7, 31),
                    'factor_percentage': 150.0,
                }),
            ],
        })

        # Component products
        cls.comp_product_a = cls.env['product.product'].create({
            'name': 'Component A',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 10.0,
        })
        cls.comp_product_b = cls.env['product.product'].create({
            'name': 'Component B',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 15.0,
        })

        # --- Fixed-price set ---
        cls.fixed_set_tmpl = cls.env['product.template'].create({
            'name': 'Fixed Price Set',
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'type': 'consu',
            'list_price': 50.0,
            'set_pricing_mode': 'fixed',
            'set_component_ids': [
                (0, 0, {
                    'product_id': cls.comp_product_a.id,
                    'quantity': 2,
                }),
                (0, 0, {
                    'product_id': cls.comp_product_b.id,
                    'quantity': 1,
                }),
            ],
        })
        cls.fixed_set_product = cls.fixed_set_tmpl.product_variant_id

        # --- Sum-of-components set ---
        cls.sum_set_tmpl = cls.env['product.template'].create({
            'name': 'Sum Price Set',
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'type': 'consu',
            'list_price': 0.0,
            'set_pricing_mode': 'sum',
            'set_component_ids': [
                (0, 0, {
                    'product_id': cls.comp_product_a.id,
                    'quantity': 2,
                }),
                (0, 0, {
                    'product_id': cls.comp_product_b.id,
                    'quantity': 1,
                }),
            ],
        })
        cls.sum_set_product = cls.sum_set_tmpl.product_variant_id

        # Pricing config for both sets
        for tmpl in (cls.fixed_set_tmpl, cls.sum_set_tmpl):
            cls.env['rental.product.warehouse.pricing.config'].create({
                'product_tmpl_id': tmpl.id,
                'warehouse_id': cls.warehouse.id,
                'coefficient_table_ids': [(6, 0, [cls.coeff_table.id])],
                'dynamic_pricing_table_id': cls.dp_table.id,
            })

        # Customer
        cls.partner = cls.env['res.partner'].create({
            'name': 'Set Test Customer',
            'allowed_coefficient_table_ids': [(6, 0, [cls.coeff_table.id])],
        })

    def _create_set_order(self, product, start, end, qty=1):
        """Create a rental order, add a set product, trigger expansion.

        After expansion, calls _recompute_rental_prices() which
        triggers _compute_price_unit with force_price_recomputation.
        This ensures coefficient × dynamic is applied on top of the
        set parent price (fixed or sum-of-components).
        """
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        parent_line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'product_uom_qty': qty,
            'is_rental': True,
        })
        # Trigger set expansion (creates component lines + applies pricing)
        parent_line._expand_rental_set()
        # Reapply coefficient × dynamic after set pricing
        order._recompute_rental_prices()
        parent_line.invalidate_recordset()
        return order, parent_line

    # =====================================================================
    # Fixed-price set tests
    # =====================================================================

    def test_fixed_set_coefficient_applied_to_parent(self):
        """Fixed set: coefficient is applied to the parent's fixed price."""
        start = datetime(2026, 6, 1, 10, 0)  # outside DP range
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        self.assertTrue(parent.is_set)
        self.assertEqual(parent.applied_coefficient, 5.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.0)
        # base_rental_price should be the fixed set price
        self.assertTrue(parent.base_rental_price > 0)
        expected = parent.base_rental_price * 5.0 * 1.0
        self.assertAlmostEqual(parent.price_unit, expected, places=2)

    def test_fixed_set_dynamic_factor_applied(self):
        """Fixed set in July: coefficient + dynamic factor applied."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        self.assertEqual(parent.applied_coefficient, 5.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.5)
        expected = parent.base_rental_price * 5.0 * 1.5
        self.assertAlmostEqual(parent.price_unit, expected, places=2)

    def test_fixed_set_components_stay_zero(self):
        """Fixed set: component price_unit must remain 0."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        self.assertTrue(len(components) >= 2)
        for comp in components:
            self.assertEqual(comp.price_unit, 0.0,
                             f"Component {comp.product_id.name} should have price 0")

    # =====================================================================
    # Sum-of-components set tests
    # =====================================================================

    def test_sum_set_coefficient_applied_to_parent(self):
        """Sum set: coefficient is applied to the parent's summed price."""
        start = datetime(2026, 6, 1, 10, 0)  # outside DP range
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        self.assertTrue(parent.is_set)
        self.assertEqual(parent.applied_coefficient, 5.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.0)
        # base_rental_price is the sum of component prices (2×10 + 1×15 = 35)
        self.assertTrue(parent.base_rental_price > 0)
        expected = parent.base_rental_price * 5.0 * 1.0
        self.assertAlmostEqual(parent.price_unit, expected, places=2)

    def test_sum_set_dynamic_factor_applied(self):
        """Sum set in July: coefficient + dynamic factor applied."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        self.assertEqual(parent.applied_coefficient, 5.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.5)
        expected = parent.base_rental_price * 5.0 * 1.5
        self.assertAlmostEqual(parent.price_unit, expected, places=2)

    def test_sum_set_components_stay_zero(self):
        """Sum set: component price_unit must remain 0."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        self.assertTrue(len(components) >= 2)
        for comp in components:
            self.assertEqual(comp.price_unit, 0.0,
                             f"Component {comp.product_id.name} should have price 0")

    # =====================================================================
    # Rental set + dynamic pricing: add & date-change behaviour
    # =====================================================================

    def test_sum_set_price_computed_immediately_on_add(self):
        """Sum set: the price is computed as soon as the line is added.

        Adding a sum-mode set line via a plain create() (exactly what the
        UI does) must immediately produce the correct base price = sum of
        the component prices — WITHOUT any manual 'Update rental prices'.

        Regression guard for the create() hook that previously reset the
        base to the set product's own list price (0.0 for a sum set)
        instead of the component sum computed during set expansion.
        """
        start = datetime(2026, 6, 1, 10, 0)  # 1 day, outside DP range
        end = start + timedelta(days=1)
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_start_date': start,
            'rental_return_date': end,
            'is_rental_order': True,
        })
        # Add the set line like the UI: a plain create(), with NO explicit
        # _expand_rental_set() and NO _recompute_rental_prices() call.
        parent = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.sum_set_product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })
        parent.invalidate_recordset()

        # The set expanded into its components automatically.
        self.assertTrue(parent.is_set)
        self.assertGreaterEqual(len(parent.set_child_line_ids), 2)

        # Base is the component sum (not the set's own list_price of 0),
        # available right away on add.
        expected_base = sum(
            parent._get_component_unit_price(c)
            * (c.set_component_qty or c.product_uom_qty)
            for c in parent.set_child_line_ids
        )
        self.assertGreater(
            expected_base, 0.0,
            "Component sum must be strictly positive for this test to be meaningful",
        )
        self.assertAlmostEqual(
            parent.base_rental_price, expected_base, places=2,
            msg="Base price on add must equal the component sum, not the "
                "set product's own list price",
        )

        # 1 day → coefficient 1.0; June → dynamic 1.0.
        self.assertAlmostEqual(parent.applied_coefficient, 1.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.0)
        self.assertAlmostEqual(
            parent.price_unit,
            parent.base_rental_price * 1.0 * 1.0,
            places=2,
        )

        # Components never carry a price.
        for comp in parent.set_child_line_ids:
            self.assertEqual(comp.price_unit, 0.0)

    def test_sum_set_price_updates_when_dates_change(self):
        """Sum set: changing the rental period recomputes the price.

        The coefficient (duration factor) must follow the new period while
        the base (component sum) stays duration-independent, so the final
        price_unit updates accordingly.
        """
        # Start with a 1-day rental → coefficient 1.0.
        start = datetime(2026, 6, 1, 10, 0)  # outside DP range
        end = start + timedelta(days=1)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        base = parent.base_rental_price
        self.assertGreater(base, 0.0)
        self.assertAlmostEqual(parent.applied_coefficient, 1.0)
        self.assertAlmostEqual(parent.price_unit, base * 1.0, places=2)

        # Extend the period to 7 days → coefficient 5.0, then recompute
        # prices like the "Update rental prices" button does.
        new_end = start + timedelta(days=7)
        order.write({'rental_return_date': new_end})
        order.order_line.write({'return_date': new_end})
        order._recompute_rental_prices()
        parent.invalidate_recordset()

        # Base (component sum) is duration-independent and unchanged.
        self.assertAlmostEqual(
            parent.base_rental_price, base, places=2,
            msg="Component sum base must not depend on the rental duration",
        )
        # Coefficient and final price reflect the new 7-day duration.
        self.assertAlmostEqual(parent.applied_coefficient, 5.0)
        self.assertAlmostEqual(parent.applied_dynamic_multiplier, 1.0)
        self.assertAlmostEqual(parent.price_unit, base * 5.0 * 1.0, places=2)

    # =====================================================================
    # Component-change reprice  [RF03]
    # =====================================================================

    def test_sum_set_reprices_on_component_qty_change(self):
        """Editing a component qty reprices the sum set as sum × coefficient.

        Regression: a component qty edit used to leave the parent at the raw
        component sum (coefficient dropped) and desync technical_price_unit.
        """
        start = datetime(2026, 6, 1, 10, 0)  # outside DP range
        end = start + timedelta(days=7)      # coefficient 5.0, dynamic 1.0
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        self.assertEqual(parent.applied_coefficient, 5.0)
        base0 = parent.base_rental_price
        self.assertGreater(base0, 0.0)
        self.assertAlmostEqual(parent.price_unit, base0 * 5.0, places=2)

        # Edit component A quantity 2 → 4 (adds two more units to the sum).
        comp_a = parent.set_child_line_ids.filtered(
            lambda l: l.product_id == self.comp_product_a
        )
        comp_a.product_uom_qty = 4
        parent.invalidate_recordset()

        base1 = parent.base_rental_price
        # base tracks the NEW component sum ...
        self.assertGreater(
            base1, base0, "base must grow with the added component qty",
        )
        # ... and the coefficient is reapplied to it (not dropped to raw sum).
        self.assertAlmostEqual(
            parent.price_unit, base1 * 5.0, places=2,
            msg="price must be the new sum × coefficient",
        )
        # technical stays in sync (so it is not misread as a manual price).
        self.assertAlmostEqual(
            parent.technical_price_unit, parent.price_unit, places=2,
        )
        self.assertFalse(parent.manual_price_override)
        # Allocations sum to the coefficient-adjusted total.
        alloc = sum(parent.set_child_line_ids.mapped('set_allocated_price'))
        self.assertAlmostEqual(
            alloc, parent.price_unit * parent.product_uom_qty, places=2,
        )

    def test_sum_set_manual_price_frozen_on_component_change(self):
        """A hand-typed set-parent price is kept when components change."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        # User types a manual parent price → line becomes manually priced.
        parent.price_unit = 999.0
        parent._onchange_price_unit_manual()
        self.assertTrue(parent.manual_price_override)

        # Changing a component must NOT overwrite the manual price.
        comp_a = parent.set_child_line_ids.filtered(
            lambda l: l.product_id == self.comp_product_a
        )
        comp_a.product_uom_qty = 4
        parent.invalidate_recordset()
        self.assertAlmostEqual(parent.price_unit, 999.0, places=2)
        self.assertTrue(parent.manual_price_override)

    # =====================================================================
    # No double application
    # =====================================================================

    def test_no_double_coefficient_on_components(self):
        """Components must not have coefficient/dynamic fields set."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        for comp in components:
            # Component pricing fields should be zero/empty
            self.assertEqual(comp.applied_coefficient, 0.0,
                             f"Component {comp.product_id.name} should not have coefficient")
            self.assertFalse(comp.applied_coefficient_table_id,
                             f"Component should not reference a coefficient table")

    def test_parent_coefficient_applied_exactly_once(self):
        """Parent has coefficient applied exactly once — verify by formula."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        # If coefficient were applied twice, price would be base × 5 × 5
        base = parent.base_rental_price
        coeff = parent.applied_coefficient
        mult = parent.applied_dynamic_multiplier
        self.assertAlmostEqual(parent.price_unit, base * coeff * mult, places=2)
        # Verify it's NOT base × coeff × coeff
        self.assertNotAlmostEqual(
            parent.price_unit, base * coeff * coeff * mult, places=2,
        ) if coeff != 1.0 else None

    # =====================================================================
    # Update Prices
    # =====================================================================

    def test_update_prices_recalculates_set(self):
        """Update Prices recalculates coefficient on set parent."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        original_coeff = parent.applied_coefficient
        self.assertEqual(original_coeff, 5.0)

        # Change dates to 3 days (coeff should become 1.0 → "as from 1")
        order.rental_start_date = datetime(2026, 6, 1, 10, 0)
        order.rental_return_date = datetime(2026, 6, 2, 10, 0)
        order._recompute_rental_prices()
        parent.invalidate_recordset()

        self.assertEqual(parent.applied_coefficient, 1.0)

    # =====================================================================
    # Allocation tests — set_allocated_price must include coefficient
    # =====================================================================

    def test_fixed_set_allocations_match_total(self):
        """Fixed set: sum of allocations = parent price_unit × qty."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        alloc_sum = sum(c.set_allocated_price for c in components)
        parent_total = parent.price_unit * parent.product_uom_qty
        self.assertAlmostEqual(alloc_sum, parent_total, places=2,
                               msg="Fixed set allocations must sum to coefficient-adjusted total")

    def test_sum_set_allocations_match_total(self):
        """Sum set: sum of allocations = parent price_unit × qty."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        alloc_sum = sum(c.set_allocated_price for c in components)
        parent_total = parent.price_unit * parent.product_uom_qty
        self.assertAlmostEqual(alloc_sum, parent_total, places=2,
                               msg="Sum set allocations must sum to coefficient-adjusted total")

    def test_fixed_set_allocations_with_dynamic_factor(self):
        """Fixed set in July: allocations include coefficient × dynamic."""
        start = datetime(2026, 7, 2, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.fixed_set_product, start, end)

        components = order.order_line.filtered('is_set_component')
        alloc_sum = sum(c.set_allocated_price for c in components)
        parent_total = parent.price_unit * parent.product_uom_qty
        self.assertAlmostEqual(alloc_sum, parent_total, places=2)
        # Verify the total actually includes the coefficient + dynamic
        self.assertTrue(parent_total > parent.base_rental_price,
                        "Total should be larger than base due to coefficient × dynamic")

    def test_sum_set_allocations_after_update_prices(self):
        """Sum set: allocations recalculated after Update Prices."""
        start = datetime(2026, 6, 1, 10, 0)
        end = start + timedelta(days=7)
        order, parent = self._create_set_order(self.sum_set_product, start, end)

        # Change to 1 day (coefficient = 1.0)
        order.rental_start_date = datetime(2026, 6, 1, 10, 0)
        order.rental_return_date = datetime(2026, 6, 2, 10, 0)
        order._recompute_rental_prices()
        parent.invalidate_recordset()

        components = order.order_line.filtered('is_set_component')
        alloc_sum = sum(c.set_allocated_price for c in components)
        parent_total = parent.price_unit * parent.product_uom_qty
        self.assertAlmostEqual(alloc_sum, parent_total, places=2,
                               msg="Allocations must match after Update Prices")


class TestNonSetProductsWithRentalSet(TransactionCase):
    """Verify non-set rental products work correctly alongside rental_set."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.company = cls.warehouse.company_id
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'Non-Set Customer',
        })
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Non-Set Rental Product',
            'rent_periodicity': 'days',
            'type': 'consu',
            'list_price': 25.0,
        })
        cls.product = cls.product_tmpl.product_variant_id

    def test_non_set_product_works_normally(self):
        """A non-set product gets coefficient/dynamic applied normally."""
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
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })
        self.assertTrue(line.applied_coefficient > 0)
        self.assertTrue(line.base_rental_price > 0)

    def test_is_set_component_line_returns_false(self):
        """For a non-set product, _is_set_component_line() returns False."""
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
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1,
            'is_rental': True,
        })
        self.assertFalse(line._is_set_component_line())
        self.assertFalse(line._is_set_parent_line())
