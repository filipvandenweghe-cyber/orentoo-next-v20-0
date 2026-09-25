# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_cart_and_free_qty(self, product):
        """Override to compute set availability from leaf components.

        For rental set products the parent product has no stock of its own.
        We compute the maximum number of complete sets that can be rented
        during the order's rental period by evaluating all leaf component
        products.
        """
        tmpl = product.product_tmpl_id
        if not tmpl.is_rental_set or not product.rent_periodicity:
            return super()._get_cart_and_free_qty(product)

        if tmpl.allow_out_of_stock_order:
            return super()._get_cart_and_free_qty(product)

        # Collect leaf components with cumulative qty per 1 set
        leaves = []
        self.env['product.template']._collect_leaf_components_for_availability(
            tmpl, 1.0, leaves,
        )
        if not leaves:
            # No components defined: treat as unlimited
            return super()._get_cart_and_free_qty(product)

        # Group by product, summing cumulative quantities
        product_demand = {}
        for leaf_product, cumulative_qty in leaves:
            pid = leaf_product.id
            if pid not in product_demand:
                product_demand[pid] = {
                    'product': leaf_product,
                    'total_qty': 0.0,
                }
            product_demand[pid]['total_qty'] += cumulative_qty

        if not product_demand:
            return super()._get_cart_and_free_qty(product)

        # How many sets are in the cart?
        cart_qty = self._get_cart_qty(product.id)

        # For each leaf component, compute how many sets its stock supports
        min_sets = float('inf')
        for data in product_demand.values():
            comp_product = data['product']
            total_qty = data['total_qty']
            if not comp_product.is_storable or not total_qty:
                continue

            # Get the component's own cart+free qty using rental-aware logic
            comp_cart_qty, comp_free_qty = super()._get_cart_and_free_qty(
                comp_product,
            )
            # Available = free - what's already in other carts/orders
            # But we need to subtract what THIS set already claims
            # comp_cart_qty already includes qty from expanded set lines
            comp_available = comp_free_qty - comp_cart_qty
            # Add back what our own set lines are consuming (they're
            # already counted in comp_cart_qty via the expanded lines)
            own_set_comp_qty = cart_qty * total_qty
            comp_available += own_set_comp_qty

            sets_possible = comp_available / total_qty
            min_sets = min(min_sets, sets_possible)

        if min_sets == float('inf'):
            min_sets = 0.0

        available_sets = max(int(min_sets), 0)
        return cart_qty, available_sets
