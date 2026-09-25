from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase


class TestConfirmationEmail(TransactionCase):
    """Tests for confirmation email and lookup credentials.  [CE01–CE10]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Email Test Customer',
            'email': 'emailtest@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Email Pricelist',
            'company_id': cls.company.id,
        })
        cls.website = cls.env['website'].search([], limit=1)

        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF Email Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
        })

        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF Email Test Kayak',
            'type': 'consu',
            'is_storable': True,
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.product_rental,
            cls.warehouse.lot_stock_id,
            10.0,
        )

        cls.profile_web = cls.env['multi.channel.rental.profile'].create({
            'name': 'Email Test Website Profile',
            'profile_type': 'website',
            'warehouse_id': cls.warehouse.id,
            'website_id': cls.website.id,
            'allow_demo_payment': True,
        })
        cls.profile_kiosk = cls.env['multi.channel.rental.profile'].create({
            'name': 'Email Test Kiosk Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'allow_demo_payment': True,
        })

        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

    def _create_paid_dossier(self, source='website', profile=None):
        """Create a dossier, prepare, and simulate payment."""
        profile = profile or (
            self.profile_web if source == 'website' else self.profile_kiosk
        )
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'emailtest@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': profile.id,
            'source': source,
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_service.id,
            'item_role': 'service',
            'quantity': 1,
            'price_unit': 40.0,
        })
        dossier.action_prepare_for_payment()
        dossier.action_payment_success()
        return dossier

    # ------------------------------------------------------------------
    # Website sends email  [CE01]
    # ------------------------------------------------------------------

    def test_01_website_payment_sends_email(self):
        """Website dossier payment success sends confirmation email."""
        dossier = self._create_paid_dossier(source='website')
        self.assertTrue(dossier.confirmation_sent)
        self.assertTrue(dossier.confirmation_sent_at)

    def test_02_kiosk_payment_does_not_send_email(self):
        """Kiosk dossier payment success does not send email."""
        dossier = self._create_paid_dossier(source='kiosk')
        self.assertFalse(dossier.confirmation_sent)

    # ------------------------------------------------------------------
    # No duplicate emails  [CE06]
    # ------------------------------------------------------------------

    def test_10_no_duplicate_on_repeated_callback(self):
        """Calling action_payment_success twice does not resend."""
        dossier = self._create_paid_dossier(source='website')
        first_sent_at = dossier.confirmation_sent_at

        # Call again (simulates duplicate callback)
        dossier.action_payment_success()

        # Still sent only once
        self.assertEqual(dossier.confirmation_sent_at, first_sent_at)

    # ------------------------------------------------------------------
    # Manual resend  [CE07]
    # ------------------------------------------------------------------

    def test_20_manual_resend(self):
        """Backend user can manually resend confirmation email."""
        dossier = self._create_paid_dossier(source='website')
        self.assertTrue(dossier.confirmation_sent)

        # Count chatter messages before resend
        msg_count_before = len(dossier.message_ids)

        # Resend
        dossier.action_resend_confirmation_email()

        # Still marked as sent, and a new chatter message was posted
        self.assertTrue(dossier.confirmation_sent)
        self.assertGreater(len(dossier.message_ids), msg_count_before)

    def test_21_manual_resend_for_kiosk(self):
        """Backend user can manually send email for kiosk dossier."""
        dossier = self._create_paid_dossier(source='kiosk')
        self.assertFalse(dossier.confirmation_sent)

        dossier.action_resend_confirmation_email()
        self.assertTrue(dossier.confirmation_sent)

    # ------------------------------------------------------------------
    # Email content  [CE03–CE05]
    # ------------------------------------------------------------------

    def test_30_confirmation_body_includes_dossier_number(self):
        """Confirmation body includes dossier number."""
        dossier = self._create_paid_dossier(source='website')
        body = dossier._build_confirmation_body()
        self.assertIn(dossier.name, body)

    def test_31_confirmation_body_includes_order_refs(self):
        """Confirmation body includes linked order references."""
        dossier = self._create_paid_dossier(source='website')
        body = dossier._build_confirmation_body()
        for order in dossier.sale_order_ids:
            self.assertIn(order.name, body)

    def test_32_confirmation_body_includes_items(self):
        """Confirmation body includes item product names."""
        dossier = self._create_paid_dossier(source='website')
        body = dossier._build_confirmation_body()
        self.assertIn('MCRF Email Test Guide', body)

    def test_33_confirmation_body_includes_lookup_instructions(self):
        """Confirmation body includes kiosk lookup instructions."""
        dossier = self._create_paid_dossier(source='website')
        body = dossier._build_confirmation_body()
        self.assertIn(dossier.name, body)
        self.assertIn('emailtest@mcrf.example.com', body)
        self.assertIn('kiosk', body.lower())

    def test_34_confirmation_body_includes_total(self):
        """Confirmation body includes total amount."""
        dossier = self._create_paid_dossier(source='website')
        body = dossier._build_confirmation_body()
        self.assertIn(dossier.currency_id.name, body)

    def test_35_confirmation_body_with_rental_slot(self):
        """Confirmation body includes rental timeslot details."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'emailtest@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile_web.id,
            'source': 'website',
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
        dossier.action_prepare_for_payment()
        dossier.action_payment_success()

        body = dossier._build_confirmation_body()
        # Should contain the slot name (date/time range)
        self.assertIn(slot.name, body)

    # ------------------------------------------------------------------
    # Chatter logging
    # ------------------------------------------------------------------

    def test_40_chatter_logs_email_sent(self):
        """Chatter logs when confirmation email is sent."""
        dossier = self._create_paid_dossier(source='website')
        messages = dossier.message_ids.mapped('body')
        self.assertTrue(any(
            'confirmation email sent' in (m or '').lower()
            for m in messages
        ))
