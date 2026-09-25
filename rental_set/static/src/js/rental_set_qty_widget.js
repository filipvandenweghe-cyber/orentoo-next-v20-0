/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillRender, onMounted } from "@odoo/owl";
import { usePopover } from "@web/core/popover/popover_hook";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import {
    QtyAtDatePopover,
    QtyAtDateWidget,
    qtyAtDateWidget,
} from "@sale_stock/widgets/qty_at_date_widget";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/*
 * RS12: Patch the standard QtyAtDateWidget to check aggregate demand.
 *
 * The standard widget compares free_qty_today >= qty_to_deliver per line.
 * We override to compare against order_product_demand (total demand for
 * the same product across all lines on the order).  If aggregate demand
 * exceeds available stock, the icon turns red on ALL lines for that product.
 *
 * The actual free_qty_today stays unchanged (shows real available stock).
 * The red/green indicator reflects whether the ORDER can be fulfilled.
 */
patch(QtyAtDateWidget.prototype, {
    initCalcData() {
        super.initCalcData();
        const { data } = this.props.record;
        if (!data.scheduled_date || !data.product_id) return;

        // Skip aggregate demand check for set lines — they have no real
        // stock (availability is computed from components via
        // set_availability, not free_qty_today).  This applies to both
        // top-level sets AND nested sets (is_set + is_set_component).
        if (data.is_set) return;

        const orderDemand = data.order_product_demand || 0;
        const lineQty = data.product_uom_qty || 0;
        const available = data.state === 'sale'
            ? data.free_qty_today
            : data.virtual_available_at_date;

        const markIssue = () => {
            this.calcData.will_be_fulfilled = false;
            this.calcData.forecasted_issue =
                ['draft', 'sent'].includes(data.state) ? !data.is_mto : true;
        };

        // Aggregate order demand across lines exceeds available.
        if (orderDemand > lineQty && available !== undefined
                && orderDemand > available) {
            markIssue();
        }

        // Risk-based "on option": red when live options held by OTHER orders
        // could make this line unfulfillable (worst case if they confirm).
        // Never noisy — only when the worst case actually falls short.
        if (data.rental_flag_options) {
            const onOption = data.rental_on_option_other || 0;
            if (onOption > 0) {
                const avail = data.rental_pickable || 0;
                const worstCase = Math.max(avail - onOption, 0);
                const demand = Math.max(orderDemand, lineQty);
                if (demand > worstCase) {
                    markIssue();
                }
            }
        }
    },
});

// Add custom field dependencies so they're loaded for the widget
patch(qtyAtDateWidget, {
    fieldDependencies: [
        ...qtyAtDateWidget.fieldDependencies,
        { name: 'order_product_demand', type: 'float' },
        { name: 'product_uom_qty', type: 'float' },
        { name: 'all_warehouse_available', type: 'float' },
        { name: 'all_warehouse_count', type: 'integer' },
        // Rental availability breakdown (RAV-05, RAV-13)
        { name: 'rental_reserved_self', type: 'float' },
        { name: 'rental_reserved_other', type: 'float' },
        { name: 'rental_in_repair', type: 'float' },
        { name: 'rental_pickable', type: 'float' },
        { name: 'rental_total_stock', type: 'float' },
        { name: 'rental_repair_installed', type: 'boolean' },
        { name: 'rental_onhand_json', type: 'json' },
        { name: 'rental_show_stock_locations', type: 'boolean' },
        { name: 'rental_on_option_other', type: 'float' },
        { name: 'rental_on_option_until', type: 'char' },
        { name: 'rental_flag_options', type: 'boolean' },
    ],
});

/*
 * Patch the standard QtyAtDatePopover to:
 * 1. Add "View Forecast" button alongside "View Rentals" on rental lines.
 * 2. Show "Used in this order" aggregate demand when it exceeds per-line qty.
 */
