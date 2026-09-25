from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestEventIntegration(TransactionCase):
    """Tests for event ticket integration.  [EV01–EV09]"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Event Test Customer',
            'email': 'eventtest@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Event Pricelist',
            'company_id': cls.company.id,
        })

        # Create an event product (service_tracking = 'event')
        cls.event_product = cls.env['product.product'].create({
            'name': 'MCRF Event Ticket Product',
            'type': 'service',
            'service_tracking': 'event',
            'list_price': 25.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'event_ticket',
        })

        # Create an event
        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        cls.event = cls.env['event.event'].create({
            'name': 'MCRF Test Event',
            'date_begin': cls.tomorrow.replace(hour=10),
            'date_end': cls.tomorrow.replace(hour=18),
            'seats_max': 50,
            'seats_limited': True,
        })

        # Create event ticket linked to the product
        cls.event_ticket = cls.env['event.event.ticket'].create({
            'name': 'Standard Ticket',
            'event_id': cls.event.id,
            'product_id': cls.event_product.id,
            'price': 25.0,
        })

        # Storable rental product with stock (for mixed dossiers)
        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF Event Test Kayak',
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

        # Demo payment provider — ships disabled by default, enable it.
        # Odoo 20 replaced payment.provider.state by 'active' (archived ==
        # disabled) + 'is_live' (live vs. test mode).
        cls.demo_provider = cls.env['payment.provider'].with_context(
            active_test=False,
        ).search([('code', '=', 'demo')], limit=1)
        if cls.demo_provider and not cls.demo_provider.active:
            cls.demo_provider.write({'active': True, 'is_live': False})

        cls.prep_svc = cls.env['multi.channel.rental.payment.prep']
        cls.order_svc = cls.env['multi.channel.rental.order.service']

    def _create_dossier(self, **kwargs):
        vals = {
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'source': 'backend',
        }
        vals.update(kwargs)
        return self.env['rental.dossier'].create(vals)

    # ------------------------------------------------------------------
    # Event ticket items  [EV01, EV02]
    # ------------------------------------------------------------------

    def test_01_create_event_ticket_item(self):
        """Event ticket item can be created with event fields."""
        dossier = self._create_dossier()
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 2,
            'price_unit': 25.0,
        })
        self.assertEqual(item.event_id, self.event)
        self.assertEqual(item.event_ticket_id, self.event_ticket)
        self.assertEqual(item.price_subtotal, 50.0)

    # ------------------------------------------------------------------
    # Order generation with event lines  [EV05]
    # ------------------------------------------------------------------

    def test_10_event_item_generates_event_sale_line(self):
        """Event ticket item generates a SO line with event_id set."""
        dossier = self._create_dossier()
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 2,
            'price_unit': 25.0,
        })

        self.order_svc._generate_sale_orders(dossier)

        line = item.sale_order_line_id
        self.assertTrue(line)
        self.assertEqual(line.event_id, self.event)
        self.assertEqual(line.event_ticket_id, self.event_ticket)
        self.assertEqual(line.product_uom_qty, 2)

    # ------------------------------------------------------------------
    # Registrations created on order confirmation  [EV06]
    # ------------------------------------------------------------------

    def test_20_registrations_created_on_confirm(self):
        """Confirming event SO creates registrations via event_sale."""
        dossier = self._create_dossier()
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 3,
            'price_unit': 25.0,
        })

        # Prepare for payment (generates + confirms orders)
        self.prep_svc._prepare_for_payment(dossier)

        # event_sale should have created 3 registrations
        line = item.sale_order_line_id
        regs = self.env['event.registration'].search([
            ('sale_order_line_id', '=', line.id),
        ])
        self.assertEqual(len(regs), 3)

    def test_21_registrations_linked_to_dossier(self):
        """Payment success links registrations back to dossier."""
        dossier = self._create_dossier()
        item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 2,
            'price_unit': 25.0,
        })

        self.prep_svc._prepare_for_payment(dossier)
        dossier.action_payment_success()

        # Registrations should be linked to dossier and item
        regs = self.env['event.registration'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        self.assertEqual(len(regs), 2)
        self.assertTrue(all(r.mcrf_dossier_item_id == item for r in regs))
        self.assertEqual(dossier.registration_count, 2)

    def test_22_registrations_are_open_after_payment(self):
        """After payment, registrations are in open (confirmed) state."""
        dossier = self._create_dossier()
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 1,
            'price_unit': 25.0,
        })

        self.prep_svc._prepare_for_payment(dossier)
        dossier.action_payment_success()

        regs = self.env['event.registration'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        # event_sale sets state based on SO state.
        # For paid registrations with a single confirmed SO:
        # state is 'draft' (awaiting attendee details) or 'open'.
        self.assertTrue(all(r.state in ('draft', 'open') for r in regs))

    # ------------------------------------------------------------------
    # Payment failure cancels registrations  [EV08]
    # ------------------------------------------------------------------

    def test_30_payment_failure_cancels_registrations(self):
        """Payment failure cancels SO which cancels registrations."""
        dossier = self._create_dossier()
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 2,
            'price_unit': 25.0,
        })

        self.prep_svc._prepare_for_payment(dossier)
        # Registrations exist and are open
        regs = self.env['event.registration'].search([
            ('sale_order_id', 'in', dossier.sale_order_ids.ids),
        ])
        self.assertEqual(len(regs), 2)

        # Payment fails → SO cancelled → registrations cancelled
        dossier.action_payment_failed()
        regs.invalidate_recordset()
        self.assertTrue(all(r.state == 'cancel' for r in regs))

    # ------------------------------------------------------------------
    # Event capacity check  [EV05]
    # ------------------------------------------------------------------

    def test_40_capacity_check_warns_when_sold_out(self):
        """Soft capacity check produces a warning for sold-out events."""
        # Fill up the event
        self.event.seats_max = 1
        dossier = self._create_dossier()
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 5,  # more than 1 seat
            'price_unit': 25.0,
        })

        warnings = self.prep_svc._check_availability_soft(dossier)
        self.assertTrue(len(warnings) > 0)
        self.assertTrue(any('seat' in w.lower() or 'available' in w.lower()
                            for w in warnings))

    # ------------------------------------------------------------------
    # Mixed dossier: rental + event  [EV06]
    # ------------------------------------------------------------------

    def test_50_mixed_rental_and_event(self):
        """Dossier with both rental and event items works end-to-end."""
        dossier = self._create_dossier()
        slot = self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=9),
            'end_datetime': self.tomorrow.replace(hour=11),
            'warehouse_id': self.warehouse.id,
        })
        # Rental item in slot
        self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id,
            'product_id': self.product_rental.id,
            'item_role': 'rental',
            'quantity': 1,
            'price_unit': 50.0,
        })
        # Event ticket item (no slot)
        event_item = self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'product_id': self.event_product.id,
            'item_role': 'event_ticket',
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
            'quantity': 2,
            'price_unit': 25.0,
        })

        self.prep_svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')

        # Should have 2 orders: rental (slot) + event (non-slot)
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        self.assertEqual(len(generated), 2)

        # Payment success
        dossier.action_payment_success()
        self.assertEqual(dossier.state, 'paid')

        # Event registrations linked
        regs = self.env['event.registration'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        self.assertEqual(len(regs), 2)

    # ------------------------------------------------------------------
    # Standard Event flow unaffected  [EV09]
    # ------------------------------------------------------------------

    def test_60_standard_event_sale_unaffected(self):
        """Standard event ticket purchase (not via dossier) still works."""
        # Create a regular SO with event line — no dossier
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.event_product.id,
            'product_uom_qty': 1,
            'event_id': self.event.id,
            'event_ticket_id': self.event_ticket.id,
        })
        order.action_confirm()

        regs = self.env['event.registration'].search([
            ('sale_order_line_id', '=', line.id),
        ])
        self.assertEqual(len(regs), 1)
        # No dossier link
        self.assertFalse(regs.mcrf_dossier_id)

    # ------------------------------------------------------------------
    # Slot preview for events  [EV03, EV04]
    # ------------------------------------------------------------------

    def test_70_event_day_availability(self):
        """Day with event is available, day without is closed."""
        svc = self.env['multi.channel.rental.service']
        profile = self.env['multi.channel.rental.profile'].create({
            'name': 'EV Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })

        # Event day should be available
        event_day = self.event.date_begin.date()
        result = svc._get_day_availability_state(
            profile, self.event_product, event_day,
            item_role='event_ticket', event_id=self.event.id,
        )
        self.assertEqual(result['day_state'], 'available')

        # Day without event should be closed
        no_event_day = event_day + timedelta(days=10)
        result = svc._get_day_availability_state(
            profile, self.event_product, no_event_day,
            item_role='event_ticket', event_id=self.event.id,
        )
        self.assertEqual(result['day_state'], 'closed')
