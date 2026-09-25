from odoo.tests import Form
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestRentalFlowProfile(TransactionCase):
    """Tests for multi.channel.rental.profile and product assortment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.website = cls.env['website'].search([], limit=1)
        cls.pricelist = cls.env['product.pricelist'].search(
            [('company_id', 'in', (cls.company.id, False))], limit=1,
        )

        # --- eCommerce category ---
        cls.ecom_categ = cls.env['product.public.category'].create({
            'name': 'MCRF Test eCommerce Category',
        })

        # --- POS category ---
        cls.pos_categ = cls.env['pos.category'].create({
            'name': 'MCRF Test POS Category',
        })

        # --- Products ---
        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF Test Kayak',
            'type': 'consu',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
            'available_in_multi_channel_website': True,
            'available_in_multi_channel_kiosk': True,
            'requires_timeslot': True,
            'requires_duration': True,
        })
        cls.product_rental.product_tmpl_id.public_categ_ids = cls.ecom_categ
        cls.product_rental.product_tmpl_id.pos_categ_ids = cls.pos_categ

        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF Test Lunch Package',
            'type': 'consu',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
            'available_in_multi_channel_website': True,
            'available_in_multi_channel_kiosk': True,
        })
        cls.product_addon.product_tmpl_id.public_categ_ids = cls.ecom_categ

        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF Test Guide Service',
            'type': 'service',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
            'available_in_multi_channel_website': True,
            'available_in_multi_channel_kiosk': False,
        })

        cls.product_excluded = cls.env['product.product'].create({
            'name': 'MCRF Test Excluded Product',
            'type': 'consu',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
            'available_in_multi_channel_website': True,
            'available_in_multi_channel_kiosk': True,
        })
        cls.product_excluded.product_tmpl_id.public_categ_ids = cls.ecom_categ

        cls.product_not_in_flow = cls.env['product.product'].create({
            'name': 'MCRF Test Regular Product',
            'type': 'consu',
            'use_in_multi_channel_rental_flow': False,
        })

    # ------------------------------------------------------------------
    # Profile creation
    # ------------------------------------------------------------------

    def test_01_create_kiosk_profile(self):
        """Create a kiosk profile with all fields."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Test Kiosk 1',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'rental_slot_interval_minutes': 15,
            'low_dynamic_factor_threshold': 85.0,
            'high_dynamic_factor_threshold': 115.0,
        })
        self.assertTrue(profile.active)
        self.assertEqual(profile.profile_type, 'kiosk')
        self.assertEqual(profile.rental_slot_interval_minutes, 15)

    def test_02_create_website_profile(self):
        """Create a website profile — requires website_id."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Test Website 1',
            'profile_type': 'website',
            'warehouse_id': self.warehouse.id,
            'website_id': self.website.id,
        })
        self.assertEqual(profile.profile_type, 'website')
        self.assertEqual(profile.website_id, self.website)

    def test_03_website_profile_requires_website(self):
        """Website profile without website_id raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['multi.channel.rental.profile'].create({
                'name': 'Bad Website Profile',
                'profile_type': 'website',
                'warehouse_id': self.warehouse.id,
            })

    def test_04_threshold_validation(self):
        """Low threshold must be less than high threshold."""
        with self.assertRaises(ValidationError):
            self.env['multi.channel.rental.profile'].create({
                'name': 'Bad Thresholds',
                'profile_type': 'kiosk',
                'warehouse_id': self.warehouse.id,
                'low_dynamic_factor_threshold': 120.0,
                'high_dynamic_factor_threshold': 80.0,
            })

    # ------------------------------------------------------------------
    # Product assortment
    # ------------------------------------------------------------------

    def test_10_kiosk_all_products(self):
        """Kiosk profile with no category filter returns all kiosk products."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk All',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })
        products = profile._get_available_products()
        # Should include rental + addon (kiosk=True), exclude service (kiosk=False)
        self.assertIn(self.product_rental, products)
        self.assertIn(self.product_addon, products)
        self.assertNotIn(self.product_service, products)
        self.assertNotIn(self.product_not_in_flow, products)

    def test_11_website_all_products(self):
        """Website profile returns all website-enabled products for enabled roles."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Website All',
            'profile_type': 'website',
            'warehouse_id': self.warehouse.id,
            'website_id': self.website.id,
            'enable_services': True,
        })
        products = profile._get_available_products()
        self.assertIn(self.product_rental, products)
        self.assertIn(self.product_addon, products)
        self.assertIn(self.product_service, products)
        self.assertNotIn(self.product_not_in_flow, products)

    def test_11b_website_role_toggle_excludes_services(self):
        """Website profile with services disabled excludes service products."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Website No Services',
            'profile_type': 'website',
            'warehouse_id': self.warehouse.id,
            'website_id': self.website.id,
            'enable_services': False,
        })
        products = profile._get_available_products()
        self.assertIn(self.product_rental, products)
        self.assertNotIn(self.product_service, products)

    def test_12_ecommerce_category_filter(self):
        """Filter by eCommerce category."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk eComCat',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'allowed_ecommerce_category_ids': [(6, 0, [self.ecom_categ.id])],
        })
        products = profile._get_available_products()
        # Kayak and addon are in ecom_categ, excluded_product too
        self.assertIn(self.product_rental, products)
        self.assertIn(self.product_addon, products)
        self.assertIn(self.product_excluded, products)

    def test_13_pos_category_filter(self):
        """Filter by POS category."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk PosCat',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'allowed_pos_category_ids': [(6, 0, [self.pos_categ.id])],
        })
        products = profile._get_available_products()
        # Only kayak is in pos_categ
        self.assertIn(self.product_rental, products)
        self.assertNotIn(self.product_addon, products)

    def test_14_explicit_exclusion(self):
        """Excluded products are filtered out."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk Exclusion',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'excluded_product_ids': [(6, 0, [self.product_excluded.id])],
        })
        products = profile._get_available_products()
        self.assertNotIn(self.product_excluded, products)
        self.assertIn(self.product_rental, products)

    def test_15_explicit_inclusion(self):
        """Explicitly included products bypass category filters."""
        # Create product NOT in any category
        product_orphan = self.env['product.product'].create({
            'name': 'MCRF Orphan Product',
            'type': 'consu',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
            'available_in_multi_channel_kiosk': True,
        })
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk Include',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'allowed_ecommerce_category_ids': [(6, 0, [self.ecom_categ.id])],
            'included_product_ids': [(6, 0, [product_orphan.id])],
        })
        products = profile._get_available_products()
        # Orphan is included explicitly even though not in ecom_categ
        self.assertIn(product_orphan, products)
        # Kayak is still there (in category)
        self.assertIn(self.product_rental, products)

    def test_16_role_filter(self):
        """Filter by item role."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk Role',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })
        rental_products = profile._get_available_products(item_role='rental')
        self.assertIn(self.product_rental, rental_products)
        self.assertNotIn(self.product_addon, rental_products)

        addon_products = profile._get_available_products(item_role='addon')
        self.assertIn(self.product_addon, addon_products)
        self.assertNotIn(self.product_rental, addon_products)

    def test_17_role_toggle(self):
        """Disabled role toggles filter out products."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Kiosk No Addons',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'enable_rental_items': True,
            'enable_addons': False,
        })
        products = profile._get_available_products()
        self.assertIn(self.product_rental, products)
        self.assertNotIn(self.product_addon, products)

    # ------------------------------------------------------------------
    # Color helper
    # ------------------------------------------------------------------

    def test_20_dynamic_color(self):
        """Color helper returns correct color based on factor thresholds."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Color Test',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'low_dynamic_factor_threshold': 90.0,
            'high_dynamic_factor_threshold': 110.0,
            'low_color': '#LOW',
            'normal_color': '#NORM',
            'high_color': '#HIGH',
        })
        self.assertEqual(profile._get_dynamic_color(80.0), '#LOW')
        self.assertEqual(profile._get_dynamic_color(90.0), '#NORM')
        self.assertEqual(profile._get_dynamic_color(100.0), '#NORM')
        self.assertEqual(profile._get_dynamic_color(110.0), '#NORM')
        self.assertEqual(profile._get_dynamic_color(120.0), '#HIGH')

    # ------------------------------------------------------------------
    # Printer info computed
    # ------------------------------------------------------------------

    def test_30_printer_info_empty(self):
        """Profile without printer shows empty info."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'No Printer',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'printer_mode': 'none',
        })
        self.assertEqual(profile.printer_ip_display, '')
        self.assertEqual(profile.printer_type_display, '')

    def test_31_printer_info_epos(self):
        """Profile with ePOS printer shows printer IP."""
        printer = self.env['pos.printer'].create({
            'name': 'MCRF Test Epson',
            'printer_type': 'epson_epos',
            'printer_ip': '192.168.1.100',
        })
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'ePOS Profile',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'printer_mode': 'pos_epos_ip',
            'pos_printer_id': printer.id,
        })
        self.assertEqual(profile.printer_ip_display, '192.168.1.100')

    # ------------------------------------------------------------------
    # Product count stat button
    # ------------------------------------------------------------------

    def test_40_available_product_count(self):
        """Stat button count matches _get_available_products result."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Count Test',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })
        products = profile._get_available_products()
        self.assertEqual(profile.available_product_count, len(products))

    def test_41_action_view_available_products(self):
        """Stat button action returns correct domain."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Action Test',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })
        action = profile.action_view_available_products()
        self.assertEqual(action['res_model'], 'product.product')
        products = profile._get_available_products()
        self.assertEqual(action['domain'], [('id', 'in', products.ids)])

    # ------------------------------------------------------------------
    # Fallback durations
    # ------------------------------------------------------------------

    def test_50_fallback_durations(self):
        """Fallback duration options are stored on the profile."""
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'Duration Test',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'fallback_duration_ids': [
                (0, 0, {'name': '30 min', 'duration_value': 30,
                         'duration_unit': 'minute', 'sequence': 1}),
                (0, 0, {'name': '1 hour', 'duration_value': 1,
                         'duration_unit': 'hour', 'sequence': 2}),
                (0, 0, {'name': '2 hours', 'duration_value': 2,
                         'duration_unit': 'hour', 'sequence': 3}),
            ],
        })
        self.assertEqual(len(profile.fallback_duration_ids), 3)
        self.assertEqual(profile.fallback_duration_ids[0].name, '30 min')

    # ------------------------------------------------------------------
    # Guest checkout modes  [PF07]
    # ------------------------------------------------------------------

    def test_55_guest_checkout_modes(self):
        """Guest checkout mode field stores all valid values."""
        for mode in ('login_required', 'guest_with_email', 'guest_minimal'):
            profile = self.env['multi.channel.rental.profile'].create({
                'name': f'Guest {mode}',
                'profile_type': 'kiosk',
                'warehouse_id': self.warehouse.id,
                'guest_checkout_mode': mode,
            })
            self.assertEqual(profile.guest_checkout_mode, mode)
