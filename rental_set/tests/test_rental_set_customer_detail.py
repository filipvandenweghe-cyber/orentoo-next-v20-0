"""Rental set contents on the customer documents — docs/rental_set_requirements.md.

Covers the three-tier gating (RS-01…RS-07), the freeze at confirmation
(RS-02a), invoicing matching the offer (RS-20…RS-24) and the invariant that
none of it moves any money (RS-14).
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRentalSetCustomerDetail(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        # Keep absolute prices predictable: the dynamic-pricing add-on would
        # otherwise multiply every rental price by the company's standard
        # coefficient table (see TestRentalSetCommon for the same guard).
        if 'rental.coefficient.table' in cls.env:
            cls.env['rental.coefficient.table'].search([
                ('is_standard', '=', True),
                ('company_id', '=', cls.company.id),
            ]).is_standard = False

        Product = cls.env['product.product']
        cls.led = Product.create({
            'name': 'CD LED Par', 'list_price': 25.0, 'type': 'consu',
            'is_storable': True, 'rent_periodicity': 'days',
            'invoice_policy': 'order'})
        cls.cable = Product.create({
            'name': 'CD DMX Cable', 'list_price': 5.0, 'type': 'consu',
            'is_storable': True, 'rent_periodicity': 'days',
            'invoice_policy': 'order'})
        cls.set_tmpl = cls.env['product.template'].create({
            'name': 'CD Front Light Set', 'list_price': 0.0, 'type': 'consu',
            'rent_periodicity': 'days', 'is_rental_set': True,
            'set_pricing_mode': 'sum', 'invoice_policy': 'order'})
        cls.env['rental.set.component'].create([
            {'set_product_tmpl_id': cls.set_tmpl.id, 'product_id': cls.led.id,
             'quantity': 4, 'sequence': 10},
            {'set_product_tmpl_id': cls.set_tmpl.id, 'product_id': cls.cable.id,
             'quantity': 3, 'sequence': 20},
        ])
        cls.partner = cls.env['res.partner'].create({'name': 'CD Client'})

    # ── helpers ──────────────────────────────────────────────────────────────
    def _order(self, show_detail=False, allow=True):
        self.company.rental_set_allow_detail = allow
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        order.rental_set_show_detail = show_detail
        return order

    @staticmethod
    def _products(lines):
        return set(lines.mapped('product_id.name'))

    # ── RS-03: components are created visible; the gate is elsewhere ─────────
    def test_rs03_components_are_created_visible(self):
        order = self._order()
        components = order.order_line.filtered('is_set_component')
        self.assertTrue(components)
        self.assertTrue(
            all(components.mapped('visible_to_customer')),
            "components are created visible; whether they REACH the customer "
            "is decided by the company flag + the order's own flag")

    # ── RS-04: nothing shown unless both flags are on ───────────────────────
    def test_rs04_flag_off_reports_the_set_line_only(self):
        order = self._order(show_detail=False)
        reported = order._get_order_lines_to_report()
        self.assertEqual(
            self._products(reported), {'CD Front Light Set'},
            "with the order flag off only the set line is shown")

    def test_rs04_company_flag_off_overrides_the_order(self):
        order = self._order(show_detail=True)
        self.company.rental_set_allow_detail = False
        self.assertEqual(
            self._products(order._get_order_lines_to_report()),
            {'CD Front Light Set'},
            "the company flag gates the capability: no order may bypass it")

    # ── RS-05: shown, minus the per-line exception ───────────────────────────
    def test_rs05_flags_on_report_every_component(self):
        order = self._order(show_detail=True)
        self.assertEqual(
            self._products(order._get_order_lines_to_report()),
            {'CD Front Light Set', 'CD LED Par', 'CD DMX Cable'})

    def test_rs05_visible_to_customer_is_the_exception(self):
        order = self._order(show_detail=True)
        order.order_line.filtered(
            lambda l: l.product_id == self.cable
        ).visible_to_customer = False
        self.assertEqual(
            self._products(order._get_order_lines_to_report()),
            {'CD Front Light Set', 'CD LED Par'},
            "unticking one component hides exactly that one")

    # ── RS-07: the website cart follows the same rule ───────────────────────
    def test_rs07_cart_follows_the_same_rule(self):
        order = self._order(show_detail=False)
        component = order.order_line.filtered('is_set_component')[0]
        self.assertFalse(component._show_in_cart())
        order.rental_set_show_detail = True
        self.assertTrue(component._show_in_cart())

    # ── RS-14: showing the contents moves no money ──────────────────────────
    def test_rs14_totals_are_identical(self):
        hidden = self._order(show_detail=False)
        shown = self._order(show_detail=True)
        self.assertAlmostEqual(hidden.amount_untaxed, 115.0, places=2)
        self.assertAlmostEqual(
            shown.amount_untaxed, hidden.amount_untaxed, places=2,
            msg="the contents are presentation only — totals must not move")
        self.assertAlmostEqual(
            shown.amount_total, hidden.amount_total, places=2)

    def test_rs21_components_carry_no_price(self):
        order = self._order(show_detail=True)
        components = order.order_line.filtered('is_set_component')
        self.assertEqual(set(components.mapped('price_unit')), {0.0})
        self.assertEqual(set(components.mapped('price_subtotal')), {0.0})

    # ── RS-02a: frozen at confirmation ──────────────────────────────────────
    def test_rs02a_editable_on_a_quotation(self):
        order = self._order(show_detail=False)
        order.write({'rental_set_show_detail': True})
        self.assertTrue(order.rental_set_show_detail)

    def test_rs02a_frozen_once_confirmed(self):
        order = self._order(show_detail=True)
        order.action_confirm()
        with self.assertRaises(UserError):
            order.write({'rental_set_show_detail': False})
        self.assertTrue(
            order.rental_set_show_detail,
            "the accepted offer keeps its shape")

    def test_rs02a_frozen_for_a_sales_manager_too(self):
        manager = self.env['res.users'].create({
            'name': 'CD Manager', 'login': 'cd_manager',
            'group_ids': [(6, 0, [
                self.env.ref('sales_team.group_sale_manager').id,
                self.env.ref('base.group_user').id,
            ])],
        })
        order = self._order(show_detail=True)
        order.action_confirm()
        with self.assertRaises(UserError):
            order.with_user(manager).write({'rental_set_show_detail': False})

    def test_rs02a_editable_again_after_reset_to_draft(self):
        order = self._order(show_detail=True)
        order.action_confirm()
        order._action_cancel()
        order.action_draft()
        order.write({'rental_set_show_detail': False})
        self.assertFalse(order.rental_set_show_detail)

    # ── RS-20…RS-22: the invoice matches the offer ───────────────────────────
    def test_rs20_invoice_lists_the_contents_when_the_offer_did(self):
        order = self._order(show_detail=True)
        order.action_confirm()
        invoice = order._create_invoices()
        invoiced = set(
            invoice.invoice_line_ids.mapped('product_id.name')
        )
        self.assertEqual(
            invoiced, {'CD Front Light Set', 'CD LED Par', 'CD DMX Cable'},
            "a client offered the contents is invoiced the contents")
        self.assertAlmostEqual(
            invoice.amount_total, order.amount_total, places=2,
            msg="RS-21: invoiced components are 0.00 and move no money")

    def test_rs20_invoice_stays_summary_when_the_offer_was(self):
        order = self._order(show_detail=False)
        order.action_confirm()
        invoice = order._create_invoices()
        self.assertEqual(
            set(invoice.invoice_line_ids.mapped('product_id.name')),
            {'CD Front Light Set'})
        self.assertAlmostEqual(
            invoice.amount_total, order.amount_total, places=2)

    def test_rs22_invoice_document_matches_the_quotation(self):
        order = self._order(show_detail=True)
        order.action_confirm()
        invoice = order._create_invoices()
        offered = self._products(order._get_order_lines_to_report())
        printed = set(
            invoice._get_move_lines_to_report().mapped('product_id.name')
        )
        self.assertEqual(
            printed, offered,
            "what is printed on the invoice is what was offered")

    def test_rs23_a_manual_invoice_line_is_never_a_component(self):
        order = self._order(show_detail=False)
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.write({'invoice_line_ids': [(0, 0, {
            'name': 'CD manual line', 'quantity': 1, 'price_unit': 10.0,
        })]})
        manual = invoice.invoice_line_ids.filtered(
            lambda l: not l.sale_line_ids and l.display_type == 'product')
        self.assertTrue(manual)
        self.assertTrue(all(l._rs_shown_to_customer() for l in manual))
        self.assertFalse(any(l._rs_is_set_component() for l in manual))

    # ── RS-41: the box-drawing glyph field is gone ───────────────────────────
    def test_rs41_no_indent_label_field_remains(self):
        self.assertNotIn(
            'set_indent_label', self.env['sale.order.line']._fields,
            "hierarchy is drawn with layout, not characters")
        self.assertNotIn(
            'rental_set_indent_label', self.env['stock.move']._fields)
