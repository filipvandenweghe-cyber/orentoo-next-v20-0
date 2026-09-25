from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalOnOption(TransactionCase):
    """'On option by other orders' — unconfirmed quotations (valid until their
    validity_date) are surfaced for awareness, exclude the current order, and
    never change the committed availability figure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.rental_flag_options = True
        cls.partner = cls.env['res.partner'].create({'name': 'Opt Client'})
        cls.wh = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.stock_loc = cls.wh.lot_stock_id
        cls.prod = cls.env['product.product'].create({
            'name': 'Opt Widget', 'type': 'consu', 'is_storable': True,
            'rent_periodicity': 'days'})

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_stock(self, qty):
        self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.prod.id, 'location_id': self.stock_loc.id,
            'inventory_quantity': qty,
        }).action_apply_inventory()

    def _rental_order(self, start_offset=0, days=1, validity_offset=10,
                      qty=2, confirm=False, on_option=False):
        now = fields.Datetime.now()
        order = self.env['sale.order'].with_context(in_rental_app=True).create({
            'partner_id': self.partner.id,
            'warehouse_id': self.wh.id,
            'rental_on_option': on_option,
            'rental_start_date': now + timedelta(days=start_offset),
            'rental_return_date': now + timedelta(days=start_offset + days),
        })
        if validity_offset is not None:
            order.validity_date = fields.Date.context_today(order) \
                + timedelta(days=validity_offset)
        self.env['sale.order.line'].with_context(in_rental_app=True).create({
            'order_id': order.id, 'product_id': self.prod.id,
            'product_uom_qty': qty})
        if confirm:
            order.action_confirm()
        return order

    def _qty(self, viewing):
        f = viewing.rental_start_date
        t = viewing.rental_return_date
        return self.prod._get_on_option_qty(
            f, t, ignored_order_id=viewing.id, warehouse_id=self.wh.id)

    # ── tests ────────────────────────────────────────────────────────────
    def test_counts_other_flagged_option(self):
        self._set_stock(5)
        self._rental_order(on_option=True)   # explicitly on option (qty 2)
        viewing = self._rental_order()
        self.assertEqual(self._qty(viewing), 2.0)

    def test_unflagged_quotation_not_counted(self):
        self._set_stock(5)
        self._rental_order(on_option=False)  # a quotation NOT marked on option
        viewing = self._rental_order()
        self.assertEqual(self._qty(viewing), 0.0)

    def test_excludes_current_order(self):
        self._set_stock(5)
        viewing = self._rental_order(on_option=True)  # its own option
        self.assertEqual(self._qty(viewing), 0.0)

    def test_confirmed_order_not_on_option(self):
        self._set_stock(5)
        # flagged, but confirmed → state 'sale' → reserved, not on option
        self._rental_order(on_option=True, confirm=True)
        viewing = self._rental_order()
        self.assertEqual(self._qty(viewing), 0.0)

    def test_lapsed_option_not_counted(self):
        self._set_stock(5)
        self._rental_order(on_option=True, validity_offset=-1)  # expired
        viewing = self._rental_order()
        self.assertEqual(self._qty(viewing), 0.0)

    def test_non_overlapping_option_not_counted(self):
        self._set_stock(5)
        self._rental_order(on_option=True, start_offset=30)  # far future
        viewing = self._rental_order()                       # now
        self.assertEqual(self._qty(viewing), 0.0)

    def test_breakdown_fields_and_hard_avail_unchanged(self):
        self._set_stock(5)
        viewing = self._rental_order()
        line = viewing.order_line
        line.invalidate_recordset(['free_qty_today'])
        base_avail = line.free_qty_today
        # Add an option held by another order.
        self._rental_order(on_option=True)
        line.invalidate_recordset(
            ['free_qty_today', 'rental_pickable',
             'rental_on_option_other', 'rental_on_option_until'])
        # Committed availability is unchanged by soft options.
        self.assertEqual(line.free_qty_today, base_avail)
        self.assertEqual(line.rental_pickable, base_avail)
        # But the awareness figures are populated.
        self.assertEqual(line.rental_on_option_other, 2.0)
        self.assertTrue(line.rental_on_option_until)

    def test_toggle_off_hides_option(self):
        self.company.rental_flag_options = False
        self._set_stock(5)
        self._rental_order(on_option=True)
        viewing = self._rental_order()
        line = viewing.order_line
        line.invalidate_recordset(['rental_on_option_other'])
        self.assertEqual(line.rental_on_option_other, 0.0)

    def test_report_include_options_toggle(self):
        """The availability report's 'Include options' checkbox subtracts the
        on-option soft holds from the shown figure; off leaves it committed."""
        self._set_stock(5)
        self._rental_order(on_option=True, start_offset=0, days=1)  # qty 2 now
        Report = self.env['rental.availability.report']
        base = {
            'product_ids': self.prod.ids,
            'company_ids': self.company.ids,
            'warehouse_ids': self.wh.ids,
            'interval': 'day',
        }
        off = Report.get_availability_matrix({**base, 'include_options': False})
        on = Report.get_availability_matrix({**base, 'include_options': True})
        key = '%s-%s-%s' % (self.prod.id, self.company.id, self.wh.id)
        off0 = off['cells'].get('%s-0' % key)
        on0 = on['cells'].get('%s-0' % key)
        self.assertIsNotNone(off0)
        self.assertIsNotNone(on0)
        # committed availability unchanged; toggled figure drops by the option.
        self.assertAlmostEqual(off0['available'], on0['available'] + 2.0,
                               places=2)

    def test_report_detail_exposes_on_option(self):
        self._set_stock(5)
        self._rental_order(on_option=True, start_offset=0, days=1)
        Report = self.env['rental.availability.report']
        data = Report.get_availability_matrix({
            'product_ids': self.prod.ids,
            'company_ids': self.company.ids,
            'warehouse_ids': self.wh.ids,
            'interval': 'day',
        })
        col0 = data['columns'][0]
        detail = Report.get_cell_detail(
            self.prod.id, self.company.id, self.wh.id,
            col0['start'], col0['stop'])
        self.assertEqual(detail['on_option'], 2.0)
        self.assertTrue(detail['option_orders'])
