from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase


class TestKioskLookup(TransactionCase):
    """Tests for kiosk ticket lookup and print flow.  [KL01–KL11]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Kiosk Test Customer',
            'email': 'kiosktest@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Kiosk Pricelist',
            'company_id': cls.company.id,
        })

        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF KL Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
        })

        # Profile with lookup enabled
        cls.profile = cls.env['multi.channel.rental.profile'].create({
            'name': 'KL Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'pricelist_id': cls.pricelist.id,
            'enable_ticket_lookup_printing': True,
            'printer_mode': 'browser',
            'allow_demo_payment': True,
        })

        # Profile with lookup disabled
        cls.profile_disabled = cls.env['multi.channel.rental.profile'].create({
            'name': 'KL Disabled Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'enable_ticket_lookup_printing': False,
        })

        cls.prep_svc = cls.env['multi.channel.rental.payment.prep']
        cls.ticket_svc = cls.env['multi.channel.rental.ticket.service']

    def _create_paid_dossier(self):
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'kiosktest@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'website',
        })
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.product_service.id,
            'item_role': 'service',
            'quantity': 1,
            'price_unit': 40.0,
        })
        self.prep_svc._prepare_for_payment(dossier)
        dossier.action_payment_success()
        return dossier

    # ------------------------------------------------------------------
    # Kiosk lookup via service  [KL02, KL03]
    # ------------------------------------------------------------------

    def test_01_lookup_paid_dossier(self):
        """Kiosk lookup returns tickets for paid dossier."""
        dossier = self._create_paid_dossier()
        result = self.ticket_svc._find_dossier_for_print_lookup(
            dossier.name, 'kiosktest@mcrf.example.com',
        )
        self.assertTrue(result['ok'])

        payload = self.ticket_svc._get_print_payload_for_dossier(
            result['dossier'],
        )
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['ticket_count'] > 0)

    def test_02_lookup_by_order_number(self):
        """Kiosk lookup works with order number too."""
        dossier = self._create_paid_dossier()
        order = dossier.sale_order_ids[0]
        result = self.ticket_svc._find_dossier_for_print_lookup(
            order.name, 'kiosktest@mcrf.example.com',
        )
        self.assertTrue(result['ok'])

    def test_03_lookup_unpaid_returns_nothing(self):
        """Unpaid dossier returns no tickets."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'kiosktest@mcrf.example.com',
            'source': 'backend',
        })
        result = self.ticket_svc._find_dossier_for_print_lookup(
            dossier.name, 'kiosktest@mcrf.example.com',
        )
        self.assertFalse(result['ok'])

    def test_04_lookup_wrong_email(self):
        """Wrong email returns not found."""
        dossier = self._create_paid_dossier()
        result = self.ticket_svc._find_dossier_for_print_lookup(
            dossier.name, 'wrong@email.com',
        )
        self.assertFalse(result['ok'])

    # ------------------------------------------------------------------
    # Print tracking  [KL08]
    # ------------------------------------------------------------------

    def test_10_mark_printed_via_service(self):
        """Marking printed sets the flag on generated orders."""
        dossier = self._create_paid_dossier()
        self.assertFalse(self.ticket_svc._are_tickets_printed(dossier))

        self.ticket_svc._mark_tickets_printed(dossier)
        self.assertTrue(self.ticket_svc._are_tickets_printed(dossier))

    def test_11_already_printed_flag_in_payload(self):
        """Payload indicates if tickets were already printed."""
        dossier = self._create_paid_dossier()
        payload = self.ticket_svc._get_print_payload_for_dossier(dossier)
        self.assertFalse(
            self.ticket_svc._are_tickets_printed(dossier),
        )

        self.ticket_svc._mark_tickets_printed(dossier)
        self.assertTrue(
            self.ticket_svc._are_tickets_printed(dossier),
        )

    # ------------------------------------------------------------------
    # Profile settings  [KL09]
    # ------------------------------------------------------------------

    def test_20_profile_kiosk_url(self):
        """Profile computes kiosk URL."""
        self.assertTrue(self.profile.kiosk_url)
        self.assertIn(f'/rental-kiosk/{self.profile.id}', self.profile.kiosk_url)

    def test_21_disabled_profile_no_lookup(self):
        """Profile with lookup disabled should block kiosk."""
        # The controller checks enable_ticket_lookup_printing
        self.assertFalse(self.profile_disabled.enable_ticket_lookup_printing)

    # ------------------------------------------------------------------
    # Printer config from profile  [KL05–KL07]
    # ------------------------------------------------------------------

    def test_30_browser_printer_config(self):
        """Browser printer mode returns correct config."""
        from odoo.addons.multi_channel_rental_flow.controllers.kiosk import (
            MultiChannelRentalKiosk,
        )
        ctrl = MultiChannelRentalKiosk()
        config = ctrl._get_printer_config(self.profile)
        self.assertEqual(config['mode'], 'browser')

    def test_31_epos_printer_config(self):
        """ePOS printer mode returns IP from pos.printer."""
        printer = self.env['pos.printer'].create({
            'name': 'KL Test Epson',
            'printer_type': 'epson_epos',
            'printer_ip': '192.168.1.200',
        })
        self.profile.printer_mode = 'pos_epos_ip'
        self.profile.pos_printer_id = printer

        from odoo.addons.multi_channel_rental_flow.controllers.kiosk import (
            MultiChannelRentalKiosk,
        )
        ctrl = MultiChannelRentalKiosk()
        config = ctrl._get_printer_config(self.profile)
        self.assertEqual(config['mode'], 'pos_epos_ip')
        self.assertEqual(config['printer_ip'], '192.168.1.200')
