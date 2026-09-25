from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('-standard', 'post_install', '-at_install', 'rental_set_ui')
class TestRentalSetQtyWidgetUI(HttpCase):
    """The Rental Set availability widget must render in the SO form.

    Odoo 20 runs Owl 3, which ignores a static ``props``/``defaultProps`` on a
    component and throws instead.  It also feeds ``calcData`` from the RETURN
    value of ``initCalcData()``.  Both are render-time failures that no Python
    test can catch, so this opens a real quotation carrying a rental set line
    in a browser: any component crash surfaces as an uncaught promise error
    and fails the test.

    NOTE: this test needs a working headless Chrome, which the AI dev sandbox
    does not provide (Chrome cannot fork: "RuntimeError: can't start new
    thread").  It is therefore tagged ``-standard`` so it never reddens a
    build on a container without a browser; run it deliberately with::

        odoo-bin -u rental_set --test-enable --test-tags rental_set_ui \
                 --stop-after-init
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env['product.product']
        cls.component = Product.create({
            'name': 'UI LED Par',
            'list_price': 25.0,
            'type': 'consu',
            'is_storable': True,
            'sale_ok': True,
            'rent_periodicity': 'days',
        })
        cls.set_tmpl = cls.env['product.template'].create({
            'name': 'UI Lighting Set',
            'list_price': 100.0,
            'type': 'consu',
            'sale_ok': True,
            'rent_periodicity': 'days',
            'is_rental_set': True,
            'set_pricing_mode': 'fixed',
        })
        cls.env['rental.set.component'].create({
            'set_product_tmpl_id': cls.set_tmpl.id,
            'product_id': cls.component.id,
            'quantity': 2,
            'sequence': 10,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'UI Rental Customer'})

    def test_qty_widget_renders_on_quotation(self):
        now = fields.Datetime.now()
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'rental_start_date': now,
            'rental_return_date': now + timedelta(days=2),
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.set_tmpl.product_variant_id.id,
            'product_uom_qty': 1,
        })
        self.assertTrue(
            order.order_line.filtered(lambda line: line.is_set and not line.is_set_component),
            "The set line must have expanded, otherwise the widget never renders.",
        )

        self.browser_js(
            f"/odoo/action-sale_renting.rental_order_action/{order.id}",
            # Wait until the order-line list is on screen; an Owl crash in the
            # widget rejects before this resolves and fails the test.
            """
            (async () => {
                for (let i = 0; i < 200; i++) {
                    if (document.querySelector(".o_sale_order_line_o2m .o_data_row")) {
                        await new Promise((r) => setTimeout(r, 500));
                        console.log("test successful");
                        return;
                    }
                    await new Promise((r) => setTimeout(r, 100));
                }
                console.error("order line rows never rendered");
            })();
            """,
            login="admin",
        )
