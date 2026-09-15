from odoo import api, fields, models, _
from odoo.tools import float_compare


class SaleFlowLostBrokenService(models.AbstractModel):
    """Service for handling lost and broken item flow.

    Implements: R09, R10.

    Business rules:
      * Lost = not returned.  Broken = returned damaged.
      * Lost/broken charges use the company's lost_broken_fee_product_id.  (R10)
      * Default price comes from product.product.sales_price_broken_lost.
      * If no price is defined, use 0 — still create the invoiceable line.  (R09)
      * Charge lines are charge-only (no delivery moves).  (R10)
      * Invoice line description mentions actual product
        (e.g. "Lost/Broken Fee — Speaker A").  (R10)
      * If Repairs app is installed, broken items route to repair location.
    """

    _name = 'sale.flow.lost.broken.service'
    _description = 'Sale Flow Lost/Broken Service'

    def _process_lost_broken(self, wizard):
        """Process lost/broken wizard results.

        For each wizard line and each of the three buckets:
          1. Update the flow line's broken/lost quantities.
          2. **Scrap** the units from the rental (at-customer) location — all
             three buckets are terminal losses, so the units leave stock.
          3. For the *charged* buckets, add a fee line to the invoice.

        Repairable-broken items are handled by the standard repair flow and
        are not part of this wizard.
        """
        order = wizard.sale_order_id
        prec = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')

        for wiz_line in wizard.line_ids:
            flow_line = wiz_line.flow_line_id
            product = wiz_line.product_id
            sale_line = flow_line.sale_line_id
            fully_broken = wiz_line.fully_broken_qty or 0.0
            lost_charged = wiz_line.lost_charged_qty or 0.0
            lost_uncharged = wiz_line.lost_uncharged_qty or 0.0
            lost_total = lost_charged + lost_uncharged

            # 1. Update operational quantities on the original flow line.
            vals = {
                'last_flow_update_at': fields.Datetime.now(),
                'last_flow_update_by_id': self.env.uid,
            }
            if float_compare(fully_broken, 0, precision_digits=prec) > 0:
                vals['broken_qty'] = flow_line.broken_qty + fully_broken
            if float_compare(lost_total, 0, precision_digits=prec) > 0:
                vals['lost_qty'] = flow_line.lost_qty + lost_total

            flow_line.with_context(skip_sale_flow_sync=True).write(vals)
            flow_line._update_state()
            flow_line._compute_warning_level()

            # 2. Scrap every classified unit from the rental location.
            self._scrap_from_rental(
                order, product, fully_broken + lost_total, sale_line)

            # 3. Charge the customer for the charged buckets only.
            if float_compare(fully_broken, 0, precision_digits=prec) > 0:
                self._create_charge_line(
                    order, flow_line,
                    qty=fully_broken,
                    price=wiz_line.broken_lost_unit_price,
                    charge_type='broken',
                    product=product,
                )
            if float_compare(lost_charged, 0, precision_digits=prec) > 0:
                self._create_charge_line(
                    order, flow_line,
                    qty=lost_charged,
                    price=wiz_line.broken_lost_unit_price,
                    charge_type='lost',
                    product=product,
                )
            # lost_uncharged: scrapped above, no fee line.

    def _scrap_from_rental(self, order, product, qty, sale_line):
        """Scrap ``qty`` of ``product`` from the rental (at-customer)
        location to the company scrap location.

        The scrap move is linked to the rental sale line (``sale_line_id``)
        so rental availability attributes it to the right warehouse and
        releases the unit (see rental_set ``_rental_at_customer_qty`` and
        ``_rental_effective_reserved_qty``).

        **Serial-aware**: for a serial-tracked product the scrap MUST target
        the specific units that were not returned (``pickedup − returned``
        still on hand at the rental location).  Scrapping by product+qty alone
        leaves the actual missing serial on hand and may relieve the wrong one.
        """
        prec = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        if float_compare(qty, 0, precision_digits=prec) <= 0:
            return
        rental_loc = order.company_id.rental_loc_id
        if not rental_loc:
            return

        lots = self.env['stock.lot']
        if product.tracking == 'serial' and sale_line:
            # The serials still out at the client on this line.
            missing = sale_line.pickedup_lot_ids - sale_line.returned_lot_ids
            on_hand = missing.filtered(lambda lot: any(
                q.location_id == rental_loc
                and float_compare(q.quantity, 0, precision_digits=prec) > 0
                for q in lot.quant_ids))
            lots = on_hand.sorted('name')[:int(round(qty))]

        if lots:
            for lot in lots:
                self._create_rental_scrap(order, product, 1.0, rental_loc,
                                          sale_line, lot=lot)
            # Defensive: if fewer identifiable serials than classified, scrap
            # the remainder generically so quantities still reconcile.
            remainder = qty - len(lots)
            if float_compare(remainder, 0, precision_digits=prec) > 0:
                self._create_rental_scrap(order, product, remainder,
                                          rental_loc, sale_line)
        else:
            self._create_rental_scrap(order, product, qty, rental_loc,
                                      sale_line)

    def _create_rental_scrap(self, order, product, qty, rental_loc, sale_line,
                             lot=None):
        """Create and process one scrap from the rental location, optionally
        for a specific serial ``lot``."""
        vals = {
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'scrap_qty': qty,
            'location_id': rental_loc.id,
            'company_id': order.company_id.id,
            'origin': order.name,
        }
        if lot:
            vals['lot_id'] = lot.id
        scrap = self.env['stock.scrap'].create(vals)
        scrap.do_scrap()
        if sale_line:
            scrap.move_ids.write({'sale_line_id': sale_line.id})

    def _get_fee_product(self, company):
        """Return the SERVICE product used for lost/broken charge lines.

        A lost/broken charge is a pure invoice fee, never a delivery, so this
        is always a *service* product (no stock move, no reservation) — the
        one configured on the company, or the shared default.  It must NEVER
        be the physical rented product: charging through a storable/rental
        good would spawn a delivery and re-reserve the very item that was
        lost/broken.
        """
        fee = company.lost_broken_fee_product_id
        if fee:
            return fee
        default = self.env.ref(
            'sale_flow.product_lost_broken_fee', raise_if_not_found=False)
        if default:
            return default
        # Safety net if the data record is somehow absent.
        return self.env['product.product'].create({
            'name': 'Lost/Broken Fee',
            'type': 'service',
            'rent_ok': False,
        })

    def _create_charge_line(self, order, origin_flow_line,
                            qty, price, charge_type, product):
        """Create a charge-only sale order line and flow line.

        Business rules:
          * Charge through a SERVICE fee product (never the physical product,
            which would create a delivery) — description names the product.
          * Charge-only: skip_delivery=True, is_charge_only=True.
          * Always create even if price is 0.
          * Red warning level by default.
        """
        label = _('Lost') if charge_type == 'lost' else _('Broken')
        actual_name = product.display_name
        description = f"{label} Fee — {actual_name}"

        # Always a service fee product — never the physical (deliverable) one.
        line_product = self._get_fee_product(order.company_id)

        # Create the charge sale order line
        sol = self.env['sale.order.line'].with_context(
            skip_sale_flow_sync=True,
        ).create({
            'order_id': order.id,
            'product_id': line_product.id,
            'name': description,
            'product_uom_qty': qty,
            'price_unit': price,
            'is_downpayment': False,
        })

        # Create the charge flow line
        policy = 'lost_charge' if charge_type == 'lost' else 'broken_charge'
        self.env['sale.flow.line'].create({
            'name': description,
            'sale_order_id': order.id,
            'sale_line_id': sol.id,
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'confirmed_qty': 0,
            'current_qty': qty,
            'unit_price_effective': price,
            'broken_lost_unit_price': price,
            'is_charge_only': True,
            'skip_delivery': True,
            'state': 'confirmed',
            'commercial_policy': policy,
            'invoice_warning_level': 'red',
            'was_changed_after_confirmation': True,
            'added_after_confirmation': True,
            'change_origin': 'return',
            'change_note': _(
                '%(type)s charge for %(product)s (%(qty)s units)',
                type=label, product=actual_name, qty=qty,
            ),
            'origin_flow_line_id': origin_flow_line.id,
            'visible_to_customer': True,
            'last_flow_update_at': fields.Datetime.now(),
            'last_flow_update_by_id': self.env.uid,
        })
