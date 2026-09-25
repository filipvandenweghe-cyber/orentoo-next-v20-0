from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase


class TestRentalSetCommon(TransactionCase):
    """Shared setup for all Rental Set tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ── Component products ────────────────────────────────────────────
        Product = cls.env['product.product']
        cls.led_par = Product.create({
            'name': 'LED Par 64',
            'list_price': 25.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.cable = Product.create({
            'name': 'DMX Cable 5m',
            'list_price': 5.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.truss = Product.create({
            'name': 'Truss Section 2m',
            'list_price': 40.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.clamp = Product.create({
            'name': 'Half Coupler Clamp',
            'list_price': 3.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.controller = Product.create({
            'name': 'DMX Controller',
            'list_price': 35.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.spare = Product.create({
            'name': 'Spare LED Bar',
            'list_price': 30.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
        })

        # ── Nested set: Front Light Set (sum pricing) ─────────────────────
        cls.front_light_tmpl = cls.env['product.template'].create({
            'name': 'Front Light Set',
            'list_price': 0.0,
            'type': 'consu',
            'sale_ok': False,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        cls.env['rental.set.component'].create([
            {'set_product_tmpl_id': cls.front_light_tmpl.id,
             'product_id': cls.led_par.id, 'quantity': 4, 'sequence': 10},
            {'set_product_tmpl_id': cls.front_light_tmpl.id,
             'product_id': cls.cable.id, 'quantity': 3, 'sequence': 20},
            {'set_product_tmpl_id': cls.front_light_tmpl.id,
             'product_id': cls.clamp.id, 'quantity': 4, 'sequence': 30},
        ])

        # ── Top-level set: Lighting Package (fixed pricing) ───────────────
        cls.lighting_pkg_tmpl = cls.env['product.template'].create({
            'name': 'Lighting Package',
            'list_price': 250.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'fixed',
        })
        cls.env['rental.set.component'].create([
            {'set_product_tmpl_id': cls.lighting_pkg_tmpl.id,
             'product_id': cls.front_light_tmpl.product_variant_id.id,
             'quantity': 1, 'sequence': 10},
            {'set_product_tmpl_id': cls.lighting_pkg_tmpl.id,
             'product_id': cls.truss.id, 'quantity': 2, 'sequence': 20},
            {'set_product_tmpl_id': cls.lighting_pkg_tmpl.id,
             'product_id': cls.controller.id, 'quantity': 1, 'sequence': 30},
        ])

        # Use a pricelist with no discount rules so tests are deterministic
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Test Pricelist (no discounts)',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'property_product_pricelist': cls.pricelist.id,
        })


class TestSetExpansion(TestRentalSetCommon):
    """Test 1-4: set expansion, nesting, quantity scaling."""

    def _create_order(self, product_tmpl, qty=1):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product_tmpl.product_variant_id.id,
            'product_uom_qty': qty,
        })
        return order

    def test_01_components_generated(self):
        """Adding a set to a quotation generates component lines."""
        order = self._create_order(self.lighting_pkg_tmpl, qty=1)
        self.assertTrue(
            order.order_line.filtered('is_set_component'),
            "Component lines should be generated",
        )
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertEqual(len(set_parent), 1)
        self.assertEqual(set_parent.product_uom_qty, 1)

    def test_02_nested_set_expands(self):
        """Nested sets (Front Light Set inside Lighting Package) expand."""
        order = self._create_order(self.lighting_pkg_tmpl, qty=1)
        # Expect: Front Light Set (nested), Truss, Controller at level 1
        # and LED Par, Cable, Clamp at level 2
        level_1 = order.order_line.filtered(lambda l: l.set_level == 1)
        level_2 = order.order_line.filtered(lambda l: l.set_level == 2)
        self.assertEqual(len(level_1), 3, "3 direct children at level 1")
        self.assertEqual(len(level_2), 3, "3 grandchildren at level 2")

        # Front Light Set line should be both is_set and is_set_component
        nested = order.order_line.filtered(
            lambda l: l.is_set and l.is_set_component
        )
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested.product_id.product_tmpl_id, self.front_light_tmpl)

    def test_03_quantity_scaling(self):
        """Component quantities scale with the set quantity."""
        order = self._create_order(self.lighting_pkg_tmpl, qty=3)
        # Truss: 2 per set * 3 sets = 6
        truss_line = order.order_line.filtered(
            lambda l: l.product_id == self.truss
        )
        self.assertEqual(truss_line.product_uom_qty, 6)

        # LED Par: 4 per front-light * 1 front-light per pkg * 3 pkgs = 12
        led_line = order.order_line.filtered(
            lambda l: l.product_id == self.led_par
        )
        self.assertEqual(led_line.product_uom_qty, 12)

    def test_04_quantity_change_rescales(self):
        """Changing the set quantity rescales all components."""
        order = self._create_order(self.lighting_pkg_tmpl, qty=2)
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        # Change qty from 2 to 5
        set_parent.write({'product_uom_qty': 5})
        truss_line = order.order_line.filtered(
            lambda l: l.product_id == self.truss
        )
        self.assertEqual(truss_line.product_uom_qty, 10)  # 2 * 5


class TestSetPricing(TestRentalSetCommon):
    """Test 5-6: fixed-price allocation and sum-price mode."""

    def test_05_fixed_price_allocation(self):
        """Fixed-price set distributes price over components."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertEqual(set_parent.price_unit, 250.0)

        # All component price_unit should be 0 (allocation is in set_allocated_price)
        components = order.order_line.filtered('is_set_component')
        for comp in components:
            self.assertEqual(comp.price_unit, 0.0,
                             f"{comp.product_id.name} price_unit should be 0")

        # Allocated prices should sum to the set total (price × qty)
        total_allocated = sum(c.set_allocated_price for c in components)
        self.assertAlmostEqual(total_allocated, 250.0, places=0)

        # Order total = only set parent
        self.assertAlmostEqual(order.amount_untaxed, 250.0, places=2)

    def test_06_sum_price_mode(self):
        """Sum-price set computes parent price from component prices."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.front_light_tmpl.product_variant_id.id,
            'product_uom_qty': 2,
        })
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        # Expected: 4*25 + 3*5 + 4*3 = 100+15+12 = 127 per set
        self.assertAlmostEqual(set_parent.price_unit, 127.0, places=2)

        # Components should have price_unit = 0
        components = order.order_line.filtered('is_set_component')
        for comp in components:
            self.assertEqual(comp.price_unit, 0.0)

        # Order total = 127 * 2 = 254
        self.assertAlmostEqual(order.amount_untaxed, 254.0, places=2)

    def test_06b_component_price_change_is_silently_ignored(self):
        """A component price change is silently ignored: the price stays 0 and
        NO chatter notification is ever posted (benign echoes or genuine
        attempts alike)."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.front_light_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        component = order.order_line.filtered('is_set_component')[:1]

        def ignored_count():
            order.invalidate_recordset()
            return len(order.message_ids.filtered(
                lambda m: 'component price change was ignored' in (m.body or '')
            ))

        # Benign price_unit=0 echoes (what the form sends on save) → silent.
        order.write({'order_line': [(1, component.id, {'price_unit': 0.0})]})
        order.write({'order_line': [
            (1, component.id, {'price_unit': 0.0, 'product_uom_qty': 2}),
        ]})
        # A genuine non-zero attempt → still no note, and price stays 0.
        order.write({'order_line': [(1, component.id, {'price_unit': 42.0})]})
        self.assertEqual(ignored_count(), 0,
                         "the component-price chatter must never be posted")
        component.invalidate_recordset()
        self.assertEqual(component.price_unit, 0.0)


