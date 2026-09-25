import logging

from odoo.tools import config

_logger = logging.getLogger(__name__)

COMPANY_NAME = 'Pro-Designed.com'
COMPANY_VAT = 'BE0715939182'
CHART_TEMPLATE = 'be_comp'  # Belgium - Companies (PCMN)


def post_init_hook(env):
    """Create/configure the Pro-Designed.com Belgian company.

    Idempotent: safe to run again on module update or on a fresh Odoo.sh
    rebuild. It creates the company if missing, loads the Belgian chart of
    accounts (which brings EUR + the Belgian fiscal positions), sets the VAT
    number, and makes the company the default one for the admin user.
    """
    country_be = env.ref('base.be')
    eur = env.ref('base.EUR')
    if not eur.active:
        eur.active = True

    company = env['res.company'].search([('name', '=', COMPANY_NAME)], limit=1)
    if not company:
        company = env['res.company'].create({
            'name': COMPANY_NAME,
            'country_id': country_be.id,
        })
        _logger.info('pro_designed_setup: created company %s (id=%s)', COMPANY_NAME, company.id)
    else:
        _logger.info('pro_designed_setup: reusing existing company %s (id=%s)', COMPANY_NAME, company.id)
        if company.country_id != country_be:
            company.country_id = country_be

    # Load the Belgian chart of accounts if not already loaded.
    # This also sets the company currency to EUR and creates the Belgian
    # fiscal positions (Domestic, Intra-Community, EU B2C, Extra-Community,
    # Co-Contractant, Non Deductible).
    #
    # Note: on demo builds we deliberately clear the `chart_template` marker
    # further down (see the demo-data compatibility guard), so we must not rely
    # on that marker alone to decide whether the chart is loaded — otherwise a
    # re-run (module update) would try to reload it. Treat the presence of
    # accounts as the source of truth.
    chart_already_loaded = company.chart_template == CHART_TEMPLATE or bool(
        env['account.account'].sudo().search_count(
            [('company_ids', 'in', company.id)], limit=1
        )
    )
    if not chart_already_loaded:
        _logger.info('pro_designed_setup: loading chart template %s for %s', CHART_TEMPLATE, COMPANY_NAME)
        env['account.chart.template'].try_loading(
            CHART_TEMPLATE, company=company, install_demo=False,
        )
        company.invalidate_recordset()
    else:
        _logger.info('pro_designed_setup: chart template already loaded for %s', COMPANY_NAME)

    # Set the VAT number on the company's partner.
    if company.partner_id.vat != COMPANY_VAT:
        company.partner_id.vat = COMPANY_VAT

    # Make sure the company is on EUR (should be set by the chart load).
    if company.currency_id != eur:
        company.currency_id = eur

    # Grant the admin user access to the company and make it the default one.
    admin = env.ref('base.user_admin', raise_if_not_found=False)
    if admin:
        if company not in admin.company_ids:
            admin.company_ids = [(4, company.id)]
        admin.company_id = company

    # ------------------------------------------------------------------
    # Ensure the company has a stock warehouse.
    #
    # Creating a res.company does not generate a warehouse on its own, and
    # without one the company has no internal stock location.  Any inventory
    # adjustment or rental stock move performed in this company then fails
    # with "Missing required value for the field 'Location' (location_id)"
    # when Odoo tries to create the stock.quant / stock.move.  Creating the
    # warehouse builds the full location hierarchy (view / stock / input /
    # output) plus the routes, rules and picking types the company needs to
    # operate rentals.
    warehouse = env['stock.warehouse'].search(
        [('company_id', '=', company.id)], limit=1,
    )
    if not warehouse:
        warehouse = env['stock.warehouse'].create({
            'name': COMPANY_NAME,
            'code': 'PRO',
            'company_id': company.id,
            'partner_id': company.partner_id.id,
        })
        _logger.info(
            'pro_designed_setup: created stock warehouse %s (id=%s) for %s',
            warehouse.code, warehouse.id, COMPANY_NAME,
        )
    else:
        _logger.info(
            'pro_designed_setup: warehouse already present for %s (%s)',
            COMPANY_NAME, warehouse.code,
        )

    # ------------------------------------------------------------------
    # Give the warehouse an opening-hours calendar in the local timezone.
    #
    # The multi-channel kiosk builds rental start-time slots from
    # ``warehouse.opening_hours`` and uses that calendar's timezone as the
    # single source of truth for slot times.  Without a calendar the flow
    # falls back to naive/UTC times, so the picker showed times ~2h off from
    # Belgian local and offered slots already in the past.  A Europe/Brussels
    # calendar (08:00-18:00, 7 days) makes slot times correct and lets the
    # past-slot cutoff work.  Idempotent.
    _ensure_warehouse_opening_hours(env, company, warehouse)

    # ------------------------------------------------------------------
    # Replicate the rental coefficient / dynamic pricing configuration into
    # this company + warehouse.
    #
    # The kayak pricing demo data (coefficient types, coefficient tables and
    # their lines, the dynamic pricing table, and the per-product/warehouse
    # pricing configs) is created without an explicit company, so it lands in
    # the main company (My Company) on its warehouse.  Because this customer
    # operates rentals in Pro-Designed.com, orders default to this company and
    # the PRO warehouse — where none of that pricing config exists — so the
    # coefficient/dynamic engine finds no table and leaves the price at the
    # bare base (e.g. a 2h kayak stays at 10.00 instead of 10 x coefficient).
    #
    # Duplicating the config into this company/warehouse makes the engine
    # resolve a matching table again.  Idempotent and a no-op on non-demo
    # builds (where the source demo records do not exist).
    _replicate_rental_pricing(env, company, warehouse)

    # ------------------------------------------------------------------
    # Demo-data compatibility guard.
    #
    # Standard Odoo's `account.chart.template._install_demo()` iterates over
    # *every* company that has a chart of accounts
    # (`search([('chart_template', '!=', False)])`), but it resolves the demo
    # record XML-IDs (bank journal, bank account, ...) with `company_xmlid()`,
    # which is hard-wired to `self.env.company` — i.e. the *main* company —
    # instead of the company currently being processed. As a result it works
    # only when the main company is the single charted company.
    #
    # Because this module creates a *second* charted company, the account demo
    # would try to attach Pro-Designed.com's demo bank account to the main
    # company's bank journal and blow up with:
    #   "The partners of the journal's company and the related bank account
    #    mismatch."
    # which makes the whole `account` demo roll back ("installed without demo
    # data") and shows up on Odoo.sh as "failed to load demo data".
    #
    # The account demo runs later, during the demo phase (after all
    # post_init_hooks). To keep it from picking up this company we clear the
    # `chart_template` marker on demo builds only. The company keeps every
    # account, tax, journal and fiscal position that was just loaded and stays
    # fully operational (invoices post correctly); only the "which template was
    # loaded" marker is dropped so the buggy multi-company demo skips it.
    # Production/staging builds (no demo) keep the marker untouched.
    demo_enabled = not config.get('without_demo')
    if demo_enabled and company.chart_template:
        _logger.info(
            'pro_designed_setup: demo build detected — clearing chart_template '
            'marker on %s so the standard multi-company account demo skips it.',
            COMPANY_NAME,
        )
        company.sudo().write({'chart_template': False})

    _logger.info(
        'pro_designed_setup: done. company=%s currency=%s chart=%s vat=%s',
        company.name, company.currency_id.name, company.chart_template, company.partner_id.vat,
    )


