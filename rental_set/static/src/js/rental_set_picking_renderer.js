/**
 * rental_set_picking_renderer.js
 *
 * Patches MovesListRenderer (used in the picking form) with Rental Set
 * collapse / expand behaviour, identical to the sale order line list.
 *
 * Only effective on outgoing pickings (deliveries / pickups).  On
 * incoming pickings (returns) the fold column is hidden via XML so
 * this code has no visual effect.
 *
 * ── How it works ─────────────────────────────────────────────────────
 * 1. Before every render, `buildSetParentMap()` builds a Map from
 *    move DB id → OWL record for every move in the list.
 *
 * 2. `shouldCollapseSetComponent(record)` walks up the
 *    rental_set_parent_move_id chain and returns true if any ancestor
 *    has rental_set_folded = true.
 *
 * 3. `getRowClass(record)` appends `o_rental_set_hidden` to component
 *    rows that should be hidden by a collapsed ancestor.
 */
import { patch } from "@web/core/utils/patch";
import { MovesListRenderer } from "@stock/views/picking_form/stock_move_one2many";
import { onWillRender } from "@web/owl2/utils";

patch(MovesListRenderer.prototype, {

    setup() {
        super.setup();
        this.setParentMoveMap = new Map();
        onWillRender(() => this.buildSetParentMap());
    },

    // ── Collapse helpers ──────────────────────────────────────────────

    buildSetParentMap() {
        this.setParentMoveMap.clear();
        for (const record of this.props.list.records) {
            if (record.resId) {
                this.setParentMoveMap.set(record.resId, record);
            }
        }
    },

    shouldCollapseSetComponent(record) {
        if (!this.setParentMoveMap || !record.data.rental_set_is_component) {
            return false;
        }
        const parentId = record.data.rental_set_parent_move_id?.id;
        if (!parentId) return false;

        const parentRecord = this.setParentMoveMap.get(parentId);
        if (!parentRecord) return false;

        if (parentRecord.data.rental_set_folded) return true;
        return this.shouldCollapseSetComponent(parentRecord);
    },

    getRowClass(record) {
        let classNames = super.getRowClass(record);
        if (
            record.data.rental_set_is_component &&
            this.shouldCollapseSetComponent(record)
        ) {
            classNames = `${classNames} o_rental_set_hidden`;
        }
        return classNames;
    },

    // ── Chevron click handling ────────────────────────────────────────

    /**
     * @override
     * Prevent onCellClicked from entering edit mode when the chevron
     * button is clicked — the widget's onToggle handles the toggle.
     */
    async onCellClicked(record, column, ev, newWindow) {
        if (column.name === 'rental_set_folded' && ev.target.closest('button')) {
            if (record !== this.props.list.editedRecord) {
                await this.props.list.enterEditMode(record);
            }
            return;
        }
        return super.onCellClicked(record, column, ev, newWindow);
    },
});
