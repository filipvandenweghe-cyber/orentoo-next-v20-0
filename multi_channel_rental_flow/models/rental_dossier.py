import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Business requirements: DO01–DO14, PP01–PP10, PY01–PY10
# See __init__.py for full index.

DOSSIER_STATES = [
    ('draft', "Draft"),
    ('confirmed', "Confirmed"),
    ('payment_pending', "Payment Pending"),
    ('paid', "Paid"),
    ('done', "Done"),
    ('payment_failed', "Payment Failed"),
    ('payment_expired', "Payment Expired"),
    ('payment_exception', "Payment Exception"),
    ('cancelled', "Cancelled"),
]

DOSSIER_SOURCES = [
    ('backend', "Backend"),
    ('website', "Website"),
    ('kiosk', "Kiosk"),
]


class RentalDossier(models.Model):
    """Rental dossier: one customer, one currency, one payment.  [DO01]"""

    _name = 'rental.dossier'
    _description = 'Rental Dossier'
    _inherit = ['mail.thread', 'mail.activity.mixin']  # DO08
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    name = fields.Char(
        string="Dossier Number",
        required=True,
        copy=False,
        readonly=True,
        default='/',
        index='trigram',
    )
    description = fields.Char(
        string="Description",
        tracking=True,
        help="Short description to easily identify this dossier.",
    )
    display_name = fields.Char(
        compute='_compute_display_name',
    )
    active = fields.Boolean(default=True)
    is_multi_channel = fields.Boolean(
        string="Multi-Channel Flow",
        default=False,
        help=(
            "When enabled, shows slots, items, payment and "
            "multi-channel source configuration tabs. "
            "Automatically enabled for website and kiosk dossiers."
        ),
    )
    state = fields.Selection(
        selection=DOSSIER_STATES,
        string="Status",
        default='draft',
        required=True,
        tracking=True,
        copy=False,
    )
    source = fields.Selection(
        selection=DOSSIER_SOURCES,
        string="Source",
        default='backend',
        required=True,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Relations — customer, company, commerce
    # ------------------------------------------------------------------

    partner_id = fields.Many2one(
        'res.partner',
        string="Customer",
        tracking=True,
        index=True,
    )
    partner_email = fields.Char(
        string="Email",
        tracking=True,
        help="Customer email — used for ticket lookup and confirmation.",
    )
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Warehouse",
        check_company=True,
        tracking=True,
    )
    pricelist_id = fields.Many2one(
        'product.pricelist',
        string="Pricelist",
        check_company=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string="Currency",
        compute='_compute_currency_id',
        store=True,
        precompute=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string="Salesperson",
        default=lambda self: self.env.user,
        tracking=True,
    )
    profile_id = fields.Many2one(
        'multi.channel.rental.profile',
        string="Profile",
        check_company=True,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Content — slots and items
    # ------------------------------------------------------------------

    slot_ids = fields.One2many(
        'rental.dossier.slot',
        'dossier_id',
        string="Slots",
        copy=True,
    )
    item_ids = fields.One2many(
        'rental.dossier.item',
        'dossier_id',
        string="Items",
        copy=True,
    )
    slot_count = fields.Integer(
        compute='_compute_counts',
    )
    item_count = fields.Integer(
        compute='_compute_counts',
    )

    # ------------------------------------------------------------------
    # Linked records
    # ------------------------------------------------------------------

    sale_order_ids = fields.Many2many(
        'sale.order',
        'mcrf_dossier_sale_order_rel',
        'dossier_id',
        'order_id',
        string="Sale Orders",
    )
    sale_order_count = fields.Integer(
        compute='_compute_counts',
    )

    transaction_ids = fields.One2many(
        'payment.transaction',
        'mcrf_dossier_id',
        string="Payment Transactions",
    )
    transaction_count = fields.Integer(
        compute='_compute_counts',
    )

    # Event registrations — soft dependency
    registration_count = fields.Integer(
        compute='_compute_registration_count',
    )

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------

    amount_untaxed = fields.Monetary(
        string="Untaxed Amount",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    amount_tax = fields.Monetary(
        string="Taxes",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    amount_total = fields.Monetary(
        string="Total",
        compute='_compute_amounts',
        store=True,
        currency_field='currency_id',
    )
    linked_order_total = fields.Monetary(
        string="Linked Orders Total",
        compute='_compute_linked_order_total',
        currency_field='currency_id',
        help="Sum of totals from all linked sale/rental orders.",
    )

    # ------------------------------------------------------------------
    # Payment  [PY01–PY10]
    # ------------------------------------------------------------------

    last_transaction_id = fields.Many2one(
        'payment.transaction',
        string="Last Transaction",
        compute='_compute_last_transaction_id',
        store=True,
    )
    payment_state = fields.Selection(
        related='last_transaction_id.state',
        string="Payment State",
        store=True,
    )
    payment_reference = fields.Char(
        string="Payment Reference",
        copy=False,
        help="Reference of the last payment transaction.",
    )
    payment_pending_until = fields.Datetime(
        string="Payment Deadline",
        help="Auto-expire dossier if payment not received by this time.",
    )

    # ------------------------------------------------------------------
    # Confirmation email  [CE01–CE07]
    # ------------------------------------------------------------------

    confirmation_sent = fields.Boolean(
        string="Confirmation Sent",
        copy=False,
        help="True when the confirmation email has been sent.",
    )
    confirmation_sent_at = fields.Datetime(
        string="Confirmation Sent At",
        copy=False,
    )

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    note = fields.Html(string="Customer Note")
    internal_note = fields.Html(string="Internal Note")

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('name', 'description')
    def _compute_display_name(self):
        for dossier in self:
            if dossier.description:
                dossier.display_name = f"{dossier.name} — {dossier.description}"
            else:
                dossier.display_name = dossier.name or ''

    @api.depends('transaction_ids', 'transaction_ids.state')
    def _compute_last_transaction_id(self):
        for dossier in self:
            txs = dossier.transaction_ids.sorted('create_date', reverse=True)
            dossier.last_transaction_id = txs[:1] if txs else False

    @api.depends('pricelist_id', 'company_id')
    def _compute_currency_id(self):
        for dossier in self:
            dossier.currency_id = (
                dossier.pricelist_id.currency_id
                or dossier.company_id.currency_id
            )

    @api.depends('slot_ids', 'item_ids', 'sale_order_ids', 'transaction_ids')
    def _compute_counts(self):
        for dossier in self:
            dossier.slot_count = len(dossier.slot_ids)
            dossier.item_count = len(dossier.item_ids)
            dossier.sale_order_count = len(dossier.sale_order_ids)
            dossier.transaction_count = len(dossier.transaction_ids)

    def _compute_registration_count(self):
        """Count event registrations linked to this dossier."""
        for dossier in self:
            # New (in-memory) dossiers have a NewId and no persisted
            # registrations yet — skip the search to avoid a domain
            # against a NewId value.
            if 'event.registration' in self.env and isinstance(dossier.id, int):
                dossier.registration_count = self.env[
                    'event.registration'
                ].search_count([
                    ('mcrf_dossier_id', '=', dossier.id),
                ])
            else:
                dossier.registration_count = 0

    @api.depends('item_ids.price_subtotal', 'item_ids.state')
    def _compute_amounts(self):
        for dossier in self:
            items = dossier.item_ids.filtered(
                lambda i: i.state != 'cancelled'
            )
            dossier.amount_untaxed = sum(items.mapped('price_subtotal'))
            # Tax computation will be refined when order generation is
            # implemented — for now, tax = 0, total = untaxed.
            dossier.amount_tax = 0.0
            dossier.amount_total = dossier.amount_untaxed

    @api.depends('sale_order_ids', 'sale_order_ids.amount_total')
    def _compute_linked_order_total(self):
        for dossier in self:
            dossier.linked_order_total = sum(
                dossier.sale_order_ids.mapped('amount_total')
            )

    # ------------------------------------------------------------------
    # Onchanges
    # ------------------------------------------------------------------

    @api.onchange('profile_id')
    def _onchange_profile_id(self):
        if self.profile_id:
            if not self.warehouse_id:
                self.warehouse_id = self.profile_id.warehouse_id
            if not self.pricelist_id:
                self.pricelist_id = self.profile_id.pricelist_id
            if not self.source or self.source == 'backend':
                self.source = self.profile_id.profile_type
            self.is_multi_channel = True

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            if not self.partner_email:
                self.partner_email = self.partner_id.email

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rental.dossier'
                ) or '/'
            # Auto-enable multi-channel for website/kiosk dossiers
            if vals.get('source') in ('website', 'kiosk'):
                vals.setdefault('is_multi_channel', True)
        return super().create(vals_list)

    def copy(self, default=None):
        default = dict(default or {})
        default.setdefault('name', '/')
        return super().copy(default)

    # ------------------------------------------------------------------
    # Validation — called before confirmation
    # ------------------------------------------------------------------

    def _validate_for_confirmation(self):
        """Check dossier data is complete enough to confirm.

        Raises UserError with all issues collected.
        """
        self.ensure_one()
        errors = []

        if not self.partner_id:
            errors.append(_("Customer is required."))

        if self.source in ('website', 'kiosk') and not self.partner_email:
            errors.append(
                _("Customer email is required for %s dossiers.", self.source)
            )

        if self.source in ('website', 'kiosk') and not self.profile_id:
            errors.append(
                _("Profile is required for %s dossiers.", self.source)
            )

        # Validate items — warehouse required for rental items
        rental_items = self.item_ids.filtered(
            lambda i: i.item_role == 'rental' and i.state != 'cancelled'
        )
        if rental_items and not self.warehouse_id:
            # Check if all rental items have a slot with a warehouse
            items_without_wh = rental_items.filtered(
                lambda i: not i.slot_id or not i.slot_id.warehouse_id
            )
            if items_without_wh:
                errors.append(
                    _("Warehouse is required on the dossier or on each "
                      "slot that contains rental items.")
                )

        for item in self.item_ids.filtered(lambda i: i.state != 'cancelled'):
            if item.quantity <= 0:
                errors.append(
                    _("Quantity must be positive for item '%(product)s'.",
                      product=item.product_id.display_name)
                )
            if item.item_role == 'event_ticket' and 'event.event' in self.env:
                if not item.event_id:
                    errors.append(
                        _("Event is required for event ticket item '%(product)s'.",
                          product=item.product_id.display_name)
                    )
                elif not item.event_ticket_id:
                    errors.append(
                        _("Ticket type is required for event ticket item '%(product)s'.",
                          product=item.product_id.display_name)
                    )
                elif item.event_id.is_multi_slots and not item.event_slot_id:
                    errors.append(
                        _("Event slot is required for '%(product)s' because "
                          "'%(event)s' has multiple time slots.",
                          product=item.product_id.display_name,
                          event=item.event_id.name)
                    )

        # Validate slots
        for slot in self.slot_ids:
            if not slot.start_datetime or not slot.end_datetime:
                errors.append(
                    _("Slot '%(slot)s' must have start and end times.",
                      slot=slot.display_name)
                )

        if errors:
            raise UserError('\n'.join(errors))

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def action_confirm(self):
        """Validate and confirm the dossier."""
        for dossier in self:
            if dossier.state != 'draft':
                continue
            dossier._validate_for_confirmation()
            dossier.write({'state': 'confirmed'})

    def action_generate_orders(self):
        """Generate draft sale/rental orders from dossier slots and items.

        Can be called on draft or confirmed dossiers.
        Does not confirm the generated orders.
        """
        svc = self.env['multi.channel.rental.order.service']
        for dossier in self:
            if dossier.state in ('done', 'cancelled'):
                continue
            svc._generate_sale_orders(dossier)
        return True

    def action_prepare_for_payment(self):
        """Prepare dossier for payment: validate, check availability,
        generate and confirm orders, then set payment_pending.

        All-or-nothing: if any step fails, no orders stay confirmed
        and payment is not started.
        """
        svc = self.env['multi.channel.rental.payment.prep']
        for dossier in self:
            svc._prepare_for_payment(dossier)
        return True

    def action_request_payment(self):
        """Move to payment pending. Called by prepare_for_payment or
        manually for dossiers that skip the availability flow."""
        self.filtered(lambda d: d.state == 'confirmed').write({
            'state': 'payment_pending',
        })

    def action_mark_paid(self):
        """Mark as paid. Called by payment post-processing later."""
        self.filtered(
            lambda d: d.state in ('payment_pending', 'confirmed')
        ).write({
            'state': 'paid',
        })

    def action_done(self):
        """Administrative closure."""
        self.filtered(lambda d: d.state == 'paid').write({
            'state': 'done',
        })

    def action_cancel(self):
        """Cancel the dossier, its items/slots, and generated orders."""
        svc = self.env['multi.channel.rental.order.service']
        cancellable = self.filtered(
            lambda d: d.state not in ('done', 'cancelled')
        )
        for dossier in cancellable:
            # Cancel generated sale orders
            svc._cancel_generated_orders(dossier)
            dossier.item_ids.filtered(
                lambda i: i.state not in ('confirmed', 'cancelled')
            ).write({'state': 'cancelled'})
            dossier.slot_ids.filtered(
                lambda s: s.state not in ('confirmed', 'cancelled')
            ).write({'state': 'cancelled'})
        cancellable.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        """Reset a cancelled dossier to draft.

        Also resets cancelled items/slots that were not previously confirmed.
        """
        cancelled = self.filtered(lambda d: d.state == 'cancelled')
        for dossier in cancelled:
            dossier.item_ids.filtered(
                lambda i: i.state == 'cancelled'
            ).write({'state': 'draft'})
            dossier.slot_ids.filtered(
                lambda s: s.state == 'cancelled'
            ).write({'state': 'draft'})
        cancelled.write({'state': 'draft'})

    def action_recompute_totals(self):
        """Force recomputation of totals from items and linked orders."""
        self.item_ids._compute_price_subtotal()
        self._compute_amounts()
        self._compute_linked_order_total()

    # ------------------------------------------------------------------
    # Payment orchestration  [PY01–PY10]
    # ------------------------------------------------------------------

    def action_start_payment(self, provider_id=None, payment_method_id=None):
        """Create a payment transaction and return the processing values.

        1. Ensures dossier is prepared (orders generated + confirmed).
        2. Creates a payment.transaction for the dossier total.
        3. Links the transaction to all generated sale orders.
        4. Sets payment_pending_until.
        5. Returns transaction processing values for redirect/inline flow.
        """
        self.ensure_one()

        # Idempotency: if already payment_pending with a pending tx, return it
        if self.state == 'payment_pending' and self.last_transaction_id:
            tx = self.last_transaction_id
            if tx.state == 'draft':
                return tx

        # Step 1: prepare (validate + generate + confirm orders)
        if self.state in ('draft', 'confirmed'):
            self.action_prepare_for_payment()

        if self.state not in ('payment_pending',):
            raise UserError(
                _("Dossier %s is not ready for payment (state: %s).",
                  self.name, self.state)
            )

        # Step 2: resolve provider
        provider = self._resolve_payment_provider(provider_id)
        self._ensure_provider_journal(provider)

        # Step 3: resolve payment method
        pay_method = self._resolve_payment_method(
            provider, payment_method_id,
        )

        # Step 4: compute amount from confirmed generated orders
        amount = self.linked_order_total or self.amount_total
        if amount <= 0:
            raise UserError(_("Dossier total must be positive for payment."))

        # Step 5: create transaction
        tx = self._create_payment_transaction(
            provider, pay_method, amount,
        )

        # Step 6: set deadline
        timeout = 15  # default minutes
        if self.profile_id:
            timeout = self.profile_id.payment_pending_timeout_minutes or 15
        self.write({
            'payment_pending_until': fields.Datetime.add(
                fields.Datetime.now(), minutes=timeout,
            ),
            'payment_reference': tx.reference,
        })

        return tx

    def action_simulate_demo_payment(self):
        """Simulate a successful demo payment for backend testing.

        Creates a transaction (if needed), sets it to done, and
        triggers post-processing — just as if the customer completed
        the payment on the demo provider's hosted page.

        Odoo 20 forbids writing on payment.transaction unless the context
        carries ``payment_safe_write``; forcing a demo payment done is
        exactly such a deliberate, rollback-tolerant write.  Post-processing
        must go through ``_post_process_with_lock()``, which locks the
        transaction, runs ``_post_process()`` once and sets that context.
        """
        self.ensure_one()
        tx = self.action_start_payment()

        if tx.state == 'draft':
            tx.with_context(payment_safe_write=True)._set_done()
            tx._post_process_with_lock()
        elif tx.state in ('pending', 'authorized'):
            tx.with_context(payment_safe_write=True)._set_done()
            tx._post_process_with_lock()

        return True

    def _resolve_payment_provider(self, provider_id=None):
        """Find the payment provider to use."""
        if provider_id:
            return self.env['payment.provider'].browse(provider_id)

        # Profile default
        if self.profile_id and self.profile_id.default_payment_provider_id:
            return self.profile_id.default_payment_provider_id

        # Demo provider for testing.  Odoo 20 dropped
        # payment.provider.state (disabled/test/enabled): a provider is now
        # enabled when it is active (archived == disabled) and 'is_live'
        # tells test mode from live mode.  Archived records are already
        # excluded by the default search, so no explicit condition is needed.
        if self.profile_id and self.profile_id.allow_demo_payment:
            demo = self.env['payment.provider'].search(
                [('code', '=', 'demo')], limit=1,
            )
            if demo:
                return demo

        # Any enabled provider
        provider = self.env['payment.provider'].search([], limit=1)
        if not provider:
            raise UserError(_("No payment provider configured."))
        return provider

    def _ensure_provider_journal(self, provider):
        """Guarantee the provider posts payments to a bank journal.

        Odoo only auto-assigns ``payment.provider.journal_id`` when a bank
        journal already exists for the provider's company at the moment the
        provider is enabled. If none was available (or the provider was
        enabled via a raw ``write``), ``journal_id`` stays empty and
        ``payment.transaction._create_payment`` would insert an
        ``account.payment`` with ``journal_id = NULL`` — violating the
        not-null constraint. Assign a bank journal here so the payment can
        be posted. [PY03]
        """
        if not provider or provider.journal_id:
            return

        company = provider.company_id or self.company_id or self.env.company
        journal = self.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', company.id)],
            limit=1,
        )
        if not journal:
            # No bank journal exists for this company (e.g. a demo/test
            # company without an accounting localization). Create a minimal
            # one so the payment can be posted. account.journal.create()
            # auto-provisions the default account even when no chart template
            # is installed, so this is safe. [PY03]
            journal = self.env['account.journal'].sudo().with_company(
                company
            ).create({
                'name': _("Bank"),
                'type': 'bank',
                'code': 'BNK1',
                'company_id': company.id,
            })
        # Writing journal_id triggers _ensure_payment_method_line(), which
        # creates the account.payment.method.line the payment needs.
        provider.sudo().journal_id = journal

    def _resolve_payment_method(self, provider, payment_method_id=None):
        """Find a compatible payment method for the provider."""
        if payment_method_id:
            return self.env['payment.method'].browse(payment_method_id)
        methods = provider.payment_method_ids
        if not methods:
            # Search for any method compatible with this provider
            methods = self.env['payment.method'].search(
                [('provider_ids', 'in', provider.id)], limit=1,
            )
        if not methods:
            raise UserError(
                _("No payment method available for provider '%s'.",
                  provider.name)
            )
        return methods[:1]

    def _create_payment_transaction(self, provider, payment_method, amount):
        """Create a payment.transaction linked to this dossier."""
        self.ensure_one()

        # Build reference from dossier name
        reference = self.env['payment.transaction']._compute_reference(
            provider.code,
            prefix=self.name,
        )

        tx_vals = {
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': reference,
            'amount': amount,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'mcrf_dossier_id': self.id,
        }

        # Link to all generated sale orders
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', self.id),
            ('state', '!=', 'cancel'),
        ])
        if generated:
            tx_vals['sale_order_ids'] = [(6, 0, generated.ids)]

        tx = self.env['payment.transaction'].create(tx_vals)
        return tx

    def action_payment_success(self, transaction=None):
        """Handle successful payment.  [PY05]

        Idempotent: safe to call multiple times.
        """
        self.ensure_one()
        if self.state == 'paid':
            return  # already processed

        if self.state not in ('payment_pending', 'confirmed'):
            return

        self.write({
            'state': 'paid',
            'payment_reference': (
                transaction.reference if transaction
                else self.payment_reference
            ),
        })

        # Mark items/slots as confirmed if not already
        self.item_ids.filtered(
            lambda i: i.state in ('generated', 'draft', 'priced')
        ).write({'state': 'confirmed'})
        self.slot_ids.filtered(
            lambda s: s.state in ('order_created', 'draft')
        ).write({'state': 'confirmed'})

        # Link event registrations back to dossier  [EV06]
        self._link_event_registrations()

        # Website: trigger confirmation email hook
        if self.source == 'website':
            self._send_confirmation_email()

        self.message_post(
            body=_("Payment received. Dossier marked as paid."),
        )

    def action_payment_failed(self, transaction=None):
        """Handle failed payment.  [PY06]

        Cancels all generated orders and sets state to payment_failed.
        Idempotent.
        """
        self.ensure_one()
        if self.state in ('paid', 'done', 'payment_failed'):
            return

        order_svc = self.env['multi.channel.rental.order.service']
        order_svc._cancel_generated_orders(self)

        self.item_ids.filtered(
            lambda i: i.state not in ('cancelled',)
        ).write({'state': 'cancelled'})
        self.slot_ids.filtered(
            lambda s: s.state not in ('cancelled',)
        ).write({'state': 'cancelled'})

        self.write({'state': 'payment_failed'})
        self.message_post(
            body=_("Payment failed. Generated orders cancelled."),
        )

    def action_payment_expired(self):
        """Handle payment timeout/expiry.  [PY07]

        Same as payment_failed but with a different state.
        Idempotent.
        """
        self.ensure_one()
        if self.state in ('paid', 'done', 'payment_expired'):
            return

        order_svc = self.env['multi.channel.rental.order.service']
        order_svc._cancel_generated_orders(self)

        self.item_ids.filtered(
            lambda i: i.state not in ('cancelled',)
        ).write({'state': 'cancelled'})
        self.slot_ids.filtered(
            lambda s: s.state not in ('cancelled',)
        ).write({'state': 'cancelled'})

        self.write({'state': 'payment_expired'})
        self.message_post(
            body=_("Payment deadline expired. Generated orders cancelled."),
        )

    # ------------------------------------------------------------------
    # Cron  [PY08]
    # ------------------------------------------------------------------

    @api.model
    def _cron_expire_pending_dossiers(self):
        """Expire dossiers whose payment deadline has passed."""
        expired = self.search([
            ('state', '=', 'payment_pending'),
            ('payment_pending_until', '!=', False),
            ('payment_pending_until', '<', fields.Datetime.now()),
        ])
        for dossier in expired:
            try:
                dossier.action_payment_expired()
            except Exception as e:
                _logger.warning(
                    "Failed to expire dossier %s: %s", dossier.name, e,
                )

    def _send_confirmation_email(self):
        """Send confirmation email for website dossiers.  [CE01]

        Uses the mail.template defined in data/mail_template_data.xml.
        Idempotent: skips if already sent (unless force=True via resend).
        """
        self.ensure_one()
        if self.confirmation_sent:
            return  # CE06: no duplicate emails

        if not self.partner_email and not self.partner_id.email:
            _logger.warning(
                "Cannot send confirmation for %s: no email address.",
                self.name,
            )
            return

        template = self.env.ref(
            'multi_channel_rental_flow.email_template_dossier_confirmation',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)
        else:
            # Fallback: post summary in chatter
            self._post_confirmation_summary()

        self.write({
            'confirmation_sent': True,
            'confirmation_sent_at': fields.Datetime.now(),
        })
        self.message_post(
            body=_("Confirmation email sent to %s.",
                   self.partner_email or self.partner_id.email),
        )

    def action_resend_confirmation_email(self):
        """Manually resend the confirmation email.  [CE07]"""
        self.ensure_one()
        # Reset sent flag to allow resend
        self.confirmation_sent = False
        self._send_confirmation_email()

    def _post_confirmation_summary(self):
        """Post a confirmation summary in chatter as fallback."""
        self.ensure_one()
        body = self._build_confirmation_body()
        self.message_post(
            body=body,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
            partner_ids=self.partner_id.ids if self.partner_id else [],
        )

    def _build_confirmation_body(self):
        """Build the HTML body for the confirmation email/message."""
        self.ensure_one()
        lines = []
        lines.append(_(
            "<p>Dear %(name)s,</p>",
            name=self.partner_id.name or _("Customer"),
        ))
        lines.append(_(
            "<p>Your booking <strong>%(dossier)s</strong> has been "
            "confirmed and paid.</p>",
            dossier=self.display_name,
        ))

        # Linked orders
        if self.sale_order_ids:
            lines.append(_("<p><strong>Order references:</strong></p><ul>"))
            for order in self.sale_order_ids:
                detail = order.name
                if order.is_rental_order and order.rental_start_date:
                    start = fields.Datetime.context_timestamp(
                        self, order.rental_start_date,
                    )
                    end = fields.Datetime.context_timestamp(
                        self, order.rental_return_date,
                    ) if order.rental_return_date else None
                    detail += _(
                        " — Rental %(start)s",
                        start=start.strftime('%d/%m/%Y %H:%M'),
                    )
                    if end:
                        detail += _(" to %(end)s", end=end.strftime('%H:%M'))
                lines.append(f"<li>{detail}</li>")
            lines.append("</ul>")

        # Items summary
        active_items = self.item_ids.filtered(
            lambda i: i.state != 'cancelled'
        )
        if active_items:
            lines.append(_("<p><strong>Items:</strong></p><ul>"))
            for item in active_items:
                desc = f"{item.product_id.display_name} × {item.quantity}"
                if item.item_role == 'event_ticket' and item.event_id:
                    desc += _(
                        " — Event: %(event)s",
                        event=item.event_id.name,
                    )
                    if item.event_slot_id:
                        desc += _(
                            " (%(slot)s)",
                            slot=item.event_slot_id.display_name,
                        )
                elif item.slot_id:
                    desc += _(
                        " — %(slot)s",
                        slot=item.slot_id.name,
                    )
                lines.append(f"<li>{desc}</li>")
            lines.append("</ul>")

        # Total
        lines.append(_(
            "<p><strong>Total paid:</strong> %(amount)s</p>",
            amount=f"{self.amount_total:.2f} {self.currency_id.name}",
        ))

        # Lookup instructions
        lines.append(_(
            "<hr/>"
            "<p><strong>Retrieve your tickets at the kiosk:</strong></p>"
            "<p>Enter your dossier number <strong>%(dossier)s</strong> "
            "and your email address <strong>%(email)s</strong> at any "
            "kiosk terminal to print your tickets.</p>",
            dossier=self.name,
            email=self.partner_email or self.partner_id.email or '',
        ))

        # Explanation
        lines.append(_(
            "<p><em>This dossier groups all your bookings, rental orders "
            "and event tickets into a single confirmation.</em></p>"
        ))

        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # Ticket printing  [TK11–TK15]
    # ------------------------------------------------------------------

    def action_print_tickets(self):
        """Print/download tickets as PDF report.  [TK12]"""
        self.ensure_one()
        if self.state not in ('paid', 'done'):
            raise UserError(_("Tickets can only be printed for paid dossiers."))
        svc = self.env['multi.channel.rental.ticket.service']
        svc._mark_tickets_printed(self)
        # config=False: return the report action directly instead of the
        # printer-configuration wizard (ir.actions.act_window) that
        # report_action() would emit when wkhtmltopdf is not fully set up
        # in the container.
        return self.env.ref(
            'multi_channel_rental_flow.action_report_dossier_tickets'
        ).report_action(self, config=False)

    def action_reset_tickets_printed(self):
        """Allow reprinting by resetting the printed flag.  [TK11]"""
        self.ensure_one()
        generated = self.env['sale.order'].search([
            ('mcrf_dossier_id', '=', self.id),
            ('state', '!=', 'cancel'),
        ])
        generated.write({'mcrf_tickets_printed': False})
        self.message_post(body=_("Ticket print flag reset — reprinting allowed."))

    # ------------------------------------------------------------------
    # Event registration linking  [EV06]
    # ------------------------------------------------------------------

    def _link_event_registrations(self):
        """Link event registrations created by event_sale back to
        the dossier and dossier items.

        When a sale order with event lines is confirmed, event_sale's
        _init_registrations creates event.registration records linked
        to the SO line. This method finds those registrations and
        links them to the dossier/item for ticket lookup and printing.
        """
        if 'event.registration' not in self.env:
            return

        Registration = self.env['event.registration']
        for item in self.item_ids.filtered(
            lambda i: i.item_role == 'event_ticket'
            and i.sale_order_line_id
        ):
            regs = Registration.search([
                ('sale_order_line_id', '=', item.sale_order_line_id.id),
            ])
            if regs:
                regs.write({
                    'mcrf_dossier_id': self.id,
                    'mcrf_dossier_item_id': item.id,
                })
                # Confirm draft registrations to open (paid)
                draft_regs = regs.filtered(lambda r: r.state == 'draft')
                if draft_regs:
                    draft_regs.action_confirm()

    # ------------------------------------------------------------------
    # Smart button actions
    # ------------------------------------------------------------------

    def action_view_sale_orders(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Sale Orders — %s", self.name),
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.sale_order_ids.ids)],
            'context': {'default_mcrf_dossier_ids': [(4, self.id)]},
        }
        if self.sale_order_count == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.sale_order_ids[0].id,
            })
        return action

    def action_view_transactions(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Transactions — %s", self.name),
            'res_model': 'payment.transaction',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.transaction_ids.ids)],
        }
        if self.transaction_count == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.transaction_ids[0].id,
            })
        return action

    def action_view_registrations(self):
        self.ensure_one()
        if 'event.registration' not in self.env:
            return {'type': 'ir.actions.act_window_close'}
        regs = self.env['event.registration'].search([
            ('mcrf_dossier_id', '=', self.id),
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': _("Registrations — %s", self.name),
            'res_model': 'event.registration',
            'view_mode': 'list,form',
            'domain': [('id', 'in', regs.ids)],
        }

    def action_link_sale_order(self):
        """Open a wizard-like view to pick existing sale orders."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Link Sale Orders to %s", self.name),
            'res_model': 'sale.order',
            'view_mode': 'list',
            'target': 'current',
            'domain': [
                ('id', 'not in', self.sale_order_ids.ids),
                ('state', '!=', 'cancel'),
            ],
            'context': {
                'mcrf_link_dossier_id': self.id,
            },
        }
