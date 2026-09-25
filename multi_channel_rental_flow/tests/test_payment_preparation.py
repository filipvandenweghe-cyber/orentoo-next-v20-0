from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPaymentPreparation(TransactionCase):
    """Tests for the prepare-for-payment flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF PrepPay Customer',
            'email': 'preppay@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF PrepPay Pricelist',
            'company_id': cls.company.id,
        })

        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF PP Test Kayak',
            'type': 'consu',
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })
        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF PP Test Lunch',
            'type': 'consu',
            'list_price': 15.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })
        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF PP Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
        })

        # Storable rental product with stock
        cls.product_rental_storable = cls.env['product.product'].create({
            'name': 'MCRF PP Storable Kayak',
            'type': 'consu',
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'is_storable': True,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.product_rental_storable,
            cls.warehouse.lot_stock_id,
            10.0,
        )

        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        cls.svc = cls.env['multi.channel.rental.payment.prep']

    def _create_dossier(self, **kwargs):
        vals = {
            'partner_id': self.partner.id,
            'warehouse_id': self.warehouse.id,
            'pricelist_id': self.pricelist.id,
            'source': 'backend',
        }
        vals.update(kwargs)
        return self.env['rental.dossier'].create(vals)

    def _add_slot(self, dossier, start_hour=9, end_hour=11):
        return self.env['rental.dossier.slot'].create({
            'dossier_id': dossier.id,
            'start_datetime': self.tomorrow.replace(hour=start_hour),
            'end_datetime': self.tomorrow.replace(hour=end_hour),
            'warehouse_id': dossier.warehouse_id.id,
        })

    def _add_item(self, dossier, product=None, slot=None,
                   item_role='rental', quantity=1, price_unit=50.0):
        return self.env['rental.dossier.item'].create({
            'dossier_id': dossier.id,
            'slot_id': slot.id if slot else False,
            'product_id': (product or self.product_rental).id,
            'item_role': item_role,
            'quantity': quantity,
            'price_unit': price_unit,
        })

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_01_prepare_basic(self):
        """Basic prepare-for-payment: generates, confirms, sets state."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot, quantity=2)

        self.svc._prepare_for_payment(dossier)

        # Dossier is payment_pending
        self.assertEqual(dossier.state, 'payment_pending')

        # Order is confirmed (sale state)
        order = slot.sale_order_id
        self.assertTrue(order)
        self.assertEqual(order.state, 'sale')
        self.assertTrue(order.is_rental_order)

    def test_02_items_and_slots_confirmed(self):
        """Items and slots move to confirmed state."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, product=self.product_rental_storable,
                               slot=slot)

        self.svc._prepare_for_payment(dossier)

        self.assertEqual(item.state, 'confirmed')
        self.assertEqual(slot.state, 'confirmed')
        self.assertEqual(item.availability_state, 'available')

    def test_03_multiple_slots(self):
        """Multiple slots: all orders confirmed."""
        dossier = self._create_dossier()
        slot1 = self._add_slot(dossier, start_hour=9, end_hour=11)
        slot2 = self._add_slot(dossier, start_hour=14, end_hour=16)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot1)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot2)

        self.svc._prepare_for_payment(dossier)

        self.assertEqual(dossier.state, 'payment_pending')
        self.assertEqual(slot1.sale_order_id.state, 'sale')
        self.assertEqual(slot2.sale_order_id.state, 'sale')

    def test_04_service_items_no_stock_check(self):
        """Service/addon items pass without stock."""
        dossier = self._create_dossier()
        self._add_item(
            dossier, product=self.product_service,
            item_role='service', quantity=1, price_unit=40.0,
        )

        self.svc._prepare_for_payment(dossier)

        self.assertEqual(dossier.state, 'payment_pending')

    def test_05_mixed_rental_and_addon(self):
        """Rental + non-slot addon: both orders confirmed."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot, quantity=1)
        self._add_item(
            dossier, product=self.product_addon,
            item_role='addon', quantity=2, price_unit=15.0,
        )

        self.svc._prepare_for_payment(dossier)

        self.assertEqual(dossier.state, 'payment_pending')
        # Two orders: rental + non-slot
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', dossier.id),
        ])
        self.assertEqual(len(generated), 2)
        self.assertTrue(all(o.state == 'sale' for o in generated))

    # ------------------------------------------------------------------
    # Soft availability warnings + hard confirmation gate
    # ------------------------------------------------------------------

    def test_10_soft_warning_does_not_block(self):
        """Forecast warning does not block if confirmation succeeds.

        Even if the forecast looks tight, the hard gate is stock
        move reservation during order confirmation.
        """
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        # Request exactly what's available (10) — forecast may show
        # slightly less due to inaccuracies, but confirmation should
        # succeed because actual stock is sufficient.
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot, quantity=10)

        self.svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')

    def test_11_rental_blocked_by_availability_check(self):
        """Rental with qty > stock is blocked by per-point availability
        check even though the hard gate (move state) is skipped."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier, start_hour=9, end_hour=11)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot, quantity=9999)  # more than 10 in stock

        with self.assertRaises(UserError):
            self.svc._prepare_for_payment(dossier)
        self.assertIn(dossier.state, ('draft', 'confirmed'))

    def test_11b_items_show_availability_state(self):
        """Items get availability_state set by soft check for display."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, product=self.product_rental_storable,
                               slot=slot, quantity=1)

        self.svc._prepare_for_payment(dossier)
        # After successful confirmation, item is marked available
        self.assertEqual(item.availability_state, 'available')

    def test_12_no_orders_without_items(self):
        """Fails gracefully if dossier has no items."""
        dossier = self._create_dossier()

        with self.assertRaises(UserError, msg="No orders to process"):
            self.svc._prepare_for_payment(dossier)

    # ------------------------------------------------------------------
    # Validation failures
    # ------------------------------------------------------------------

    def test_20_fails_without_partner(self):
        """Fails if no customer set."""
        dossier = self._create_dossier(partner_id=False)
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot)

        with self.assertRaises(UserError, msg="Customer is required"):
            self.svc._prepare_for_payment(dossier)

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def test_30_idempotent_call(self):
        """Calling prepare_for_payment twice is safe."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot)

        self.svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')
        order = slot.sale_order_id

        # Call again — should not raise or create duplicates
        self.svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')
        self.assertEqual(slot.sale_order_id, order)

    # ------------------------------------------------------------------
    # State guards
    # ------------------------------------------------------------------

    def test_40_rejects_paid_dossier(self):
        """Cannot prepare a paid dossier."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot)
        self.svc._prepare_for_payment(dossier)
        dossier.action_mark_paid()

        with self.assertRaises(UserError, msg="already paid"):
            self.svc._prepare_for_payment(dossier)

    def test_41_rejects_cancelled_dossier(self):
        """Cannot prepare a cancelled dossier."""
        dossier = self._create_dossier()
        dossier.action_cancel()

        with self.assertRaises(UserError, msg="cancelled"):
            self.svc._prepare_for_payment(dossier)

    # ------------------------------------------------------------------
    # Dossier action button
    # ------------------------------------------------------------------

    def test_50_action_prepare_for_payment(self):
        """Dossier action button calls the service."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot)

        dossier.action_prepare_for_payment()

        self.assertEqual(dossier.state, 'payment_pending')
        self.assertEqual(slot.sale_order_id.state, 'sale')

    # ------------------------------------------------------------------
    # Generated orders cancelled on dossier cancel
    # ------------------------------------------------------------------

    def test_60_cancel_after_prepare(self):
        """Cancelling after prepare cancels confirmed orders."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, product=self.product_rental_storable,
                        slot=slot)

        dossier.action_prepare_for_payment()
        order = slot.sale_order_id
        self.assertEqual(order.state, 'sale')

        dossier.action_cancel()
        self.assertEqual(dossier.state, 'cancelled')
        self.assertEqual(order.state, 'cancel')

    # ------------------------------------------------------------------
    # Rollback scope — only generated orders  [PP07]
    # ------------------------------------------------------------------

    def test_70_rollback_only_touches_generated_orders(self):
        """Rollback does not cancel manually linked orders.

        Uses a non-rental storable product (not rent_periodicity) so the hard
        gate applies — rental orders skip reservation verification.
        """
        # Non-rental storable product for hard gate testing
        product_nonrental = self.env['product.product'].create({
            'name': 'MCRF PP Non-Rental Storable',
            'type': 'consu',
            'is_storable': True,
            'list_price': 20.0,
            'sale_ok': True,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })
        self.env['stock.quant']._update_available_quantity(
            product_nonrental, self.warehouse.lot_stock_id, 5.0,
        )

        dossier = self._create_dossier()
        manual_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        dossier.sale_order_ids = [(4, manual_order.id)]

        # Non-slot addon with qty > stock → hard gate will catch it
        self._add_item(dossier, product=product_nonrental,
                        item_role='addon', quantity=9999, price_unit=20.0)

        with self.assertRaises(UserError):
            self.svc._prepare_for_payment(dossier)

        # Manual order is untouched
        self.assertEqual(manual_order.state, 'draft')

    def test_71_rollback_checks_mcrf_dossier_id(self):
        """Only orders with mcrf_dossier_id matching this dossier
        are rolled back — orders from other dossiers are safe."""
        dossier1 = self._create_dossier()
        dossier2 = self._create_dossier()

        # Dossier2 with service item succeeds
        self._add_item(dossier2, product=self.product_service,
                        item_role='service', quantity=1, price_unit=40.0)
        self.svc._prepare_for_payment(dossier2)
        self.assertEqual(dossier2.state, 'payment_pending')

        # Non-rental storable for hard gate
        product_nonrental = self.env['product.product'].create({
            'name': 'MCRF PP Non-Rental Storable 2',
            'type': 'consu',
            'is_storable': True,
            'list_price': 20.0,
            'sale_ok': True,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })
        self.env['stock.quant']._update_available_quantity(
            product_nonrental, self.warehouse.lot_stock_id, 5.0,
        )

        # Dossier1 with impossible qty of non-rental product fails
        self._add_item(dossier1, product=product_nonrental,
                        item_role='addon', quantity=9999, price_unit=20.0)

        with self.assertRaises(UserError):
            self.svc._prepare_for_payment(dossier1)

        # Dossier2 is untouched
        self.assertEqual(dossier2.state, 'payment_pending')

    # ------------------------------------------------------------------
    # Soft forecast does not block  [PP04]
    # ------------------------------------------------------------------

    def test_80_rental_reservation_succeeds(self):
        """Rental orders skip hard gate — partial reservation is OK."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, product=self.product_rental_storable,
                               slot=slot, quantity=10)

        self.svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')
        self.assertEqual(item.availability_state, 'available')

    # ------------------------------------------------------------------
    # Hard gate: non-rental reservation verification  [PP06]
    # ------------------------------------------------------------------

    def test_81_non_rental_partial_reservation_triggers_rollback(self):
        """Non-rental storable product: partial reservation is rolled back."""
        product_nonrental = self.env['product.product'].create({
            'name': 'MCRF PP Hard Gate Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'sale_ok': True,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })
        self.env['stock.quant']._update_available_quantity(
            product_nonrental, self.warehouse.lot_stock_id, 5.0,
        )

        dossier = self._create_dossier()
        self._add_item(dossier, product=product_nonrental,
                        item_role='addon', quantity=9999, price_unit=10.0)

        with self.assertRaises(UserError, msg="could be reserved"):
            self.svc._prepare_for_payment(dossier)

        self.assertIn(dossier.state, ('draft', 'confirmed'))

    def test_82_service_items_bypass_reservation_check(self):
        """Service items have no pickings — verification skips them."""
        dossier = self._create_dossier()
        self._add_item(
            dossier, product=self.product_service,
            item_role='service', quantity=9999, price_unit=40.0,
        )

        # Should succeed — services have no stock moves to verify
        self.svc._prepare_for_payment(dossier)
        self.assertEqual(dossier.state, 'payment_pending')
