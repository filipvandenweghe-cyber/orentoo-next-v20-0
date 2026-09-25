/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";

const MODEL = "rental.availability.report";

/** Drill-down dialog for a single cell. */
export class AvailabilityCellDialog extends Component {
    setup() {
        this.action = useService("action");
    }

    /** Open the sale/rental order form for a contributing order. */
    openOrder(orderId) {
        if (!orderId) {
            return;
        }
        this.props.close();
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: orderId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}
AvailabilityCellDialog.template = "rental_set.AvailabilityCellDialog";
AvailabilityCellDialog.components = { Dialog };
AvailabilityCellDialog.props = { detail: Object, close: Function };

/**
 * Read-only Availability Report client action.
 *
 * All numbers come from the server (`rental.availability.report`, which reuses
 * the canonical `_rental_available_qty` engine). This component only renders,
 * lets the user actively pick scope (tags stay visible), and switches display
 * modes client-side (each cell carries `available` + `capacity`).
 */
export class AvailabilityMatrix extends Component {
    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");

        this.state = useState({
            loading: false,
            displayMode: "available",
            filters: {
                categoryIds: [],
                productIds: [],
                companyIds: [],
                warehouseIds: [],
                orderIds: [],
                onlyUnavailable: false,
                includeOptions: false,
                start: null,
                interval: "day",
            },
            data: { columns: [], rows: [], cells: {} },
        });

        onWillStart(() => this.load());
    }

    // ── domains for the selectors ───────────────────────────────────────
    get productDomain() {
        const dom = [
            ["is_storable", "=", true],
            ["is_rental_set", "=", false],
        ];
        if (this.state.filters.categoryIds.length) {
            dom.push(["categ_id", "child_of", this.state.filters.categoryIds]);
        }
        return dom;
    }

    get warehouseDomain() {
        if (this.state.filters.companyIds.length) {
            return [["company_id", "in", this.state.filters.companyIds]];
        }
        return [];
    }

    // ── selector update handlers ────────────────────────────────────────
    onFilterUpdate(field, resIds) {
        this.state.filters[field] = resIds;
    }

    onIntervalChange(ev) {
        this.state.filters.interval = ev.target.value;
        this.state.filters.start = null; // realign on interval change
        this.load();
    }

    onDisplayChange(ev) {
        this.state.displayMode = ev.target.value;
    }

    onOnlyUnavailableChange(ev) {
        this.state.filters.onlyUnavailable = ev.target.checked;
        this.load();
    }

    onIncludeOptionsChange(ev) {
        this.state.filters.includeOptions = ev.target.checked;
        this.load();
    }

    // ── start date/time picker ──────────────────────────────────────────
    /** "YYYY-MM-DD" for the day-interval <input type="date">. */
    get startDateValue() {
        const s = this.state.filters.start;
        return s ? s.slice(0, 10) : "";
    }

    /** "YYYY-MM-DDTHH:MM" for the <input type="datetime-local">. */
    get startDateTimeValue() {
        const s = this.state.filters.start;
        return s ? `${s.slice(0, 10)}T${s.slice(11, 16)}` : "";
    }

    onDateChange(ev) {
        const v = ev.target.value;
        if (!v) {
            this.state.filters.start = null;
        } else if (this.state.filters.interval === "day") {
            this.state.filters.start = `${v} 00:00:00`;
        } else {
            // datetime-local "YYYY-MM-DDTHH:MM"
            this.state.filters.start = `${v.replace("T", " ")}:00`;
        }
        this.load();
    }

