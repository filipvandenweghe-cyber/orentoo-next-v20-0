from datetime import datetime, timedelta
from odoo.tests.common import TransactionCase


class TestOrderGeneration(TransactionCase):
    """Tests for sale/rental order generation from dossier slots."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF OrderGen Customer',
            'email': 'ordergen@mcrf.example.com',
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF OrderGen Pricelist',
            'company_id': cls.company.id,
        })

        # Rental product (rent_periodicity must be True for is_rental to work)
        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF OG Test Kayak',
            'type': 'consu',
            'list_price': 50.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })

        # Add-on product (not rentable)
        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF OG Test Lunch',
            'type': 'consu',
            'list_price': 15.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
        })

        # Service product
        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF OG Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
        })

        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

        cls.svc = cls.env['multi.channel.rental.order.service']

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
    # Single slot — basic generation
    # ------------------------------------------------------------------

    def test_01_single_slot_generates_one_order(self):
        """One rental slot → one draft sale/rental order."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        orders = self.svc._generate_sale_orders(dossier)

        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order.state, 'draft')
        self.assertTrue(order.is_rental_order)
        self.assertEqual(order.partner_id, self.partner)
        self.assertEqual(order.mcrf_dossier_id, dossier)
        self.assertEqual(order.mcrf_dossier_slot_id, slot)
        self.assertEqual(slot.sale_order_id, order)

    def test_02_rental_dates_on_order_level(self):
        """Generated rental order has start/end dates from the slot."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier, start_hour=10, end_hour=14)
        self._add_item(dossier, slot=slot)

        orders = self.svc._generate_sale_orders(dossier)
        order = orders[0]

        self.assertEqual(order.rental_start_date, slot.start_datetime)
        self.assertEqual(order.rental_return_date, slot.end_datetime)

    def test_03_order_line_linked_to_item(self):
        """Generated order line is linked back to the dossier item."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)

        self.assertTrue(item.sale_order_line_id)
        self.assertEqual(item.sale_order_line_id.mcrf_dossier_item_id, item)
        self.assertEqual(item.state, 'generated')

    def test_04_rental_line_is_rental(self):
        """Rental product line has is_rental=True."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)
        line = item.sale_order_line_id

        self.assertTrue(line.is_rental)
        self.assertEqual(line.product_id, self.product_rental)
        self.assertEqual(line.product_uom_qty, 1.0)

    # ------------------------------------------------------------------
    # Multiple slots
    # ------------------------------------------------------------------

    def test_10_multiple_slots_generate_multiple_orders(self):
        """Two rental slots → two separate draft orders."""
        dossier = self._create_dossier()
        slot1 = self._add_slot(dossier, start_hour=9, end_hour=11)
        slot2 = self._add_slot(dossier, start_hour=14, end_hour=16)
        self._add_item(dossier, slot=slot1)
        self._add_item(dossier, slot=slot2)

        orders = self.svc._generate_sale_orders(dossier)

        self.assertEqual(len(orders), 2)
        self.assertNotEqual(slot1.sale_order_id, slot2.sale_order_id)
        # Each order has its own rental dates
        self.assertEqual(
            slot1.sale_order_id.rental_start_date, slot1.start_datetime,
        )
        self.assertEqual(
            slot2.sale_order_id.rental_start_date, slot2.start_datetime,
        )

    def test_11_multiple_items_per_slot(self):
        """Multiple items in one slot → one order with multiple lines."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item1 = self._add_item(dossier, slot=slot, quantity=2)
        item2 = self._add_item(
            dossier, product=self.product_addon, slot=slot,
            item_role='addon', quantity=3, price_unit=15.0,
        )

        orders = self.svc._generate_sale_orders(dossier)

        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(len(order.order_line), 2)
        self.assertTrue(item1.sale_order_line_id)
        self.assertTrue(item2.sale_order_line_id)

    # ------------------------------------------------------------------
    # Non-slot items
    # ------------------------------------------------------------------

    def test_20_non_slot_items_separate_order(self):
        """Items without a slot generate a separate non-rental order."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)
        addon = self._add_item(
            dossier, product=self.product_addon,
            item_role='addon', quantity=2, price_unit=15.0,
        )  # no slot

        orders = self.svc._generate_sale_orders(dossier)

        self.assertEqual(len(orders), 2)
        # The non-slot order should NOT be a rental order
        non_slot_order = addon.sale_order_line_id.order_id
        self.assertFalse(non_slot_order.is_rental_order)
        self.assertFalse(non_slot_order.mcrf_dossier_slot_id)

    def test_21_only_non_slot_items(self):
        """Dossier with only non-slot items generates one non-rental order."""
        dossier = self._create_dossier()
        self._add_item(
            dossier, product=self.product_addon,
            item_role='addon', quantity=1, price_unit=15.0,
        )
        self._add_item(
            dossier, product=self.product_service,
            item_role='service', quantity=1, price_unit=40.0,
        )

        orders = self.svc._generate_sale_orders(dossier)

        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertFalse(order.is_rental_order)
        self.assertEqual(len(order.order_line), 2)

    # ------------------------------------------------------------------
    # Idempotency — no duplicates
    # ------------------------------------------------------------------

    def test_30_regeneration_does_not_duplicate(self):
        """Calling generate twice does not create duplicate orders/lines."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        orders1 = self.svc._generate_sale_orders(dossier)
        orders2 = self.svc._generate_sale_orders(dossier)

        # Same order reused
        self.assertEqual(orders1, orders2)
        self.assertEqual(len(slot.sale_order_id.order_line), 1)

    def test_31_new_item_added_after_generation(self):
        """Adding a new item after generation creates only the new line."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)
        self.assertEqual(len(slot.sale_order_id.order_line), 1)

        # Add a second item to the same slot
        self._add_item(
            dossier, product=self.product_addon, slot=slot,
            item_role='addon', quantity=1, price_unit=15.0,
        )
        self.svc._generate_sale_orders(dossier)
        self.assertEqual(len(slot.sale_order_id.order_line), 2)

    # ------------------------------------------------------------------
    # Dossier M2M link
    # ------------------------------------------------------------------

    def test_40_generated_orders_linked_to_dossier_m2m(self):
        """Generated orders appear in dossier.sale_order_ids."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)

        self.assertIn(slot.sale_order_id, dossier.sale_order_ids)
        self.assertEqual(dossier.sale_order_count, 1)

    def test_41_manual_orders_preserved(self):
        """Manually linked orders are not removed by generation."""
        dossier = self._create_dossier()
        manual_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        dossier.sale_order_ids = [(4, manual_order.id)]

        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)
        self.svc._generate_sale_orders(dossier)

        # Both manual and generated orders present
        self.assertIn(manual_order, dossier.sale_order_ids)
        self.assertIn(slot.sale_order_id, dossier.sale_order_ids)
        self.assertEqual(dossier.sale_order_count, 2)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def test_50_sync_updates_quantity(self):
        """Sync updates generated line quantity from dossier item."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, slot=slot, quantity=2)

        self.svc._generate_sale_orders(dossier)
        line = item.sale_order_line_id
        self.assertEqual(line.product_uom_qty, 2.0)

        item.quantity = 5
        self.svc._sync_dossier_items_to_order_lines(dossier)
        self.assertEqual(line.product_uom_qty, 5.0)

    def test_51_sync_removes_cancelled_item_lines(self):
        """Sync removes lines for cancelled items."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        item = self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)
        line = item.sale_order_line_id
        self.assertTrue(line.exists())

        item.state = 'cancelled'
        self.svc._sync_dossier_items_to_order_lines(dossier)
        self.assertFalse(line.exists())

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def test_60_cancel_dossier_cancels_generated_orders(self):
        """Cancelling dossier cancels generated orders."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)
        order = slot.sale_order_id
        self.assertEqual(order.state, 'draft')

        dossier.action_cancel()
        self.assertEqual(order.state, 'cancel')

    def test_61_cancel_preserves_manual_orders(self):
        """Cancelling dossier does not cancel manually linked orders."""
        dossier = self._create_dossier()
        manual_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        dossier.sale_order_ids = [(4, manual_order.id)]

        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)
        self.svc._generate_sale_orders(dossier)

        dossier.action_cancel()
        # Generated order cancelled
        self.assertEqual(slot.sale_order_id.state, 'cancel')
        # Manual order untouched
        self.assertEqual(manual_order.state, 'draft')

    # ------------------------------------------------------------------
    # Generate via dossier action button
    # ------------------------------------------------------------------

    def test_70_action_generate_orders(self):
        """Dossier action_generate_orders creates orders."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        dossier.action_generate_orders()

        self.assertTrue(slot.sale_order_id)
        self.assertEqual(slot.state, 'order_created')

    # ------------------------------------------------------------------
    # Orders are NOT confirmed
    # ------------------------------------------------------------------

    def test_80_generated_orders_stay_draft(self):
        """Generated orders remain in draft state."""
        dossier = self._create_dossier()
        slot = self._add_slot(dossier)
        self._add_item(dossier, slot=slot)

        self.svc._generate_sale_orders(dossier)

        for order in dossier.sale_order_ids:
            if order.mcrf_dossier_id:
                self.assertEqual(order.state, 'draft')