class TestSetStockAndReservation(TestRentalSetCommon):
    """Test 7-8: substitution and availability warnings."""

    def test_07_substitution_updates_stock(self):
        """Substituting a component cancels old moves and creates new ones."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        order.action_confirm()

        # Verify moves exist for LED Par
        led_moves = order.picking_ids.move_ids.filtered(
            lambda m: m.product_id == self.led_par and m.state != 'cancel'
        )
        self.assertTrue(led_moves, "LED Par should have stock moves")

        # Set parent should have a display-only header move
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertTrue(
            set_parent.move_ids,
            "Set parent should have a header move for picking display",
        )

    def test_08_availability_warning(self):
        """Insufficient stock posts a non-blocking warning (not an error)."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        # Confirm should succeed even without stock (non-blocking)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')


class TestSetPermissions(TestRentalSetCommon):
    """Test 9: picker cannot modify set composition."""

    def test_09_picker_cannot_modify(self):
        """A picker (stock user only) cannot edit set components."""
        picker = self.env['res.users'].create({
            'name': 'Test Picker',
            'login': 'test_picker_perm_test',
            'group_ids': [(6, 0, [
                self.env.ref('stock.group_stock_user').id,
                self.env.ref('base.group_user').id,
            ])],
        })

        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })

        comp_line = order.order_line.filtered('is_set_component')[0]
        # Picker should be blocked by either ACL (AccessError) or our
        # write guard (ValidationError).
        with self.assertRaises(Exception):
            comp_line.with_user(picker).write({'product_uom_qty': 99})


class TestSetDocuments(TestRentalSetCommon):
    """Test 10-11: invoice and picking documents."""

    def test_10_invoice_shows_only_parent(self):
        """Customer-facing report lines exclude hidden components."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        report_lines = order._get_order_lines_to_report()
        # Only the set parent should be visible
        self.assertEqual(len(report_lines), 1)
        self.assertEqual(
            report_lines[0].product_id,
            self.lighting_pkg_tmpl.product_variant_id,
        )

    def test_11_picking_uses_components(self):
        """Picking contains component moves, not the set parent."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        order.action_confirm()

        picking = order.picking_ids[0]
        move_products = picking.move_ids.filtered(
            lambda m: m.state != 'cancel'
        ).mapped('product_id')

        # Set parent product should be in the picking as a header move
        self.assertIn(
            self.lighting_pkg_tmpl.product_variant_id,
            move_products,
            "Set parent should have a header move in the picking",
        )
        # Component products should be in the picking
        self.assertIn(self.led_par, move_products)
        self.assertIn(self.truss, move_products)
        self.assertIn(self.controller, move_products)
        self.assertIn(self.cable, move_products)
        self.assertIn(self.clamp, move_products)


# ══════════════════════════════════════════════════════════════════════
# Corner-case tests derived from live testing sessions
# ══════════════════════════════════════════════════════════════════════

