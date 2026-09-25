"""Performance / load benchmark for the rental availability engine.

This is NOT part of the standard test suite (tagged ``-standard``).  Run it on
demand to measure how the availability engine, the batch Availability Report and
the catalog field behave as the dataset grows:

    /home/odoo/src/odoo/odoo-bin \\
      --addons-path=/home/odoo/src/odoo/addons,/home/odoo/src/enterprise,\\
/home/odoo/src/themes,/home/odoo/src/user \\
      -d <db> -u rental_set --test-enable --test-tags rental_perf \\
      --stop-after-init --no-http

Scale it via environment variables (defaults in parentheses):

    RENTAL_PERF_PRODUCTS    number of rentable products      (40)
    RENTAL_PERF_WAREHOUSES  warehouses in the company        (3)
    RENTAL_PERF_ORDERS      confirmed rental orders          (60)
    RENTAL_PERF_TRANSFERS   open interwarehouse transfers    (20)
    RENTAL_PERF_REPAIRS     open repairs                     (10)
    RENTAL_PERF_USERS       simulated sequential report hits (10)

Example — stress it:

    RENTAL_PERF_PRODUCTS=200 RENTAL_PERF_ORDERS=500 RENTAL_PERF_TRANSFERS=100 \\
    <odoo-bin ...> --test-tags rental_perf

The benchmark only measures (it does not assert hard limits, so it never fails
CI); it prints a timing + SQL-query table and a rough concurrency estimate.
"""

import logging
import os
import time
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