patch(QtyAtDatePopover.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        onMounted(() => {
            this._injectForecastButton();
            this._injectRentalBreakdown();
            this._injectOrderDemand();
            this._injectWarehouseAvailability();
        });
    },

    async openRentalGanttView() {
        // Override to group by customer instead of product.
        // We intercept the doAction call to modify the context before
        // the action is executed, rather than re-calling the RPC.
        const origDoAction = this.actionService.doAction.bind(this.actionService);
        this.actionService.doAction = (action, options) => {
            // Restore original immediately
            this.actionService.doAction = origDoAction;
            // Modify context if it's our rental action
            if (action && action.context) {
                delete action.context['search_default_groupby_product'];
                action.context['search_default_groupby_customer'] = true;
            }
            return origDoAction(action, options);
        };
        // Call the original method — it does the RPC correctly
        return super.openRentalGanttView();
    },

    _injectForecastButton() {
        const data = this.props.record?.data;
        if (!data?.is_rental || !data?.return_date || !data?.start_date) {
            return;
        }
        const popovers = document.querySelectorAll('.o_popover');
        if (!popovers.length) return;
        const popoverEl = popovers[popovers.length - 1];
        if (!popoverEl) return;

        const buttons = popoverEl.querySelectorAll('button.btn-link');
        let rentalBtn = null;
        for (const btn of buttons) {
            if (btn.textContent.includes('View Rentals') || btn.textContent.includes(_t('View Rentals'))) {
                rentalBtn = btn;
                break;
            }
        }
        if (!rentalBtn) return;
        if (popoverEl.querySelector('.rental_set_forecast_btn')) return;

        const forecastBtn = document.createElement('button');
        forecastBtn.className = 'text-start btn btn-link rental_set_forecast_btn';
        forecastBtn.type = 'button';
        forecastBtn.innerHTML =
            '<i class="oi oi-fw o_button_icon oi-arrow-right"></i> ' + _t('View Forecast');
        forecastBtn.addEventListener('click', () => this.openForecast());
        rentalBtn.after(forecastBtn);
    },

    /*
     * Inject the auditable availability breakdown into the rental popover:
     * per-location on-hand, Reserved (this order), Reserved (other orders),
     * In Repair (only if the repair module is installed) and Pickable.
     * (RAV-05, RAV-13)
     */
    _injectRentalBreakdown() {
        const data = this.props.record?.data;
        if (!data?.product_id) return;
        // Rental lines only; sets have their own set-availability popover.
        if (!data.is_rental || !data.return_date || !data.start_date) return;
        if (data.is_set) return;

        const popovers = document.querySelectorAll('.o_popover');
        if (!popovers.length) return;
        const popoverEl = popovers[popovers.length - 1];
        if (!popoverEl) return;
        if (popoverEl.querySelector('.rental_set_breakdown')) return;

        const table = popoverEl.querySelector('table tbody');
        if (!table) return;

        const uom = data.product_uom_id && data.product_uom_id[1] ? data.product_uom_id[1] : '';
        const fmt = (v) => {
            const n = Number(v || 0);
            return Number.isInteger(n) ? String(n) : n.toFixed(2);
        };
        const addRow = (label, value, opts = {}) => {
            const row = document.createElement('tr');
            row.className = 'rental_set_breakdown' + (opts.top ? ' border-top' : '');
            const strong = opts.strong ? 'strong' : 'span';
            const sign = opts.sign ? `<span class="text-muted me-1">${opts.sign}</span>` : '';
            const valCls = opts.danger ? ' class="text-danger"'
                : (opts.warn ? ' class="text-warning fw-bold"' : '');
            row.innerHTML = `
                <td class="${opts.indent ? 'ps-3' : ''}"><${strong}${opts.muted ? ' class="text-muted"' : ''}>${label}</${strong}></td>
                <td class="text-end">${sign}<${strong}${valCls}>${fmt(value)}</${strong}> ${uom}</td>
            `;
            table.appendChild(row);
        };

        const total = data.rental_total_stock || 0;
        const other = data.rental_reserved_other || 0;
        const avail = data.rental_pickable || 0;
        const self = data.rental_reserved_self || 0;
        const requested = data.order_product_demand || data.product_uom_qty || 0;
        const missing = Math.max(requested - avail, 0);

        const addHeader = (label) => {
            const hdr = document.createElement('tr');
            hdr.className = 'rental_set_breakdown border-top';
            hdr.innerHTML = `<td colspan="2" class="text-muted small pt-1">${label}</td>`;
            table.appendChild(hdr);
        };

        // ── Section 1: Availability (time-based; returns within the window
        // are counted, so this can exceed the physically-free stock).
        addHeader(_t('For this rental'));
        addRow(_t('Reserved by other orders'), other, { muted: true });
        addRow(_t('Available to this order'), avail, { strong: true });
        if (self > 0) {
            addRow(_t('of which reserved for this order'), self,
                   { muted: true, indent: true });
        }

        // On option by OTHER orders (soft hold) — awareness line + worst case.
        const onOption = data.rental_flag_options
            ? (data.rental_on_option_other || 0) : 0;
        if (onOption > 0) {
            let label = _t('On option by other orders');
            if (data.rental_on_option_until) {
                label += ` (${_t('until')} ${data.rental_on_option_until})`;
            }
            addRow(label, onOption, { warn: true });
            const worst = Math.max(avail - onOption, 0);
            addRow(_t('Available if those options confirm'), worst,
                   { strong: true, danger: requested > worst });
        }

        addRow(_t('Requested by this order'), requested);
        addRow(_t('Missing for this order'), missing,
               { strong: missing > 0, danger: missing > 0 });

        // ── Section 2: Physical stock (point-in-time; a stable, conserved
        // Total that the location list always sums back to).  Off by default
        // — enabled via the Rental setting for troubleshooting.
        if (data.rental_show_stock_locations) {
            addHeader(_t('Physical stock (right now)'));
            addRow(_t('Total stock'), total, { strong: true });
            const onhand = data.rental_onhand_json || [];
            if (Array.isArray(onhand) && onhand.length) {
                for (const entry of onhand) {
                    addRow(entry.location, entry.qty,
                           { muted: true, indent: true });
                }
            }
        }
    },

    /*
     * Lazily fetch and show this product's availability per warehouse of the
     * company (only fetched when the pop-up opens).  Hidden for single-
     * warehouse companies, sets and non-rental lines (see the backend
     * get_rental_warehouse_availability).  Informational only.
     */
    async _injectWarehouseAvailability() {
        const data = this.props.record?.data;
        if (!data?.product_id) return;
        if (!data.is_rental || !data.return_date || !data.start_date) return;
        if (data.is_set) return;
        const resId = this.props.record.resId;
        if (!resId) return;

        let rows = [];
        try {
            rows = await this.orm.call(
                "sale.order.line", "get_rental_warehouse_availability",
                [resId],
            );
        } catch {
            return;
        }
        if (!rows || !rows.length) return;

        const popovers = document.querySelectorAll('.o_popover');
        if (!popovers.length) return;
        const popoverEl = popovers[popovers.length - 1];
        if (!popoverEl) return;
        if (popoverEl.querySelector('.rental_set_wh_avail')) return;
        const table = popoverEl.querySelector('table tbody');
        if (!table) return;

        const uom = data.product_uom_id && data.product_uom_id[1]
            ? data.product_uom_id[1] : '';
        const fmt = (v) => {
            const n = Number(v || 0);
            return Number.isInteger(n) ? String(n) : n.toFixed(2);
        };

        const hdr = document.createElement('tr');
        hdr.className = 'rental_set_wh_avail border-top';
        hdr.innerHTML = `<td colspan="2" class="text-muted small pt-1">`
            + `${_t('Availability by warehouse')}</td>`;
        table.appendChild(hdr);

        for (const r of rows) {
            const row = document.createElement('tr');
            row.className = 'rental_set_wh_avail';
            const tag = r.is_current
                ? ` <span class="text-muted">(${_t('this order')})</span>`
                : '';
            row.innerHTML = `
                <td class="ps-3">${r.name}${tag}</td>
                <td class="text-end"><b>${fmt(r.available)}</b> ${uom}</td>
            `;
            table.appendChild(row);
        }
    },

    _injectOrderDemand() {
        const data = this.props.record?.data;
        if (!data?.product_id) return;
        // Skip for set lines — they have no real stock
        if (data.is_set) return;

        const popovers = document.querySelectorAll('.o_popover');
        if (!popovers.length) return;
        const popoverEl = popovers[popovers.length - 1];
        if (!popoverEl) return;
        if (popoverEl.querySelector('.rental_set_extra_info')) return;

        const orderDemand = data.order_product_demand || 0;
        const lineQty = data.product_uom_qty || 0;
        const available = data.free_qty_today || data.virtual_available_at_date || 0;
        const isOverDemand = orderDemand > available;
        const allWhAvailable = data.all_warehouse_available || 0;
        const allWhCount = data.all_warehouse_count || 0;
        const uom = data.product_uom_id && data.product_uom_id[1] ? data.product_uom_id[1] : '';

        const table = popoverEl.querySelector('table tbody');
        if (!table) return;

        // Show "Used in this order" when there is competing demand
        if (orderDemand > lineQty) {
            const row = document.createElement('tr');
            row.className = 'rental_set_extra_info';
            row.innerHTML = `
                <td><strong>${_t('Used in this order')}</strong></td>
                <td class="text-end">
                    <b class="${isOverDemand ? 'text-danger' : ''}">${orderDemand}</b>
                    ${uom}
                </td>
            `;
            table.appendChild(row);
        }

        // Show "Available all warehouses" when product is in multiple warehouses
        if (allWhCount > 1) {
            const row = document.createElement('tr');
            row.className = 'rental_set_extra_info';
            row.innerHTML = `
                <td><strong>${_t('Available all warehouses')}</strong></td>
                <td class="text-end">
                    <b>${allWhAvailable}</b> ${uom}
                </td>
            `;
            table.appendChild(row);
        }

        // Add warning text if aggregate demand exceeds available
        if (isOverDemand && orderDemand > lineQty) {
            const btnContainer = popoverEl.querySelector('button.btn-link')?.parentElement;
            if (btnContainer) {
                const warning = document.createElement('div');
                warning.className = 'rental_set_extra_info text-danger small mt-1 mb-1';
                warning.innerHTML =
                    '<i class="fa fa-exclamation-triangle"></i> ' +
                    _t('Total order demand exceeds available stock.');
                btnContainer.parentElement.insertBefore(warning, btnContainer);
            }
        }
    },
});


