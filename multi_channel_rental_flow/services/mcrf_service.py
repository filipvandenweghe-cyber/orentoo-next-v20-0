import math
from datetime import datetime as dt_class, time as t_class, timedelta

from pytz import timezone, UTC

from odoo import api, fields, models, _

# Business requirements: PR01–PR15
# See __init__.py for full index.


# Keys for duration unit labels — translated at call time via _get_unit_labels()
_UNIT_LABEL_KEYS = {
    'minute': ("minute", "minutes"),
    'hour': ("hour", "hours"),
    'day': ("day", "days"),
    'week': ("week", "weeks"),
    'month': ("month", "months"),
}

# Default fallback duration values (labels are built at call time)
_DEFAULT_FALLBACK_DURATION_VALUES = [
    {'value': 1, 'unit': 'hour'},
    {'value': 2, 'unit': 'hour'},
    {'value': 4, 'unit': 'hour'},
    {'value': 8, 'unit': 'hour'},
]


def _get_unit_labels(env):
    """Return translated unit labels. Must be called at request time.

    Takes ``env`` so translation uses the env-aware ``env._()`` rather than
    the frame-inspecting module-level ``_()``, which cannot detect a language
    from a plain function scope (it would log "no translation language
    detected" and skip translation).
    """
    return {
        'minute': (env._("minute"), env._("minutes")),
        'hour': (env._("hour"), env._("hours")),
        'day': (env._("day"), env._("days")),
        'week': (env._("week"), env._("weeks")),
        'month': (env._("month"), env._("months")),
    }