OPENING_HOURS_TZ = 'Europe/Brussels'
OPENING_HOURS_NAME = 'Pro-Designed.com Opening Hours'


def _ensure_warehouse_opening_hours(env, company, warehouse):
    """Ensure the warehouse has a local-timezone opening-hours calendar.

    Creates (once) a Europe/Brussels calendar open 08:00-18:00 every day and
    assigns it to ``warehouse.opening_hours`` if not already set.  The kiosk
    rental flow reads this calendar's timezone to compute and display slot
    times, so it must exist for slot times to be correct.  Idempotent, and a
    no-op when the ``opening_hours`` field is absent (module not installed).
    """
    if 'opening_hours' not in warehouse._fields:
        _logger.info(
            'pro_designed_setup: stock.warehouse has no opening_hours field — '
            'skipping opening-hours setup.',
        )
        return

    if warehouse.opening_hours:
        _logger.info(
            'pro_designed_setup: warehouse %s already has opening hours (%s).',
            warehouse.code, warehouse.opening_hours.name,
        )
        return

    # Odoo 20 removed resource.calendar.tz: a calendar is now read in the
    # company timezone, so carry the intended timezone on the company itself.
    if not company.tz:
        company.sudo().tz = OPENING_HOURS_TZ

    calendar = env['resource.calendar'].search([
        ('company_id', '=', company.id),
        ('name', '=', OPENING_HOURS_NAME),
    ], limit=1)
    if not calendar:
        # Odoo 20: resource.calendar.attendance dropped 'name' and computes
        # 'day_period'; resource.calendar dropped 'tz' (it now follows the
        # company timezone, res.company.tz).
        attendance = [
            (0, 0, {
                'dayofweek': str(dow),
                'hour_from': 8.0,
                'hour_to': 18.0,
            })
            for dow in range(7)  # Monday .. Sunday
        ]
        calendar = env['resource.calendar'].create({
            'name': OPENING_HOURS_NAME,
            'company_id': company.id,
            'attendance_ids': attendance,
        })
        _logger.info(
            'pro_designed_setup: created opening-hours calendar %s (id=%s, '
            'company tz=%s).',
            calendar.name, calendar.id, company.tz,
        )
    warehouse.opening_hours = calendar.id
    _logger.info(
        'pro_designed_setup: assigned opening hours %s to warehouse %s.',
        calendar.name, warehouse.code,
    )


