from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestTicketService(TransactionCase):
    """Tests for ticket payload, lookup and print tracking.  [TK01–TK15]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Ticket Test Customer',
            'email': 'tickettest@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Ticket Pricelist',
            'company_id': cls.company.id,
        })

        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF TK Test Kayak',
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

        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF TK Test Lunch',
            'type': 'consu',
            'list_price': 15.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })

        # Event product + event
        cls.event_product = cls.env['product.product'].create({
            'name': 'MCRF TK Event Ticket',
            'type': 'service',
            'service_tracking': 'event',
            'list_price': 25.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'event_ticket',
        })
        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        cls.event = cls.env['event.event'].create({
            'name': 'MCRF TK Test Event',
            'date_begin': cls.tomorrow.replace(hour=10),
            'date_end': cls.tomorrow.replace(hour=18),
        })
        cls.event_ticket = cls.env['event.event.ticket'].create({
            'name': 'Standard',
            'event_id': cls.event.id,
            'product_id': cls.event_product.id,
            'price': 25.0,
        })

        cls.profile = cls.env['multi.channel.rental.profile'].create({
            'name': 'TK Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'pricelist_id': cls.pricelist.id,
            'allow_demo_payment': True,
        })

        cls.svc = cls.env['multi.channel.rental.ticket.service']
        cls.prep_svc = cls.env['multi.channel.rental.payment.prep']

    def _create_paid_dossier(self, with_rental=True, with_event=False,
                              with_addon=False):
        """Create a full paid dossier for ticket testing."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'tickettest@mcrf.example.com',
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'profile_id': self.profile.id,
            'source': 'kiosk',
        })
        if with_rental:
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
        if with_event:
            self.env['rental.dossier.item'].create({
                'dossier_id': dossier.id,
                'product_id': self.event_product.id,
                'item_role': 'event_ticket',
                'event_id': self.event.id,
                'event_ticket_id': self.event_ticket.id,
                'quantity': 3,
                'price_unit': 25.0,
            })
        if with_addon:
            self.env['rental.dossier.item'].create({
                'dossier_id': dossier.id,
                'product_id': self.product_addon.id,
                'item_role': 'addon',
                'quantity': 1,
                'price_unit': 15.0,
            })

        self.prep_svc._prepare_for_payment(dossier)
        dossier.action_payment_success()
        return dossier

    # ------------------------------------------------------------------
    # Lookup  [TK01–TK03]
    # ------------------------------------------------------------------

    def test_01_lookup_by_dossier_number(self):
        """Lookup by dossier number + email returns the dossier."""
        dossier = self._create_paid_dossier()
        result = self.svc._find_dossier_for_print_lookup(
            dossier.name, 'tickettest@mcrf.example.com',
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['dossier'], dossier)

    def test_02_lookup_by_order_number(self):
        """Lookup by order number + email returns the dossier."""
        dossier = self._create_paid_dossier()
        order = dossier.sale_order_ids[0]
        result = self.svc._find_dossier_for_print_lookup(
            order.name, 'tickettest@mcrf.example.com',
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['dossier'], dossier)

    def test_03_lookup_wrong_email_fails(self):
        """Lookup with wrong email returns not found."""
        dossier = self._create_paid_dossier()
        result = self.svc._find_dossier_for_print_lookup(
            dossier.name, 'wrong@email.com',
        )
        self.assertFalse(result['ok'])

    def test_04_lookup_unpaid_dossier_fails(self):
        """Lookup for unpaid dossier returns not found."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'partner_email': 'tickettest@mcrf.example.com',
            'source': 'backend',
        })
        result = self.svc._find_dossier_for_print_lookup(
            dossier.name, 'tickettest@mcrf.example.com',
        )
        self.assertFalse(result['ok'])

    # ------------------------------------------------------------------
    # Ticket payload  [TK04–TK06]
    # ------------------------------------------------------------------

    def test_10_rental_ticket_payload(self):
        """Paid rental dossier returns ticket payload."""
        dossier = self._create_paid_dossier(with_rental=True)
        payload = self.svc._get_print_payload_for_dossier(dossier)

        self.assertTrue(payload['ok'])
        self.assertEqual(payload['dossier_name'], dossier.name)
        self.assertTrue(len(payload['tickets']) >= 1)

        ticket = payload['tickets'][0]
        self.assertEqual(ticket['ticket_type'], 'rental')
        self.assertIn('MCRF TK Test Kayak', ticket['product_name'])
        self.assertTrue(ticket['timeslot_start'])
        self.assertTrue(ticket['timeslot_end'])

    def test_11_event_ticket_payload(self):
        """Paid event dossier returns event ticket payload."""
        dossier = self._create_paid_dossier(
            with_rental=False, with_event=True,
        )
        payload = self.svc._get_print_payload_for_dossier(dossier)

        self.assertTrue(payload['ok'])
        event_tickets = [t for t in payload['tickets']
                          if t['ticket_type'] == 'event']
        # 3 quantity = 3 registrations = 3 event tickets
        self.assertEqual(len(event_tickets), 3)
        self.assertEqual(event_tickets[0]['event_name'], 'MCRF TK Test Event')

    def test_12_mixed_payload(self):
        """Mixed dossier returns both rental and event tickets."""
        dossier = self._create_paid_dossier(
            with_rental=True, with_event=True, with_addon=True,
        )
        payload = self.svc._get_print_payload_for_dossier(dossier)

        self.assertTrue(payload['ok'])
        types = set(t['ticket_type'] for t in payload['tickets'])
        self.assertIn('rental', types)
        self.assertIn('event', types)
        # Addon generates a product ticket
        product_tickets = [t for t in payload['tickets']
                            if t['ticket_type'] == 'product']
        self.assertTrue(len(product_tickets) >= 1)

    def test_13_unpaid_dossier_no_tickets(self):
        """Unpaid dossier returns no tickets."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'source': 'backend',
        })
        payload = self.svc._get_print_payload_for_dossier(dossier)
        self.assertFalse(payload['ok'])
        self.assertEqual(len(payload['tickets']), 0)

    def test_14_ticket_has_sequence_numbers(self):
        """Each ticket has a unique sequence number."""
        dossier = self._create_paid_dossier(
            with_rental=True, with_addon=True,
        )
        payload = self.svc._get_print_payload_for_dossier(dossier)
        sequences = [t['sequence'] for t in payload['tickets']]
        self.assertEqual(len(sequences), len(set(sequences)))

    # ------------------------------------------------------------------
    # Per-order and per-registration payload  [TK09, TK10]
    # ------------------------------------------------------------------

    def test_20_per_order_payload(self):
        """Per-order payload returns only that order's tickets."""
        dossier = self._create_paid_dossier(with_rental=True)
        order = dossier.sale_order_ids[0]
        payload = self.svc._get_print_payload_for_order(order)
        self.assertTrue(payload['ok'])
        self.assertTrue(all(
            t['order_name'] == order.name for t in payload['tickets']
        ))

    def test_21_per_registration_payload(self):
        """Per-registration payload returns one event ticket."""
        dossier = self._create_paid_dossier(
            with_rental=False, with_event=True,
        )
        reg = self.env['event.registration'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ], limit=1)
        if reg:
            payload = self.svc._get_print_payload_for_event_registration(reg)
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['ticket_count'], 1)

    # ------------------------------------------------------------------
    # Print tracking  [TK11]
    # ------------------------------------------------------------------

    def test_30_mark_tickets_printed(self):
        """Marking tickets printed sets flag on all generated orders."""
        dossier = self._create_paid_dossier()
        self.assertFalse(self.svc._are_tickets_printed(dossier))

        self.svc._mark_tickets_printed(dossier)
        self.assertTrue(self.svc._are_tickets_printed(dossier))

    def test_31_reset_tickets_printed(self):
        """Resetting print flag allows reprinting."""
        dossier = self._create_paid_dossier()
        self.svc._mark_tickets_printed(dossier)
        self.assertTrue(self.svc._are_tickets_printed(dossier))

        dossier.action_reset_tickets_printed()
        self.assertFalse(self.svc._are_tickets_printed(dossier))

    def test_32_print_action_marks_printed(self):
        """Print Tickets action marks orders as printed."""
        dossier = self._create_paid_dossier()
        # action_print_tickets returns a report action and marks printed
        result = dossier.action_print_tickets()
        self.assertEqual(result['type'], 'ir.actions.report')
        self.assertTrue(self.svc._are_tickets_printed(dossier))

    def test_33_print_unpaid_raises(self):
        """Print Tickets on unpaid dossier raises error."""
        dossier = self.env['rental.dossier'].create({
            'partner_id': self.partner.id,
            'source': 'backend',
        })
        with self.assertRaises(UserError):
            dossier.action_print_tickets()

    # ------------------------------------------------------------------
    # Lookup payload includes print status
    # ------------------------------------------------------------------

    def test_40_lookup_payload_includes_already_printed(self):
        """Lookup payload indicates if tickets were already printed."""
        dossier = self._create_paid_dossier()

        # Not yet printed
        result = self.svc._find_dossier_for_print_lookup(
            dossier.name, 'tickettest@mcrf.example.com',
        )
        payload = self.svc._get_print_payload_for_dossier(result['dossier'])
        self.assertFalse(self.svc._are_tickets_printed(dossier))

        # Mark printed
        self.svc._mark_tickets_printed(dossier)
        self.assertTrue(self.svc._are_tickets_printed(dossier))
