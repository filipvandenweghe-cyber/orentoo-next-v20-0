"""Sale Flow Line — central business state model.

Business Requirements Index
===========================

The requirements below were developed incrementally and are documented
in-line throughout the module files.  This index serves as a reference.

R01 — Commercial baseline at confirmation
    The confirmed sale order is the agreed commercial baseline.  At
    confirmation, store qty, price, discount, subtotal and description.
    → sale_flow_service._on_order_confirmed()

R02 — Reconfirmation resets baseline
    If the order is cancelled and reconfirmed, reset the baseline to the
    new values.  Treat the reconfirmed order as the new agreement.
    → sale_flow_service._reset_baseline()

R03 — Logistics never silently change agreed price
    After confirmation, logistics changes update operational quantities
    but never overwrite the agreed commercial price/subtotal.
    → sale_flow_service._reconcile_line_change()

R04 — Post-confirmation order line changes tracked as deltas
    Adding, increasing, decreasing or deleting order lines after
    confirmation preserves the baseline and marks flow lines as changed.
    → sale_flow_service._reconcile_line_change(), _handle_line_deletion()

R05 — Delivered qty cannot be reduced
    If delivered_qty > 0, the user cannot reduce quantity below it.
    Deletion is blocked; it must go through return/credit/lost/broken.
    → sale_flow_service._reconcile_line_change(), sale_order_line.unlink()

R06 — Invoice warning levels (orange / red)
    orange = operational/logistic change, invoice <= confirmed value.
    red = invoice value increases (lost/broken charges, explicit increase).
    → sale_flow_line._compute_warning_level()

R07 — Expected return qty based on delivered qty, not ordered qty
    Formula: delivered_qty - returned_qty - lost_qty - broken_qty.
    What was not delivered cannot be expected for return.
    → sale_flow_return_service._get_expected_return_qty()

R08 — Return picking reconciliation
    After outgoing delivery, return picking demands are adjusted to match
    actual delivered quantities.  Under-delivered products get reduced
    demand.  Products added during delivery get new return moves.
    → sale_flow_sync_service._reconcile_return_pickings()

R09 — Lost/broken wizard for missing rental returns
    When a return is validated with no backorder and quantities are
    missing, the lost/broken wizard opens.  Always creates invoiceable
    charge lines, even if price is 0.
    → sale_flow_return_service._check_missing_returns()
    → sale_flow_lost_broken_service._process_lost_broken()

R10 — Lost/broken charges use fee product
    Charges use the company's lost_broken_fee_product_id for tax/
    accounting.  Description mentions the actual product.  Lines are
    charge-only (skip_delivery, is_charge_only).
    → sale_flow_lost_broken_service._create_charge_line()

R11 — Rental status: pickup reflects stock move reality
    Standard Odoo checks qty_delivered < product_uom_qty.  This module
    overrides: if all outgoing moves are done/cancel, pickup is complete.
    You cannot pick up something that has already been picked up.
    → sale_order._compute_has_action_lines()  (pickable fix)

R12 — Rental status: returnable covers delivery-added products
    Standard Odoo only checks sale order lines.  If a rental product was
    added during delivery (no SOL), pending return moves still indicate
    items to return.
    → sale_order._compute_has_action_lines()  (returnable fix)

R13 — Delivery-added products appear on invoice by default
    When logistics adds a sale product during delivery, a sale order line
    is auto-created so it appears on the invoice.  Controlled by setting
    'Do not add on Invoice if added by Logistics'.
    → sale_flow_service._create_flow_line_from_delivery()

R14 — Rental detection for delivery-added products
    Rentable products (rent_periodicity set) delivered on a rental order are marked
    is_rental=True on the flow line — they must come back.
    → sale_flow_service._create_flow_line_from_delivery()

R15 — No procurement loop for delivery-added SOLs
    Creating a SOL from delivery uses skip_procurement context flag to
    prevent _action_launch_stock_rule from creating a duplicate delivery.
    The product was already delivered — there is nothing to procure.
    → sale_flow_service._create_flow_line_from_delivery()

R16 — Context flags prevent recursive sync
    skip_sale_flow_sync prevents recursive loops between model hooks.
    skip_procurement prevents Odoo from procuring already-delivered items.
    → used throughout all services and model hooks

R17 — Manual initialization for existing orders
    Existing orders created before sale_flow can be initialized via
    admin action 'Initialize Sale Flow Lines'.  New orders use sale flow
    automatically at confirmation.
    → sale_order.action_initialize_flow_lines()

R18 — Service-layer architecture
    Business logic lives in abstract model services, not in write()
    overrides.  Services: sale_flow_service, sale_flow_sync_service,
    sale_flow_return_service, sale_flow_invoice_service,
    sale_flow_lost_broken_service.

R19 — Auto-reconcile ordered qty (product-level)
    Products with auto_reconcile_delivered_qty=True have their SOL
    product_uom_qty adjusted to match qty_delivered once all logistics
    are complete.  This prevents 'upselling' status and ensures the
    invoice reflects actual delivery.  Products without the flag keep
    standard Odoo behavior.
    → product_product.auto_reconcile_delivered_qty
    → sale_order._auto_reconcile_delivered_qty()
    → stock_picking.button_validate() triggers it

R20 — Returnable only after pickup complete
    has_returnable_lines is only set when has_pickable_lines is False.
    Return pickings may be pre-created before delivery; the order must
    show 'pickup' until all outgoing moves are done.
    → sale_order._compute_has_action_lines()

R21 — Skip invoice for logistics-added products (company setting)
    Company setting sale_flow_skip_invoice_logistics controls whether
    products added during delivery get an auto-created SOL.  When ON,
    they stay internal (flow line only, no SOL).  When OFF (default),
    a SOL is created for invoicing.
    → res_company.sale_flow_skip_invoice_logistics
    → sale_flow_service._create_flow_line_from_delivery()

R22 — Cancel backorder auto-reconciles return demand
    When a backorder is cancelled, the return picking demand is
    automatically adjusted to match only what was actually delivered.
    Pending backorder demand is no longer expected back.
    → stock_picking.action_cancel()
    → sale_flow_sync_service._reconcile_return_pickings()

Rental Set Requirements (in rental_set module):

RS01 — Set availability uses actual order qty
    _collect_leaf_availability_data uses product_uom_qty / parent_qty
    (not set_component_qty) so manual component changes are reflected.
    → rental_set/sale_order_line._collect_leaf_availability_data()

RS02 — Allocation follows actual order qty
    _allocate_sum_prices and _allocate_fixed_prices use product_uom_qty
    for total allocation.  _recompute_set_price_from_components uses
    set_component_qty for stable per-unit price during compute cycles.
    → rental_set/sale_order_line._allocate_sum_prices()
    → rental_set/sale_order_line._recompute_set_price_from_components()

RS03 — Non-storable components = limitless stock
    Products with is_storable=False are skipped from the set availability
    demand check.  If ALL components are non-storable, availability =
    ordered qty (always fully available).
    → rental_set/sale_order_line._compute_set_availability()

RS04 — Set parent header move: zero-demand, display-only
    Header moves use product_uom_qty=0, quantity=0, picked=False,
    state=assigned.  They are display-only grouping entries.
    _action_cancel override forces them to done (via SQL) instead of
    cancel.  _sanity_check override suppresses zero-qty validation error.
    → rental_set/sale_order_line._create_set_parent_header_moves()
    → rental_set/models/stock_move._action_cancel()
    → rental_set/models/stock_picking._sanity_check()

RS05 — Return wizard excludes set parent header moves
    Return picking creation sets qty=0 for set parent moves so they
    don't appear as returnable lines.
    → rental_set/models/stock_picking._prepare_return_move_default_values()

RS06 — Price stable when set qty changes (sum mode)
    Doubling set parent qty must not halve per-unit price.  Components
    double, allocation doubles, but price_unit stays constant.
    → rental_set/sale_order_line._recompute_set_price_from_components()

RS07 — Availability hidden on unsaved lines
    The set availability field and stock icon are hidden on new/unsaved
    lines to avoid showing misleading red indicators.
    → rental_set/views/sale_order_views.xml (invisible="not id")
    → rental_set/static/src/js/rental_set_qty_widget.js (isNew check)

RS08 — Confirmed order availability excludes own demand
    For confirmed orders, the set availability uses free_qty_today from
    the component SOL (same as Odoo's standard stock indicator).  This
    correctly accounts for the order's own reservations.
    For draft orders, uses rental-aware _get_component_available_qty.
    → rental_set/sale_order_line._compute_set_availability()

RS09 — Backorder preserves set header moves
    When a picking with a set is partially validated and a backorder is
    created, the backorder automatically gets a new header move so the
    set grouping is preserved.  The original header goes to done.
    → rental_set/models/stock_picking._create_backorder_header_moves()

RS10 — Header picked=False for auto-pick compatibility
    Set parent header moves are created with picked=False so Odoo's
    standard auto-pick logic (line 1486-1487 of stock_picking.py) works
    correctly.  If the header had picked=True, it would prevent
    auto-picking of component moves, causing unnecessary backorder
    dialogs.
    → rental_set/sale_order_line._create_set_parent_header_moves()

RS11 — Duplicate order does not double set components
    When duplicating an order, the copy() override sets a context flag
    (rental_set_copying) to prevent create() from expanding the set a
    second time.  After copy, set_parent_line_id is remapped to point
    to the new parent lines (not the originals).
    → rental_set/models/sale_order.copy()
    → rental_set/models/sale_order_line.create()

RS12 — Competing demand: aggregate stock check
    When multiple lines on the same order demand the same product,
    availability must reflect aggregate demand, not per-line demand.

    Server-side (3 parts):
    a) order_product_demand field: total qty for the same product
       across all lines on the order.
    b) _compute_qty_at_date override: free_qty_today shows the real
       available stock excluding the ENTIRE order's demand (not just
       one line).  All lines for the same product show the same value.
    c) _compute_set_availability: subtracts competing demand from
       non-set lines when computing set availability.

    Client-side (2 parts):
    a) QtyAtDateWidget.initCalcData patch: compares free_qty_today
       against order_product_demand (not qty_to_deliver).  Red icon
       when aggregate demand > available.
    b) QtyAtDatePopover patch: injects "Used in this order" row
       into the popover when competing demand exists.

    → rental_set/sale_order_line.order_product_demand
    → rental_set/sale_order_line._compute_qty_at_date()
    → rental_set/sale_order_line._compute_set_availability()
    → rental_set/static/src/js/rental_set_qty_widget.js

RS13 — Forecast-based availability
    free_qty_today is computed from the forecast report's move data:
    minimum forecasted stock during the rental period.  For confirmed
    orders, the order's own outgoing demand is added back ("how much
    CAN this order use").  Capped at qty_available.  Avoids timing
    artifacts from _get_unavailable_qty.
    → rental_set/sale_order_line._compute_forecast_availability()
    → rental_set/sale_order_line._compute_qty_at_date()

RS14 — All-warehouse availability in popover
    When a product has stock in multiple warehouses, the popover shows
    "Available all warehouses" with the total stock across all warehouses.
    → rental_set/sale_order_line.all_warehouse_available
    → rental_set/sale_order_line.all_warehouse_count
    → rental_set/static/src/js/rental_set_qty_widget.js

RS15 — View Rentals grouped by customer
    The "View Rentals" action from the popover opens the Gantt view
    grouped by customer (order_partner_id) instead of by product.
    When viewing from a specific product line, customer grouping is
    more useful than product grouping.
    → rental_set/static/src/js/rental_set_qty_widget.js (openRentalGanttView)

RS16 — Nested sets: independent availability
    Nested sets (is_set + is_set_component) compute set_availability
    independently from their parent.  The parent's availability
    accounts for the nested set's demand through leaf flattening.
    The nested set's availability answers "how many of THIS set
    can be fulfilled" without being reduced by the parent's other
    components or other sets on the order.
    → rental_set/sale_order_line._compute_set_availability()

RS17 — Nested sets: no procurement, no stock widget
    Nested sets are excluded from _action_launch_stock_rule (no
    procurement moves — only leaf components get stock moves).
    display_qty_widget=False for all is_set lines so the standard
    stock widget doesn't show misleading red for virtual products.
    The JS aggregate demand check also skips all is_set lines.
    → rental_set/sale_order_line._action_launch_stock_rule()
    → rental_set/sale_order_line._compute_qty_to_deliver()
    → rental_set/static/src/js/rental_set_qty_widget.js

RS18 — Nested set headers on picking form
    _create_set_parent_header_moves processes sets in hierarchy
    order (top-level first, nested second).  Each header is placed
    above its direct component moves.  The picking renderer uses
    rental_set_parent_move_id to build parent-child relationships
    for fold/expand behavior.
    → rental_set/sale_order_line._create_set_parent_header_moves()

RS19 — Multi-step: headers carried across outbound chain
    In Pick→Pack→Ship flows, validating one step creates set header
    moves on the next step's picking.  _create_chained_header_moves
    finds next-step pickings via move_dest_ids and creates zero-demand
    headers so the set grouping is preserved across all outbound steps.
    → rental_set/models/stock_picking._create_chained_header_moves()

RS20 — Multi-step: delivered qty deduplication
    _update_outgoing_quantities only counts the final delivery step
    when moves come from different picking types.  The final step is
    identified by destination location (customer, transit, or rental).
    Without this, 3-step delivery would triple the delivered_qty.
    → sale_flow/services/sale_flow_sync_service._update_outgoing_quantities()
"""

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleFlowLine(models.Model):
    """Central business state model tracking the commercial/logistic flow.

    Each sale.flow.line represents one logical product line in a sale order
    and aggregates all related stock moves, returns, and invoice lines.

    Business rules:
      * At confirmation, a baseline snapshot (qty, price, discount, subtotal)
        is stored.  This is the agreed commercial reference.  (R01)
      * Logistics changes (delivery, return, lost, broken) update operational
        quantities but never silently change the agreed commercial price.  (R03)
      * Invoice preparation highlights differences between agreed baseline
        and actual operational state via warning levels:
          - orange: operational change, no price increase
          - red: invoice value increases (lost/broken charges)  (R06)
      * If the order is cancelled and reconfirmed, the baseline is reset.  (R02)
    """

    _name = 'sale.flow.line'
    _description = 'Sale Flow Line'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, id'
    _rec_name = 'name'

    # ── Identity / links ─────────────────────────────────────────────────

    name = fields.Char(
        string='Description',
        required=True,
        tracking=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Sale Order Line',
        ondelete='set null',
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='sale_order_id.company_id',
        store=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        related='sale_order_id.currency_id',
        store=True,
    )

    # ── Set support / hierarchy ──────────────────────────────────────────
    # These fields exist so the Rental Set module can use sale.flow.line
    # for set hierarchy tracking.  This module does NOT contain set-specific
    # business logic.

    set_parent_flow_line_id = fields.Many2one(
        'sale.flow.line',
        string='Set Parent Flow Line',
        ondelete='cascade',
        index=True,
    )
    set_root_flow_line_id = fields.Many2one(
        'sale.flow.line',
        string='Set Root Flow Line',
        ondelete='set null',
        index=True,
    )
    set_parent_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Set Parent Sale Line',
        ondelete='set null',
    )
    set_path = fields.Char(
        string='Set Path',
        help='Hierarchical path for ordering/grouping within sets.',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
    )
    is_set_component = fields.Boolean(
        string='Is Set Component',
        default=False,
    )
    visible_to_customer = fields.Boolean(
        string='Visible to Customer',
        default=True,
        help=(
            'Lines with confirmed_qty=0 (added after confirmation or during '
            'delivery) are internal by default unless explicitly made visible.'
        ),
    )

    # ── Product behavior ─────────────────────────────────────────────────

    is_rental = fields.Boolean(
        string='Is Rental',
        default=False,
    )
    is_sale = fields.Boolean(
        string='Is Sale',
        default=True,
    )
    is_charge_only = fields.Boolean(
        string='Charge Only',
        default=False,
        help=(
            'Charge-only lines are invoiceable but never create stock or '
            'delivery moves.  Used for lost/broken fee lines.'
        ),
    )
    skip_delivery = fields.Boolean(
        string='Skip Delivery',
        default=False,
        help='If True, this line must never create delivery moves.',
    )

    # ── Commercial baseline ──────────────────────────────────────────────
    # Stored at confirmation.  Represents the agreed commercial contract.
    # Never overwritten by logistics changes.

    confirmed_qty = fields.Float(
        string='Confirmed Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
    )
    confirmed_unit_price = fields.Monetary(
        string='Confirmed Unit Price',
        currency_field='currency_id',
        default=0.0,
        tracking=True,
    )
    confirmed_discount = fields.Float(
        string='Confirmed Discount (%)',
        digits='Discount',
        default=0.0,
    )
    confirmed_subtotal = fields.Monetary(
        string='Confirmed Subtotal',
        currency_field='currency_id',
        default=0.0,
        tracking=True,
    )
    confirmed_description = fields.Char(
        string='Confirmed Description',
    )
    confirmed_at = fields.Datetime(
        string='Confirmed At',
    )
    confirmed_by_id = fields.Many2one(
        'res.users',
        string='Confirmed By',
    )

    # ── Operational quantities ───────────────────────────────────────────
    # Updated by stock move hooks and service methods.

    current_qty = fields.Float(
        string='Current Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
        help='Current ordered quantity (may differ from confirmed after changes).',
    )
    prepared_qty = fields.Float(
        string='Prepared Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    delivered_qty = fields.Float(
        string='Delivered Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
    )
    returned_qty = fields.Float(
        string='Returned Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
    )
    cancelled_qty = fields.Float(
        string='Cancelled Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    lost_qty = fields.Float(
        string='Lost Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
    )
    broken_qty = fields.Float(
        string='Broken Qty',
        digits='Product Unit of Measure',
        default=0.0,
        tracking=True,
    )
    credited_qty = fields.Float(
        string='Credited Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )

    # ── Invoice quantities ───────────────────────────────────────────────

    invoice_candidate_qty = fields.Float(
        string='Invoice Candidate Qty',
        digits='Product Unit of Measure',
        compute='_compute_invoice_candidate_qty',
        store=True,
    )
    invoiced_qty = fields.Float(
        string='Invoiced Qty',
        digits='Product Unit of Measure',
        default=0.0,
    )
    invoice_warning_level = fields.Selection(
        [
            ('none', 'None'),
            ('orange', 'Orange'),
            ('red', 'Red'),
        ],
        string='Invoice Warning',
        default='none',
        tracking=True,
        help=(
            'orange = operational/logistic change but invoice value does not '
            'exceed agreed order value.\n'
            'red = invoice value increases because of lost/broken charges or '
            'explicit invoice increase.'
        ),
    )

    # ── Change tracking ──────────────────────────────────────────────────

    was_changed_after_confirmation = fields.Boolean(
        string='Changed After Confirmation',
        default=False,
    )
    change_origin = fields.Selection(
        [
            ('order', 'Order'),
            ('delivery', 'Delivery'),
            ('return', 'Return'),
            ('invoice', 'Invoice'),
            ('manual', 'Manual'),
            ('system', 'System'),
        ],
        string='Change Origin',
    )
    change_note = fields.Text(
        string='Change Note',
    )
    added_after_confirmation = fields.Boolean(
        string='Added After Confirmation',
        default=False,
    )
    added_during_delivery = fields.Boolean(
        string='Added During Delivery',
        default=False,
    )

    # ── Commercial policy ────────────────────────────────────────────────

    commercial_policy = fields.Selection(
        [
            ('included', 'Included'),
            ('free', 'Free'),
            ('invoice_extra', 'Invoice Extra'),
            ('replacement', 'Replacement'),
            ('lost_charge', 'Lost Charge'),
            ('broken_charge', 'Broken Charge'),
            ('credit', 'Credit'),
        ],
        string='Commercial Policy',
        default='included',
    )

    # ── Pricing ──────────────────────────────────────────────────────────

    unit_price_effective = fields.Monetary(
        string='Effective Unit Price',
        currency_field='currency_id',
        default=0.0,
    )
    subtotal_effective = fields.Monetary(
        string='Effective Subtotal',
        currency_field='currency_id',
        compute='_compute_subtotal_effective',
        store=True,
    )
    broken_lost_unit_price = fields.Monetary(
        string='Broken/Lost Unit Price',
        currency_field='currency_id',
        default=0.0,
    )

    # ── State ────────────────────────────────────────────────────────────

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('prepared', 'Prepared'),
            ('delivered', 'Delivered'),
            ('partially_returned', 'Partially Returned'),
            ('returned', 'Returned'),
            ('cancelled', 'Cancelled'),
            ('lost', 'Lost'),
            ('broken', 'Broken'),
            ('invoiced', 'Invoiced'),
        ],
        string='State',
        default='draft',
        tracking=True,
    )

    # ── Relations ────────────────────────────────────────────────────────

    outgoing_move_ids = fields.Many2many(
        'stock.move',
        'sale_flow_line_outgoing_move_rel',
        'flow_line_id',
        'move_id',
        string='Outgoing Moves',
    )
    return_move_ids = fields.Many2many(
        'stock.move',
        'sale_flow_line_return_move_rel',
        'flow_line_id',
        'move_id',
        string='Return Moves',
    )
    invoice_line_ids = fields.Many2many(
        'account.move.line',
        'sale_flow_line_invoice_line_rel',
        'flow_line_id',
        'invoice_line_id',
        string='Invoice Lines',
    )
    origin_flow_line_id = fields.Many2one(
        'sale.flow.line',
        string='Origin Flow Line',
        ondelete='set null',
        help='Reference to the original flow line (e.g. for lost/broken charges).',
    )

    # ── Audit ────────────────────────────────────────────────────────────

    last_flow_update_at = fields.Datetime(
        string='Last Flow Update',
    )
    last_flow_update_by_id = fields.Many2one(
        'res.users',
        string='Last Flow Update By',
    )

    # ── Computed fields ──────────────────────────────────────────────────

    @api.depends(
        'current_qty', 'delivered_qty', 'returned_qty',
        'lost_qty', 'broken_qty', 'credited_qty',
    )
    def _compute_invoice_candidate_qty(self):
        """Invoice candidate = delivered minus returns/credits for rental,
        or current_qty for sale products."""
        for line in self:
            if line.is_rental:
                line.invoice_candidate_qty = (
                    line.delivered_qty
                    - line.returned_qty
                    - line.credited_qty
                )
            else:
                # Sale products: invoice based on current ordered qty
                line.invoice_candidate_qty = line.current_qty - line.credited_qty

    @api.depends('unit_price_effective', 'current_qty', 'confirmed_discount')
    def _compute_subtotal_effective(self):
        for line in self:
            price = line.unit_price_effective * (1 - (line.confirmed_discount or 0.0) / 100.0)
            line.subtotal_effective = price * line.current_qty

    # ── Business methods ─────────────────────────────────────────────────

    def action_view_outgoing_moves(self):
        """Open related outgoing stock moves."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Outgoing Moves'),
            'res_model': 'stock.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.outgoing_move_ids.ids)],
        }

    def action_view_return_moves(self):
        """Open related return stock moves."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Return Moves'),
            'res_model': 'stock.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.return_move_ids.ids)],
        }

    def action_view_invoice_lines(self):
        """Open related invoice lines."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice Lines'),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_line_ids.ids)],
        }

    def _compute_warning_level(self):
        """Recompute the invoice warning level based on current state.

        Rules:
          none   = no changes since confirmation.
          orange = operational/logistic change, but invoice value does not
                   exceed the agreed (confirmed) order value.
          red    = invoice value increases beyond agreed value
                   (lost/broken charges, explicit increase, or charge-only
                   lines like lost/broken fees).
        """
        for line in self:
            if not line.was_changed_after_confirmation and not line.lost_qty and not line.broken_qty:
                line.invoice_warning_level = 'none'
                continue

            # Red: lost/broken charges or charge policies
            if line.commercial_policy in ('lost_charge', 'broken_charge'):
                line.invoice_warning_level = 'red'
            elif line.lost_qty or line.broken_qty:
                line.invoice_warning_level = 'red'
            else:
                # Default for all other operational changes: orange
                line.invoice_warning_level = 'orange'

    def _update_state(self):
        """Recompute state based on operational quantities."""
        for line in self:
            if line.state in ('cancelled', 'invoiced'):
                continue
            if line.lost_qty and not line.delivered_qty:
                line.state = 'lost'
            elif line.broken_qty and not line.delivered_qty:
                line.state = 'broken'
            elif line.returned_qty >= line.delivered_qty > 0:
                line.state = 'returned'
            elif line.returned_qty > 0 and line.returned_qty < line.delivered_qty:
                line.state = 'partially_returned'
            elif line.delivered_qty > 0:
                line.state = 'delivered'
            elif line.prepared_qty > 0:
                line.state = 'prepared'
            elif line.confirmed_qty > 0 or line.current_qty > 0:
                line.state = 'confirmed'
            else:
                line.state = 'draft'

    def _stamp_audit(self, origin='system', note=False):
        """Record who/when made a flow update."""
        now = fields.Datetime.now()
        uid = self.env.uid
        vals = {
            'last_flow_update_at': now,
            'last_flow_update_by_id': uid,
        }
        if origin:
            vals['change_origin'] = origin
        if note:
            vals['change_note'] = note
        self.write(vals)
