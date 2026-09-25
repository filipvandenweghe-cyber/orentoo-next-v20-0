import base64
import io
import json

from odoo import http, _
from odoo.http import request


class MultiChannelRentalKiosk(http.Controller):
    """Kiosk ticket lookup and print controller.

    Provides a public-facing kiosk page for customers to look up
    their dossier/order by number + email and print tickets.
    Reuses POS/ePOS printer configuration from the profile.
    """

    # ------------------------------------------------------------------
    # Language helpers
    # ------------------------------------------------------------------

    def _get_kiosk_lang(self, kw):
        """Get language code from URL param, validate against installed languages."""
        lang = kw.get('lang', '')
        if lang:
            installed = request.env['res.lang'].sudo().get_installed()
            valid_codes = [code for code, _ in installed]
            if lang in valid_codes:
                return lang
        return request.env.lang or 'en_US'

    def _get_ticket_lookup_translations(self):
        """Build translations dict for kiosk ticket lookup JS."""
        return {
            'please_enter_both': _('Please enter both fields.'),
            'not_found': _('Not found.'),
            'look_up': _('Look Up'),
            'error_prefix': _('Error: '),
            'customer': _('Customer'),
            'tickets': _('Tickets'),
            'total': _('Total'),
            'already_printed': _('Tickets have already been printed.'),
            'event': _('Event'),
            'no_printer': _('No printer configured.'),
            'print_tickets': _('Print Tickets'),
            'printed_success': _('Tickets printed successfully!'),
            'printed_check': _('Printed \u2713'),
            'print_error': _('Print error: '),
            'retry_print': _('Retry Print'),
            'printing': _('Printing...'),
            'https_warning': _('If using HTTPS, you may need to accept the printer certificate by visiting %s first.'),
        }

    # ------------------------------------------------------------------
    # Kiosk entry page
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/<int:profile_id>',
        type='http', auth='public', website=False,
        methods=['GET'], csrf=False,
    )
    def kiosk_page(self, profile_id, **kw):
        """Render the kiosk ticket lookup page."""
        lang = self._get_kiosk_lang(kw)
        request.env = request.env(context=dict(request.env.context, lang=lang))

        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists() or not profile.active:
            return request.not_found()

        if not profile.enable_ticket_lookup_printing:
            return request.not_found()

        # Collect printer config for the JS client
        printer_config = self._get_printer_config(profile)
        translations = self._get_ticket_lookup_translations()

        # Get available languages for the language selector
        installed_langs = request.env['res.lang'].sudo().get_installed()
        kiosk_langs = [
            {'code': code, 'name': name}
            for code, name in installed_langs
        ]

        return request.render(
            'multi_channel_rental_flow.kiosk_ticket_lookup', {
                'profile': profile,
                'printer_config': json.dumps(printer_config),
                'csrf_token': request.csrf_token(),
                'translations_json': json.dumps(translations),
                'current_lang': lang,
                'kiosk_langs': kiosk_langs,
            },
        )

    # ------------------------------------------------------------------
    # JSON API — lookup (reuses ticket_service)
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/lookup',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def kiosk_lookup(self, profile_id, identifier, email, **kw):
        """Look up a dossier and return the ticket payload."""
        profile = request.env['multi.channel.rental.profile'].sudo().browse(
            profile_id,
        )
        if not profile.exists() or not profile.enable_ticket_lookup_printing:
            return {'ok': False, 'message': _("Kiosk not available.")}

        svc = request.env['multi.channel.rental.ticket.service'].sudo()
        result = svc._find_dossier_for_print_lookup(identifier, email)

        if not result['ok']:
            return result

        dossier = result['dossier']
        payload = svc._get_print_payload_for_dossier(dossier)
        payload['already_printed'] = svc._are_tickets_printed(dossier)
        return payload

    # ------------------------------------------------------------------
    # JSON API — mark printed
    # ------------------------------------------------------------------

    @http.route(
        '/rental-kiosk/mark-printed',
        type='jsonrpc', auth='public', methods=['POST'],
    )
    def kiosk_mark_printed(self, profile_id, identifier, email, **kw):
        """Mark tickets as printed after kiosk print."""
        svc = request.env['multi.channel.rental.ticket.service'].sudo()
        result = svc._find_dossier_for_print_lookup(identifier, email)
        if not result['ok']:
            return result

        svc._mark_tickets_printed(result['dossier'])
        return {'ok': True, 'message': _("Tickets marked as printed.")}

    # ------------------------------------------------------------------
    # Printer config helper
    # ------------------------------------------------------------------

    def _get_printer_config(self, profile):
        """Extract printer configuration from the profile for JS."""
        config = {
            'mode': profile.printer_mode or 'none',
            'printer_type': '',
            'printer_ip': '',
            'proxy_ip': '',
            'epos_ticket_header': profile.epos_ticket_header or '',
            'epos_ticket_body': profile.epos_ticket_body or '',
            'epos_ticket_footer': profile.epos_ticket_footer or '',
            'epos_receipt_template': profile.epos_receipt_template or '',
            'logo_raster': '',
            'logo_width': 0,
            'logo_height': 0,
        }

        printer = profile.pos_printer_id
        if printer:
            # Odoo 20: pos.printer.epson_printer_ip -> printer_ip, and
            # proxy_ip (IoT box) no longer exists on pos.printer.
            config['printer_type'] = printer.printer_type or ''
            config['printer_ip'] = printer.printer_ip or ''

        # Pre-convert logo to monochrome raster for ePOS
        logo_data = profile.receipt_logo or profile.company_id.logo
        if logo_data:
            raster = self._image_to_epos_raster(logo_data)
            if raster:
                config['logo_raster'] = raster['data']
                config['logo_width'] = raster['width']
                config['logo_height'] = raster['height']

        return config

    def _image_to_epos_raster(self, image_b64, max_width=384):
        """Convert a base64 image to monochrome raster for Epson ePOS.

        Returns dict with 'data' (base64 raster), 'width', 'height'
        or None if conversion fails.
        """
        try:
            from PIL import Image
        except ImportError:
            return None

        try:
            img_bytes = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_bytes))

            # Convert to grayscale then monochrome (1-bit)
            img = img.convert('L')

            # Resize to fit thermal printer width (max 384px for 58mm)
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, int(img.height * ratio)),
                    Image.LANCZOS,
                )

            # Ensure width is multiple of 8 (ePOS raster requirement)
            w = img.width
            if w % 8 != 0:
                w = w + (8 - w % 8)
                new_img = Image.new('L', (w, img.height), 255)
                new_img.paste(img, (0, 0))
                img = new_img

            # Convert to 1-bit monochrome (dithering for better quality)
            img = img.convert('1')

            # Build raster: each pixel = 1 bit, row-by-row
            pixels = img.load()
            raster_bytes = bytearray()
            for y in range(img.height):
                for x in range(0, img.width, 8):
                    byte = 0
                    for bit in range(8):
                        if x + bit < img.width:
                            # PIL 1-bit: 0=black, 255=white
                            # ePOS: 1=black, 0=white
                            if pixels[x + bit, y] == 0:
                                byte |= (0x80 >> bit)
                    raster_bytes.append(byte)

            return {
                'data': base64.b64encode(raster_bytes).decode('ascii'),
                'width': img.width,
                'height': img.height,
            }
        except Exception:
            return None
