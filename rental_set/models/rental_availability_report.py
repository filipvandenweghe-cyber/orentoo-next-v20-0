from datetime import timedelta

import pytz

from odoo import api, fields, models


class RentalAvailabilityReport(models.AbstractModel):
    """Read-only Availability Report data provider (Phase 2).

    This model owns NO availability logic of its own.  Every number it returns
    comes from the canonical engine ``product.product._rental_available_qty`` /
    its batch sibling ``_rental_available_batch`` (Operational Availability
    only).  The model just:

    * resolves the report scope (products / companies / warehouses),
    * builds the dynamic time columns (30 min / 1 h / 1 day) with tz-aware
      alignment,
    * calls the batch engine per ``(company, warehouse)``,
    * shapes ``{columns, rows, cells}`` for the OWL client action,
    * serves per-cell drill-down (contributing orders, repairs, availability
      elsewhere) — reusing the SAME engine, with record lists read as the user
      so ACLs / record rules apply.

    No Projected Availability, no balancing writes: strictly read-only.
    """

    _name = 'rental.availability.report'
    _description = 'Rental Availability Report (read-only)'

    # Number of columns per interval mode.
    _COLS = {'30min': 48, 'hour': 24, 'day': 21}

    # ── timezone helpers ───────────────────────────────────────────────────
    def _tz(self):
        return pytz.timezone(self.env.user.tz or 'UTC')

    def _to_utc_naive(self, aware_dt):
        return aware_dt.astimezone(pytz.utc).replace(tzinfo=None)

    def _build_columns(self, start_str, interval):
        """Return ``(pairs, meta, aligned_local)``:

        * ``pairs`` — list of ``(from_utc_naive, to_utc_naive)`` for the engine;
        * ``meta``  — list of ``{label, start, stop}`` (label in the user tz,
          start/stop as UTC-naive strings) for the client header;
        * ``aligned_local`` — the aligned window start as a **user-local-naive**
          string, echoed to the client for the date picker and Prev/Next.

        ``start_str`` is interpreted as a **user-local-naive** wall-clock (the
        moment the user wants the window to begin), so the picker is tz-correct
        regardless of the browser timezone.  Each column represents the COMPLETE
        half-open interval ``[from, to)``.  Alignment floors intuitively
        (30 min → :00/:30, hour → :00, day → local midnight).
        """
        tz = self._tz()
        if start_str:
            naive = fields.Datetime.to_datetime(start_str).replace(tzinfo=None)
            start_local = tz.localize(naive)
        else:
            start_local = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)

        cols = []
        if interval == 'day':
            base = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
            for i in range(self._COLS['day']):
                c0 = base + timedelta(days=i)
                c1 = base + timedelta(days=i + 1)
                cols.append((c0, c1, c0.strftime('%Y-%m-%d')))
        elif interval == 'hour':
            base = start_local.replace(minute=0, second=0, microsecond=0)
            for i in range(self._COLS['hour']):
                c0 = base + timedelta(hours=i)
                c1 = base + timedelta(hours=i + 1)
                cols.append((c0, c1, c0.strftime('%H:%M')))
        else:  # 30min
            minute = 0 if start_local.minute < 30 else 30
            base = start_local.replace(minute=minute, second=0, microsecond=0)
            for i in range(self._COLS['30min']):
                c0 = base + timedelta(minutes=30 * i)
                c1 = base + timedelta(minutes=30 * (i + 1))
                cols.append((c0, c1, c0.strftime('%H:%M')))

        pairs, meta = [], []
        for c0, c1, label in cols:
            f = self._to_utc_naive(c0)
            t = self._to_utc_naive(c1)
            pairs.append((f, t))
            meta.append({
                'label': label,
                'start': fields.Datetime.to_string(f),
                'stop': fields.Datetime.to_string(t),
            })
        aligned_local = cols[0][0].strftime('%Y-%m-%d %H:%M:%S') if cols else None
        return pairs, meta, aligned_local

    # ── scope resolution ───────────────────────────────────────────────────
    def _resolve_products(self, options):
        """Category (recursive) ∪ explicit products ∪ products on the selected
        orders, storable, **Sets excluded** (only physical / component products
        are shown).

        The report requires an explicit scope: with no category, product or
        order selected it returns nothing (the client shows a "pick a product
        or category" hint).  This keeps the matrix actively-scoped and avoids
        dumping the whole catalogue.

        Orders contribute the products on their lines — which already include
        the expanded set component lines, so a set on an order naturally brings
        in its physical components.
        """
        Product = self.env['product.product']
        base = [('is_storable', '=', True)]
        cat_ids = options.get('category_ids') or []
        prod_ids = options.get('product_ids') or []
        order_ids = options.get('order_ids') or []
        if not cat_ids and not prod_ids and not order_ids:
            return Product.browse()

        products = Product.browse()
        if cat_ids:
            cats = self.env['product.category'].search(
                [('id', 'child_of', cat_ids)])
            products |= Product.search(base + [('categ_id', 'in', cats.ids)])
        if prod_ids:
            products |= Product.search(base + [('id', 'in', prod_ids)])
        if order_ids:
            orders = self.env['sale.order'].browse(order_ids).exists()
            line_product_ids = orders.order_line.filtered(
                lambda l: not l.display_type).product_id.ids
            if line_product_ids:
                products |= Product.search(
                    base + [('id', 'in', line_product_ids)])
        # Sets never appear as report rows — act on their physical components.
        return products.filtered(lambda p: not p.is_rental_set)

    def _resolve_companies(self, options):
        """Selected companies, intersected with the user's allowed companies —
        never a broad sudo, never cross-company aggregation."""
        allowed = self.env.companies
        ids = options.get('company_ids')
        if ids:
            return self.env['res.company'].browse(ids) & allowed
        return allowed

    def _resolve_warehouses(self, options, companies):
        domain = [('company_id', 'in', companies.ids)]
        if options.get('warehouse_ids'):
            domain.append(('id', 'in', options['warehouse_ids']))
        return self.env['stock.warehouse'].search(domain)

    # ── main matrix API ────────────────────────────────────────────────────
    @api.model
    def get_availability_matrix(self, options):
        options = options or {}
        interval = options.get('interval') or '30min'
        if interval not in self._COLS:
            interval = '30min'
        pairs, col_meta, aligned_local = self._build_columns(
            options.get('start'), interval)

        products = self._resolve_products(options)
        companies = self._resolve_companies(options)
        warehouses = self._resolve_warehouses(options, companies)

        wh_by_company = {}
        for wh in warehouses:
            wh_by_company.setdefault(
                wh.company_id.id, self.env['stock.warehouse'])
            wh_by_company[wh.company_id.id] |= wh

        # One batch engine call per (company, warehouse) — reuses the canonical
        # scalar primitives, signed (clamp=False) so the report can show
        # overbooking / >100% utilisation.  A company-specific product is only
        # evaluated for its own company (never shown under another).
        avail = {}
        for company in companies:
            comp_products = products.filtered(
                lambda p: not p.company_id or p.company_id.id == company.id)
            if not comp_products:
                continue
            for wh in wh_by_company.get(
                    company.id, self.env['stock.warehouse']):
                batch = comp_products._rental_available_batch(
                    pairs, warehouse=wh, company=company,
                    ignored_soline_id=False, clamp=False)
                for pid, cell_list in batch.items():
                    avail[(pid, company.id, wh.id)] = cell_list

        # Optionally fold the "on option" soft holds into the shown figure —
        # available becomes the worst case if the options confirm
        # (available − on_option).  Off by default: the matrix shows committed
        # availability.  The step-function is built ONCE per (product, wh) over
        # the whole window and each column's peak is read from it (same basis
        # as the confirmed reserved term), so this stays cheap.
        if options.get('include_options') and pairs:
            full_from, full_to = pairs[0][0], pairs[-1][1]
            for company in companies:
                comp_products = products.filtered(
                    lambda p: not p.company_id or p.company_id.id == company.id)
                for wh in wh_by_company.get(
                        company.id, self.env['stock.warehouse']):
                    for product in comp_products:
                        cell_list = avail.get((product.id, company.id, wh.id))
                        if not cell_list:
                            continue
                        opt_lines = product._get_on_option_lines(
                            full_from, full_to, warehouse_id=wh.id)
                        if not opt_lines:
                            continue
                        rq, kd = opt_lines._get_rented_quantities(
                            [full_from, full_to])
                        for i, (f, t) in enumerate(pairs):
                            on_opt = product._reserved_peak(rq, kd, f, t)
                            if on_opt:
                                cell_list[i]['available'] -= on_opt

        # "Only unavailability": keep a product only when AT LEAST ONE of its
        # cells (any company / warehouse / interval) is overbooked
        # (signed available < 0), then show ALL of that product's rows so the
        # full cross-warehouse / cross-company picture is visible.
        only_unavailable = options.get('only_unavailable')
        bad_products = set()
        if only_unavailable:
            for (pid, _cid, _wid), cell_list in avail.items():
                if any(cell['available'] < 0 for cell in cell_list):
                    bad_products.add(pid)

        # Hierarchy: Product Category → Product → Company → Warehouse.
        def _cat_label(p):
            return p.categ_id.complete_name or p.categ_id.name or 'Uncategorized'

        products_sorted = products.sorted(
            lambda p: (_cat_label(p), p.display_name, p.id))
        rows, cells = [], {}
        for product in products_sorted:
            if only_unavailable and product.id not in bad_products:
                continue
            for company in companies:
                for wh in wh_by_company.get(
                        company.id, self.env['stock.warehouse']):
                    cell_list = avail.get((product.id, company.id, wh.id))
                    if cell_list is None:
                        continue
                    key = '%s-%s-%s' % (product.id, company.id, wh.id)
                    rows.append({
                        'key': key,
                        'category_id': product.categ_id.id,
                        'category': _cat_label(product),
                        'product_id': product.id,
                        'product_name': product.display_name,
                        'company_id': company.id,
                        'company_name': company.display_name,
                        'warehouse_id': wh.id,
                        'warehouse_name': wh.display_name,
                        'uom': product.uom_id.name or '',
                    })
                    for i, cell in enumerate(cell_list):
                        cells['%s-%s' % (key, i)] = {
                            'available': round(cell['available'], 2),
                            'capacity': round(cell['capacity'], 2),
                        }

        return {
            'interval': interval,
            # user-local-naive aligned start (for the date picker & Prev/Next)
            'start': aligned_local,
            'columns': col_meta,
            'rows': rows,
            'cells': cells,
        }

    # ── drill-down ─────────────────────────────────────────────────────────
    @api.model
    def get_cell_detail(self, product_id, company_id, warehouse_id,
                        from_str, to_str):
        product = self.env['product.product'].browse(product_id)
        company = self.env['res.company'].browse(company_id) & self.env.companies
        wh = self.env['stock.warehouse'].browse(warehouse_id)
        if not (product.exists() and company and wh.exists()):
            return {}
        company = company[:1]
        f = fields.Datetime.to_datetime(from_str)
        t = fields.Datetime.to_datetime(to_str)

        signed = product._rental_available_qty(
            f, t, warehouse=wh, company=company, clamp=False)
        total = product._rental_physical_total(warehouse=wh, company=company)
        t_out = product._get_transfer_out_qty(f, t, warehouse=wh)
        t_in = product._get_transfer_in_qty(f, t, warehouse=wh)
        capacity = total - t_out + t_in
        utilisation = None
        if capacity:
            utilisation = round((capacity - signed) / capacity * 100, 1)

        return {
            'product_name': product.display_name,
            'company_name': company.display_name,
            'warehouse_name': wh.display_name,
            'period': {
                'from': self._fmt_local(f),
                'to': self._fmt_local(t),
            },
            'available': round(signed, 2),
            'capacity': round(capacity, 2),
            'utilisation': utilisation,
            'uom': product.uom_id.name or '',
            'orders': self._cell_orders(product, wh, f, t),
            'on_option': round(product._get_on_option_qty(
                f, t, warehouse_id=wh.id), 2),
            'option_orders': self._cell_option_orders(product, wh, f, t),
            'repairs': self._cell_repairs(product, wh, f, t),
            'elsewhere': self._availability_elsewhere(product, company, wh, f, t),
        }

    def _fmt_local(self, dt):
        if not dt:
            return ''
        local = pytz.utc.localize(dt).astimezone(self._tz())
        return local.strftime('%Y-%m-%d %H:%M')

    def _cell_orders(self, product, wh, from_date, to_date):
        """Confirmed rental lines that actually commit units over this cell —
        the SAME set the engine's reserved term counts (``_get_active_rental_lines``).
        Read as the user, so record rules apply.  Sorted by rental start then
        end.
        """
        lines = product._get_active_rental_lines(
            from_date, to_date, warehouse_id=wh.id)
        data = []
        for line in lines:
            start = line._rental_effective_pickup_date() or line.reservation_begin \
                or line.start_date
            end = line._rental_effective_return_date() or line.return_date
            data.append({
                'sort': (start or from_date, end or to_date),
                'order_id': line.order_id.id,
                'order_name': line.order_id.name,
                'partner': line.order_id.partner_id.display_name or '',
                'start': self._fmt_local(start),
                'end': self._fmt_local(end),
                'qty': round(line._rental_effective_reserved_qty(), 2),
            })
        data.sort(key=lambda d: d['sort'])
        for d in data:
            del d['sort']
        return data

    def _cell_option_orders(self, product, wh, from_date, to_date):
        """Unconfirmed quotations holding this product **on option** over the
        cell (the same set the ``_get_on_option_qty`` term counts).  Read as
        the user, so record rules apply.  Sorted by rental start then end."""
        lines = product._get_on_option_lines(
            from_date, to_date, warehouse_id=wh.id)
        data = []
        for line in lines:
            start = line._rental_effective_pickup_date() \
                or line.reservation_begin or line.start_date
            end = line._rental_effective_return_date() or line.return_date
            data.append({
                'sort': (start or from_date, end or to_date),
                'order_id': line.order_id.id,
                'order_name': line.order_id.name,
                'partner': line.order_id.partner_id.display_name or '',
                'start': self._fmt_local(start),
                'end': self._fmt_local(end),
                'until': (line.order_id.validity_date.isoformat()
                          if line.order_id.validity_date else ''),
                'qty': round(line._rental_effective_reserved_qty(), 2),
            })
        data.sort(key=lambda d: d['sort'])
        for d in data:
            del d['sort']
        return data

    def _cell_repairs(self, product, wh, from_date, to_date):
        """Open repairs whose window overlaps this cell — read as the user
        (NOT sudo), so inaccessible repairs are never exposed."""
        if 'repair.order' not in self.env:
            return []
        domain = [
            ('product_id', '=', product.id),
            ('state', 'not in', ('done', 'cancel')),
        ]
        if wh.view_location_id:
            domain.append(('location_id', 'child_of', wh.view_location_id.id))
        repairs = self.env['repair.order'].search(domain)
        now = fields.Datetime.now()
        data = []
        for repair in repairs:
            start = repair.create_date or from_date
            end = repair.schedule_date or start
            if end < now:
                end = now
            if start <= to_date and end >= from_date:
                data.append({
                    'repair_id': repair.id,
                    'name': repair.name,
                    'state': repair.state,
                    'qty': round(repair.product_qty or 0.0, 2),
                    'schedule': self._fmt_local(repair.schedule_date),
                })
        return data

    def _availability_elsewhere(self, product, company, wh, from_date, to_date):
        """Same product's Operational Availability in OTHER pools, via the
        EXACT same engine — never aggregated into the selected warehouse.

        * ``same_company`` — other warehouses of this company (internal-transfer
          candidates);
        * ``other_company`` — warehouses of the user's other allowed companies
          (intercompany candidates).
        """
        same, other = [], []
        for w in self.env['stock.warehouse'].search(
                [('company_id', '=', company.id), ('id', '!=', wh.id)]):
            same.append({
                'warehouse_id': w.id,
                'warehouse_name': w.display_name,
                'available': round(product._rental_available_qty(
                    from_date, to_date, warehouse=w, company=company,
                    clamp=False), 2),
            })
        for c in (self.env.companies - company):
            for w in self.env['stock.warehouse'].search(
                    [('company_id', '=', c.id)]):
                other.append({
                    'company_id': c.id,
                    'company_name': c.display_name,
                    'warehouse_id': w.id,
                    'warehouse_name': w.display_name,
                    'available': round(product._rental_available_qty(
                        from_date, to_date, warehouse=w, company=c,
                        clamp=False), 2),
                })
        return {'same_company': same, 'other_company': other}