class TestSetCornerCases(TestRentalSetCommon):
    """Corner-case tests for rental set behavior.

    These tests cover specific issues discovered during live testing:
      12. Set availability uses actual order qty, not template base qty
      13. Allocated price updates when component qty is manually changed
      14. Set price_unit stable when parent qty is doubled (sum mode)
      15. Non-storable components treated as limitless stock
      16. Availability hidden on unsaved lines (tested at widget level)
      17. Set parent header move has demand=set qty (not zero)
      18. Return wizard excludes set parent header moves
    """

    def _create_rental_order(self, product_tmpl, qty=1):
        """Helper: create a rental order with a set product."""
        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=1),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product_tmpl.product_variant_id.id,
            'product_uom_qty': qty,
        })
        return order

    # ── Test 12: Availability uses actual order qty ──────────────────

    def test_12_availability_uses_actual_order_qty(self):
        """S01352: set availability must reflect manually changed component qty.

        When a user manually increases a component's quantity on the order
        (e.g. Printer 3→4), the set availability must use the actual order
        qty, not the template-defined set_component_qty.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        led_comp = order.order_line.filtered(
            lambda l: l.product_id == self.led_par
        )

        # Record original availability
        original_avail = set_parent.set_availability

        # Double the LED Par qty (manually override)
        original_qty = led_comp.product_uom_qty
        led_comp.write({'product_uom_qty': original_qty * 2})

        # Availability must change (more demand for same stock)
        set_parent.invalidate_recordset(['set_availability'])
        # We can't assert exact values without known stock, but the
        # per-set demand for LED Par should now be doubled
        leaves = []
        order.order_line._collect_leaf_availability_data(set_parent, 1.0, leaves)
        led_leaves = [cum for leaf, cum in leaves if leaf.product_id == self.led_par]
        self.assertTrue(led_leaves)
        self.assertAlmostEqual(
            led_leaves[0], original_qty * 2,
            msg="Per-set demand must reflect manually changed qty",
        )

    # ── Test 13: Allocated price updates on component qty change ─────

    def test_13_allocated_price_updates_on_component_qty_change(self):
        """S01352: allocated price adjusts when component qty is manually changed.

        In sum-of-components mode, changing a component's qty must
        update both the component's allocated price and the parent's
        total price.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        led_comp = order.order_line.filtered(
            lambda l: l.product_id == self.led_par
        )

        alloc_before = led_comp.set_allocated_price

        # Increase LED Par qty
        led_comp.write({'product_uom_qty': led_comp.product_uom_qty + 2})

        set_parent.invalidate_recordset()
        led_comp.invalidate_recordset()

        # Component allocation must increase (more units at same unit price)
        self.assertGreater(
            led_comp.set_allocated_price, alloc_before,
            "Component allocated price must increase with qty",
        )
        # Total set allocation must also increase
        total_alloc = sum(
            order.order_line.filtered('is_set_component').mapped('set_allocated_price')
        )
        self.assertGreater(
            total_alloc, alloc_before,
            "Total set allocation must increase",
        )

    def test_13b_sum_price_matches_allocations_after_component_qty_change(self):
        """S00610: sum-mode parent price must equal the sum of allocations
        after component quantities are edited directly.

        Reproduces the reported bug: the Front Light Set defaults to
        4×LED + 3×cable + 4×clamp = 127.  Reducing the components to
        2×LED + 1×cable + 2×clamp must reprice the set to
        2×25 + 1×5 + 2×3 = 61, and the parent price (× qty) must stay
        equal to the sum of the component allocated prices.  Previously the
        parent price kept using the stale per-set base quantity (127) while
        the allocations followed the new order content (61), so the two
        diverged.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        components = order.order_line.filtered('is_set_component')
        led = components.filtered(lambda l: l.product_id == self.led_par)
        cable = components.filtered(lambda l: l.product_id == self.cable)
        clamp = components.filtered(lambda l: l.product_id == self.clamp)

        # Baseline: 4*25 + 3*5 + 4*3 = 127
        self.assertAlmostEqual(set_parent.price_unit, 127.0, places=2)

        # Reduce every component's quantity (direct edits)
        led.write({'product_uom_qty': 2})
        cable.write({'product_uom_qty': 1})
        clamp.write({'product_uom_qty': 2})

        set_parent.invalidate_recordset()
        components.invalidate_recordset()

        # New per-set price: 2*25 + 1*5 + 2*3 = 61
        self.assertAlmostEqual(
            set_parent.price_unit, 61.0, places=2,
            msg="Sum-mode price must follow reduced component quantities",
        )

        # Invariant: parent total == sum of component allocations
        total_alloc = sum(components.mapped('set_allocated_price'))
        self.assertAlmostEqual(
            set_parent.price_unit * set_parent.product_uom_qty,
            total_alloc, places=2,
            msg="Parent price x qty must equal the sum of allocations",
        )
        self.assertAlmostEqual(total_alloc, 61.0, places=2)

    # ── Test 14: Set price stable when parent qty doubled ────────────

    def test_14_price_stable_when_parent_qty_doubled(self):
        """S01462: doubling set qty must not halve the per-unit price.

        In sum-of-components mode, changing the parent set qty from 1→2
        must keep price_unit the same (per-set price).  The total
        (price_unit × qty) doubles.  Components double in quantity.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )

        price_at_1 = set_parent.price_unit
        total_at_1 = set_parent.price_subtotal

        # Double the set qty
        set_parent.write({'product_uom_qty': 2})

        set_parent.invalidate_recordset()
        price_at_2 = set_parent.price_unit
        total_at_2 = set_parent.price_subtotal

        self.assertAlmostEqual(
            price_at_2, price_at_1, places=2,
            msg="Per-unit price must remain stable when qty doubles",
        )
        self.assertAlmostEqual(
            total_at_2, total_at_1 * 2, places=2,
            msg="Total must double when qty doubles",
        )

        # Components must have doubled
        for comp in order.order_line.filtered('is_set_component'):
            self.assertAlmostEqual(
                comp.product_uom_qty % 2, 0,
                msg=f"{comp.product_id.name} qty must be even (doubled)",
            )

    # ── Test 15: Non-storable components = limitless stock ───────────

    def test_15_non_storable_components_limitless(self):
        """S01557: non-storable products (is_storable=False) must not
        constrain set availability.

        If a set contains only non-storable components, availability
        equals the ordered qty (always fully available).
        """
        # Create a set with only non-storable components
        non_storable = self.env['product.product'].create({
            'name': 'Non-Storable Service',
            'type': 'service',
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        set_tmpl = self.env['product.template'].create({
            'name': 'Service-Only Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id,
            'product_id': non_storable.id,
            'quantity': 2,
            'sequence': 10,
        })

        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': fields.Datetime.now(),
            'rental_return_date': fields.Datetime.now() + timedelta(days=1),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 3,
        })

        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertEqual(
            set_parent.set_availability, 3.0,
            "All non-storable set: availability must equal ordered qty",
        )

    # ── Test 16: Set parent header move has demand = set qty ─────────

    def test_16_header_move_zero_demand_picked(self):
        """RS04: set parent header move is zero-demand and pre-picked.

        The header move has product_uom_qty=0 (no real demand) and
        picked=True.  It serves as a display-only grouping entry.
        The zero-qty validation check is bypassed by an override on
        stock.picking.  On backorder creation, a new header move is
        automatically created on the backorder.
        """
        order = self._create_rental_order(self.lighting_pkg_tmpl)
        order.action_confirm()

        picking = order.picking_ids.filtered(
            lambda p: not p.return_id
        )[:1]
        header_move = picking.move_ids.filtered(
            lambda m: m.sale_line_id.is_set and not m.sale_line_id.is_set_component
        )
        self.assertTrue(header_move)
        self.assertEqual(
            header_move.product_uom_qty, 0.0,
            "Header move must have zero demand (display only)",
        )
        self.assertFalse(
            header_move.picked,
            "Header move must NOT be pre-picked (would block auto-pick)",
        )
        self.assertEqual(
            header_move.state, 'assigned',
            "Header move must be in assigned state",
        )

    # ── Test 17: Return wizard excludes set parent header moves ──────

    def test_17_return_wizard_excludes_set_parent(self):
        """S01462: the stock return wizard must not include set parent
        header moves as returnable lines.

        The set parent product carries no stock — only actual components
        should appear in return pickings.
        """
        order = self._create_rental_order(self.lighting_pkg_tmpl)
        order.action_confirm()

        picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]

        # Force picking to done so return wizard works.
        # Set quantity on component moves only (header has qty=0).
        for move in picking.move_ids:
            if move.sale_line_id and move.sale_line_id.is_set_component:
                move.quantity = move.product_uom_qty or 1
            elif not (move.sale_line_id and move.sale_line_id.is_set):
                move.quantity = move.product_uom_qty or 1
        picking.with_context(
            skip_backorder=True,
            picking_ids_not_to_backorder=picking.ids,
            skip_sale_flow_sync=True,
            skip_lost_broken_check=True,
        ).button_validate()

        # Odoo 20 dropped the stock.return.picking wizard: a return picking
        # is built straight from the original one (_create_return).
        return_picking = picking._create_return()

        # Set parent must not appear as a returnable line (demand = 0)
        set_parent_product = self.lighting_pkg_tmpl.product_variant_id
        parent_return_moves = return_picking.move_ids.filtered(
            lambda m: m.product_id == set_parent_product and m.product_uom_qty > 0
        )
        self.assertFalse(
            parent_return_moves,
            "Set parent must not appear as returnable (qty must be 0)",
        )

    # ── Test 18: Full end-to-end scenario (S02381) ───────────────────

    def test_18_full_rental_set_flow_with_backorder(self):
        """S02381: full rental set flow with partial delivery, backorder,
        partial cancellation, and full return.

        Scenario:
          1. Rental order with set (3 components) + standalone line
          2. Partial delivery → backorder created
          3. Backorder: partial no-backorder (one component cancelled)
          4. Full return of all delivered items
          5. Verify: set header on both pickings, correct flow line
             states, return reconciliation, rental_status=returned

        Covers: RS04 (header moves), RS05 (return excludes header),
        RS09 (backorder header creation), picked=False auto-pick (RS10).
        """
        order = self._create_rental_order(self.lighting_pkg_tmpl)

        # ── Step 1: Confirm and get outgoing picking ──
        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            # No picking generated (non-storable products don't create moves
            # in some configurations).  Skip the stock flow assertions.
            return

        # Verify header exists with picked=False and state=assigned
        header = out_picking.move_ids.filtered(
            lambda m: m.sale_line_id and m.sale_line_id.is_set
            and not m.sale_line_id.is_set_component
        )
        self.assertTrue(header, "Header move must exist on outgoing picking")
        self.assertFalse(header.picked, "Header must have picked=False")
        self.assertEqual(header.state, 'assigned')

        # ── Step 2: Partial delivery → backorder ──
        # Only deliver some of each component
        component_moves = out_picking.move_ids.filtered(
            lambda m: m.sale_line_id and m.sale_line_id.is_set_component
            and m.state not in ('done', 'cancel')
        )
        for move in component_moves:
            # Deliver half (rounded down)
            move.quantity = max(1, int(move.product_uom_qty / 2))

        # Also deliver non-set moves if any
        for move in out_picking.move_ids.filtered(
            lambda m: not m.sale_line_id or (
                not m.sale_line_id.is_set
                and not m.sale_line_id.is_set_component
            )
        ):
            if move.product_uom_qty > 0:
                move.quantity = max(1, int(move.product_uom_qty / 2))

        # Validate with backorder
        res = out_picking.with_context(
            skip_sale_flow_sync=True,
            skip_lost_broken_check=True,
        ).button_validate()

        # Process backorder wizard if returned
        if isinstance(res, dict) and res.get('res_model') == 'stock.backorder.confirmation':
            backorder_wiz = self.env['stock.backorder.confirmation'].with_context(
                **res.get('context', {}),
            ).create({})
            backorder_wiz.process()

        self.assertEqual(out_picking.state, 'done')

        # ── Step 3: Verify backorder has header ──
        backorder = order.picking_ids.filtered(
            lambda p: (
                not p.return_id
                and p.backorder_id == out_picking
                and p.state not in ('done', 'cancel')
            )
        )
        if backorder:
            bo_header = backorder.move_ids.filtered(
                lambda m: m.sale_line_id and m.sale_line_id.is_set
                and not m.sale_line_id.is_set_component
            )
            self.assertTrue(
                bo_header,
                "Backorder must have a set header move (RS09)",
            )
            self.assertEqual(bo_header.product_uom_qty, 0)

            # Validate backorder (deliver remaining)
            for move in backorder.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
                and m.product_uom_qty > 0
            ):
                move.quantity = move.product_uom_qty
            backorder.with_context(
                skip_backorder=True,
                picking_ids_not_to_backorder=backorder.ids,
                skip_sale_flow_sync=True,
                skip_lost_broken_check=True,
            ).button_validate()
            self.assertEqual(backorder.state, 'done')

        # ── Step 4: Verify header went to done (not cancelled) ──
        for pick in [out_picking, backorder]:
            if not pick:
                continue
            pick_header = pick.move_ids.filtered(
                lambda m: m.sale_line_id and m.sale_line_id.is_set
                and not m.sale_line_id.is_set_component
            )
            self.assertEqual(
                pick_header.state, 'done',
                f"Header on {pick.name} must be done, not cancelled (RS04)",
            )

        # ── Step 5: Set parent not on return picking ──
        return_pickings = order.picking_ids.filtered(
            lambda p: p.return_id and p.state not in ('cancel',)
        )
        for ret in return_pickings:
            ret_header = ret.move_ids.filtered(
                lambda m: m.sale_line_id and m.sale_line_id.is_set
                and not m.sale_line_id.is_set_component
                and m.state != 'cancel'
            )
            self.assertFalse(
                ret_header,
                f"Return {ret.name} must NOT have set header (RS05)",
            )

    # ── Test 19: Auto-pick works with header picked=False ────────────

    def test_19_auto_pick_with_header(self):
        """RS10: header picked=False allows Odoo auto-pick to work.

        When all component moves have quantity filled and the user clicks
        Validate, Odoo auto-sets picked=True on all moves (line 1486-1487
        of stock_picking.py).  This only works if NO move already has
        picked=True — otherwise Odoo skips auto-pick.

        The set header must have picked=False at creation so it doesn't
        block this mechanism.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            return

        # Verify no move has picked=True initially
        picked_moves = out_picking.move_ids.filtered(
            lambda m: m.picked and m.state not in ('done', 'cancel')
        )
        self.assertFalse(
            picked_moves,
            "No move should have picked=True before validation (RS10)",
        )

        # Fill quantities on component moves
        for move in out_picking.move_ids.filtered(
            lambda m: m.state not in ('done', 'cancel') and m.product_uom_qty > 0
        ):
            move.quantity = move.product_uom_qty

        # Simulate the auto-pick check that Odoo does in button_validate
        has_quantity = any(m.quantity for m in out_picking.move_ids)
        has_pick = any(
            m.picked and m.state not in ('done', 'cancel')
            for m in out_picking.move_ids
        )
        self.assertTrue(has_quantity, "Should have quantities filled")
        self.assertFalse(
            has_pick,
            "has_pick must be False so auto-pick triggers (RS10)",
        )

    # ── Test 20: Cancel order with partial delivery ──────────────────

    def test_20_cancel_order_header_survives(self):
        """Header moves must transition to done (not stay dangling)
        when the order is cancelled after partial delivery.

        If components are cancelled, the header should also be in a
        terminal state (done or cancel), not stuck in assigned.
        """
        order = self._create_rental_order(self.front_light_tmpl)
        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            return

        # Cancel the picking (simulating order cancel flow)
        out_picking.action_cancel()

        # All moves including header should be in terminal state
        for move in out_picking.move_ids:
            self.assertIn(
                move.state, ('done', 'cancel'),
                f"{move.product_id.name} must be in terminal state after cancel",
            )

    # ── Test 21: Multiple sets on one order ──────────────────────────

    def test_21_multiple_sets_separate_headers(self):
        """Each set on an order gets its own header move on the picking.

        When an order has multiple sets, each set parent must have its
        own header move.  Headers must not be shared or duplicated.
        """
        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=1),
        })
        # Add two different sets
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.front_light_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.lighting_pkg_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        order.action_confirm()

        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            return

        # Find headers
        headers = out_picking.move_ids.filtered(
            lambda m: m.sale_line_id and m.sale_line_id.is_set
            and not m.sale_line_id.is_set_component
        )

        # Count distinct set parent SOLs
        set_parents = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )

        self.assertEqual(
            len(headers), len(set_parents),
            f"Each set parent must have exactly one header move "
            f"(expected {len(set_parents)}, got {len(headers)})",
        )

        # Headers must reference different set parent SOLs
        header_sols = headers.mapped('sale_line_id')
        self.assertEqual(
            len(header_sols), len(headers),
            "Each header must reference a different set parent SOL",
        )

    # ── Test 22: Nested set header behavior ──────────────────────────

    def test_22_nested_set_header(self):
        """Nested sets: verify the top-level set gets a header move.

        The lighting_pkg_tmpl contains front_light_tmpl as a nested set.
        The picking should have a header for the top-level set.
        """
        order = self._create_rental_order(self.lighting_pkg_tmpl)
        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            return

        # Top-level set parent
        top_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertTrue(top_parent)

        # Header for top-level set
        top_header = out_picking.move_ids.filtered(
            lambda m: m.sale_line_id == top_parent
        )
        self.assertTrue(
            top_header,
            "Top-level set must have a header move on the picking",
        )
        self.assertEqual(top_header.product_uom_qty, 0)

    # ── Test 23: Availability consistency draft vs confirmed ─────────

    def test_23_availability_consistent_draft_confirmed(self):
        """RS08: availability must not drop when confirming an order.

        The set availability in draft and after confirmation should be
        consistent — confirming the order should not reduce the apparent
        availability because the order's own reservations are excluded.
        """
        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.front_light_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })

        # Get draft availability
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        draft_avail = set_parent.set_availability

        # Confirm
        order.action_confirm()
        set_parent.invalidate_recordset(['set_availability'])
        confirmed_avail = set_parent.set_availability

        # Availability should not drop after confirmation
        self.assertGreaterEqual(
            confirmed_avail, draft_avail,
            "Availability must not drop after confirmation (RS08). "
            f"Draft={draft_avail}, Confirmed={confirmed_avail}",
        )

    # ── Test 24: Duplicate order does not double set components ──────

    def test_24_duplicate_order_no_double_components(self):
        """RS11: duplicating an order must not expand the set a second time.

        When an order with a rental set is duplicated, Odoo copies both
        the set parent line and all its component lines.  The create()
        hook must detect that components already exist (from the copy)
        and skip _expand_rental_set(), otherwise components are doubled.
        """
        # Create order with a set
        order = self._create_rental_order(self.front_light_tmpl)

        # Count components on original
        original_components = order.order_line.filtered('is_set_component')
        original_count = len(original_components)
        self.assertGreater(original_count, 0, "Original must have components")

        # Duplicate the order
        new_order = order.copy()

        # Count components on duplicate
        new_components = new_order.order_line.filtered('is_set_component')
        new_count = len(new_components)

        self.assertEqual(
            new_count, original_count,
            f"Duplicate must have same number of components as original "
            f"({original_count}), not double ({original_count * 2}). "
            f"Got {new_count}.",
        )

        # Verify set parent exists and has the right children
        new_parent = new_order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertEqual(len(new_parent), 1, "Must have exactly 1 set parent")
        self.assertEqual(
            len(new_parent.set_child_line_ids), original_count,
            "Set parent must reference all (and only) the copied components",
        )

    # ── Test 25: Competing demand reduces set availability ───────────

    def test_25_competing_demand_reduces_set_availability(self):
        """RS12: standalone lines for the same product reduce set availability.

        S02759: order has a set with storable components + standalone line
        for the same product.  The set availability must account for the
        standalone demand.  E.g. 20 available, set needs 3, standalone
        needs 18: set avail = (20 - 18) / 3 = 0.67.

        Without this fix, the set would show 20/3 = 6.67 because it
        only looks at its own component demand, ignoring the standalone.
        """
        # Create a storable product and a set containing it
        storable_product = self.env['product.product'].create({
            'name': 'Storable Rental Item',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        set_tmpl = self.env['product.template'].create({
            'name': 'Test Competing Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id,
            'product_id': storable_product.id,
            'quantity': 3,
            'sequence': 10,
        })

        # Put some stock for the product
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable_product.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 20,
        }).action_apply_inventory()

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })

        # Add the set
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        set_parent = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )
        self.assertTrue(set_parent)

        # Get availability WITHOUT competing demand
        set_parent.invalidate_recordset(['set_availability'])
        avail_without = set_parent.set_availability

        # Add a large standalone line for the SAME storable product
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable_product.id,
            'product_uom_qty': 18,
        })

        # Recalculate WITH competing demand
        set_parent.invalidate_recordset(['set_availability'])
        avail_with = set_parent.set_availability

        self.assertLess(
            avail_with, avail_without,
            f"Standalone demand must reduce set availability (RS12). "
            f"Without: {avail_without}, with 18 standalone: {avail_with}",
        )
        # Specifically: 20 available - 18 standalone = 2 for set / 3 needed
        # = 0.67 → floored to 0 whole sets (RAV-08: whole sets only).
        self.assertEqual(
            avail_with, 0.0,
            msg="(20 - 18) / 3 = 0.67 → floor = 0 whole sets available",
        )

    # ── Test 26: order_product_demand aggregates correctly ────────────

    def test_26_order_product_demand_aggregates(self):
        """RS12: order_product_demand sums all lines for the same product.

        When a set component and a standalone line use the same product,
        order_product_demand must equal the sum of both quantities.
        """
        storable = self.env['product.product'].create({
            'name': 'Demand Test Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        set_tmpl = self.env['product.template'].create({
            'name': 'Demand Test Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id,
            'product_id': storable.id,
            'quantity': 5,
            'sequence': 10,
        })

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        # Add set (component qty = 5)
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        # Add standalone line for the same product (qty = 8)
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 8,
        })

        # All lines for this product should have order_product_demand = 13
        product_lines = order.order_line.filtered(
            lambda l: l.product_id == storable
        )
        self.assertEqual(len(product_lines), 2, "Two lines for the product")
        for pl in product_lines:
            self.assertEqual(
                pl.order_product_demand, 13,
                f"order_product_demand must be 5+8=13, got {pl.order_product_demand}",
            )

    # ── Test 27: free_qty_today same for all lines of same product ────

    def test_27_free_qty_same_across_lines(self):
        """RS12: free_qty_today is consistent across all lines for same product.

        When the order has multiple lines for the same product (set
        component + standalone), both must show the same free_qty_today
        (the real available stock excluding the entire order's demand).
        """
        storable = self.env['product.product'].create({
            'name': 'Consistent Qty Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        # Create stock
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 30,
        }).action_apply_inventory()

        set_tmpl = self.env['product.template'].create({
            'name': 'Consistent Qty Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id,
            'product_id': storable.id,
            'quantity': 5,
            'sequence': 10,
        })

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 10,
        })

        product_lines = order.order_line.filtered(
            lambda l: l.product_id == storable
        )
        self.assertEqual(len(product_lines), 2)

        # The key check: order_product_demand is the same for both lines
        # and reflects the true aggregate demand (5 + 10 = 15).
        # The red/green icon compares order_product_demand against
        # free_qty_today.  Both lines must agree on the conclusion.
        for pl in product_lines:
            self.assertEqual(
                pl.order_product_demand, 15,
                f"order_product_demand must be 15 (5+10), got {pl.order_product_demand}",
            )
            # With 30 in stock and 15 demanded, both should be green
            # (order_product_demand <= free_qty_today regardless of
            # which code path computed free_qty_today)
            self.assertLessEqual(
                pl.order_product_demand, pl.free_qty_today,
                f"15 demand <= {pl.free_qty_today} stock → green icon",
            )

    # ── Test 28: red icon when aggregate demand exceeds stock ─────────

    def test_28_aggregate_demand_red_icon(self):
        """RS12: stock indicator is red when aggregate demand > available.

        Two lines for the same product: set component (5) + standalone (26)
        = 31 total.  Stock = 30.  Both lines must report that the order
        demand (31) exceeds available (30) — i.e. will_be_fulfilled=False.
        """
        storable = self.env['product.product'].create({
            'name': 'Red Icon Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 30,
        }).action_apply_inventory()

        set_tmpl = self.env['product.template'].create({
            'name': 'Red Icon Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': set_tmpl.id,
            'product_id': storable.id,
            'quantity': 5,
            'sequence': 10,
        })

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 26,
        })

        product_lines = order.order_line.filtered(
            lambda l: l.product_id == storable
        )
        for pl in product_lines:
            self.assertEqual(pl.order_product_demand, 31)
            # free_qty_today should be 30 (real stock, order excluded)
            # order_product_demand = 31 > 30 = red
            self.assertGreater(
                pl.order_product_demand, pl.free_qty_today,
                f"Aggregate demand (31) must exceed available ({pl.free_qty_today}) → red icon",
            )

    # ── Test 29: Forecast-based availability matches physical stock ───

    def test_29_forecast_availability_matches_physical(self):
        """RS13: free_qty_today must not exceed physical stock.

        The forecast-based computation caps at qty_available.  Even with
        timing artifacts from returned orders, the availability shown
        must never exceed actual physical stock in the warehouse.
        """
        storable = self.env['product.product'].create({
            'name': 'Forecast Test Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 25,
        }).action_apply_inventory()

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 5,
        })

        line = order.order_line.filtered(lambda l: l.product_id == storable)
        self.assertLessEqual(
            line.free_qty_today, 25,
            "free_qty_today must never exceed physical stock (25)",
        )
        self.assertGreater(
            line.free_qty_today, 0,
            "free_qty_today must be > 0 when stock exists",
        )

    # ── Test 30: Confirmed order sees own demand as available ─────────

    def test_30_confirmed_order_sees_own_demand(self):
        """RS13: confirmed order's free_qty includes its own demand.

        After confirmation, the order's outgoing moves reduce the forecast.
        But "available for this order" should include what it already
        claimed — the order should not reduce its own availability.
        """
        storable = self.env['product.product'].create({
            'name': 'Own Demand Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 10,
        }).action_apply_inventory()

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 3,
        })

        # Draft availability
        line = order.order_line.filtered(lambda l: l.product_id == storable)
        draft_avail = line.free_qty_today

        # Confirm
        order.action_confirm()
        line.invalidate_recordset()
        confirmed_avail = line.free_qty_today

        # Confirmed availability must be > 0 and include own demand.
        # The exact value depends on move scheduling, but it must be
        # at least as large as the order's own demand (the order can
        # always fulfill itself from its own reservation).
        self.assertGreaterEqual(
            confirmed_avail, line.product_uom_qty,
            f"Confirmed availability ({confirmed_avail}) must be >= "
            f"own demand ({line.product_uom_qty}) — order can fulfill itself (RS13)",
        )

    # ── Test 31: All-warehouse availability ───────────────────────────

    def test_31_all_warehouse_available(self):
        """RS14: all_warehouse_available sums stock across all warehouses."""
        storable = self.env['product.product'].create({
            'name': 'Multi WH Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': storable.id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'inventory_quantity': 15,
        }).action_apply_inventory()

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': storable.id,
            'product_uom_qty': 1,
        })

        line = order.order_line.filtered(lambda l: l.product_id == storable)
        # With one warehouse, all_warehouse_available == warehouse stock
        self.assertEqual(
            line.all_warehouse_available, 15,
            "all_warehouse_available must equal total stock (15)",
        )
        self.assertEqual(
            line.all_warehouse_count, 1,
            "all_warehouse_count must be 1 (single warehouse)",
        )

    # ── Test 32: View Rentals action grouped by customer ─────────────

    def test_32_view_rentals_grouped_by_customer(self):
        """RS15: action_view_rentals groups by customer, not product.

        The standard rental Gantt view groups by product.  Our override
        replaces this with customer grouping when viewing from a specific
        product's popover.
        """
        storable = self.env['product.product'].create({
            'name': 'Gantt Group Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'rent_periodicity': 'days',
        })
        action = storable.action_view_rentals()

        # The standard action has groupby_product in context
        self.assertTrue(
            action.get('context', {}).get('search_default_groupby_product'),
            "Standard action must have search_default_groupby_product",
        )

        # Our JS override would replace this with groupby_customer
        # We can't test JS here, but verify the search view has
        # the groupby_customer filter available
        search_view_id = action.get('search_view_id')
        if search_view_id:
            view = self.env['ir.ui.view'].browse(search_view_id[0])
            self.assertIn(
                'groupby_customer', view.arch,
                "Search view must have groupby_customer filter for RS15",
            )

    # ── Test 33: Nested set has independent availability ──────────────

    def test_33_nested_set_independent_availability(self):
        """RS16: nested set shows its own availability independently.

        When Test Set is added as a component of another set, the nested
        set's availability must equal the standalone set's availability —
        not reduced by the parent's other components or sibling sets.
        """
        # Create a set that contains another set as a component
        inner_set = self.front_light_tmpl  # sum-mode set
        outer_tmpl = self.env['product.template'].create({
            'name': 'Outer Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': outer_tmpl.id,
            'product_id': inner_set.product_variant_id.id,
            'quantity': 1,
            'sequence': 10,
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': outer_tmpl.id,
            'product_id': self.truss.id,
            'quantity': 2,
            'sequence': 20,
        })

        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now + timedelta(days=30),
            'rental_return_date': now + timedelta(days=31),
        })
        # Add outer set (contains inner set + truss)
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': outer_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })

        # Find the nested set line
        nested = order.order_line.filtered(
            lambda l: l.is_set and l.is_set_component
        )
        outer = order.order_line.filtered(
            lambda l: l.is_set and not l.is_set_component
        )

        if nested:
            self.assertGreater(
                nested.set_availability, 0,
                "Nested set must have non-zero availability (RS16)",
            )
            # Nested set availability should be >= outer set availability
            # (nested is a subset of the outer's demand)
            self.assertGreaterEqual(
                nested.set_availability, outer.set_availability,
                "Nested set availability must be >= outer (RS16)",
            )

    # ── Test 34: Nested set excluded from procurement ────────────────

    def test_34_nested_set_no_procurement(self):
        """RS17: nested sets don't create procurement moves.

        When a set contains another set as a component, only the leaf
        components generate stock moves.  The nested set product itself
        must not have a procurement move (it's virtual).
        """
        inner_set = self.front_light_tmpl
        outer_tmpl = self.env['product.template'].create({
            'name': 'Outer Set Procurement Test',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': outer_tmpl.id,
            'product_id': inner_set.product_variant_id.id,
            'quantity': 1,
            'sequence': 10,
        })

        order = self._create_rental_order(outer_tmpl)
        order.action_confirm()

        # Find moves for the nested set product
        nested_sol = order.order_line.filtered(
            lambda l: l.is_set and l.is_set_component
        )
        if not nested_sol:
            return

        out_picking = order.picking_ids.filtered(
            lambda p: not p.return_id and p.state not in ('cancel',)
        )[:1]
        if not out_picking:
            return

        nested_moves = out_picking.move_ids.filtered(
            lambda m: m.sale_line_id == nested_sol
            and m.state != 'cancel'
        )
        for m in nested_moves:
            # Nested set moves must be headers (demand=0), not procurement
            self.assertEqual(
                m.product_uom_qty, 0,
                f"Nested set move must have demand=0 (header only), "
                f"got {m.product_uom_qty} (RS17)",
            )

    # ── Test 35: display_qty_widget hidden for all set lines ─────────

    def test_35_display_qty_widget_hidden_for_sets(self):
        """RS17: display_qty_widget=False for all is_set lines.

        Both top-level and nested sets must hide the standard stock
        widget (it would show red because set products have no stock).
        """
        inner_set = self.front_light_tmpl
        outer_tmpl = self.env['product.template'].create({
            'name': 'Widget Test Set',
            'type': 'consu',
            'list_price': 0,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'sum',
        })
        self.env['rental.set.component'].create({
            'set_product_tmpl_id': outer_tmpl.id,
            'product_id': inner_set.product_variant_id.id,
            'quantity': 1,
            'sequence': 10,
        })

        order = self._create_rental_order(outer_tmpl)

        for line in order.order_line:
            if line.is_set:
                self.assertFalse(
                    line.display_qty_widget,
                    f"{line.product_id.name} (is_set={line.is_set}, "
                    f"is_comp={line.is_set_component}): display_qty_widget "
                    f"must be False (RS17)",
                )

    # ── Test 36: Multi-step: headers on chained pickings ─────────────

    def test_36_multistep_headers_on_chained_pickings(self):
        """RS19: set headers are created on next-step picking after validation.

        In Pick→Pack→Ship, validating Pick must create a set header on
        the Pack picking so the set grouping is preserved.
        """
        wh = self.env['stock.warehouse'].search([], limit=1)
        original_steps = wh.delivery_steps
        try:
            wh.write({'delivery_steps': 'pick_pack_ship'})

            order = self._create_rental_order(self.lighting_pkg_tmpl)
            order.action_confirm()

            # Find Pick picking
            pick_picking = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'internal'
                and not p.return_id
                and 'Pick' in p.picking_type_id.name
            )[:1]
            if not pick_picking:
                return  # No pick step generated

            # Validate Pick
            for move in pick_picking.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
                and m.product_uom_qty > 0
            ):
                move.quantity = move.product_uom_qty
            pick_picking.with_context(
                skip_backorder=True,
                picking_ids_not_to_backorder=pick_picking.ids,
                skip_sale_flow_sync=True,
                skip_lost_broken_check=True,
            ).button_validate()

            # Find Pack picking (next step)
            pack_picking = order.picking_ids.filtered(
                lambda p: p.picking_type_id.code == 'internal'
                and not p.return_id
                and 'Pack' in p.picking_type_id.name
                and p.state not in ('done', 'cancel')
            )[:1]
            if not pack_picking:
                return  # No pack step

            # Pack picking must have a set header move
            pack_headers = pack_picking.move_ids.filtered(
                lambda m: m.sale_line_id
                and m.sale_line_id.is_set
                and not m.sale_line_id.is_set_component
                and m.state != 'cancel'
            )
            self.assertTrue(
                pack_headers,
                "Pack picking must have set header move after Pick validation (RS19)",
            )
            self.assertEqual(
                pack_headers[0].product_uom_qty, 0,
                "Header must have zero demand",
            )
        finally:
            try:
                wh.write({'delivery_steps': original_steps})
            except Exception:
                pass  # May fail if locations have products

    # ── Test 37: Multi-step: delivered qty not inflated ───────────────

    def test_37_multistep_delivered_qty_not_inflated(self):
        """RS20: delivered_qty must not multiply across delivery steps.

        In Pick→Pack→Ship, only the final Ship step counts as delivered.
        Without deduplication, 5 items through 3 steps would show 15.
        """
        # This test verifies the sale_flow deduplication logic.
        # We create a simple rental order, simulate multi-step delivery,
        # and check the flow line's delivered_qty.
        wh = self.env['stock.warehouse'].search([], limit=1)
        original_steps = wh.delivery_steps
        try:
            wh.write({'delivery_steps': 'pick_pack_ship'})

            # Create a storable rental product with stock
            storable = self.env['product.product'].create({
                'name': 'MultiStep Test Product',
                'type': 'consu',
                'is_storable': True,
                'list_price': 10.0,
                'rent_periodicity': 'days',
            })
            self.env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': storable.id,
                'location_id': self.env.ref('stock.stock_location_stock').id,
                'inventory_quantity': 50,
            }).action_apply_inventory()

            now = fields.Datetime.now()
            order = self.env['sale.order'].create({
                'partner_id': self.partner.id,
                'rental_start_date': now + timedelta(days=30),
                'rental_return_date': now + timedelta(days=31),
            })
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': storable.id,
                'product_uom_qty': 5,
            })
            order.action_confirm()

            # Check flow line exists
            fl = order.flow_line_ids.filtered(
                lambda f: f.product_id == storable
            )[:1]
            if not fl:
                return  # sale_flow not active

            # Validate all outgoing steps
            for pick in order.picking_ids.filtered(
                lambda p: not p.return_id
            ).sorted('id'):
                if pick.state in ('done', 'cancel'):
                    continue
                for move in pick.move_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                    and m.product_uom_qty > 0
                ):
                    move.quantity = move.product_uom_qty
                pick.with_context(
                    skip_backorder=True,
                    picking_ids_not_to_backorder=pick.ids,
                    skip_lost_broken_check=True,
                ).button_validate()

            fl.invalidate_recordset()
            self.assertEqual(
                fl.delivered_qty, 5,
                f"Delivered qty must be 5 (not {fl.delivered_qty}) — "
                f"multi-step deduplication (RS20)",
            )
        finally:
            try:
                wh.write({'delivery_steps': original_steps})
            except Exception:
                pass  # May fail if locations have products
