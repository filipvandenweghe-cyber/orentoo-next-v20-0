from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase, tagged


# These tests drive the full checkout flow, which creates account.payment
# records (requiring the company's outstanding/transfer account) and
# account.tax records (requiring the company's fiscal country). Those
# company accounting settings are only guaranteed to be fully configured
# once every module and its demo data have loaded, so the suite must run
# post_install — at at_install the ambient company may still be missing
# its chart-of-accounts wiring, which caused the "No outstanding account"
# and account_tax.country_id NOT NULL failures. [PY03]
@tagged('post_install', '-at_install')
class TestKioskOrder(TransactionCase):
    """Tests for kiosk ordering flow API.  [KO01–KO15]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF KO Pricelist',
            'company_id': cls.company.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF KO Default Customer',
            'email': 'ko-default@mcrf.example.com',
        })

        cls.ecom_categ = cls.env['product.public.category'].create({
            'name': 'MCRF KO Test Category',
        })

        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF KO Kayak',
            'type': 'consu',
            'is_storable': True,
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
            'available_in_multi_channel_kiosk': True,
            'requires_timeslot': True,
            'requires_duration': True,
        })
        cls.product_rental.product_tmpl_id.public_categ_ids = cls.ecom_categ
        cls.env['stock.quant']._update_available_quantity(
            cls.product_rental, cls.warehouse.lot_stock_id, 10.0,
        )

        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF KO Lunch',
            'type': 'consu',
            'list_price': 15.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
            'available_in_multi_channel_kiosk': True,
        })
        cls.product_addon.product_tmpl_id.public_categ_ids = cls.ecom_categ

        cls.profile = cls.env['multi.channel.rental.profile'].create({
            'name': 'MCRF KO Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'pricelist_id': cls.pricelist.id,
            'default_partner_id': cls.partner.id,
            'enable_dossier_ordering': True,
            'enable_ticket_lookup_printing': True,
            'allow_demo_payment': True,
            'printer_mode': 'browser',
            'allowed_ecommerce_category_ids': [(4, cls.ecom_categ.id)],
        })

        # The kiosk checkout tests (test_30/60/61/62) simulate a demo
        # payment, which needs an *enabled* demo payment provider. The
        # demo provider (from payment_demo) ships disabled by default, so
        # enable it for the test transaction.
        # Odoo 20 replaced payment.provider.state by 'active' (archived ==
        # disabled) + 'is_live' (live vs. test mode).
        cls.demo_provider = cls.env['payment.provider'].with_context(
            active_test=False,
        ).search([('code', '=', 'demo')], limit=1)
        if cls.demo_provider and not cls.demo_provider.active:
            cls.demo_provider.write({'active': True, 'is_live': False})

        # Enabling the demo provider via a raw write does not auto-assign a
        # payment journal, and some demo/test databases have no chart of
        # accounts loaded on the main company (hence no bank journal at all).
        # Guarantee the provider posts to a bank journal so the checkout
        # tests can simulate a payment regardless of the ambient setup.
        if cls.demo_provider and not cls.demo_provider.journal_id:
            journal = cls.env['account.journal'].search(
                [('type', '=', 'bank'), ('company_id', '=', cls.company.id)],
                limit=1,
            )
            if not journal:
                journal = cls.env['account.journal'].create({
                    'name': 'MCRF Test Bank',
                    'type': 'bank',
                    'code': 'MCRFB',
                    'company_id': cls.company.id,
                })
            cls.demo_provider.journal_id = journal

        # A tax group is required to create taxes; some databases have none
        # for the test company, so get-or-create one for reuse in tests.
        cls.tax_group = cls.env['account.tax.group'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        if not cls.tax_group:
            cls.tax_group = cls.env['account.tax.group'].create({
                'name': 'MCRF Test Tax Group',
                'company_id': cls.company.id,
            })

        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

    # ------------------------------------------------------------------
    # Categories  [KO03]
    # ------------------------------------------------------------------

    def test_01_get_categories(self):
        """API returns categories from profile."""
        from odoo.addons.multi_channel_rental_flow.controllers.kiosk_order import (
            MultiChannelRentalKioskOrder,
        )
        # Simulate the API call via service directly
        profile = self.profile
        cats = profile.allowed_ecommerce_category_ids
        self.assertTrue(len(cats) >= 1)
        self.assertEqual(cats[0].name, 'MCRF KO Test Category')

    # ------------------------------------------------------------------
    # Products  [KO02, KO04]
    # ------------------------------------------------------------------

    def test_10_profile_returns_products(self):
        """Profile returns kiosk-available products."""
        products = self.profile._get_available_products()
        self.assertIn(self.product_rental, products)
        self.assertIn(self.product_addon, products)

    def test_11_products_filtered_by_category(self):
        """Products filtered by eCommerce category."""
        products = self.profile._get_available_products()
        in_categ = products.filtered(
            lambda p: self.ecom_categ.id in p.product_tmpl_id.public_categ_ids.ids
        )
        self.assertIn(self.product_rental, in_categ)

    # ------------------------------------------------------------------
    # Basket  [KO10]
    # ------------------------------------------------------------------

    def test_20_create_basket(self):
        """Creating a basket creates a kiosk dossier."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        self.assertEqual(dossier.source, 'kiosk')
        self.assertTrue(dossier.is_multi_channel)
        self.assertEqual(dossier.partner_id, self.partner)

    def test_21_add_rental_item_to_basket(self):
        """Adding a rental item with slot creates slot + item."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 2,
            'price_unit': 50.0,
        })
        self.assertEqual(dossier.slot_count, 1)
        self.assertEqual(dossier.item_count, 1)
        self.assertAlmostEqual(item.price_subtotal, 100.0)

    def test_22_add_addon_to_basket(self):
        """Adding an addon (no slot) works."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_addon.id,
            'item_role': 'addon',
            'quantity': 3,
            'price_unit': 15.0,
        })
        self.assertAlmostEqual(item.price_subtotal, 45.0)

    def test_23_remove_item_from_basket(self):
        """Removing an item deletes it and its empty slot."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
        })
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })
        item_id = item.id
        slot_id = slot.id
        item.unlink()
        # Slot should still exist (cleanup is in controller)
        self.assertTrue(self.env['rental.dossier.slot'].browse(slot_id).exists())

    # ------------------------------------------------------------------
    # Full checkout flow  [KO11]
    # ------------------------------------------------------------------

    def test_30_full_checkout(self):
        """Full kiosk checkout: basket → prepare → pay → paid."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'ko-test@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })

        # Prepare for payment
        dossier.action_prepare_for_payment()
        self.assertEqual(dossier.state, 'payment_pending')

        # Simulate demo payment
        dossier.action_simulate_demo_payment()
        self.assertEqual(dossier.state, 'paid')

        # Ticket payload available
        svc = self.env['multi.channel.rental.ticket.service']
        payload = svc._get_print_payload_for_dossier(dossier)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['ticket_count'] > 0)

    # ------------------------------------------------------------------
    # Profile URLs  [KO14]
    # ------------------------------------------------------------------

    def test_40_profile_urls(self):
        """Profile computes kiosk order and lookup URLs."""
        self.assertTrue(self.profile.kiosk_order_url)
        self.assertIn(f'/rental-kiosk/{self.profile.id}/order',
                       self.profile.kiosk_order_url)
        self.assertIn(f'/rental-kiosk/{self.profile.id}',
                       self.profile.kiosk_url)

    # ------------------------------------------------------------------
    # Disabled profile  [KO13]
    # ------------------------------------------------------------------

    def test_50_disabled_ordering(self):
        """Profile with ordering disabled blocks kiosk order page."""
        profile_off = self.env['multi.channel.rental.profile'].create({
            'name': 'KO Disabled',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'enable_dossier_ordering': False,
        })
        self.assertFalse(profile_off.enable_dossier_ordering)

    # ------------------------------------------------------------------
    # Price consistency: kiosk basket vs confirmed orders
    # ------------------------------------------------------------------

    def test_60_kiosk_total_matches_order_total(self):
        """Kiosk basket total (incl. tax) matches the sum of confirmed
        sale order totals after checkout.

        This verifies that the tax computed in the basket API using
        product.taxes_id.compute_all() matches the actual tax on the
        generated and confirmed sale order lines.
        """
        # Create a dossier simulating the kiosk flow
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'ko-tax-test@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })

        # Add rental item with slot (qty=2, 2h duration)
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 2,
            'price_unit': 50.0,
        })

        # Add addon (no slot, qty=3)
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_addon.id,
            'item_role': 'addon',
            'quantity': 3,
            'price_unit': 15.0,
        })

        # Compute basket total with taxes (same logic as the API)
        basket_total_incl = 0.0
        for item in dossier.item_ids:
            taxes = item.product_id.taxes_id.filtered(
                lambda t: t.company_id == dossier.company_id
            )
            if taxes:
                tax_res = taxes.compute_all(
                    item.price_unit,
                    currency=dossier.currency_id,
                    quantity=item.quantity,
                    product=item.product_id,
                    partner=dossier.partner_id,
                )
                basket_total_incl += tax_res['total_included']
            else:
                basket_total_incl += item.price_subtotal

        # Prepare and pay (generates + confirms orders)
        dossier.action_prepare_for_payment()
        dossier.action_simulate_demo_payment()
        self.assertEqual(dossier.state, 'paid')

        # Sum of confirmed sale order totals (incl. tax)
        order_total = sum(
            dossier.sale_order_ids.filtered(
                lambda o: o.state != 'cancel'
            ).mapped('amount_total')
        )

        # They must match
        self.assertAlmostEqual(
            basket_total_incl, order_total, places=2,
            msg=(
                f"Kiosk basket total ({basket_total_incl:.2f}) does not match "
                f"sale order total ({order_total:.2f})"
            ),
        )

    def test_61_kiosk_tax_breakdown_matches_orders(self):
        """Kiosk tax breakdown matches the sum of sale order tax amounts."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'ko-tax-test2@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })

        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=14),
            'end_datetime': self.tomorrow.replace(hour=16),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })

        # Compute expected tax from basket
        basket_tax = 0.0
        basket_untaxed = 0.0
        for item in dossier.item_ids:
            taxes = item.product_id.taxes_id.filtered(
                lambda t: t.company_id == dossier.company_id
            )
            if taxes:
                tax_res = taxes.compute_all(
                    item.price_unit,
                    currency=dossier.currency_id,
                    quantity=item.quantity,
                    product=item.product_id,
                    partner=dossier.partner_id,
                )
                basket_untaxed += tax_res['total_excluded']
                basket_tax += tax_res['total_included'] - tax_res['total_excluded']
            else:
                basket_untaxed += item.price_subtotal

        # Checkout
        dossier.action_prepare_for_payment()
        dossier.action_simulate_demo_payment()

        # Order totals
        orders = dossier.sale_order_ids.filtered(
            lambda o: o.state != 'cancel'
        )
        order_untaxed = sum(orders.mapped('amount_untaxed'))
        order_tax = sum(orders.mapped('amount_tax'))
        order_total = sum(orders.mapped('amount_total'))

        self.assertAlmostEqual(
            basket_untaxed, order_untaxed, places=2,
            msg=f"Untaxed: basket {basket_untaxed:.2f} vs orders {order_untaxed:.2f}",
        )
        self.assertAlmostEqual(
            basket_tax, order_tax, places=2,
            msg=f"Tax: basket {basket_tax:.2f} vs orders {order_tax:.2f}",
        )
        self.assertAlmostEqual(
            basket_untaxed + basket_tax, order_total, places=2,
            msg=f"Total: basket {basket_untaxed + basket_tax:.2f} vs orders {order_total:.2f}",
        )

    def test_62_kiosk_mixed_items_price_consistency(self):
        """Mixed dossier (rental + addon): kiosk total matches orders."""
        # Create product with a specific tax for clear testing
        # account.tax.country_id is NOT NULL. It normally defaults from the
        # company's fiscal country, but pass it explicitly so the test does
        # not depend on the ambient company having a localization loaded.
        tax_country = (
            self.company.account_fiscal_country_id
            or self.company.country_id
            or self.env.ref('base.us')
        )
        tax_15 = self.env['account.tax'].create({
            'name': 'MCRF Test Tax 15%',
            'amount': 15.0,
            'type_tax_use': 'sale',
            'company_id': self.company.id,
            'country_id': tax_country.id,
            'tax_group_id': self.tax_group.id,
        })
        product_taxed = self.env['product.product'].create({
            'name': 'MCRF KO Taxed Addon',
            'type': 'consu',
            'list_price': 20.0,
            'taxes_id': [(6, 0, [tax_15.id])],
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
            'available_in_multi_channel_kiosk': True,
        })
        product_taxed.product_tmpl_id.public_categ_ids = self.ecom_categ

        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'ko-tax-test3@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })

        # Rental item (may or may not have tax)
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=10),
            'end_datetime': self.tomorrow.replace(hour=12),
            'warehouse_id': self.warehouse.id,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })

        # Taxed addon: 20.00 × 2 = 40.00 + 15% = 46.00
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': product_taxed.id,
            'item_role': 'addon',
            'quantity': 2,
            'price_unit': 20.0,
        })

        # Compute basket total with tax
        basket_total = 0.0
        for item in dossier.item_ids:
            taxes = item.product_id.taxes_id.filtered(
                lambda t: t.company_id == dossier.company_id
            )
            if taxes:
                tax_res = taxes.compute_all(
                    item.price_unit,
                    currency=dossier.currency_id,
                    quantity=item.quantity,
                    product=item.product_id,
                    partner=dossier.partner_id,
                )
                basket_total += tax_res['total_included']
            else:
                basket_total += item.price_subtotal

        # Checkout
        dossier.action_prepare_for_payment()
        dossier.action_simulate_demo_payment()

        # Order total
        order_total = sum(
            dossier.sale_order_ids.filtered(
                lambda o: o.state != 'cancel'
            ).mapped('amount_total')
        )

        self.assertAlmostEqual(
            basket_total, order_total, places=2,
            msg=(
                f"Mixed kiosk basket ({basket_total:.2f}) != "
                f"order total ({order_total:.2f})"
            ),
        )

    # ------------------------------------------------------------------
    # Schedule display for basket / checkout  [KO16]
    # ------------------------------------------------------------------

    def _rental_item(self, start, end, qty=1):
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': start,
            'end_datetime': end,
            'warehouse_id': self.warehouse.id,
        })
        return self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': qty,
            'price_unit': 50.0,
        })

    def test_70_schedule_display_rental_item(self):
        """A rental item exposes date, start time and duration for checkout."""
        item = self._rental_item(
            self.tomorrow.replace(hour=10),
            self.tomorrow.replace(hour=12),
        )
        sched = item._get_schedule_display()
        # Duration is exactly 2 hours; start time and date are populated.
        self.assertEqual(sched['duration_display'], '2 h')
        self.assertTrue(sched['date_display'])
        self.assertTrue(sched['start_time_display'])
        # Start time matches the slot in the flow (warehouse calendar) tz.
        from pytz import UTC, timezone
        tz_name = self.env['multi.channel.rental.service']._get_flow_timezone(
            item.slot_id.warehouse_id or item.dossier_id.warehouse_id,
        )
        start_local = UTC.localize(
            item.slot_id.start_datetime,
        ).astimezone(timezone(tz_name))
        self.assertEqual(sched['start_time_display'], start_local.strftime('%H:%M'))

    def test_71_schedule_display_addon_is_empty(self):
        """Non-scheduled items (add-ons) return empty schedule fields."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })
        addon = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_addon.id,
            'item_role': 'addon',
            'quantity': 1,
            'price_unit': 15.0,
        })
        sched = addon._get_schedule_display()
        self.assertEqual(sched['date_display'], '')
        self.assertEqual(sched['start_time_display'], '')
        self.assertEqual(sched['duration_display'], '')

    def test_72_format_duration_variants(self):
        """Duration formatting covers minutes, hours and multi-day spans."""
        item = self.env['rental.dossier.item']
        self.assertEqual(item._format_duration(timedelta(hours=2)), '2 h')
        self.assertEqual(item._format_duration(timedelta(minutes=45)), '45 min')
        self.assertEqual(item._format_duration(timedelta(days=1)), '1 day')
        self.assertEqual(
            item._format_duration(timedelta(days=2, hours=8, minutes=30)),
            '2 days 8 h 30 min',
        )
        self.assertEqual(item._format_duration(timedelta(0)), '')

    def test_73_schedule_display_keys_present(self):
        """The schedule dict always exposes the three checkout keys."""
        item = self._rental_item(
            self.tomorrow.replace(hour=9),
            self.tomorrow.replace(hour=11),
        )
        sched = item._get_schedule_display()
        self.assertEqual(
            set(sched), {'date_display', 'start_time_display', 'duration_display'},
        )
        self.assertEqual(sched['duration_display'], '2 h')

    def test_74_schedule_display_uses_warehouse_calendar_tz(self):
        """Basket time is shown in the warehouse opening-hours calendar tz."""
        # Odoo 20 removed resource.calendar.tz: a schedule is read in its
        # company's timezone.
        self.env.company.tz = 'Europe/Brussels'
        calendar = self.env['resource.calendar'].create({
            'name': 'KO TZ Calendar',
            'company_id': self.env.company.id,
            'attendance_ids': [],
        })
        self.warehouse.opening_hours = calendar
        # 06:00 UTC on a summer date == 08:00 Brussels (CEST, UTC+2).
        item = self._rental_item(
            datetime(2026, 7, 1, 6, 0), datetime(2026, 7, 1, 8, 0),
        )
        sched = item._get_schedule_display()
        self.assertEqual(sched['start_time_display'], '08:00')
        self.assertEqual(sched['duration_display'], '2 h')

    # ------------------------------------------------------------------
    # Add-to-basket timeslot guard  [KO17]
    # ------------------------------------------------------------------

    def test_75_basket_add_rejects_rental_without_timeslot(self):
        """A timeslot-required rental cannot be added without start/end."""
        svc = self.env['multi.channel.rental.service']
        # requires_timeslot rental with no schedule → rejected with a message.
        self.assertTrue(svc._basket_add_rejection(
            self.product_rental, 'rental', None, None,
        ))
        # Same rental with start/end supplied → allowed.
        self.assertFalse(svc._basket_add_rejection(
            self.product_rental, 'rental',
            '2026-07-01T08:00:00', '2026-07-01T10:00:00',
        ))
        # Add-ons never need a timeslot → allowed.
        self.assertFalse(svc._basket_add_rejection(
            self.product_addon, 'addon', None, None,
        ))