/**
 * Popover content for Rental Set availability (set parent line).
 */
export class RentalSetAvailPopover extends Component {
    static template = "rental_set.RentalSetAvailPopover";
    static props = {
        record: Object,
        calcData: Object,
        close: Function,
    };
}

/**
 * Widget for Rental Set parent lines — shows green/red stock icon
 * based on set_availability vs product_uom_qty.
 */
export class RentalSetQtyWidget extends Component {
    static components = { Popover: RentalSetAvailPopover };
    static template = "rental_set.RentalSetQtyWidget";
    static props = { ...standardWidgetProps };

    setup() {
        this.popover = usePopover(this.constructor.components.Popover, {
            position: "top",
        });
        this.calcData = {};
        onWillRender(() => this.initCalcData());
    }

    initCalcData() {
        const data = this.props.record.data;
        const isSetParent = data.is_set && !data.is_set_component;
        this.calcData.isSetParent = isSetParent;

        if (!isSetParent) {
            this.calcData.show = false;
            return;
        }

        if (this.props.record.isNew) {
            this.calcData.show = false;
            return;
        }

        const requested = data.product_uom_qty || 0;
        const available = data.set_availability || 0;
        this.calcData.show = requested > 0;
        this.calcData.available = available;
        this.calcData.requested = requested;
        this.calcData.sufficient = available >= requested;
    }

    showPopup(ev) {
        if (!ev.currentTarget || !ev.currentTarget.isConnected) {
            return;
        }
        this.popover.open(ev.currentTarget, {
            record: this.props.record,
            calcData: this.calcData,
        });
    }
}

export const rentalSetQtyWidget = {
    component: RentalSetQtyWidget,
    fieldDependencies: [
        { name: "display_qty_widget", type: "boolean" },
        { name: "is_set", type: "boolean" },
        { name: "is_set_component", type: "boolean" },
        { name: "set_availability", type: "float" },
        { name: "product_uom_qty", type: "float" },
    ],
};

registry
    .category("view_widgets")
    .add("rental_set_qty_widget", rentalSetQtyWidget);