# Source (demo) records to replicate.  Coefficient types/tables + dynamic
# table live in rental_coefficient_dynamic_pricing; the product<->warehouse
# pricing configs live downstream in multi_channel_rental_flow.
_COEFF_TYPE_XMLIDS = [
    'rental_coefficient_dynamic_pricing.rental_coefficient_type_standard',
    'rental_coefficient_dynamic_pricing.demo_coeff_type_canadese_kano',
    'rental_coefficient_dynamic_pricing.demo_coeff_type_kayak_1p',
    'rental_coefficient_dynamic_pricing.demo_coeff_type_kayak_2p',
]
_COEFF_TABLE_XMLIDS = [
    'rental_coefficient_dynamic_pricing.demo_coeff_table_standard',
    'rental_coefficient_dynamic_pricing.demo_coeff_table_canadese_kano',
    'rental_coefficient_dynamic_pricing.demo_coeff_table_kayak_1p',
    'rental_coefficient_dynamic_pricing.demo_coeff_table_kayak_2p',
]
_DYNAMIC_TABLE_XMLID = (
    'rental_coefficient_dynamic_pricing.demo_dynamic_pricing_table'
)
_CONFIG_XMLIDS = [
    'multi_channel_rental_flow.demo_pricing_config_kayak_1p',
    'multi_channel_rental_flow.demo_pricing_config_kayak_2p',
]


def _replicate_rental_pricing(env, company, warehouse):
    """Duplicate the kayak rental-pricing config into ``company``/``warehouse``.

    Copies the coefficient types, coefficient tables (with their lines), the
    dynamic pricing table (with its lines) and the per-product warehouse
    pricing configs from the main-company demo data into this company and its
    PRO warehouse, so the coefficient/dynamic engine resolves a matching table
    for orders placed here.

    Idempotent: every record is looked up by its natural key in the target
    company first and reused if present, so re-runs (rebuilds, re-installs) do
    not create duplicates.  A no-op when the source demo records are absent
    (production/staging builds load no demo data).
    """
    def ref(xmlid):
        return env.ref(xmlid, raise_if_not_found=False)

    # Nothing to replicate on non-demo builds.
    if not any(ref(x) for x in _COEFF_TABLE_XMLIDS):
        _logger.info(
            'pro_designed_setup: no rental pricing demo data found — '
            'skipping pricing replication for %s.', company.name,
        )
        return

    # --- 1. Coefficient types (unique by name + company) ---------------
    type_map = {}  # source type id -> target type record
    for xmlid in _COEFF_TYPE_XMLIDS:
        src = ref(xmlid)
        if not src:
            continue
        target = env['rental.coefficient.type'].with_context(
            active_test=False,
        ).search([
            ('name', '=', src.name),
            ('company_id', '=', company.id),
        ], limit=1)
        if not target:
            target = src.copy({'name': src.name, 'company_id': company.id})
        type_map[src.id] = target

    # --- 2. Coefficient tables (+ lines, copied) -----------------------
    table_map = {}  # source table id -> target table record
    for xmlid in _COEFF_TABLE_XMLIDS:
        src = ref(xmlid)
        if not src:
            continue
        target_type = type_map.get(src.coefficient_type_id.id)
        target = env['rental.coefficient.table'].search([
            ('name', '=', src.name),
            ('company_id', '=', company.id),
            ('coefficient_type_id', '=', target_type.id if target_type else False),
        ], limit=1)
        if not target:
            target = src.copy({
                'name': src.name,
                'company_id': company.id,
                'coefficient_type_id': target_type.id if target_type else False,
            })
        table_map[src.id] = target

    # --- 3. Dynamic pricing table (+ lines, copied) --------------------
    dyn_map = {}  # source dyn table id -> target dyn table record
    src_dyn = ref(_DYNAMIC_TABLE_XMLID)
    if src_dyn:
        target_dyn = env['rental.dynamic.pricing.table'].search([
            ('name', '=', src_dyn.name),
            ('company_id', '=', company.id),
        ], limit=1)
        if not target_dyn:
            target_dyn = src_dyn.copy({
                'name': src_dyn.name,
                'company_id': company.id,
            })
        dyn_map[src_dyn.id] = target_dyn

    # --- 4. Product / warehouse pricing configs ------------------------
    Config = env['rental.product.warehouse.pricing.config']
    for xmlid in _CONFIG_XMLIDS:
        src = ref(xmlid)
        if not src:
            continue
        # (product, warehouse) is unique — reuse an existing config here.
        target = Config.search([
            ('product_tmpl_id', '=', src.product_tmpl_id.id),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        target_tables = env['rental.coefficient.table'].browse([
            table_map[t.id].id for t in src.coefficient_table_ids
            if t.id in table_map
        ])
        target_dyn = dyn_map.get(src.dynamic_pricing_table_id.id)
        vals = {
            'coefficient_table_ids': [(6, 0, target_tables.ids)],
            'dynamic_pricing_table_id': target_dyn.id if target_dyn else False,
        }
        if target:
            target.write(vals)
        else:
            Config.create(dict(vals, **{
                'product_tmpl_id': src.product_tmpl_id.id,
                'warehouse_id': warehouse.id,
            }))

    _logger.info(
        'pro_designed_setup: replicated rental pricing config into %s (%s) — '
        '%s coefficient tables, %s dynamic table(s), %s product configs.',
        company.name, warehouse.code, len(table_map), len(dyn_map),
        len(_CONFIG_XMLIDS),
    )
