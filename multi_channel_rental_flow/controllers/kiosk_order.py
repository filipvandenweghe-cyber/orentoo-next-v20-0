import json
from datetime import date, datetime, timedelta

from odoo import http, _, fields
from odoo.http import request


class MultiChannelRentalKioskImages(http.Controller):
    """Public image endpoint for kiosk product images."""

    @http.route(
        '/rental-kiosk/image/<int:product_id>',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def product_image(self, product_id, **kw):
        product = request.env['product.product'].sudo().browse(product_id)
        if not product.exists() or not product.image_512:
            return request.not_found()
        import base64
        image_data = base64.b64decode(product.image_512)
        return request.make_response(image_data, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=86400'),
        ])

    @http.route(
        '/rental-kiosk/catimage/<string:model>/<int:record_id>',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def category_image(self, model, record_id, **kw):
        if model not in ('product.public.category', 'pos.category'):
            return request.not_found()
        record = request.env[model].sudo().browse(record_id)
        if not record.exists() or not record.image_512:
            return request.not_found()
        import base64
        image_data = base64.b64decode(record.image_512)
        return request.make_response(image_data, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=86400'),
        ])

    @http.route(
        '/rental-kiosk/hero/<int:image_id>',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def hero_image(self, image_id, **kw):
        """Serve a kiosk hero/carousel image."""
        record = request.env['multi.channel.rental.hero.image'].sudo().browse(
            image_id,
        )
        if not record.exists() or not record.image:
            return request.not_found()
        import base64
        image_data = base64.b64decode(record.image)
        return request.make_response(image_data, headers=[
            ('Content-Type', 'image/jpeg'),
            ('Cache-Control', 'public, max-age=86400'),
        ])

    @http.route(
        '/rental-kiosk/logo/<int:profile_id>',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def kiosk_logo(self, profile_id, **kw):
        """Serve the kiosk profile logo."""
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists() or not profile.kiosk_logo:
            return request.not_found()
        import base64
        image_data = base64.b64decode(profile.kiosk_logo)
        return request.make_response(image_data, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=3600'),
        ])

    @http.route(
        '/rental-kiosk/home-icon/<int:profile_id>',
        type='http', auth='public', methods=['GET'], csrf=False,
    )
    def kiosk_home_icon(self, profile_id, **kw):
        """Serve the kiosk home button icon."""
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists() or not profile.kiosk_home_icon:
            return request.not_found()
        import base64
        image_data = base64.b64decode(profile.kiosk_home_icon)
        return request.make_response(image_data, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=3600'),
        ])