class MultiChannelRentalService(models.AbstractModel):
    """Central service for pricing preview, duration options, timeslot
    generation and availability checks.

    All methods are designed to be called from backend, website controllers
    and kiosk API endpoints — no pricing logic is duplicated in JavaScript.
    """

    _name = 'multi.channel.rental.service'
    _description = 'Multi-Channel Rental Flow Service'

    # =================================================================
    # 1. Price preview
    # =================================================================

    @api.model
    def _get_price_preview(
        self, profile, product, partner=None, warehouse=None,
        pricelist=None, quantity=1.0, start_dt=None, end_dt=None,
        item_role='rental',
    ):
        """Compute a full price preview for a product in the flow.

        Returns a dict with all pricing, availability and display info.
        """
        partner = partner or self.env['res.partner']
        warehouse = warehouse or profile.warehouse_id
        pricelist = pricelist or profile.pricelist_id

        result = {
            'product_id': product.id,
            'product_name': product.display_name,
            'item_role': item_role,
            'quantity': quantity,
            'price_unit': 0.0,
            'price_subtotal': 0.0,
            'coefficient': 1.0,
            'dynamic_factor_percentage': 100.0,
            'dynamic_multiplier': 1.0,
            'availability_state': 'unknown',
            'available_qty': 0.0,
            'opening_hours_state': 'unknown',
            'selectable': True,
            'color_code': profile.normal_color or '#28a745',
            'display_label': '',
        }

        if item_role == 'rental':
            self._fill_rental_price(
                result, profile, product, partner, warehouse,
                pricelist, quantity, start_dt, end_dt,
            )
        elif item_role == 'event_ticket':
            self._fill_event_ticket_price(result, product, quantity)
        else:
            # addon / service — standard product price
            self._fill_standard_price(
                result, product, pricelist, quantity,
            )

        return result

    @api.model
    def _fill_rental_price(
        self, result, profile, product, partner, warehouse,
        pricelist, quantity, start_dt, end_dt,
    ):
        """Fill result dict with rental pricing using coefficient engine."""
        svc = self.env['rental.pricing.service']
        tmpl = product.product_tmpl_id

        # Base price from pricelist / product.lst_price
        base_price = product.lst_price or 0.0
        if pricelist and start_dt and end_dt:
            try:
                pl_price = pricelist._get_product_price(
                    product, quantity,
                    currency=pricelist.currency_id,
                    date=start_dt,
                    start_date=start_dt,
                    end_date=end_dt,
                )
                if pl_price:
                    base_price = pl_price
            except Exception:
                pass  # fallback to lst_price

        # Coefficient
        coefficient = 1.0
        if start_dt and end_dt:
            coefficient = svc._get_coefficient_for_context(
                tmpl, partner, warehouse, start_dt, end_dt,
            )

        # Dynamic factor
        dp_pct = 100.0
        dp_mult = 1.0
        if start_dt and end_dt:
            dp_pct = svc._get_dynamic_factor_percentage_for_context(
                tmpl, warehouse, start_dt, end_dt,
            )
            dp_mult = dp_pct / 100.0

        price_unit = svc._compute_final_rental_price(
            base_price, coefficient, dp_mult,
        )

        # Compute tax-inclusive price
        taxes = product.taxes_id.filtered(
            lambda t: t.company_id == profile.company_id
        )
        if taxes:
            currency = (
                pricelist.currency_id if pricelist
                else profile.company_id.currency_id
            )
            tax_res = taxes.compute_all(
                price_unit, currency=currency,
                quantity=quantity, product=product, partner=partner,
            )
            price_total = tax_res['total_included']
            price_unit_incl = tax_res['total_included'] / quantity if quantity else price_unit
        else:
            price_total = price_unit * quantity
            price_unit_incl = price_unit

        result.update({
            'price_unit': price_unit_incl,
            'price_unit_excl': price_unit,
            'price_subtotal': price_total,
            'coefficient': coefficient,
            'dynamic_factor_percentage': dp_pct,
            'dynamic_multiplier': dp_mult,
            'color_code': profile._get_dynamic_color(dp_pct),
        })

    @api.model
    def _fill_event_ticket_price(self, result, product, quantity):
        """Fill result dict with event ticket pricing."""
        price = product.lst_price or 0.0
        result.update({
            'price_unit': price,
            'price_subtotal': price * quantity,
        })

    @api.model
    def _fill_standard_price(self, result, product, pricelist, quantity):
        """Fill result dict with standard pricelist/product price."""
        price = product.lst_price or 0.0
        if pricelist:
            try:
                pl_price = pricelist._get_product_price(
                    product, quantity,
                )
                if pl_price:
                    price = pl_price
            except Exception:
                pass
        result.update({
            'price_unit': price,
            'price_subtotal': price * quantity,
        })

    # =================================================================
    # 2. Duration options
    # =================================================================

    @api.model
    def _get_duration_options(self, profile, product, partner=None,
                              warehouse=None):
        """Return available duration choices for a product.

        Priority:
        1. Coefficient table lines (if product.requires_duration and a
           table exists). Lines with as_from_duration=0 are skipped —
           they represent the base rate, not a selectable duration.
        2. Profile fallback duration options
        3. System default fallback (1, 2, 4, 8 hours)

        Returns list of dicts:
            [{'value': 2, 'unit': 'hour', 'label': '2 hours',
              'coefficient': 0.8}, ...]
        """
        warehouse = warehouse or profile.warehouse_id
        partner = partner or self.env['res.partner']
        pp = product
        tmpl = pp.product_tmpl_id

        # Get translated unit labels at call time
        unit_labels = _get_unit_labels(self.env)

        # Try coefficient table
        if pp.requires_duration:
            svc = self.env['rental.pricing.service']
            table = svc._get_applicable_coefficient_table(
                tmpl, partner, warehouse,
            )
            if table and table.line_ids:
                options = []
                unit = table.duration_unit
                singular, plural = unit_labels.get(
                    unit, (self.env._("unit"), self.env._("units"))
                )
                for line in table.line_ids.sorted('as_from_duration'):
                    val = line.as_from_duration
                    if val == 0:
                        continue  # base rate, not a selectable duration
                    label_unit = singular if val == 1 else plural
                    options.append({
                        'value': val,
                        'unit': unit,
                        'label': f"{val} {label_unit}",
                        'coefficient': line.coefficient,
                    })
                if options:
                    return options

        # Profile fallback durations
        if profile.fallback_duration_ids:
            return [{
                'value': d.duration_value,
                'unit': d.duration_unit,
                'label': d.name,
                'coefficient': None,
            } for d in profile.fallback_duration_ids.sorted('sequence')]

        # System default (1, 2, 4, 8 hours)
        fallbacks = []
        for d in _DEFAULT_FALLBACK_DURATION_VALUES:
            singular, plural = unit_labels.get(
                d['unit'], (self.env._("unit"), self.env._("units"))
            )
            label_unit = singular if d['value'] == 1 else plural
            fallbacks.append({
                'value': d['value'],
                'unit': d['unit'],
                'label': f"{d['value']} {label_unit}",
                'coefficient': None,
            })
        return fallbacks

    # =================================================================
    # 3. Timeslot generation
    # =================================================================

    @api.model
    def _get_available_start_slots(self, profile, warehouse, date,
                                    item_role='rental', event_id=None,
                                    quantity=1.0):
        """Return available start-time slots for a given date.

        For rentals: generated from warehouse opening hours at the
        profile's slot interval.
        For events: uses event slots if available.

        :param date: datetime.date object
        :returns: list of dicts with slot info
        """
        if item_role == 'event_ticket' and event_id:
            return self._get_event_slots_for_date(event_id, date, quantity=quantity)
        return self._get_rental_slots_for_date(profile, warehouse, date)

    @api.model
    def _basket_add_rejection(self, product, item_role, start_datetime,
                              end_datetime):
        """Return a rejection message for an invalid basket add, else False.

        A rental that requires a timeslot must carry one — otherwise the line
        would be slot-less and base-priced, with no date/time to show in the
        basket.  [KO17]
        """
        if (
            item_role == 'rental'
            and product.requires_timeslot
            and not (start_datetime and end_datetime)
        ):
            return _('Please select a timeslot before adding this item.')
        return False

    @api.model
    def _get_flow_timezone(self, warehouse):
        """Return the timezone name driving rental slot times.

        Uses the warehouse opening-hours calendar timezone — the single
        source of truth for when the branch is open and therefore for how
        slot start times are computed and displayed.  Falls back to the
        current user's timezone, then UTC, when no calendar is configured.

        Odoo 20 removed ``resource.calendar.tz``: a working schedule is now
        read in its company's timezone (``res.company.tz``), so that is where
        the branch timezone lives.
        """
        calendar = warehouse.opening_hours if warehouse else None
        if calendar:
            return (calendar.company_id.tz
                    or warehouse.company_id.tz
                    or self.env.user.tz
                    or 'UTC')
        return self.env.user.tz or 'UTC'

    @api.model
    def _filter_past_slots(self, slots):
        """Drop start slots that already lie in the past.

        Slot ``start_datetime`` is naive UTC and ``fields.Datetime.now()`` is
        naive UTC, so the comparison is an absolute-instant comparison and is
        timezone-independent — a slot at 14:00 Brussels is correctly kept or
        dropped regardless of the display timezone.
        """
        now = fields.Datetime.now()
        return [
            s for s in slots
            if not s.get('start_datetime') or s['start_datetime'] > now
        ]

    @api.model
    def _duration_to_timedelta(self, value, unit):
        """Convert a duration (value + unit) to a timedelta.

        Mirrors the unit mapping of ``_compute_end_datetime`` so the end-time
        limit and the actual rental end stay consistent.
        """
        value = value or 0
        unit_map = {
            'minute': timedelta(minutes=value),
            'hour': timedelta(hours=value),
            'day': timedelta(days=value),
            'week': timedelta(weeks=value),
            'month': timedelta(days=value * 30),
        }
        return unit_map.get(unit, timedelta(hours=value))

    @api.model
    def _get_day_closing_datetime(self, warehouse, date):
        """Return the day's closing time as naive UTC (or None if closed).

        For a warehouse with an opening-hours calendar this is the end of the
        last work interval on ``date``; otherwise it is 18:00 local (the
        fallback closing), expressed in the flow timezone.
        """
        tz_name = self._get_flow_timezone(warehouse)
        tz = timezone(tz_name)
        calendar = warehouse.opening_hours if warehouse else None
        if not calendar:
            close_local = tz.localize(dt_class.combine(date, t_class(18, 0)))
            return close_local.astimezone(UTC).replace(tzinfo=None)

        day_start = tz.localize(dt_class.combine(date, t_class(0, 0)))
        day_end = day_start + timedelta(days=1)
        intervals = calendar._work_intervals_batch(
            day_start.astimezone(UTC), day_end.astimezone(UTC),
        ).get(False, [])
        if not intervals:
            return None
        last_end = max(iv[1] for iv in intervals)
        return last_end.astimezone(UTC).replace(tzinfo=None)

    @api.model
    def _get_end_limit_delta(self, profile, product, duration_value,
                             duration_unit, mode):
        """Return the timedelta used to cap the latest start slot, or None.

        - ``duration``: the selected duration itself.
        - ``next_contingent``: the largest available duration option strictly
          shorter than the selected one (falls back to the selected duration
          when there is no shorter option).
        """
        sel_val = duration_value or 1
        sel_unit = duration_unit or profile.default_duration_unit or 'hour'
        sel_delta = self._duration_to_timedelta(sel_val, sel_unit)

        if mode == 'duration':
            return sel_delta
        if mode == 'next_contingent':
            options = self._get_duration_options(profile, product) or []
            deltas = sorted({
                self._duration_to_timedelta(o['value'], o['unit'])
                for o in options
            })
            lower = [d for d in deltas if d < sel_delta]
            return lower[-1] if lower else sel_delta
        return None

    @api.model
    def _apply_end_time_limit(self, profile, product, warehouse, date, slots,
                              duration_value, duration_unit):
        """Drop start slots whose (limited) end would exceed closing time.

        The cap depends on ``profile.slot_end_limit_mode`` [PR17].  Returns the
        slots unchanged for mode ``none`` or when the closing time / limit
        cannot be determined.
        """
        mode = getattr(profile, 'slot_end_limit_mode', 'none') or 'none'
        if mode == 'none' or not slots:
            return slots
        limit_delta = self._get_end_limit_delta(
            profile, product, duration_value, duration_unit, mode,
        )
        if not limit_delta:
            return slots
        closing = self._get_day_closing_datetime(warehouse, date)
        if not closing:
            return slots
        return [
            s for s in slots
            if not s.get('start_datetime')
            or (s['start_datetime'] + limit_delta) <= closing
        ]

    @api.model
    def _get_rental_slots_for_date(self, profile, warehouse, date):
        """Generate rental start-time slots for a date from opening hours."""
        interval_minutes = profile.rental_slot_interval_minutes or 30
        tz_name = self._get_flow_timezone(warehouse)
        calendar = warehouse.opening_hours if warehouse else None

        if not calendar:
            return self._filter_past_slots(
                self._get_fallback_slots(date, interval_minutes, tz_name)
            )

        # Get work intervals for the full day
        tz = timezone(tz_name)

        day_start = tz.localize(
            dt_class.combine(date, t_class(0, 0))
        )
        day_end = day_start + timedelta(days=1)

        # Convert to UTC for the calendar API
        day_start_utc = day_start.astimezone(UTC)
        day_end_utc = day_end.astimezone(UTC)

        intervals = calendar._work_intervals_batch(
            day_start_utc, day_end_utc,
        )
        # intervals is a dict keyed by resource_id, we want False (no resource)
        work_intervals = intervals.get(False, [])

        slots = []
        for start_utc, end_utc, _attendance in work_intervals:
            start_local = start_utc.astimezone(tz)
            end_local = end_utc.astimezone(tz)
            cursor = start_local
            while cursor < end_local:
                slots.append({
                    'start_datetime': cursor.astimezone(UTC).replace(tzinfo=None),
                    'start_display': cursor.strftime('%H:%M'),
                    'opening_hours_state': 'open',
                    'selectable': True,
                })
                cursor += timedelta(minutes=interval_minutes)

        return self._filter_past_slots(slots)

    @api.model
    def _get_fallback_slots(self, date, interval_minutes, tz_name='UTC'):
        """Generate slots for a day when no opening hours are configured.

        Uses 08:00–18:00 (local to ``tz_name``) as a safe fallback.  Start
        times are localized to ``tz_name`` and stored as UTC so they behave
        exactly like calendar-derived slots (correct clock time in the picker
        and the basket, correct past-slot filtering).
        """
        tz = timezone(tz_name)
        slots = []
        start = tz.localize(dt_class.combine(date, t_class(8, 0)))
        end = tz.localize(dt_class.combine(date, t_class(18, 0)))
        cursor = start
        while cursor < end:
            slots.append({
                'start_datetime': cursor.astimezone(UTC).replace(tzinfo=None),
                'start_display': cursor.strftime('%H:%M'),
                'opening_hours_state': 'unknown',
                'selectable': True,
            })
            cursor += timedelta(minutes=interval_minutes)
        return slots

    @api.model
    def _get_event_slots_for_date(self, event_id, date, quantity=1.0):
        """Return event slots for a given date.

        Uses event.slot if event is multi-slot, otherwise the event
        date/time itself. Marks slots as unselectable when the
        requested quantity exceeds seats_available.
        """
        if 'event.event' not in self.env:
            return []

        event = self.env['event.event'].browse(event_id)
        if not event.exists():
            return []

        slots = []
        if event.is_multi_slots and event.event_slot_ids:
            for slot in event.event_slot_ids:
                if not slot.start_datetime:
                    continue
                slot_date = slot.start_datetime.date()
                if slot_date == date:
                    slots.append({
                        'start_datetime': slot.start_datetime,
                        'end_datetime': slot.end_datetime,
                        'start_display': slot.start_datetime.strftime('%H:%M'),
                        'end_display': (
                            slot.end_datetime.strftime('%H:%M')
                            if slot.end_datetime else ''
                        ),
                        'event_slot_id': slot.id,
                        'seats_available': slot.seats_available,
                        'is_sold_out': slot.is_sold_out,
                        'opening_hours_state': 'open',
                        'selectable': (
                            not slot.is_sold_out
                            and (slot.event_id.seats_max == 0
                                 or slot.seats_available >= quantity)
                        ),
                        'available_qty': slot.seats_available,
                    })
        else:
            # Single-date event
            if event.date_begin and event.date_begin.date() == date:
                slots.append({
                    'start_datetime': event.date_begin,
                    'end_datetime': event.date_end,
                    'start_display': event.date_begin.strftime('%H:%M'),
                    'end_display': (
                        event.date_end.strftime('%H:%M')
                        if event.date_end else ''
                    ),
                    'event_slot_id': None,
                    'seats_available': event.seats_available,
                    'is_sold_out': event.event_registrations_sold_out,
                    'opening_hours_state': 'open',
                    'selectable': (
                        not event.event_registrations_sold_out
                        and (event.seats_max == 0
                             or event.seats_available >= quantity)
                    ),
                    'available_qty': event.seats_available,
                })
        return slots

    # =================================================================
    # 4. Slot preview (with prices + stock availability)
    # =================================================================

    @api.model
    def _get_slot_preview(
        self, profile, product, date, partner=None, warehouse=None,
        pricelist=None, quantity=1.0, duration_value=None,
        duration_unit=None, item_role='rental', event_id=None,
    ):
        """Return start-time slots with price previews and stock
        availability for a product/date.

        Each slot includes:
        - pricing info (price_unit, coefficient, dynamic factor, color)
        - available_qty: forecasted stock for the rental period
        - availability_state: 'available', 'unavailable', 'unknown'
        - selectable: False if quantity > available_qty (shown red)
        """
        warehouse = warehouse or profile.warehouse_id
        pricelist = pricelist or profile.pricelist_id

        raw_slots = self._get_available_start_slots(
            profile, warehouse, date,
            item_role=item_role, event_id=event_id,
            quantity=quantity,
        )

        # PR17: cap the latest start slot based on the selected duration and
        # the profile's end-limit mode (rentals only — events have fixed slots).
        if item_role != 'event_ticket':
            raw_slots = self._apply_end_time_limit(
                profile, product, warehouse, date, raw_slots,
                duration_value, duration_unit,
            )

        # Determine end_dt for each slot
        dur_val = duration_value or 1
        dur_unit = duration_unit or profile.default_duration_unit or 'hour'

        result = []
        for slot in raw_slots:
            start_dt = slot['start_datetime']
            if 'end_datetime' in slot and slot['end_datetime']:
                end_dt = slot['end_datetime']
            else:
                end_dt = self._compute_end_datetime(
                    start_dt, dur_val, dur_unit,
                )

            # Price preview
            price = self._get_price_preview(
                profile, product, partner=partner,
                warehouse=warehouse, pricelist=pricelist,
                quantity=quantity, start_dt=start_dt, end_dt=end_dt,
                item_role=item_role,
            )

            # Stock availability for this slot period
            avail = self._get_forecasted_availability(
                product, warehouse, start_dt, end_dt,
                quantity=quantity, item_role=item_role,
            )

            slot_result = {**slot, **price}
            slot_result['end_datetime'] = end_dt
            slot_result['available_qty'] = avail['available_qty']
            slot_result['availability_state'] = avail['availability_state']

            # Mark slot as not selectable (red) if qty > available
            if avail['availability_state'] == 'unavailable':
                slot_result['selectable'] = False
                slot_result['color_code'] = (
                    profile.unavailable_color or '#dc3545'
                )

            result.append(slot_result)

        return result

    @api.model
    def _compute_end_datetime(self, start_dt, duration_value, duration_unit):
        """Compute end datetime from start + duration."""
        unit_map = {
            'minute': timedelta(minutes=duration_value),
            'hour': timedelta(hours=duration_value),
            'day': timedelta(days=duration_value),
            'week': timedelta(weeks=duration_value),
            'month': timedelta(days=duration_value * 30),
        }
        delta = unit_map.get(duration_unit, timedelta(hours=duration_value))
        return start_dt + delta

    # =================================================================
    # 5. Forecasted stock availability
    # =================================================================

    @api.model
    def _get_forecasted_availability(self, product, warehouse, start_dt,
                                      end_dt, quantity=1.0,
                                      item_role='rental'):
        """Return rental availability for a product/period.

        Uses the SAME central definition as the rest of Orentoo — the
        canonical Option-A engine ``product.product._rental_available_qty``
        (from ``rental_set``):

            Available = max(Total physical stock
                            − reserved by OTHER orders (period-aware)
                            − in repair, 0)

        This is repair-, rental-location- and reservation-aware, and its
        reserved-by-others term is the *peak* concurrent reservation across
        the window, so it is the worst-case availability for the **complete**
        interval.  The multi-channel flow therefore returns exactly the same
        number the rental order lines would show for that period.

        ``rental_set`` is a soft dependency of this module: if the engine is
        not installed we fall back to ``_compute_min_availability`` (native
        ``virtual_available`` sampled across the window).

        :returns: dict with:
            - available_qty: float (available across the period)
            - availability_state: 'available', 'unavailable', 'unknown'
            - message: str (explanation when unavailable)
        """
        result = {
            'available_qty': 0.0,
            'availability_state': 'unknown',
            'message': '',
        }

        # Service products and add-ons are always available
        if item_role in ('service', 'addon') or product.type == 'service':
            result['availability_state'] = 'available'
            return result

        if not warehouse:
            return result

        # Check if product tracks stock (storable goods)
        is_storable = getattr(product, 'is_storable', False)
        if not is_storable:
            result['availability_state'] = 'available'
            return result

        if not start_dt or not end_dt:
            return result

        try:
            if hasattr(product, '_rental_available_qty'):
                # Canonical Orentoo availability (single source of truth).
                avail_qty = product._rental_available_qty(
                    start_dt, end_dt, warehouse=warehouse,
                )
            else:
                # rental_set engine absent — soft fallback.
                avail_qty = self._compute_min_availability(
                    product, warehouse.lot_stock_id, start_dt, end_dt,
                )
        except Exception:
            # Last-resort single-point check so the flow never hard-fails.
            try:
                avail_qty = product.with_context(
                    location=warehouse.lot_stock_id.id,
                    to_date=start_dt,
                ).virtual_available or 0.0
            except Exception:
                result['availability_state'] = 'available'
                return result

        result['available_qty'] = avail_qty
        if avail_qty >= quantity:
            result['availability_state'] = 'available'
        elif avail_qty > 0:
            result['availability_state'] = 'unavailable'
            result['message'] = _(
                "%(available)s available (%(requested)s requested).",
                available=avail_qty,
                requested=quantity,
            )
        else:
            result['availability_state'] = 'unavailable'
            result['message'] = _("Not available for this period.")

        return result

    @api.model
    def _compute_min_availability(self, product, stock_location,
                                   start_dt, end_dt):
        """Compute the minimum virtual_available across a rental period.

        Walks through the rental period at adaptive intervals and checks
        virtual_available at each point. Returns the minimum — this is
        the true capacity for the full rental window.

        Interval logic:
        - Duration < 1 hour: check start and end only
        - Duration < 24 hours: check every hour
        - Duration >= 24 hours: check every day

        :param product: product.product record
        :param stock_location: stock.location (warehouse lot_stock_id)
        :param start_dt: datetime — rental start
        :param end_dt: datetime — rental end
        :returns: float — minimum available quantity
        """
        duration = end_dt - start_dt
        total_hours = duration.total_seconds() / 3600

        # Determine check interval
        if total_hours < 1:
            # Very short rental: just check start and end
            check_points = [start_dt, end_dt]
        elif total_hours <= 24:
            # Sub-day: check every hour
            check_points = []
            cursor = start_dt
            while cursor <= end_dt:
                check_points.append(cursor)
                cursor += timedelta(hours=1)
            if check_points[-1] < end_dt:
                check_points.append(end_dt)
        else:
            # Multi-day: check every day
            check_points = []
            cursor = start_dt
            while cursor <= end_dt:
                check_points.append(cursor)
                cursor += timedelta(days=1)
            if check_points[-1] < end_dt:
                check_points.append(end_dt)

        # Check virtual_available at each point
        min_qty = float('inf')
        for check_dt in check_points:
            qty = product.with_context(
                location=stock_location.id,
                to_date=check_dt,
            ).virtual_available or 0.0
            if qty < min_qty:
                min_qty = qty

        return max(min_qty, 0.0) if min_qty != float('inf') else 0.0

    # =================================================================
    # 6. Day availability (with stock)
    # =================================================================

    @api.model
    def _get_day_availability_state(
        self, profile, product, date, warehouse=None,
        quantity=1.0, duration_value=None, duration_unit=None,
        item_role='rental', event_id=None,
    ):
        """Return the availability state for a full day, incorporating
        forecasted stock for each slot.

        :returns: dict with:
            - day_state: 'available', 'partial', 'unavailable', 'closed'
            - has_selectable_slots: bool
            - total_slot_count: int (all slots from opening hours)
            - available_slot_count: int (slots where qty <= available)
            - unavailable_slot_count: int (slots where qty > available)
            - min_available_qty: float (lowest available qty across slots)
            - max_available_qty: float (highest available qty across slots)
            - color_code: str
        """
        warehouse = warehouse or profile.warehouse_id
        dur_val = duration_value or 1
        dur_unit = duration_unit or profile.default_duration_unit or 'hour'

        # Get opening-hours slots
        raw_slots = self._get_available_start_slots(
            profile, warehouse, date,
            item_role=item_role, event_id=event_id,
            quantity=quantity,
        )

        # PR17: apply the same latest-start-slot cap used by the slot picker,
        # so the day calendar counts only genuinely selectable start times.
        if item_role != 'event_ticket':
            raw_slots = self._apply_end_time_limit(
                profile, product, warehouse, date, raw_slots,
                duration_value, duration_unit,
            )

        if not raw_slots:
            return {
                'day_state': 'closed',
                'has_selectable_slots': False,
                'total_slot_count': 0,
                'available_slot_count': 0,
                'unavailable_slot_count': 0,
                'min_available_qty': 0.0,
                'max_available_qty': 0.0,
                'color_code': profile.closed_color or '#6c757d',
            }

        # Check availability for each slot
        available_count = 0
        unavailable_count = 0
        available_qtys = []

        for slot in raw_slots:
            if item_role == 'event_ticket':
                # Event: use the slot's selectable flag (already checks
                # seats_available >= quantity)
                avail_qty = slot.get('available_qty', 0)
                available_qtys.append(avail_qty)
                if slot.get('selectable', False):
                    available_count += 1
                else:
                    unavailable_count += 1
            else:
                # Rental: check forecasted stock
                start_dt = slot['start_datetime']
                if 'end_datetime' in slot and slot['end_datetime']:
                    end_dt = slot['end_datetime']
                else:
                    end_dt = self._compute_end_datetime(
                        start_dt, dur_val, dur_unit,
                    )

                avail = self._get_forecasted_availability(
                    product, warehouse, start_dt, end_dt,
                    quantity=quantity, item_role=item_role,
                )
                avail_qty = avail['available_qty']
                available_qtys.append(avail_qty)

                if avail['availability_state'] == 'available':
                    available_count += 1
                else:
                    unavailable_count += 1

        total = len(raw_slots)
        min_qty = min(available_qtys) if available_qtys else 0.0
        max_qty = max(available_qtys) if available_qtys else 0.0

        if available_count == 0:
            day_state = 'unavailable'
            color = profile.unavailable_color or '#dc3545'
        elif unavailable_count == 0:
            day_state = 'available'
            color = profile.normal_color or '#28a745'
        else:
            day_state = 'partial'
            color = profile.normal_color or '#28a745'

        return {
            'day_state': day_state,
            'has_selectable_slots': available_count > 0,
            'total_slot_count': total,
            'available_slot_count': available_count,
            'unavailable_slot_count': unavailable_count,
            'min_available_qty': min_qty,
            'max_available_qty': max_qty,
            'color_code': color,
        }

    @api.model
    def _get_price_calendar_for_product(
        self, profile, product, center_date, partner=None, warehouse=None,
        pricelist=None, quantity=1.0, duration_value=None,
        duration_unit=None, item_role='rental', mode='hourly',
        event_id=None,
    ):
        """Build a multi-day calendar with availability and pricing.

        :param mode: 'hourly' (3-day window) or 'daily' (7-day window)
        :returns: list of day dicts, each with slots and day_state
        """
        warehouse = warehouse or profile.warehouse_id
        pricelist = pricelist or profile.pricelist_id

        if mode == 'hourly':
            day_range = range(-1, 2)  # -1, 0, +1
        else:
            day_range = range(-3, 4)  # -3 through +3

        calendar = []
        for offset in day_range:
            day = center_date + timedelta(days=offset)
            day_avail = self._get_day_availability_state(
                profile, product, day,
                warehouse=warehouse, quantity=quantity,
                duration_value=duration_value,
                duration_unit=duration_unit,
                item_role=item_role, event_id=event_id,
            )

            day_entry = {
                'date': day.isoformat(),
                'date_display': day.strftime('%a %d/%m'),
                **day_avail,
            }

            if mode == 'hourly' and day_avail['has_selectable_slots']:
                day_entry['slots'] = self._get_slot_preview(
                    profile, product, day,
                    partner=partner, warehouse=warehouse,
                    pricelist=pricelist, quantity=quantity,
                    duration_value=duration_value,
                    duration_unit=duration_unit,
                    item_role=item_role, event_id=event_id,
                )
            else:
                day_entry['slots'] = []

            calendar.append(day_entry)

        return calendar

    # =================================================================
    # 7. Color helper
    # =================================================================

    @api.model
    def _get_dynamic_color_code(self, profile, dynamic_factor_percentage):
        """Return hex color based on profile thresholds."""
        return profile._get_dynamic_color(dynamic_factor_percentage)

    # =================================================================
    # 8. Opening hours check
    # =================================================================

    @api.model
    def _check_opening_hours(self, warehouse, start_dt, end_dt):
        """Check if a period falls within warehouse opening hours.

        :returns: 'open', 'closed', or 'unknown'
        """
        calendar = warehouse.opening_hours if warehouse else None
        if not calendar:
            return 'unknown'

        tz_name = calendar.tz or 'UTC'

        start_aware = (
            UTC.localize(start_dt) if start_dt.tzinfo is None else start_dt
        )
        end_check = end_dt or (start_dt + timedelta(hours=1))
        end_aware = (
            UTC.localize(end_check) if end_check.tzinfo is None else end_check
        )

        intervals = calendar._work_intervals_batch(start_aware, end_aware)
        work = intervals.get(False, [])
        return 'open' if work else 'closed'
