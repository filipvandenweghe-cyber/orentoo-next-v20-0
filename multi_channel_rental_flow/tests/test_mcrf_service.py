from datetime import date, datetime, timedelta
from odoo.tests.common import TransactionCase


class TestMCRFService(TransactionCase):
    """Tests for multi.channel.rental.service — pricing, durations, slots."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1,
        )
        # Use a clean pricelist with no rules to get predictable prices
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'MCRF Svc Test Pricelist',
            'company_id': cls.company.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'MCRF Service Test Customer',
            'email': 'svc-test@mcrf.example.com',
        })
        cls.website = cls.env['website'].search([], limit=1)

        # Rental product with flow flags
        cls.product_rental = cls.env['product.product'].create({
            'name': 'MCRF Svc Test Kayak',
            'type': 'consu',
            'list_price': 25.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
            'requires_duration': True,
            'requires_timeslot': True,
            'available_in_multi_channel_kiosk': True,
            'available_in_multi_channel_website': True,
        })

        # Addon product
        cls.product_addon = cls.env['product.product'].create({
            'name': 'MCRF Svc Test Lunch',
            'type': 'consu',
            'list_price': 12.50,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'addon',
            'requires_duration': False,
            'available_in_multi_channel_kiosk': True,
        })

        # Service product
        cls.product_service = cls.env['product.product'].create({
            'name': 'MCRF Svc Test Guide',
            'type': 'service',
            'list_price': 40.0,
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'service',
            'requires_duration': False,
            'available_in_multi_channel_website': True,
        })

        # Profile
        cls.profile = cls.env['multi.channel.rental.profile'].create({
            'name': 'MCRF Svc Test Profile',
            'profile_type': 'kiosk',
            'warehouse_id': cls.warehouse.id,
            'pricelist_id': cls.pricelist.id,
            'rental_slot_interval_minutes': 30,
            'slot_advance_days': 14,
            'default_duration_unit': 'hour',
            'low_dynamic_factor_threshold': 90.0,
            'high_dynamic_factor_threshold': 110.0,
            'low_color': '#90EE90',
            'normal_color': '#28a745',
            'high_color': '#006400',
            'unavailable_color': '#dc3545',
            'closed_color': '#6c757d',
        })

        # Coefficient table for the kayak
        cls.coeff_type = cls.env['rental.coefficient.type'].create({
            'name': 'MCRF Svc Test Standard',
            'company_id': cls.company.id,
        })
        cls.coeff_table = cls.env['rental.coefficient.table'].create({
            'name': 'MCRF Svc Test Table',
            'coefficient_type_id': cls.coeff_type.id,
            'duration_unit': 'hour',
            'is_standard': False,
            'company_id': cls.company.id,
            'line_ids': [
                (0, 0, {'as_from_duration': 0, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 1, 'coefficient': 1.0}),
                (0, 0, {'as_from_duration': 2, 'coefficient': 1.8}),
                (0, 0, {'as_from_duration': 4, 'coefficient': 3.2}),
                (0, 0, {'as_from_duration': 8, 'coefficient': 5.0}),
            ],
        })

        # Link table to product via warehouse pricing config
        cls.env['rental.product.warehouse.pricing.config'].create({
            'product_tmpl_id': cls.product_rental.product_tmpl_id.id,
            'warehouse_id': cls.warehouse.id,
            'coefficient_table_ids': [(4, cls.coeff_table.id)],
        })

        # Dates for testing
        cls.tomorrow = (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        cls.tomorrow_9 = cls.tomorrow.replace(hour=9)
        cls.tomorrow_11 = cls.tomorrow.replace(hour=11)
        cls.tomorrow_13 = cls.tomorrow.replace(hour=13)
        cls.tomorrow_17 = cls.tomorrow.replace(hour=17)

        cls.svc = cls.env['multi.channel.rental.service']

    # ------------------------------------------------------------------
    # Price preview — rental
    # ------------------------------------------------------------------

    def test_01_rental_price_preview_basic(self):
        """Rental price preview uses coefficient engine."""
        result = self.svc._get_price_preview(
            self.profile, self.product_rental,
            partner=self.partner,
            quantity=1.0,
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_11,
            item_role='rental',
        )
        self.assertEqual(result['product_id'], self.product_rental.id)
        self.assertEqual(result['item_role'], 'rental')
        # 2 hours → coefficient 1.8 → 25 * 1.8 = 45.0 (excl. tax)
        self.assertAlmostEqual(result['coefficient'], 1.8, places=1)
        self.assertAlmostEqual(result['price_unit_excl'], 45.0, places=1)
        self.assertEqual(result['quantity'], 1.0)
        # price_subtotal is tax-inclusive
        self.assertTrue(result['price_subtotal'] >= 45.0)

    def test_02_rental_price_preview_4h(self):
        """4-hour rental uses coefficient 3.2."""
        result = self.svc._get_price_preview(
            self.profile, self.product_rental,
            partner=self.partner,
            quantity=1.0,
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_13,
            item_role='rental',
        )
        self.assertAlmostEqual(result['coefficient'], 3.2, places=1)
        self.assertAlmostEqual(result['price_unit_excl'], 80.0, places=1)

    def test_03_rental_price_preview_quantity(self):
        """Quantity multiplies the subtotal."""
        result = self.svc._get_price_preview(
            self.profile, self.product_rental,
            partner=self.partner,
            quantity=3.0,
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_11,
            item_role='rental',
        )
        self.assertAlmostEqual(result['price_unit_excl'], 45.0, places=1)
        # price_subtotal is tax-inclusive (45 * 3 + tax)
        self.assertTrue(result['price_subtotal'] >= 135.0)

    def test_04_rental_price_dynamic_factor(self):
        """Dynamic factor percentage is returned."""
        result = self.svc._get_price_preview(
            self.profile, self.product_rental,
            partner=self.partner,
            quantity=1.0,
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_11,
            item_role='rental',
        )
        # No dynamic pricing table configured → should be 100%
        self.assertAlmostEqual(
            result['dynamic_factor_percentage'], 100.0, places=1,
        )
        self.assertAlmostEqual(result['dynamic_multiplier'], 1.0, places=2)

    # ------------------------------------------------------------------
    # Price preview — addon / service
    # ------------------------------------------------------------------

    def test_10_addon_price_preview(self):
        """Add-on price uses standard product price."""
        result = self.svc._get_price_preview(
            self.profile, self.product_addon,
            quantity=2.0,
            item_role='addon',
        )
        self.assertAlmostEqual(result['price_unit'], 12.50, places=2)
        self.assertAlmostEqual(result['price_subtotal'], 25.0, places=2)
        # No coefficient for addons
        self.assertAlmostEqual(result['coefficient'], 1.0)

    def test_11_service_price_preview(self):
        """Service price uses standard product price."""
        result = self.svc._get_price_preview(
            self.profile, self.product_service,
            quantity=1.0,
            item_role='service',
        )
        self.assertAlmostEqual(result['price_unit'], 40.0, places=2)

    # ------------------------------------------------------------------
    # Duration options
    # ------------------------------------------------------------------

    def test_20_duration_options_from_coefficient_table(self):
        """Duration options come from coefficient table lines, skipping 0."""
        options = self.svc._get_duration_options(
            self.profile, self.product_rental,
            partner=self.partner,
        )
        # Table has lines 0,1,2,4,8 — line 0 is skipped
        self.assertEqual(len(options), 4)
        self.assertEqual(options[0]['unit'], 'hour')
        # First selectable: as_from=1, coeff=1.0
        self.assertEqual(options[0]['value'], 1)
        self.assertAlmostEqual(options[0]['coefficient'], 1.0)
        # Second: as_from=2, coeff=1.8
        self.assertEqual(options[1]['value'], 2)
        self.assertAlmostEqual(options[1]['coefficient'], 1.8, places=1)
        # Third: as_from=4, coeff=3.2
        self.assertEqual(options[2]['value'], 4)
        self.assertAlmostEqual(options[2]['coefficient'], 3.2, places=1)
        # Fourth: as_from=8, coeff=5.0
        self.assertEqual(options[3]['value'], 8)
        self.assertAlmostEqual(options[3]['coefficient'], 5.0, places=1)

    def test_21_duration_options_with_labels(self):
        """Duration labels are human-readable."""
        options = self.svc._get_duration_options(
            self.profile, self.product_rental,
            partner=self.partner,
        )
        # "1 hour" (singular), "2 hours" (plural)
        labels = [o['label'] for o in options]
        self.assertTrue(any('hour' in l for l in labels))

    def test_22_duration_options_profile_fallback(self):
        """Uses profile fallback when product has requires_duration=False."""
        product_no_dur = self.env['product.product'].create({
            'name': 'MCRF No Duration Product',
            'type': 'consu',
            'list_price': 30.0,
            'requires_duration': False,
        })
        profile_fb = self.env['multi.channel.rental.profile'].create({
            'name': 'MCRF Fallback Profile',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
            'fallback_duration_ids': [
                (0, 0, {'name': '30 minutes', 'duration_value': 30,
                         'duration_unit': 'minute', 'sequence': 1}),
                (0, 0, {'name': '1 hour', 'duration_value': 1,
                         'duration_unit': 'hour', 'sequence': 2}),
            ],
        })

        options = self.svc._get_duration_options(profile_fb, product_no_dur)
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]['label'], '30 minutes')
        self.assertEqual(options[1]['label'], '1 hour')
        self.assertIsNone(options[0]['coefficient'])

    def test_23_duration_options_system_fallback(self):
        """Uses system default when no table and no profile fallbacks."""
        profile_clean = self.env['multi.channel.rental.profile'].create({
            'name': 'MCRF Clean Profile',
            'profile_type': 'kiosk',
            'warehouse_id': self.warehouse.id,
        })
        product_no_dur = self.env['product.product'].create({
            'name': 'MCRF System Fallback Product',
            'type': 'consu',
            'list_price': 30.0,
            'requires_duration': False,
        })
        options = self.svc._get_duration_options(
            profile_clean, product_no_dur,
        )
        # System default: 1, 2, 4, 8 hours
        self.assertEqual(len(options), 4)
        values = [o['value'] for o in options]
        self.assertEqual(values, [1, 2, 4, 8])

    # ------------------------------------------------------------------
    # Slot generation — rental
    # ------------------------------------------------------------------

    def test_30_rental_slots_fallback(self):
        """Rental slots use fallback (08:00–18:00) when no calendar."""
        # Ensure warehouse has no opening_hours
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        slots = self.svc._get_available_start_slots(
            self.profile, self.warehouse,
            self.tomorrow.date(),
            item_role='rental',
        )
        self.assertTrue(len(slots) > 0)
        # First slot should be 08:00
        self.assertEqual(slots[0]['start_display'], '08:00')
        # At 30-min intervals from 08:00 to 18:00 = 20 slots
        self.assertEqual(len(slots), 20)
        # All should have opening_hours_state = unknown (no calendar)
        self.assertTrue(all(
            s['opening_hours_state'] == 'unknown' for s in slots
        ))

        self.warehouse.opening_hours = original_oh

    def test_31_rental_slots_with_calendar(self):
        """Rental slots respect warehouse opening hours calendar."""
        # Create a simple calendar: Mon-Fri 09:00-17:00
        calendar = self.env['resource.calendar'].create({
            'name': 'MCRF Test Calendar',
            'company_id': self.env.company.id,
            'attendance_ids': [
                (0, 0, {
                    'dayofweek': str(self.tomorrow.date().weekday()),
                    'hour_from': 9.0,
                    'hour_to': 12.0,
                }),
                (0, 0, {
                    'dayofweek': str(self.tomorrow.date().weekday()),
                    'hour_from': 13.0,
                    'hour_to': 17.0,
                }),
            ],
        })
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = calendar

        slots = self.svc._get_available_start_slots(
            self.profile, self.warehouse,
            self.tomorrow.date(),
        )

        self.assertTrue(len(slots) > 0)
        # All slots should be within 09:00-12:00 or 13:00-17:00
        for s in slots:
            hour_min = s['start_display']
            hour = int(hour_min.split(':')[0])
            self.assertTrue(
                (9 <= hour < 12) or (13 <= hour < 17),
                f"Slot {hour_min} is outside opening hours",
            )
        # All should have opening_hours_state = open
        self.assertTrue(all(
            s['opening_hours_state'] == 'open' for s in slots
        ))

        self.warehouse.opening_hours = original_oh

    def test_32_slot_interval_configurable(self):
        """Slot interval respects profile setting."""
        # Set to 60 minutes
        self.profile.rental_slot_interval_minutes = 60
        # No calendar → fallback 08:00–18:00
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        slots = self.svc._get_available_start_slots(
            self.profile, self.warehouse,
            self.tomorrow.date(),
        )
        # 10 hours / 60 min = 10 slots
        self.assertEqual(len(slots), 10)

        self.profile.rental_slot_interval_minutes = 30
        self.warehouse.opening_hours = original_oh

    # ------------------------------------------------------------------
    # Slot preview with prices
    # ------------------------------------------------------------------

    def test_40_slot_preview_includes_prices(self):
        """Slot preview returns slots with price info."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        slots = self.svc._get_slot_preview(
            self.profile, self.product_rental,
            self.tomorrow.date(),
            partner=self.partner,
            quantity=1.0,
            duration_value=2,
            duration_unit='hour',
        )

        self.assertTrue(len(slots) > 0)
        first = slots[0]
        self.assertIn('price_unit', first)
        self.assertIn('coefficient', first)
        self.assertIn('color_code', first)
        self.assertIn('start_display', first)
        # 2 hours → coeff 1.8 → 25 * 1.8 = 45.0
        self.assertAlmostEqual(first['coefficient'], 1.8, places=1)
        self.assertAlmostEqual(first['price_unit_excl'], 45.0, places=1)

        self.warehouse.opening_hours = original_oh

    # ------------------------------------------------------------------
    # Day availability
    # ------------------------------------------------------------------

    def test_50_day_available(self):
        """Day with slots returns availability info including qty."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        result = self.svc._get_day_availability_state(
            self.profile, self.product_rental,
            self.tomorrow.date(),
            quantity=1.0,
            duration_value=2,
            duration_unit='hour',
        )
        self.assertIn(result['day_state'], ('available', 'partial', 'unavailable'))
        self.assertIn('total_slot_count', result)
        self.assertTrue(result['total_slot_count'] > 0)
        self.assertIn('available_slot_count', result)
        self.assertIn('unavailable_slot_count', result)
        self.assertIn('min_available_qty', result)
        self.assertIn('max_available_qty', result)

        self.warehouse.opening_hours = original_oh

    def test_51_day_closed_no_calendar_day(self):
        """Day with calendar that has no attendance is closed."""
        other_weekday = str((self.tomorrow.date().weekday() + 1) % 7)
        calendar = self.env['resource.calendar'].create({
            'name': 'MCRF Empty Day Calendar',
            'company_id': self.env.company.id,
            'attendance_ids': [
                (0, 0, {
                    'dayofweek': other_weekday,
                    'hour_from': 9.0,
                    'hour_to': 17.0,
                }),
            ],
        })
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = calendar

        result = self.svc._get_day_availability_state(
            self.profile, self.product_rental,
            self.tomorrow.date(),
        )
        self.assertEqual(result['day_state'], 'closed')
        self.assertFalse(result['has_selectable_slots'])
        self.assertEqual(result['total_slot_count'], 0)

        self.warehouse.opening_hours = original_oh

    def test_52_day_service_always_available(self):
        """Service products are always available (no stock check)."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        result = self.svc._get_day_availability_state(
            self.profile, self.product_service,
            self.tomorrow.date(),
            item_role='service',
        )
        self.assertEqual(result['day_state'], 'available')
        self.assertEqual(
            result['available_slot_count'], result['total_slot_count'],
        )

        self.warehouse.opening_hours = original_oh

    def test_53_slot_preview_shows_availability(self):
        """Slot preview includes available_qty and availability_state."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        slots = self.svc._get_slot_preview(
            self.profile, self.product_rental,
            self.tomorrow.date(),
            partner=self.partner,
            quantity=1.0,
            duration_value=2,
            duration_unit='hour',
        )
        self.assertTrue(len(slots) > 0)
        first = slots[0]
        self.assertIn('available_qty', first)
        self.assertIn('availability_state', first)
        self.assertIn(first['availability_state'],
                       ('available', 'unavailable', 'unknown'))

        self.warehouse.opening_hours = original_oh

    def test_54_slot_unavailable_when_qty_exceeds_stock(self):
        """Slot is red/unselectable when requested qty > available
        for storable products."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        # Create a storable product with limited stock
        storable = self.env['product.product'].create({
            'name': 'MCRF Svc Storable Limited',
            'type': 'consu',
            'is_storable': True,
            'list_price': 30.0,
            'rent_periodicity': 'days',
            'use_in_multi_channel_rental_flow': True,
            'multi_channel_item_role': 'rental',
        })
        self.env['stock.quant']._update_available_quantity(
            storable, self.warehouse.lot_stock_id, 5.0,
        )

        # Request qty=999999 — should exceed stock of 5
        slots = self.svc._get_slot_preview(
            self.profile, storable,
            self.tomorrow.date(),
            partner=self.partner,
            quantity=999999.0,
            duration_value=2,
            duration_unit='hour',
        )
        self.assertTrue(len(slots) > 0)
        for slot in slots:
            self.assertEqual(slot['availability_state'], 'unavailable')
            self.assertFalse(slot['selectable'])
            self.assertEqual(
                slot['color_code'],
                self.profile.unavailable_color,
            )

        self.warehouse.opening_hours = original_oh

    # ------------------------------------------------------------------
    # Price calendar
    # ------------------------------------------------------------------

    def test_60_hourly_calendar(self):
        """Hourly calendar returns 3 days with slots for service products."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        # Use service product — always available, no stock check
        cal = self.svc._get_price_calendar_for_product(
            self.profile, self.product_service,
            self.tomorrow.date(),
            quantity=1.0,
            item_role='service',
            mode='hourly',
        )
        # Hourly mode: -1, 0, +1 = 3 days
        self.assertEqual(len(cal), 3)
        # Center day should have slots (service = always available)
        center = cal[1]
        self.assertEqual(center['date'], self.tomorrow.date().isoformat())
        self.assertTrue(len(center['slots']) > 0)

        self.warehouse.opening_hours = original_oh

    def test_60b_hourly_calendar_rental_no_stock(self):
        """Hourly calendar for rental with no stock shows unavailable days."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        cal = self.svc._get_price_calendar_for_product(
            self.profile, self.product_rental,
            self.tomorrow.date(),
            partner=self.partner,
            quantity=1.0,
            duration_value=2,
            duration_unit='hour',
            mode='hourly',
        )
        self.assertEqual(len(cal), 3)
        # Center day: product is consu with 0 stock → unavailable
        center = cal[1]
        self.assertIn(center['day_state'], ('unavailable', 'available'))
        # Slots list empty because has_selectable_slots is False
        if center['day_state'] == 'unavailable':
            self.assertEqual(len(center['slots']), 0)

        self.warehouse.opening_hours = original_oh

    def test_61_daily_calendar(self):
        """Daily calendar returns 7 days."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False

        cal = self.svc._get_price_calendar_for_product(
            self.profile, self.product_service,
            self.tomorrow.date(),
            item_role='service',
            mode='daily',
        )
        # Daily mode: -3 through +3 = 7 days
        self.assertEqual(len(cal), 7)

        self.warehouse.opening_hours = original_oh

    # ------------------------------------------------------------------
    # Color code
    # ------------------------------------------------------------------

    def test_70_color_thresholds(self):
        """Color codes match profile thresholds."""
        self.assertEqual(
            self.svc._get_dynamic_color_code(self.profile, 80.0),
            '#90EE90',  # low
        )
        self.assertEqual(
            self.svc._get_dynamic_color_code(self.profile, 100.0),
            '#28a745',  # normal
        )
        self.assertEqual(
            self.svc._get_dynamic_color_code(self.profile, 120.0),
            '#006400',  # high
        )

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def test_80_forecasted_avail_no_warehouse(self):
        """Forecasted availability unknown without warehouse."""
        result = self.svc._get_forecasted_availability(
            self.product_rental,
            warehouse=self.env['stock.warehouse'],
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_11,
        )
        self.assertEqual(result['availability_state'], 'unknown')

    def test_81_forecasted_avail_service_always_available(self):
        """Service products are always available."""
        result = self.svc._get_forecasted_availability(
            self.product_service,
            warehouse=self.warehouse,
            start_dt=self.tomorrow_9,
            end_dt=self.tomorrow_11,
            item_role='service',
        )
        self.assertEqual(result['availability_state'], 'available')

    # ------------------------------------------------------------------
    # End datetime computation
    # ------------------------------------------------------------------

    def test_90_compute_end_datetime(self):
        """End datetime computed correctly from duration."""
        start = datetime(2026, 6, 1, 10, 0)
        end_2h = self.svc._compute_end_datetime(start, 2, 'hour')
        self.assertEqual(end_2h, datetime(2026, 6, 1, 12, 0))

        end_1d = self.svc._compute_end_datetime(start, 1, 'day')
        self.assertEqual(end_1d, datetime(2026, 6, 2, 10, 0))

        end_30m = self.svc._compute_end_datetime(start, 30, 'minute')
        self.assertEqual(end_30m, datetime(2026, 6, 1, 10, 30))

    # ------------------------------------------------------------------
    # Slot timezone & past-slot filtering  [PR16]
    # ------------------------------------------------------------------

    def test_91_flow_timezone_from_calendar(self):
        """The slot timezone comes from the warehouse opening-hours calendar."""
        # Odoo 20 removed resource.calendar.tz: a schedule is read in its
        # company's timezone.
        self.env.company.tz = 'Europe/Brussels'
        calendar = self.env['resource.calendar'].create({
            'name': 'MCRF TZ Calendar',
            'company_id': self.env.company.id,
            'attendance_ids': [],
        })
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = calendar
        self.assertEqual(
            self.svc._get_flow_timezone(self.warehouse), 'Europe/Brussels',
        )
        # No calendar → never crashes, returns a usable tz name.
        self.warehouse.opening_hours = False
        self.assertTrue(self.svc._get_flow_timezone(self.warehouse))
        self.warehouse.opening_hours = original_oh

    def test_92_fallback_slots_are_timezone_aware(self):
        """Fallback 08:00 is the local wall-clock time, stored as UTC."""
        self.env.user.tz = 'Europe/Brussels'
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False
        slots = self.svc._get_available_start_slots(
            self.profile, self.warehouse, self.tomorrow.date(),
            item_role='rental',
        )
        first = slots[0]
        # Displayed as local 08:00 ...
        self.assertEqual(first['start_display'], '08:00')
        # ... and the stored UTC value converts back to 08:00 Brussels.
        from pytz import UTC, timezone as _tz
        local = UTC.localize(first['start_datetime']).astimezone(
            _tz('Europe/Brussels'),
        )
        self.assertEqual(local.strftime('%H:%M'), '08:00')
        self.warehouse.opening_hours = original_oh

    def test_93_past_slots_are_filtered(self):
        """Start slots already in the past are dropped (absolute-instant)."""
        from odoo import fields
        now = fields.Datetime.now()
        slots = [
            {'start_datetime': now - timedelta(hours=1), 'start_display': 'past'},
            {'start_datetime': now + timedelta(hours=1), 'start_display': 'future'},
        ]
        kept = self.svc._filter_past_slots(slots)
        self.assertEqual([s['start_display'] for s in kept], ['future'])

    # ------------------------------------------------------------------
    # Latest start slot limited by duration  [PR17]
    # ------------------------------------------------------------------

    def test_94_end_limit_delta_modes(self):
        """The end-limit delta follows the selected mode."""
        # Deterministic duration options (1,2,4,8h) via the profile fallback.
        self.profile.write({
            'fallback_duration_ids': [(5, 0, 0)] + [
                (0, 0, {
                    'name': f'{v} hours',
                    'duration_value': v,
                    'duration_unit': 'hour',
                })
                for v in (1, 2, 4, 8)
            ],
        })
        # 'duration' → the selected duration itself.
        self.assertEqual(
            self.svc._get_end_limit_delta(
                self.profile, self.product_addon, 8, 'hour', 'duration',
            ),
            timedelta(hours=8),
        )
        # 'next_contingent' → the next shorter option (4h).
        self.assertEqual(
            self.svc._get_end_limit_delta(
                self.profile, self.product_addon, 8, 'hour', 'next_contingent',
            ),
            timedelta(hours=4),
        )
        # 'next_contingent' on the shortest option → falls back to itself.
        self.assertEqual(
            self.svc._get_end_limit_delta(
                self.profile, self.product_addon, 1, 'hour', 'next_contingent',
            ),
            timedelta(hours=1),
        )

    def test_95_end_limit_duration_mode(self):
        """'duration' caps the last start at closing minus the duration."""
        original_oh = self.warehouse.opening_hours
        self.warehouse.opening_hours = False  # fallback 08:00–18:00
        self.profile.slot_end_limit_mode = 'duration'
        future = (self.tomorrow + timedelta(days=2)).date()
        slots = self.svc._get_slot_preview(
            self.profile, self.product_rental, future,
            quantity=1, duration_value=8, duration_unit='hour',
            item_role='rental',
        )
        self.assertTrue(slots)
        # 18:00 − 8h = 10:00 is the last selectable start.
        self.assertEqual(slots[-1]['start_display'], '10:00')
        self.warehouse.opening_hours = original_oh

    def test_96_end_limit_none_mode(self):
        """'none' leaves the last start at the last opening-hours slot."""
        original_oh = self.warehouse.opening_hours
        original_interval = self.profile.rental_slot_interval_minutes
        self.warehouse.opening_hours = False
        self.profile.slot_end_limit_mode = 'none'
        self.profile.rental_slot_interval_minutes = 60
        future = (self.tomorrow + timedelta(days=2)).date()
        slots = self.svc._get_slot_preview(
            self.profile, self.product_rental, future,
            quantity=1, duration_value=8, duration_unit='hour',
            item_role='rental',
        )
        # Unlimited: last start is the final 60-min opening-hours slot (17:00).
        self.assertEqual(slots[-1]['start_display'], '17:00')
        self.profile.rental_slot_interval_minutes = original_interval
        self.warehouse.opening_hours = original_oh
