from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Business requirements: PF01–PF14
# See __init__.py for full index.


class RentalFlowProfile(models.Model):
    """Channel profile for kiosk, website or backend.  [PF01]"""

    _name = 'multi.channel.rental.profile'
    _description = 'Multi-Channel Rental Flow Profile'
    _order = 'sequence, name'
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Core identification
    # ------------------------------------------------------------------

    name = fields.Char(
        required=True,
        translate=True,
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
    )

    profile_type = fields.Selection(
        selection=[
            ('kiosk', "Kiosk"),
            ('website', "Website"),
            ('backend', "Backend"),
        ],
        required=True,
        default='kiosk',
    )

    # ------------------------------------------------------------------
    # Channel link
    # ------------------------------------------------------------------

    website_id = fields.Many2one(
        'website',
        string="Website",
        check_company=True,
        help="Website this profile applies to. Required for website profiles.",
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string="POS Configuration",
        check_company=True,
        help=(
            "Link to a POS configuration in kiosk mode. "
            "Reuses its printer settings, payment methods and access token."
        ),
    )

    # ------------------------------------------------------------------
    # Commerce settings
    # ------------------------------------------------------------------

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Warehouse",
        required=True,
        check_company=True,
        help="Warehouse used for stock availability and opening hours.",
    )
    pricelist_id = fields.Many2one(
        'product.pricelist',
        string="Pricelist",
        check_company=True,
    )
    default_partner_id = fields.Many2one(
        'res.partner',
        string="Default Customer",
        help=(
            "Default customer for kiosk orders (guest checkout). "
            "For website orders this is the logged-in user."
        ),
    )

    # ------------------------------------------------------------------
    # Product assortment — categories
    # ------------------------------------------------------------------

    allowed_ecommerce_category_ids = fields.Many2many(
        'product.public.category',
        'mcrf_profile_ecom_categ_rel',
        'profile_id',
        'categ_id',
        string="Allowed eCommerce Categories",
        help="Leave empty to allow all eCommerce categories.",
    )
    allowed_pos_category_ids = fields.Many2many(
        'pos.category',
        'mcrf_profile_pos_categ_rel',
        'profile_id',
        'categ_id',
        string="Allowed POS Categories",
        help="Leave empty to allow all POS categories. Used for kiosk profiles.",
    )

    # ------------------------------------------------------------------
    # Product assortment — explicit include / exclude
    # ------------------------------------------------------------------

    included_product_ids = fields.Many2many(
        'product.product',
        'mcrf_profile_included_product_rel',
        'profile_id',
        'product_id',
        string="Included Products",
        help="Products always included regardless of category filters.",
    )
    excluded_product_ids = fields.Many2many(
        'product.product',
        'mcrf_profile_excluded_product_rel',
        'profile_id',
        'product_id',
        string="Excluded Products",
        help="Products always excluded regardless of category or inclusion.",
    )

    # ------------------------------------------------------------------
    # Feature toggles
    # ------------------------------------------------------------------

    enable_rental_items = fields.Boolean(
        string="Enable Rental Items",
        default=True,
    )
    enable_event_tickets = fields.Boolean(
        string="Enable Event Tickets",
        default=False,
        help="Requires the event and event_sale modules to be installed.",
    )
    enable_addons = fields.Boolean(
        string="Enable Add-ons",
        default=True,
    )
    enable_services = fields.Boolean(
        string="Enable Services",
        default=False,
    )
    enable_dossier_ordering = fields.Boolean(
        string="Enable Dossier Ordering",
        default=True,
        help="Allow customers to create dossiers from this profile.",
    )
    enable_ticket_lookup_printing = fields.Boolean(
        string="Enable Ticket Lookup & Printing",
        default=True,
        help="Allow customers to look up and print tickets at the kiosk.",
    )

    # ------------------------------------------------------------------
    # Guest checkout
    # ------------------------------------------------------------------

    guest_checkout_mode = fields.Selection(
        selection=[
            ('login_required', "Login Required"),
            ('guest_with_email', "Guest with Email"),
            ('guest_minimal', "Guest (Minimal)"),
        ],
        string="Checkout Mode",
        default='login_required',
        help=(
            "Login Required: customer must log in before payment.\n"
            "Guest with Email: customer provides email, partner created on the fly.\n"
            "Guest (Minimal): no customer data required (typical for kiosk)."
        ),
    )

    # ------------------------------------------------------------------
    # Date / time / duration
    # ------------------------------------------------------------------

    rental_slot_interval_minutes = fields.Integer(
        string="Slot Interval (minutes)",
        default=30,
        help="Generate rental start times every X minutes within opening hours.",
    )
    slot_advance_days = fields.Integer(
        string="Advance Days",
        default=30,
        help="How many days ahead to show available timeslots.",
    )
    slot_end_limit_mode = fields.Selection(
        selection=[
            ('none', "Do not limit end time"),
            ('duration', "Limit end time by selected duration"),
            ('next_contingent', "Limit end time to next lower duration"),
        ],
        string="Latest Start Time Limit",
        default='none',
        required=True,
        help=(
            "How the latest selectable start time is limited relative to the "
            "warehouse closing time, based on the selected duration:\n"
            "• Do not limit end time: the last start slot is simply the last "
            "one within opening hours (a long rental may end after closing).\n"
            "• Limit end time by selected duration: the last start slot is "
            "closing time minus the selected duration (e.g. closes 18:00, "
            "8h selected → last start 10:00, so the rental ends at closing).\n"
            "• Limit end time to next lower duration: the last start slot is "
            "closing time minus the next shorter duration option (e.g. closes "
            "18:00, 8h selected with 4h as the next lower option → last start "
            "14:00). Falls back to the selected duration when none is lower."
        ),
    )
    default_duration_unit = fields.Selection(
        selection=[
            ('minute', "Minutes"),
            ('hour', "Hours"),
            ('day', "Days"),
            ('week', "Weeks"),
            ('month', "Months"),
        ],
        string="Default Duration Unit",
        default='hour',
    )
    fallback_duration_ids = fields.One2many(
        'multi.channel.rental.duration.option',
        'profile_id',
        string="Fallback Duration Options",
        help=(
            "Duration choices shown when no coefficient table applies. "
            "Leave empty to use 1, 2, 4, 8 hours as default."
        ),
    )

    # ------------------------------------------------------------------
    # Dynamic pricing color thresholds
    # ------------------------------------------------------------------

    low_dynamic_factor_threshold = fields.Float(
        string="Low Factor Threshold (%)",
        default=90.0,
        help="Dynamic factor below this value is shown in the low (discount) color.",
    )
    high_dynamic_factor_threshold = fields.Float(
        string="High Factor Threshold (%)",
        default=110.0,
        help="Dynamic factor above this value is shown in the high (peak) color.",
    )
    low_color = fields.Char(
        string="Low / Discount Color",
        default='#90EE90',
        help="Light green — indicates a discount period.",
    )
    normal_color = fields.Char(
        string="Normal Color",
        default='#28a745',
        help="Standard green — indicates normal pricing.",
    )
    high_color = fields.Char(
        string="High / Peak Color",
        default='#006400',
        help="Dark green — indicates a peak pricing period.",
    )
    unavailable_color = fields.Char(
        string="Unavailable Color",
        default='#dc3545',
        help="Red — no stock or event availability.",
    )
    closed_color = fields.Char(
        string="Closed / Outside Hours Color",
        default='#6c757d',
        help="Grey — outside warehouse opening hours.",
    )

    # ------------------------------------------------------------------
    # Payment
    # ------------------------------------------------------------------

    payment_provider_ids = fields.Many2many(
        'payment.provider',
        'mcrf_profile_payment_provider_rel',
        'profile_id',
        'provider_id',
        string="Payment Providers",
        help="Allowed payment providers for this profile. Leave empty for all.",
    )
    default_payment_provider_id = fields.Many2one(
        'payment.provider',
        string="Default Payment Provider",
    )
    allow_demo_payment = fields.Boolean(
        string="Allow Demo Payment Provider",
        default=True,
        help="Enable the Odoo demo payment provider for testing.",
    )
    payment_mode = fields.Selection(
        selection=[
            ('online_redirect', "Online Redirect"),
            ('terminal_iot', "Terminal / IoT"),
            ('pos_payment', "POS Payment Method"),
            ('both', "Online + Terminal"),
        ],
        string="Payment Mode",
        default='online_redirect',
        help=(
            "Online Redirect: standard Odoo payment flow (Mollie, Stripe, demo, etc.).\n"
            "Terminal / IoT: physical payment terminal via IoT Box.\n"
            "POS Payment Method: reuse POS payment method from linked POS config.\n"
            "Online + Terminal: allow both options."
        ),
    )
    payment_pending_timeout_minutes = fields.Integer(
        string="Payment Timeout (minutes)",
        default=15,
        help="Cancel pending dossiers after this many minutes without payment.",
    )

    # ------------------------------------------------------------------
    # Printer — reuses standard POS printer configuration
    # ------------------------------------------------------------------

    printer_mode = fields.Selection(
        selection=[
            ('pos_epos_ip', "Epson ePOS / IP Printer"),
            ('pos_iot_box', "IoT Box Printer"),
            ('browser', "Browser Print"),
            ('none', "No Printer"),
        ],
        string="Printer Mode",
        default='none',
    )
    pos_printer_id = fields.Many2one(
        'pos.printer',
        string="POS Printer",
        help=(
            "Select a POS printer record. For ePOS mode, the printer IP is read "
            "from this record. For IoT mode, the linked IoT device is used."
        ),
    )

    # Convenience computed fields — read from the linked POS printer
    printer_ip_display = fields.Char(
        string="Printer IP",
        compute='_compute_printer_info',
        help="IP address read from the linked POS printer or IoT device.",
    )
    printer_type_display = fields.Char(
        string="Printer Type",
        compute='_compute_printer_info',
    )

    print_receipt_on_payment = fields.Boolean(
        string="Print Receipt on Payment",
        default=True,
        help="Print a sales receipt immediately after successful payment.",
    )
    print_tickets_on_payment = fields.Boolean(
        string="Print Tickets on Payment",
        default=True,
        help="Print all tickets/vouchers immediately after successful payment.",
    )
    print_tickets_with_receipt = fields.Boolean(
        string="Print Tickets Together with Receipt",
        default=False,
        help="Combine tickets and receipt into a single print job.",
    )
    receipt_logo = fields.Image(
        string="Receipt Logo",
        help=(
            "Optional logo for ePOS receipt printing. "
            "If empty, the company logo is used. "
            "Max 512px wide recommended for thermal printers."
        ),
        max_width=512,
        max_height=512,
    )

    # ------------------------------------------------------------------
    # ePOS ticket template  [TK16]
    # ------------------------------------------------------------------

    epos_ticket_header = fields.Text(
        string="ePOS Ticket Header",
        default=(
            "{{ALIGN_CENTER}}\n"
            "{{LOGO}}\n"
            "{{FEED}}\n"
            "{{BOLD_ON}}{{SIZE_3}}TICKETS{{SIZE_1}}{{BOLD_OFF}}\n"
            "{{FEED}}\n"
            "{{dossier_name}}\n"
            "{{customer}}\n"
            "{{FEED}}{{FEED}}"
        ),
        help=(
            "Header printed before tickets on the ePOS printer.\n\n"
            "DATA CODES:\n"
            "  {{dossier_name}} — dossier number + description\n"
            "  {{customer}} — customer name\n"
            "  {{email}} — customer email\n"
            "  {{total}} — total amount with currency\n"
            "  {{payment_ref}} — payment reference\n\n"
            "TEXT FORMATTING:\n"
            "  {{BOLD_ON}} / {{BOLD_OFF}} — bold text\n"
            "  {{ALIGN_LEFT}} / {{ALIGN_CENTER}} / {{ALIGN_RIGHT}}\n"
            "  {{SIZE_1}} to {{SIZE_8}} — text size (1=normal, 2=double, etc.)\n"
            "  {{WIDE_1}} to {{WIDE_8}} — width only\n"
            "  {{TALL_1}} to {{TALL_8}} — height only\n"
            "  {{DOUBLE}} / {{DOUBLE_OFF}} — shortcut for size 2/1\n\n"
            "IMAGE:\n"
            "  {{LOGO}} — company logo (or profile receipt logo if set)\n\n"
            "QR & BARCODE:\n"
            "  {{QR:literal text}} — QR code with literal content\n"
            "  {{QR_dossier_name}} — QR code with field value\n"
            "  {{BARCODE:literal text}} — Code128 barcode\n"
            "  {{BARCODE_dossier_name}} — barcode with field value\n\n"
            "LAYOUT:\n"
            "  {{FEED}} — line feed / {{FEED_3}} — 3 line feeds\n"
            "  {{CUT}} — paper cut"
        ),
    )
    epos_ticket_body = fields.Text(
        string="ePOS Ticket Body",
        default=(
            "{{ALIGN_LEFT}}\n"
            "{{BOLD_ON}}{{SIZE_2}}#{{sequence}} {{print_title}}{{SIZE_1}}{{BOLD_OFF}}\n"
            "{{FEED}}\n"
            "{{print_subtitle}}\n"
            "{{quantity_line}}\n"
            "{{order_name}}\n"
            "{{FEED}}\n"
            "{{timeslot_line}}\n"
            "{{event_line}}\n"
            "{{barcode_line}}\n"
            "{{FEED}}\n"
            "{{ALIGN_CENTER}}\n"
            "{{QR:{{dossier_name}}}}\n"
            "{{FEED}}"
        ),
        help=(
            "Body printed for EACH ticket. Repeated per ticket.\n\n"
            "TICKET DATA CODES:\n"
            "  {{sequence}} — ticket number\n"
            "  {{print_title}} — main title\n"
            "  {{print_subtitle}} — subtitle (role)\n"
            "  {{product_name}} — product name\n"
            "  {{order_name}} — sale order reference\n"
            "  {{quantity}} — quantity number\n"
            "  {{quantity_line}} — 'x 2' (only if qty > 1)\n"
            "  {{timeslot_start}} / {{timeslot_end}} — datetimes\n"
            "  {{timeslot_line}} — full timeslot range\n"
            "  {{event_name}} / {{event_line}} — event info\n"
            "  {{event_slot}} — event slot name\n"
            "  {{attendee_name}} / {{attendee_email}}\n"
            "  {{registration_barcode}} / {{barcode_line}}\n\n"
            "QR CODE EXAMPLE:\n"
            "  {{QR:{{dossier_name}}}} — QR with dossier number\n"
            "  {{QR_registration_barcode}} — QR with barcode value\n\n"
            "All header formatting codes also work here."
        ),
    )
    epos_ticket_footer = fields.Text(
        string="ePOS Ticket Footer",
        default=(
            "{{ALIGN_CENTER}}\n"
            "{{BOLD_ON}}Total: {{total}}{{BOLD_OFF}}\n"
            "{{FEED}}{{FEED}}{{FEED}}\n"
            "{{CUT}}"
        ),
        help="Footer printed after all tickets.",
    )

    # ------------------------------------------------------------------
    # ePOS receipt template (sales summary)
    # ------------------------------------------------------------------

    epos_receipt_template = fields.Text(
        string="ePOS Receipt Template",
        default=(
            "{{ALIGN_CENTER}}\n"
            "{{LOGO}}\n"
            "{{FEED}}\n"
            "{{BOLD_ON}}{{SIZE_3}}RECEIPT{{SIZE_1}}{{BOLD_OFF}}\n"
            "{{FEED}}\n"
            "{{dossier_name}}\n"
            "{{customer}}\n"
            "{{email}}\n"
            "{{FEED}}\n"
            "{{ALIGN_LEFT}}\n"
            "{{ITEMS}}\n"
            "{{FEED}}\n"
            "{{TAXES}}\n"
            "{{FEED}}\n"
            "{{ALIGN_LEFT}}\n"
            "Subtotal: {{subtotal}}\n"
            "Tax: {{tax}}\n"
            "{{ALIGN_CENTER}}\n"
            "{{BOLD_ON}}{{SIZE_2}}Total: {{total}}{{SIZE_1}}{{BOLD_OFF}}\n"
            "{{FEED}}\n"
            "Payment ref: {{payment_ref}}\n"
            "{{FEED}}\n"
            "{{QR_dossier_name}}\n"
            "{{FEED}}\n"
            "Thank you for your order!\n"
            "{{FEED}}{{FEED}}{{FEED}}\n"
            "{{CUT}}"
        ),
        help=(
            "Sales receipt printed at the kiosk after payment.\n"
            "Printed BEFORE the individual tickets.\n\n"
            "Same codes as ticket templates, plus:\n"
            "  {{ITEMS}} — all items with qty × price\n"
            "  {{TAXES}} — per-tax breakdown (name, rate, base, amount)\n"
            "  {{subtotal}} — total excl. tax\n"
            "  {{tax}} — total tax amount\n"
            "  {{LINE}} — horizontal separator line"
        ),
    )

    # ------------------------------------------------------------------
    # Kiosk branding & appearance
    # ------------------------------------------------------------------

    kiosk_logo = fields.Image(
        string="Kiosk Logo",
        help="Logo displayed in the kiosk top bar. If empty, the profile name is shown.",
        max_width=512,
        max_height=128,
    )
    kiosk_home_icon = fields.Image(
        string="Home Button Icon",
        help="Small icon for the home button in the kiosk top bar. "
             "If empty, a default home icon is used. "
             "Recommended size: 64x64px.",
        max_width=128,
        max_height=128,
    )
    kiosk_welcome_title = fields.Char(
        string="Welcome Title",
        translate=True,
        help="Custom title on the kiosk start page. Leave empty for default 'Welcome'.",
    )
    kiosk_welcome_subtitle = fields.Char(
        string="Welcome Subtitle",
        translate=True,
        help="Custom subtitle on the kiosk start page.",
    )
    kiosk_primary_color = fields.Char(
        string="Primary Color",
        default='#0f3460',
        help="Main accent color for buttons and highlights (hex code).",
    )
    kiosk_success_color = fields.Char(
        string="Success Color",
        default='#28a745',
        help="Color for success states and 'Add to Basket' buttons (hex code).",
    )
    kiosk_bg_color = fields.Char(
        string="Background Color",
        default='#1a1a2e',
        help="Kiosk page background color (hex code).",
    )
    kiosk_card_color = fields.Char(
        string="Card Color",
        default='#16213e',
        help="Background color for cards and panels (hex code).",
    )
    kiosk_hero_image_ids = fields.One2many(
        'multi.channel.rental.hero.image',
        'profile_id',
        string="Hero Images",
        help="Images displayed in a rotating carousel on the kiosk start page.",
    )
    kiosk_hero_interval = fields.Integer(
        string="Carousel Interval (seconds)",
        default=5,
        help="Time between automatic image transitions. Set 0 to disable auto-rotation.",
    )

    # ------------------------------------------------------------------
    # Computed / helpers
    # ------------------------------------------------------------------

    kiosk_url = fields.Char(
        string="Kiosk Ticket Lookup URL",
        compute='_compute_kiosk_url',
        help="Public URL for the kiosk ticket lookup page.",
    )
    kiosk_order_url = fields.Char(
        string="Kiosk Order URL",
        compute='_compute_kiosk_url',
        help="Public URL for the kiosk ordering flow.",
    )

    def _compute_kiosk_url(self):
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '')
        for profile in self:
            if profile.id and isinstance(profile.id, int):
                profile.kiosk_url = f"{base}/rental-kiosk/{profile.id}"
                profile.kiosk_order_url = f"{base}/rental-kiosk/{profile.id}/order"
            else:
                profile.kiosk_url = ''
                profile.kiosk_order_url = ''

    available_product_count = fields.Integer(
        string="Available Products",
        compute='_compute_available_product_count',
    )

    @api.depends('profile_type', 'included_product_ids', 'excluded_product_ids',
                 'allowed_ecommerce_category_ids', 'allowed_pos_category_ids',
                 'enable_rental_items', 'enable_event_tickets',
                 'enable_addons', 'enable_services')
    def _compute_available_product_count(self):
        for profile in self:
            if profile.id and isinstance(profile.id, int):
                profile.available_product_count = len(
                    profile._get_available_products()
                )
            else:
                profile.available_product_count = 0

    def action_view_available_products(self):
        """Open a list of products available in this profile."""
        self.ensure_one()
        products = self._get_available_products()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Available Products — %s", self.name),
            'res_model': 'product.product',
            'view_mode': 'list,form',
            'domain': [('id', 'in', products.ids)],
            'context': {'default_use_in_multi_channel_rental_flow': True},
        }

    # Odoo 20 renamed pos.printer.epson_printer_ip to printer_ip and dropped
    # proxy_ip (IoT-box proxy printing): 'epson_epos' is now the only
    # printer_type, so the single printer_ip covers every case.
    @api.depends('pos_printer_id', 'pos_printer_id.printer_type',
                 'pos_printer_id.printer_ip')
    def _compute_printer_info(self):
        for profile in self:
            printer = profile.pos_printer_id
            if printer:
                profile.printer_type_display = dict(
                    printer._fields['printer_type'].selection
                ).get(printer.printer_type, '')
                profile.printer_ip_display = printer.printer_ip or ''
            else:
                profile.printer_type_display = ''
                profile.printer_ip_display = ''

    @api.onchange('profile_type')
    def _onchange_profile_type(self):
        """Set sensible defaults per profile type."""
        for profile in self:
            if profile.profile_type == 'kiosk':
                profile.guest_checkout_mode = 'guest_minimal'
                profile.payment_mode = 'online_redirect'
            elif profile.profile_type == 'website':
                profile.guest_checkout_mode = 'login_required'
                profile.payment_mode = 'online_redirect'
                profile.printer_mode = 'none'
            elif profile.profile_type == 'backend':
                profile.guest_checkout_mode = 'login_required'
                profile.payment_mode = 'online_redirect'
                profile.printer_mode = 'none'

    @api.onchange('printer_mode')
    def _onchange_printer_mode(self):
        """Clear printer link when mode changes to none/browser."""
        for profile in self:
            if profile.printer_mode in ('browser', 'none'):
                profile.pos_printer_id = False

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @api.constrains('profile_type', 'website_id')
    def _check_website_profile(self):
        for profile in self:
            if profile.profile_type == 'website' and not profile.website_id:
                raise ValidationError(
                    _("Website profiles require a linked website.")
                )

    @api.constrains('low_dynamic_factor_threshold',
                     'high_dynamic_factor_threshold')
    def _check_thresholds(self):
        for profile in self:
            if profile.low_dynamic_factor_threshold >= profile.high_dynamic_factor_threshold:
                raise ValidationError(
                    _("Low threshold must be less than high threshold.")
                )

    # ------------------------------------------------------------------
    # Product domain — effective product assortment for this profile
    # ------------------------------------------------------------------

    def _get_available_products(self, item_role=False):
        """Return product.product recordset available for this profile.

        :param item_role: optional role filter ('rental', 'event_ticket',
            'addon', 'service')
        :returns: product.product recordset
        """
        self.ensure_one()
        Product = self.env['product.product']

        # --- base domain: enabled + channel flag ---
        if self.profile_type == 'kiosk':
            domain = Product._mcrf_kiosk_domain()
        elif self.profile_type == 'website':
            domain = Product._mcrf_website_domain(website=self.website_id)
        else:
            # backend — all enabled products
            domain = [('use_in_multi_channel_rental_flow', '=', True)]

        # --- role filter ---
        if item_role:
            domain += Product._mcrf_role_domain(item_role)

        # --- role toggle filter ---
        enabled_roles = self._get_enabled_roles()
        if enabled_roles:
            domain += [('multi_channel_item_role', 'in', enabled_roles)]

        # --- category filter ---
        categ_domain = self._build_category_domain()

        # --- explicit inclusions ---
        included_ids = self.included_product_ids.ids

        if categ_domain or included_ids:
            # Products in allowed categories OR explicitly included
            combined = []
            if categ_domain and included_ids:
                combined = ['|'] + categ_domain + [('id', 'in', included_ids)]
            elif categ_domain:
                combined = categ_domain
            else:
                combined = [('id', 'in', included_ids)]
            domain += combined

        # --- explicit exclusions ---
        if self.excluded_product_ids:
            domain += [('id', 'not in', self.excluded_product_ids.ids)]

        return Product.search(domain)

    def _get_enabled_roles(self):
        """Return list of enabled item roles for this profile."""
        self.ensure_one()
        roles = []
        if self.enable_rental_items:
            roles.append('rental')
        if self.enable_event_tickets:
            roles.append('event_ticket')
        if self.enable_addons:
            roles.append('addon')
        if self.enable_services:
            roles.append('service')
        return roles

    def _build_category_domain(self):
        """Build a domain fragment filtering by allowed categories.

        Returns an empty list if no category restrictions are set (= all).
        """
        self.ensure_one()
        domains = []

        ecom_ids = self.allowed_ecommerce_category_ids.ids
        pos_ids = self.allowed_pos_category_ids.ids

        if ecom_ids and pos_ids:
            domains = [
                '|',
                ('product_tmpl_id.public_categ_ids', 'in', ecom_ids),
                ('product_tmpl_id.pos_categ_ids', 'in', pos_ids),
            ]
        elif ecom_ids:
            domains = [('product_tmpl_id.public_categ_ids', 'in', ecom_ids)]
        elif pos_ids:
            domains = [('product_tmpl_id.pos_categ_ids', 'in', pos_ids)]

        return domains

    def _get_dynamic_color(self, factor_percentage):
        """Return the color hex code for a given dynamic pricing factor.

        :param factor_percentage: float, e.g. 85.0, 100.0, 120.0
        :returns: hex color string
        """
        self.ensure_one()
        if factor_percentage < self.low_dynamic_factor_threshold:
            return self.low_color
        elif factor_percentage > self.high_dynamic_factor_threshold:
            return self.high_color
        return self.normal_color