class MultiChannelRentalKioskOrder(http.Controller):
    """Kiosk ordering flow controller.

    Provides the ordering page and JSON API for the full kiosk flow:
    product browsing, datepicker, basket, checkout and printing.
    All pricing is computed server-side — no JS price calculations.
    """

    # ------------------------------------------------------------------
    # Translations
    # ------------------------------------------------------------------

    def _get_kiosk_order_translations(self):
        """Build translations dict for kiosk order JS."""
        return {
            # Navigation & general
            'all': _('All'),
            'loading': _('Loading...'),
            'error_prefix': _('Error: '),
            # Products
            'no_products': _('No products available.'),
            'from': _('from'),
            # Calendar
            'mon': _('Mon'), 'tue': _('Tue'), 'wed': _('Wed'),
            'thu': _('Thu'), 'fri': _('Fri'), 'sat': _('Sat'), 'sun': _('Sun'),
            'selected': _('Selected: '),
            # Selection summary (detail page + basket/checkout)
            'summary_adding': _('You are adding'),
            'summary_product': _('Product'),
            'summary_event': _('Event'),
            'summary_date': _('Date'),
            'summary_start': _('Start time'),
            'summary_duration': _('Duration'),
            'select_timeslot_first': _('Please select a timeslot first.'),
            # Slots
            'no_slots': _('No slots available.'),
            'no_slots_date': _('No slots on this date.'),
            'no_more_availability': _('No more availability'),
            'available': _('available'),
            'seats': _('seats'),
            # Availability
            'not_enough_avail': _('Not enough availability.'),
            'avail_only': _('Only'),
            'avail_available_for': _('available for the selected timeslot, but'),
            'avail_requested': _('requested.'),
            # Events
            'no_events': _('No events available.'),
            # Basket
            'basket_empty': _('Your basket is empty.'),
            'subtotal': _('Subtotal'),
            'tax': _('Tax'),
            'total': _('Total'),
            # Customer info
            'email_required': _('Email address is required.'),
            'invalid_email': _('Please enter a valid email address.'),
            # Checkout
            'processing_payment': _('Processing payment...'),
            'payment_failed': _('Payment failed.'),
            'ticket_count': _('ticket(s)'),
            # Printing
            'printing_receipt': _('Printing receipt...'),
            'printing_tickets': _('Printing tickets...'),
            'receipt_tickets_printed': _('Receipt and tickets printed!'),
            'receipt_print_error': _('Receipt print error: '),
            'ticket_print_error': _('Ticket print error: '),
        }

    def _apply_lang(self, kw):
        """Set request language from the lang parameter if provided."""
        lang = kw.get('lang')
        if lang:
            installed = request.env['res.lang'].sudo().get_installed()
            valid_codes = [code for code, _ in installed]
            if lang in valid_codes:
                request.env = request.env(context=dict(
                    request.env.context, lang=lang))

    # ------------------------------------------------------------------
    # Kiosk ordering page
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/<int:profile_id>/order',
        type='http', auth='public', website=False,
        methods=['GET'], csrf=False,
    )
    def kiosk_order_page(self, profile_id, **kw):
        """Render the kiosk ordering page."""
        from odoo.addons.multi_channel_rental_flow.controllers.kiosk import (
            MultiChannelRentalKiosk,
        )
        kiosk_ctrl = MultiChannelRentalKiosk()
        lang = kiosk_ctrl._get_kiosk_lang(kw)
        request.env = request.env(context=dict(request.env.context, lang=lang))

        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists() or not profile.active:
            return request.not_found()
        if not profile.enable_dossier_ordering:
            return request.not_found()

        printer_config = kiosk_ctrl._get_printer_config(profile)
        translations = self._get_kiosk_order_translations()

        installed_langs = request.env['res.lang'].sudo().get_installed()
        kiosk_langs = [
            {'code': code, 'name': name}
            for code, name in installed_langs
        ]

        # Branding data
        hero_images = profile.kiosk_hero_image_ids.filtered('active').sorted('sequence')
        branding = {
            'primary_color': profile.kiosk_primary_color or '#0f3460',
            'success_color': profile.kiosk_success_color or '#28a745',
            'bg_color': profile.kiosk_bg_color or '#1a1a2e',
            'card_color': profile.kiosk_card_color or '#16213e',
            'welcome_title': profile.kiosk_welcome_title or '',
            'welcome_subtitle': profile.kiosk_welcome_subtitle or '',
            'hero_interval': profile.kiosk_hero_interval or 5,
        }

        return request.render(
            'multi_channel_rental_flow.kiosk_order_page', {
                'profile': profile,
                'printer_config': json.dumps(printer_config),
                'csrf_token': request.csrf_token(),
                'translations_json': json.dumps(translations),
                'current_lang': lang,
                'kiosk_langs': kiosk_langs,
                'branding': branding,
                'hero_images': hero_images,
            },
        )

    # ------------------------------------------------------------------
    # Products & categories
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/api/categories',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_categories(self, profile_id, **kw):
        """Return categories available for this profile."""
        self._apply_lang(kw)
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists():
            return {'categories': []}

        cats = []
        # eCommerce categories
        ecom_ids = profile.allowed_ecommerce_category_ids
        if not ecom_ids:
            # All categories that have products in the flow
            products = profile._get_available_products()
            ecom_ids = products.mapped('product_tmpl_id.public_categ_ids')

        for cat in ecom_ids.sorted('sequence'):
            cats.append({
                'id': cat.id,
                'name': cat.name,
                'type': 'ecommerce',
                'image_url': f'/rental-kiosk/catimage/product.public.category/{cat.id}' if cat.image_512 else '',
            })

        # POS categories (kiosk only)
        pos_ids = profile.allowed_pos_category_ids
        for cat in pos_ids.sorted('sequence'):
            cats.append({
                'id': cat.id,
                'name': cat.name,
                'type': 'pos',
                'image_url': f'/rental-kiosk/catimage/pos.category/{cat.id}' if cat.image_512 else '',
            })

        return {'categories': cats}

    @http.route(
        '/rental-kiosk/api/products',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_products(self, profile_id, category_id=None,
                     category_type=None, **kw):
        """Return products for the profile, optionally filtered by category."""
        self._apply_lang(kw)
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists():
            return {'products': []}

        products = profile._get_available_products()

        # Filter by category
        if category_id and category_type == 'ecommerce':
            products = products.filtered(
                lambda p: category_id in p.product_tmpl_id.public_categ_ids.ids
            )
        elif category_id and category_type == 'pos':
            products = products.filtered(
                lambda p: category_id in p.product_tmpl_id.pos_categ_ids.ids
            )

        svc = request.env['multi.channel.rental.service'].sudo()
        currency = (
            profile.pricelist_id.currency_id.name
            if profile.pricelist_id
            else profile.company_id.currency_id.name
        )

        company = profile.company_id
        currency_rec = (
            profile.pricelist_id.currency_id
            if profile.pricelist_id
            else company.currency_id
        )
        partner = profile.default_partner_id or request.env['res.partner'].sudo()

        result = []
        for p in products:
            min_price = p.lst_price
            has_from_price = False

            if p.multi_channel_item_role == 'rental' and p.requires_duration:
                # Rental: min price from coefficient table
                options = svc._get_duration_options(profile, p)
                if options:
                    prices = []
                    for opt in options:
                        coeff = opt.get('coefficient')
                        if coeff is not None:
                            prices.append(p.lst_price * coeff)
                        else:
                            prices.append(p.lst_price * opt.get('value', 1))
                    if prices:
                        min_price = min(prices)
                        has_from_price = True

            elif p.multi_channel_item_role == 'event_ticket':
                # Event: lowest available ticket price
                if 'event.event.ticket' in request.env:
                    tickets = request.env['event.event.ticket'].sudo().search([
                        ('product_id', '=', p.id),
                    ]).filtered(lambda t: t.event_id.event_registrations_open)
                    if tickets:
                        available_prices = [
                            t.price for t in tickets
                            if not t.is_sold_out
                        ]
                        if available_prices:
                            min_price = min(available_prices)
                            has_from_price = True

            # Apply taxes to get price incl. tax
            taxes = p.taxes_id.filtered(lambda t: t.company_id == company)
            if taxes:
                tax_res = taxes.compute_all(
                    min_price,
                    currency=currency_rec,
                    quantity=1,
                    product=p,
                    partner=partner,
                )
                display_price = tax_res['total_included']
                list_price_incl = taxes.compute_all(
                    p.lst_price, currency=currency_rec,
                    quantity=1, product=p, partner=partner,
                )['total_included']
            else:
                display_price = min_price
                list_price_incl = p.lst_price

            result.append({
                'id': p.id,
                'name': p.name,
                'list_price': list_price_incl,
                'min_price': display_price,
                'has_from_price': has_from_price,
                'item_role': p.multi_channel_item_role,
                'requires_timeslot': p.requires_timeslot,
                'requires_duration': p.requires_duration,
                'allow_quantity_selection': p.allow_quantity_selection,
                'use_image': p.use_product_image_in_flow,
                'image_url': f'/rental-kiosk/image/{p.id}' if p.image_512 else '',
                'currency': currency,
            })

        return {'products': result}

    # ------------------------------------------------------------------
    # Duration options & slots
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/api/durations',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_durations(self, profile_id, product_id, **kw):
        """Return duration options for a product."""
        self._apply_lang(kw)
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        product = request.env['product.product'].sudo().browse(product_id)
        svc = request.env['multi.channel.rental.service'].sudo()
        options = svc._get_duration_options(profile, product)
        return {'durations': options}

    @http.route(
        '/rental-kiosk/api/slots',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_slots(self, profile_id, product_id, date_str,
                  duration_value=None, duration_unit=None,
                  quantity=1, item_role='rental', event_id=None, **kw):
        self._apply_lang(kw)
        """Return available slots with prices for a date."""
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        product = request.env['product.product'].sudo().browse(product_id)
        svc = request.env['multi.channel.rental.service'].sudo()
        target_date = date.fromisoformat(date_str)

        slots = svc._get_slot_preview(
            profile, product, target_date,
            quantity=float(quantity),
            duration_value=int(duration_value) if duration_value else None,
            duration_unit=duration_unit,
            item_role=item_role,
            event_id=int(event_id) if event_id else None,
        )

        # Serialize datetimes
        for s in slots:
            for key in ('start_datetime', 'end_datetime'):
                if key in s and isinstance(s[key], datetime):
                    s[key] = s[key].isoformat()

        return {'slots': slots}

    @http.route(
        '/rental-kiosk/api/day-states',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_day_states(self, profile_id, product_id, center_date_str,
                       duration_value=None, duration_unit=None,
                       quantity=1, item_role='rental', event_id=None, **kw):
        """Return day availability states for a date range (calendar)."""
        self._apply_lang(kw)
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        product = request.env['product.product'].sudo().browse(product_id)
        svc = request.env['multi.channel.rental.service'].sudo()
        center = date.fromisoformat(center_date_str)

        days = []
        for offset in range(-17, 18):
            day = center + timedelta(days=offset)
            if day < date.today():
                days.append({
                    'date': day.isoformat(),
                    'day_state': 'closed',
                    'color_code': profile.closed_color or '#6c757d',
                })
                continue
            state = svc._get_day_availability_state(
                profile, product, day,
                quantity=float(quantity),
                duration_value=int(duration_value) if duration_value else None,
                duration_unit=duration_unit,
                item_role=item_role,
                event_id=int(event_id) if event_id else None,
            )
            days.append({
                'date': day.isoformat(),
                'day_state': state['day_state'],
                'color_code': state['color_code'],
                'available_slot_count': state.get('available_slot_count', 0),
            })

        return {'days': days}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/api/events',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def get_events(self, profile_id, product_id, **kw):
        """Return available events for an event ticket product."""
        self._apply_lang(kw)
        if 'event.event' not in request.env:
            return {'events': []}

        product = request.env['product.product'].sudo().browse(product_id)
        tickets = request.env['event.event.ticket'].sudo().search([
            ('product_id', '=', product.id),
        ])
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        company = profile.company_id
        currency = (
            profile.pricelist_id.currency_id
            if profile.pricelist_id
            else company.currency_id
        )

        events = []
        for ticket in tickets:
            ev = ticket.event_id
            if not ev.event_registrations_open:
                continue
            price = ticket.price
            taxes = product.taxes_id.filtered(lambda t: t.company_id == company)
            if taxes:
                tax_res = taxes.compute_all(
                    price, currency=currency, quantity=1, product=product,
                )
                price_incl = tax_res['total_included']
            else:
                price_incl = price
            events.append({
                'event_id': ev.id,
                'event_name': ev.name,
                'ticket_id': ticket.id,
                'ticket_name': ticket.name,
                'price': price_incl,
                'price_excl': price,
                'date_begin': ev.date_begin.isoformat() if ev.date_begin else '',
                'date_end': ev.date_end.isoformat() if ev.date_end else '',
                'seats_available': ev.seats_available,
                'is_multi_slots': ev.is_multi_slots,
            })

        return {'events': events}

    # ------------------------------------------------------------------
    # Basket / dossier management
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/api/basket/create',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_create(self, profile_id, **kw):
        """Create a new dossier for the kiosk order."""
        self._apply_lang(kw)
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        partner = profile.default_partner_id or request.env.ref(
            'base.public_partner', raise_if_not_found=False,
        )
        if not partner:
            partner = request.env['res.partner'].sudo().search([], limit=1)

        dossier = request.env['rental.dossier'].sudo().create({
            'partner_id': partner.id,
            'partner_email': partner.email or '',
            'warehouse_id': profile.warehouse_id.id,
            'pricelist_id': profile.pricelist_id.id if profile.pricelist_id else False,
            'profile_id': profile.id,
            'source': 'kiosk',
            'is_multi_channel': True,
        })

        return {
            'dossier_id': dossier.id,
            'dossier_name': dossier.name,
            'currency': dossier.currency_id.name or profile.company_id.currency_id.name,
        }

    @http.route(
        '/rental-kiosk/api/basket/add',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_add(self, dossier_id, product_id, item_role='rental',
                   quantity=1, start_datetime=None, end_datetime=None,
                   event_id=None, event_ticket_id=None,
                   event_slot_id=None, price_unit=0, **kw):
        """Add an item to the dossier basket."""
        self._apply_lang(kw)
        dossier = request.env['rental.dossier'].sudo().browse(dossier_id)
        if not dossier.exists():
            return {'ok': False, 'message': _('Dossier not found.')}

        product = request.env['product.product'].sudo().browse(product_id)
        slot = False

        # Guard: a rental that requires a timeslot must always carry one.
        # Without this, a race in the quantity buttons could add a slot-less,
        # base-priced line with no date/time to show in the basket.  [KO17]
        rejection = request.env['multi.channel.rental.service'].sudo(
        )._basket_add_rejection(
            product, item_role, start_datetime, end_datetime,
        )
        if rejection:
            return {'ok': False, 'message': rejection}

        # Create or find a slot for rental items with timeslots
        if start_datetime and end_datetime and item_role == 'rental':
            start_dt = datetime.fromisoformat(start_datetime)
            end_dt = datetime.fromisoformat(end_datetime)
            slot = request.env['rental.dossier.slot'].sudo().create({
                'dossier_id': dossier.id,
                'start_datetime': start_dt,
                'end_datetime': end_dt,
                'warehouse_id': dossier.warehouse_id.id,
            })

        item_vals = {
            'dossier_id': dossier.id,
            'slot_id': slot.id if slot else False,
            'product_id': product.id,
            'item_role': item_role,
            'quantity': float(quantity),
            'price_unit': float(price_unit),
        }

        if event_id:
            item_vals['event_id'] = int(event_id)
        if event_ticket_id:
            item_vals['event_ticket_id'] = int(event_ticket_id)
        if event_slot_id:
            item_vals['event_slot_id'] = int(event_slot_id)

        item = request.env['rental.dossier.item'].sudo().create(item_vals)

        return {
            'ok': True,
            'item_id': item.id,
            'dossier_name': dossier.name,
        }

    @http.route(
        '/rental-kiosk/api/basket/update-qty',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_update_qty(self, dossier_id, item_id, quantity, **kw):
        """Update the quantity of a basket item."""
        self._apply_lang(kw)
        item = request.env['rental.dossier.item'].sudo().browse(int(item_id))
        if not item.exists() or item.dossier_id.id != dossier_id:
            return {'ok': False, 'message': _('Item not found.')}

        qty = float(quantity)
        if qty <= 0:
            slot = item.slot_id
            item.unlink()
            if slot and not slot.item_ids:
                slot.unlink()
            return {'ok': True}

        # Update quantity and recalculate subtotal
        item.write({
            'quantity': qty,
            'price_subtotal': item.price_unit * qty,
        })
        return {'ok': True}

    @http.route(
        '/rental-kiosk/api/basket/remove',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_remove(self, dossier_id, item_id, **kw):
        """Remove an item from the dossier basket."""
        self._apply_lang(kw)
        item = request.env['rental.dossier.item'].sudo().browse(int(item_id))
        if item.exists() and item.dossier_id.id == dossier_id:
            slot = item.slot_id
            item.unlink()
            if slot and not slot.item_ids:
                slot.unlink()
        return {'ok': True}

    @http.route(
        '/rental-kiosk/api/basket/set-customer',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_set_customer(self, dossier_id, email, name=None, **kw):
        """Set customer email (and optionally name) on the dossier."""
        self._apply_lang(kw)
        dossier = request.env['rental.dossier'].sudo().browse(dossier_id)
        if not dossier.exists():
            return {'ok': False, 'message': _('Dossier not found.')}

        vals = {'partner_email': email.strip()}

        # Find or create partner
        Partner = request.env['res.partner'].sudo()
        partner = Partner.search([
            ('email', '=ilike', email.strip()),
        ], limit=1)
        if not partner:
            partner = Partner.create({
                'name': name or email.strip(),
                'email': email.strip(),
            })
        elif name and not partner.name:
            partner.name = name

        vals['partner_id'] = partner.id
        dossier.write(vals)

        return {'ok': True}

    @http.route(
        '/rental-kiosk/api/basket/get',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def basket_get(self, dossier_id, **kw):
        """Return the current basket contents."""
        self._apply_lang(kw)
        dossier = request.env['rental.dossier'].sudo().browse(dossier_id)
        if not dossier.exists():
            return {'ok': False, 'items': [], 'total': 0}

        items = []
        subtotal = 0.0
        tax_total = 0.0

        for item in dossier.item_ids.filtered(lambda i: i.state != 'cancelled'):
            item_subtotal = item.price_subtotal
            # Estimate tax from product's taxes
            item_tax = 0.0
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
                item_tax = tax_res['total_included'] - tax_res['total_excluded']
                item_total_incl = tax_res['total_included']
            else:
                item_total_incl = item_subtotal

            subtotal += item_subtotal
            tax_total += item_tax

            schedule = item._get_schedule_display()
            items.append({
                'id': item.id,
                'product_name': item.product_id.name,
                'item_role': item.item_role,
                'quantity': item.quantity,
                'price_unit': item.price_unit,
                'price_subtotal': item_subtotal,
                'price_total': item_total_incl,
                'slot_name': item.slot_id.name if item.slot_id else '',
                'event_name': item.event_id.name if item.event_id else '',
                # Labeled schedule for the basket / checkout display.
                'date_display': schedule['date_display'],
                'start_time_display': schedule['start_time_display'],
                'duration_display': schedule['duration_display'],
            })

        return {
            'ok': True,
            'dossier_name': dossier.name,
            'items': items,
            'subtotal': subtotal,
            'tax': tax_total,
            'total': subtotal + tax_total,
            'currency': dossier.currency_id.name,
        }

    # ------------------------------------------------------------------
    # Checkout & payment
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/api/checkout',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def checkout(self, dossier_id, **kw):
        """Prepare dossier for payment and simulate demo payment."""
        self._apply_lang(kw)
        dossier = request.env['rental.dossier'].sudo().browse(dossier_id)
        if not dossier.exists():
            return {'ok': False, 'message': _('Dossier not found.')}

        try:
            # Prepare (validate, generate orders, confirm, check stock)
            dossier.action_prepare_for_payment()

            # Start payment + simulate demo
            tx = dossier.action_start_payment()
            if tx.state == 'draft':
                tx.with_context(payment_safe_write=True)._set_done()
                tx._post_process_with_lock()

            # Get ticket payload for printing
            ticket_svc = request.env[
                'multi.channel.rental.ticket.service'
            ].sudo()
            payload = ticket_svc._get_print_payload_for_dossier(dossier)

            # Use linked order totals (includes taxes) after confirmation
            order_total = dossier.linked_order_total
            order_tax = sum(
                dossier.sale_order_ids.filtered(
                    lambda o: o.state != 'cancel'
                ).mapped('amount_tax')
            )
            order_untaxed = sum(
                dossier.sale_order_ids.filtered(
                    lambda o: o.state != 'cancel'
                ).mapped('amount_untaxed')
            )

            return {
                'ok': True,
                'state': dossier.state,
                'dossier_name': dossier.name,
                'tickets': payload.get('tickets', []),
                'ticket_count': payload.get('ticket_count', 0),
                'amount_untaxed': order_untaxed,
                'amount_tax': order_tax,
                'amount_total': order_total or dossier.amount_total,
                'currency': dossier.currency_id.name,
            }
        except Exception as e:
            return {
                'ok': False,
                'message': str(e),
                'state': dossier.state,
            }