    // ── data loading ────────────────────────────────────────────────────
    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(MODEL, "get_availability_matrix", [
                {
                    category_ids: this.state.filters.categoryIds,
                    product_ids: this.state.filters.productIds,
                    company_ids: this.state.filters.companyIds,
                    warehouse_ids: this.state.filters.warehouseIds,
                    order_ids: this.state.filters.orderIds,
                    only_unavailable: this.state.filters.onlyUnavailable,
                    include_options: this.state.filters.includeOptions,
                    start: this.state.filters.start,
                    interval: this.state.filters.interval,
                },
            ]);
            this.state.data = data;
            // Remember the aligned window start so Prev/Next shift from it.
            this.state.filters.start = data.start;
        } finally {
            this.state.loading = false;
        }
    }

    // ── navigation ──────────────────────────────────────────────────────
    _shiftStart(days) {
        const s = this.state.data.start;
        if (!s) {
            return null;
        }
        // Server start is UTC-naive "YYYY-MM-DD HH:MM:SS".
        const d = new Date(s.replace(" ", "T") + "Z");
        d.setUTCDate(d.getUTCDate() + days);
        const p = (n) => String(n).padStart(2, "0");
        return (
            `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
            `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
        );
    }

    onPrev() {
        const step = this.state.filters.interval === "day" ? -21 : -1;
        this.state.filters.start = this._shiftStart(step);
        this.load();
    }

    onNext() {
        const step = this.state.filters.interval === "day" ? 21 : 1;
        this.state.filters.start = this._shiftStart(step);
        this.load();
    }

    onNow() {
        this.state.filters.start = null;
        this.load();
    }

    // ── rendering helpers (single combined identity column) ─────────────
    /** True when this row opens a new category → render a group header row. */
    isNewCategory(row, index) {
        if (index === 0) {
            return true;
        }
        return this.state.data.rows[index - 1].category !== row.category;
    }

    /** True when this row opens a new product → render a product sub-header. */
    isNewProduct(row, index) {
        if (index === 0) {
            return true;
        }
        const prev = this.state.data.rows[index - 1];
        return prev.category !== row.category || prev.product_id !== row.product_id;
    }

    /** Show the company prefix only when it changes within the product. */
    showCompany(row, index) {
        if (this.isNewProduct(row, index)) {
            return true;
        }
        return this.state.data.rows[index - 1].company_id !== row.company_id;
    }

    _cell(row, colIndex) {
        return (
            this.state.data.cells[`${row.key}-${colIndex}`] || {
                available: 0,
                capacity: 0,
            }
        );
    }

    _fmt(n) {
        return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
    }

    cellText(row, colIndex) {
        const c = this._cell(row, colIndex);
        if (this.state.displayMode === "ratio") {
            return `${this._fmt(c.available)} / ${this._fmt(c.capacity)}`;
        }
        if (this.state.displayMode === "utilisation") {
            if (!c.capacity) {
                return "N/A";
            }
            const util = ((c.capacity - c.available) / c.capacity) * 100;
            return `${this._fmt(Math.round(util * 10) / 10)}%`;
        }
        return this._fmt(c.available);
    }

    cellClass(row, colIndex) {
        const c = this._cell(row, colIndex);
        if (this.state.displayMode === "utilisation") {
            if (!c.capacity) {
                return "o_ra_normal";
            }
            const util = ((c.capacity - c.available) / c.capacity) * 100;
            if (util > 100) return "o_ra_neg";
            if (util === 100) return "o_ra_zero";
            return "o_ra_normal";
        }
        // Available and ratio: colour by signed availability.
        if (c.available < 0) return "o_ra_neg";
        if (c.available === 0) return "o_ra_zero";
        return "o_ra_normal";
    }

    // ── drill-down ──────────────────────────────────────────────────────
    async onCellClick(row, colIndex) {
        const col = this.state.data.columns[colIndex];
        if (!col) {
            return;
        }
        const detail = await this.orm.call(MODEL, "get_cell_detail", [
            row.product_id,
            row.company_id,
            row.warehouse_id,
            col.start,
            col.stop,
        ]);
        if (detail && Object.keys(detail).length) {
            this.dialog.add(AvailabilityCellDialog, { detail });
        }
    }
}

AvailabilityMatrix.template = "rental_set.AvailabilityMatrix";
AvailabilityMatrix.components = { MultiRecordSelector };

registry.category("actions").add("rental_availability_matrix", AvailabilityMatrix);