_logger = logging.getLogger(__name__)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@tagged('post_install', '-at_install', '-standard', 'rental_perf')
class TestRentalAvailabilityPerf(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.N_PRODUCTS = _env_int('RENTAL_PERF_PRODUCTS', 40)
        cls.N_WAREHOUSES = _env_int('RENTAL_PERF_WAREHOUSES', 3)
        cls.N_ORDERS = _env_int('RENTAL_PERF_ORDERS', 60)
        cls.N_TRANSFERS = _env_int('RENTAL_PERF_TRANSFERS', 20)
        cls.N_REPAIRS = _env_int('RENTAL_PERF_REPAIRS', 10)
        cls.N_USERS = _env_int('RENTAL_PERF_USERS', 10)

        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Perf Client'})
        cls.category = cls.env['product.category'].create(
            {'name': 'Perf Rental'})

        # Warehouses (single-step to keep seeding cheap).
        cls.warehouses = cls.env['stock.warehouse']
        for i in range(cls.N_WAREHOUSES):
            cls.warehouses |= cls.env['stock.warehouse'].create({
                'name': 'Perf WH %d' % i, 'code': 'PW%d' % i,
                'company_id': cls.company.id,
                'reception_steps': 'one_step', 'delivery_steps': 'ship_only'})
        cls.env['res.config.settings'].create(
            {'group_rental_stock_picking': True}).set_values()
        cls.env['stock.warehouse'].update_rental_rules()
        if not cls.company.rental_loc_id:
            cls.env['res.company'].create_missing_rental_location()
            cls.company.invalidate_recordset(['rental_loc_id'])

        cls.now = fields.Datetime.now()
        cls._seed_products()
        cls._seed_orders()
        cls._seed_transfers()
        cls._seed_repairs()
        cls._results = []

    # ── seeding ────────────────────────────────────────────────────────────
    @classmethod
    def _seed_products(cls):
        vals = [{
            'name': 'Perf Product %03d' % i,
            'type': 'consu', 'is_storable': True, 'rent_periodicity': 'days',
            'categ_id': cls.category.id,
        } for i in range(cls.N_PRODUCTS)]
        cls.products = cls.env['product.product'].create(vals)
        # Stock: give each product plenty in every warehouse.
        quant_vals = []
        for product in cls.products:
            for wh in cls.warehouses:
                quant_vals.append({
                    'product_id': product.id,
                    'location_id': wh.lot_stock_id.id,
                    'inventory_quantity': 50.0,
                })
        cls.env['stock.quant'].with_context(inventory_mode=True).create(
            quant_vals).action_apply_inventory()

    @classmethod
    def _seed_orders(cls):
        """Confirmed rental orders spread across the 3-week window, each with a
        few products — this is what makes the reserved term expensive."""
        products = cls.products
        whs = cls.warehouses
        SO = cls.env['sale.order'].with_context(in_rental_app=True)
        SOL = cls.env['sale.order.line'].with_context(in_rental_app=True)
        for i in range(cls.N_ORDERS):
            wh = whs[i % len(whs)]
            start = cls.now + timedelta(days=(i % 20), hours=(i % 8))
            end = start + timedelta(days=1 + (i % 3))
            order = SO.create({
                'partner_id': cls.partner.id, 'warehouse_id': wh.id,
                'rental_start_date': start, 'rental_return_date': end})
            # 3 products per order (rotating), qty 1-3.
            for k in range(3):
                product = products[(i * 3 + k) % len(products)]
                SOL.create({
                    'order_id': order.id, 'product_id': product.id,
                    'product_uom_qty': 1 + (k % 3)})
            order.action_confirm()

    @classmethod
    def _seed_transfers(cls):
        if len(cls.warehouses) < 2:
            return
        Move = cls.env['stock.move']
        for i in range(cls.N_TRANSFERS):
            src = cls.warehouses[i % len(cls.warehouses)]
            dst = cls.warehouses[(i + 1) % len(cls.warehouses)]
            product = cls.products[i % len(cls.products)]
            move = Move.create({
                'product_id': product.id, 'uom_id': product.uom_id.id,
                'product_uom_qty': 2.0,
                'location_id': src.lot_stock_id.id,
                'location_dest_id': dst.lot_stock_id.id,
                'date': cls.now + timedelta(days=(i % 20))})
            move._action_confirm()
            move.date = cls.now + timedelta(days=(i % 20))

    @classmethod
    def _seed_repairs(cls):
        if 'repair.order' not in cls.env:
            return
        Repair = cls.env['repair.order']
        for i in range(cls.N_REPAIRS):
            product = cls.products[i % len(cls.products)]
            wh = cls.warehouses[i % len(cls.warehouses)]
            try:
                Repair.create({
                    'product_id': product.id, 'product_qty': 1.0,
                    'location_id': wh.lot_stock_id.id,
                    'schedule_date': cls.now + timedelta(days=(i % 20)),
                })
            except Exception:  # pragma: no cover - schema variance
                break

    # ── timing helper ──────────────────────────────────────────────────────
    def _time(self, label, fn, cold=True):
        cr = self.env.cr
        if cold:
            self.env.invalidate_all()
        q0 = getattr(cr, 'sql_log_count', 0)
        t0 = time.perf_counter()
        res = fn()
        dt = time.perf_counter() - t0
        dq = getattr(cr, 'sql_log_count', 0) - q0
        self._results.append((label, dt, dq))
        return res

    def _report(self, title):
        line = '=' * 78
        _logger.info('\n%s\n%s', line, title)
        _logger.info(
            'dataset: %d products x %d warehouses | %d orders | %d transfers | '
            '%d repairs',
            self.N_PRODUCTS, self.N_WAREHOUSES, self.N_ORDERS,
            self.N_TRANSFERS, self.N_REPAIRS)
        _logger.info('%-52s %10s %10s', 'scenario', 'seconds', 'queries')
        _logger.info('%s', '-' * 78)
        for label, dt, dq in self._results:
            _logger.info('%-52s %10.3f %10d', label, dt, dq)
        _logger.info('%s', line)
        self._results = []

    # ── the benchmark ──────────────────────────────────────────────────────
    def test_availability_performance(self):
        Report = self.env['rental.availability.report']
        opts_base = {'category_ids': self.category.ids}

        # 1) Full report matrix — day (21 cols) and 30-min (48 cols).
        day = self._time(
            'report matrix — 1 day (21 cols)',
            lambda: Report.get_availability_matrix(
                {**opts_base, 'interval': 'day'}))
        self._time(
            'report matrix — 30 min (48 cols)',
            lambda: Report.get_availability_matrix(
                {**opts_base, 'interval': '30min'}))
        cells = len(day['rows']) * len(day['columns'])
        _logger.info('report produced %d rows x %d cols = %d cells',
                     len(day['rows']), len(day['columns']), cells)

        # 2) Batch vs scalar for one warehouse over 48 columns.
        wh = self.warehouses[0]
        cols = [(self.now + timedelta(days=d), self.now + timedelta(days=d, hours=12))
                for d in range(21)]
        self._time(
            'batch: %d products x 21 cols (1 wh)' % self.N_PRODUCTS,
            lambda: self.products._rental_available_batch(
                cols, warehouse=wh, company=self.company, clamp=False))

        def _scalar_loop():
            for product in self.products:
                for (f, t) in cols:
                    product._rental_available_qty(
                        f, t, warehouse=wh, company=self.company, clamp=False)
        self._time(
            'scalar: %d products x 21 cols (1 wh)' % self.N_PRODUCTS,
            _scalar_loop)

        # 3) Catalog field for a page of products (mimics one catalog page).
        page = self.products[:40]
        ctx = dict(
            start_date=fields.Datetime.to_string(self.now + timedelta(days=1)),
            end_date=fields.Datetime.to_string(self.now + timedelta(days=2)),
            rental_catalog_wh=wh.id, rental_catalog_company=self.company.id)
        self._time(
            'catalog field — %d product cards' % len(page),
            lambda: page.with_context(**ctx).mapped('rental_avail_catalog'))

        self._report('RENTAL AVAILABILITY — PERFORMANCE')

        # 4) Repeated report hits → average latency and a concurrency estimate.
        n = max(self.N_USERS, 1)
        t0 = time.perf_counter()
        for _i in range(n):
            self.env.invalidate_all()
            Report.get_availability_matrix({**opts_base, 'interval': 'day'})
        total = time.perf_counter() - t0
        avg = total / n
        _logger.info(
            'repeated report: %d cold calls in %.3fs → avg %.3fs/call '
            '(~%.1f calls/s per worker); N concurrent users need ceil(N*%.3fs / '
            'workers) wall time',
            n, total, avg, (1.0 / avg if avg else 0.0), avg)
        _logger.info(
            'NOTE: true multi-user concurrency needs an HTTP load tool '
            '(locust/ab) hitting get_availability_matrix across worker '
            'processes; this figure is single-worker throughput.')

        # Soft sanity only (never fails CI on absolute time): results exist.
        self.assertTrue(day['rows'], 'report returned rows for seeded data')
