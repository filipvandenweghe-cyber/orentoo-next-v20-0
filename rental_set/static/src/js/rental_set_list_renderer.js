/**
 * rental_set_list_renderer.js
 *
 * Patches SaleOrderLineListRenderer with Rental Set drag-drop and
 * collapse / expand behaviour.
 *
 * ── Collapse / expand ────────────────────────────────────────────────────
 * 1. Before every render, `buildSetParentMap()` rebuilds a Map from
 *    resId → OWL record for every saved line currently in the list.
 *
 * 2. `shouldCollapseSetComponent(record)` walks up the set_parent_line_id
 *    chain and returns true if any ancestor has set_components_folded=true.
 *    NOTE: Odoo 19 OWL stores Many2one values as { id, display_name },
 *    so we use ?.id (NOT the legacy [id, name] RPC tuple).
 *
 * 3. `getRowClass(record)` appends `o_rental_set_hidden` (display:none) to
 *    component rows that should be hidden by a collapsed ancestor.
 *
 * ── Drag-drop rules ───────────────────────────────────────────────────────
 * • Component rows are draggable (o_row_draggable preserved) so the user
 *   can reorder components within their own set.
 *
 * • `sortDrop` override — after the standard drop, `normalizeSetBlocks()`
 *   restores one invariant: a set parent is immediately followed by its
 *   descendants (DFS order), with nothing in between.  That single rule
 *   covers a set parent being moved, a component being dragged out of its
 *   set, and a foreign row being dropped inside a set block.
 *
 *   Records are keyed on DB resIds (not OWL datapoint IDs) because
 *   sale_management's sortDrop calls leaveEditMode() BEFORE the actual
 *   resequence, which can trigger a record reload and assign new datapoint
 *   IDs.  resIds are stable across reloads.
 */
import { patch } from "@web/core/utils/patch";
import { SaleOrderLineListRenderer } from "@sale/js/sale_order_line_field/sale_order_line_field";
import { onWillRender } from "@web/owl2/utils";

patch(SaleOrderLineListRenderer.prototype, {

    setup() {
        super.setup();
        this.setParentMap = new Map();
        onWillRender(() => this.buildSetParentMap());
    },

    // ── Set deletion: cascade to component rows ────────────────────────────

    /**
     * @override
     * When a Rental Set line is deleted, immediately remove all its
     * descendant component lines from the UI list (before saving).
     *
     * Descendants are collected first, then deleted bottom-up so that
     * nested sets are cleaned up before their parents.
     */
    async onDeleteRecord(record) {
        if (record.data.is_set) {
            const descendants = this._collectDescendantRecords(record);
            if (descendants.length) {
                // Delete bottom-up (deepest children first)
                for (const desc of descendants.reverse()) {
                    if (this.activeActions.onDelete) {
                        await this.activeActions.onDelete(desc);
                    }
                }
            }
        }
        return super.onDeleteRecord(record);
    },

    /**
     * Collect all descendant records (children, grandchildren, etc.) of a
     * set record using breadth-first traversal.
     *
     * @param  {Object} parentRecord  OWL record with is_set=true
     * @returns {Object[]}  flat array of descendant OWL records
     */
    _collectDescendantRecords(parentRecord) {
        const result = [];
        const parentId = parentRecord.resId;
        if (!parentId) return result;

        const queue = [parentId];
        while (queue.length) {
            const pid = queue.shift();
            for (const rec of this.props.list.records) {
                if (rec.data.set_parent_line_id?.id === pid) {
                    result.push(rec);
                    if (rec.resId) {
                        queue.push(rec.resId);
                    }
                }
            }
        }
        return result;
    },

    // ── Collapse helpers ──────────────────────────────────────────────────

    buildSetParentMap() {
        this.setParentMap.clear();
        for (const record of this.props.list.records) {
            if (record.resId) {
                this.setParentMap.set(record.resId, record);
            }
        }
    },

    shouldCollapseSetComponent(record) {
        if (!this.setParentMap || !record.data.is_set_component) {
            return false;
        }
        const parentId = record.data.set_parent_line_id?.id;
        if (!parentId) return false;

        const parentRecord = this.setParentMap.get(parentId);
        if (!parentRecord) return false;

        if (parentRecord.data.set_components_folded) return true;
        return this.shouldCollapseSetComponent(parentRecord);
    },

    getRowClass(record) {
        let classNames = super.getRowClass(record);
        if (
            record.data.is_set_component &&
            this.shouldCollapseSetComponent(record)
        ) {
            classNames = `${classNames} o_rental_set_hidden`;
        }
        return classNames;
    },

    // ── One-click interaction overrides ──────────────────────────────────

    /**
     * @override
     * Safety net for the fold-column chevron.
     *
     * In normal flow the button's `t-on-click.stop.prevent` + `special_click`
     * flag prevent this method from doing anything for chevron clicks.
     * If that chain somehow fails (e.g. focus-management edge cases), we catch
     * it here: enter edit mode but do NOT auto-toggle — the widget's onToggle
     * already fired and is handling the toggle.
     */
    async onCellClicked(record, column, ev, newWindow) {
        if (column.name === 'set_components_folded' && ev.target.closest('button')) {
            // Chevron clicked on non-selected row: the widget's onToggle
            // handles the actual toggle; here we only ensure the row enters
            // edit mode (consistent state), then bail.
            if (record !== this.props.list.editedRecord) {
                await this.props.list.enterEditMode(record);
            }
            return;
        }
        return super.onCellClicked(record, column, ev, newWindow);
    },

    /**
     * @override
     * Called by ViewButton's onClick when the clicked record `isNew`
     * (parent sale order not yet saved).  Odoo's default shows "Please save
     * your changes first", which leaves the user stuck.  We auto-save the
     * parent instead — after save the set-parent line has a real DB id and the
     * wizard can open on the very next click.
     */
    async displaySaveNotification() {
        await this.props.list.model.root.save();
    },

    // ── Drag-drop helpers ─────────────────────────────────────────────────

    /**
     * Desired row order for the whole list.
     *
     * Everything that is not a set component keeps its current relative
     * position; every set parent is immediately followed by its descendants
     * (DFS, siblings in their current relative order).
     *
     * Orphans — a component whose parent line is not in the list — are
     * treated as roots and left where they are, so nothing can ever vanish
     * from view because of a broken link.
     *
     * @returns {Object[]}  records, in the order they should appear
     */
    computeDesiredSetOrder() {
        const records = this.props.list.records;

        // Only saved lines have a resId, and set_parent_line_id points at one.
        const resIdsInList = new Set(records.map((r) => r.resId).filter(Boolean));
        const parentResIdOf = (rec) =>
            rec.data.is_set_component ? rec.data.set_parent_line_id?.id || null : null;
        const hasParentInList = (rec) => {
            const pid = parentResIdOf(rec);
            return !!pid && resIdsInList.has(pid);
        };

        // parentResId → [child records], in current visual order
        const childrenByParent = new Map();
        for (const rec of records) {
            if (!hasParentInList(rec)) continue;
            const pid = parentResIdOf(rec);
            if (!childrenByParent.has(pid)) {
                childrenByParent.set(pid, []);
            }
            childrenByParent.get(pid).push(rec);
        }

        const desired = [];
        const seen = new Set();
        const emit = (rec) => {
            if (seen.has(rec.id)) return; // also guards against a parent cycle
            seen.add(rec.id);
            desired.push(rec);
            for (const child of childrenByParent.get(rec.resId) || []) {
                emit(child);
            }
        };
        for (const rec of records) {
            if (!hasParentInList(rec)) {
                emit(rec);
            }
        }
        return desired;
    },

    /**
     * Resequence the list until it matches computeDesiredSetOrder().
     *
     * Walks left to right and only issues a resequence for a position that
     * is actually wrong, so a clean drop costs nothing.  The record list is
     * re-read on every iteration because resequence() re-sorts it.
     *
     * Records are matched on resId when they have one (stable across the
     * reload that sale_management.sortDrop can trigger) and on the datapoint
     * id otherwise.
     */
    async normalizeSetBlocks() {
        const keyOf = (rec) => rec.resId || rec.id;
        const desiredKeys = this.computeDesiredSetOrder().map(keyOf);

        for (let i = 0; i < desiredKeys.length; i++) {
            const records = this.props.list.records;
            if (records[i] && keyOf(records[i]) === desiredKeys[i]) {
                continue; // already in place
            }
            const rec = records.find((r) => keyOf(r) === desiredKeys[i]);
            if (!rec) continue;
            const prevRec =
                i > 0 ? records.find((r) => keyOf(r) === desiredKeys[i - 1]) : null;
            // Odoo 20: StaticList.resequence([movedId], targetId) — the moved
            // id is an array and the options argument no longer exists.
            await this.props.list.resequence(
                [String(rec.id)],
                prevRec ? String(prevRec.id) : null
            );
        }
    },

    /**
     * @override
     * Extends the base sortDrop with the Rental Set layout invariant:
     *
     *   a set parent is immediately followed by its components (and their
     *   own nested components, DFS order), with nothing in between.
     *
     * Enforcing that invariant after the drop — rather than special-casing
     * "set parent moved" and "component moved" — closes the case that used
     * to slip through: a foreign row (a plain product line, or a component
     * of another set) dropped in the middle of a set block silently became
     * part of that block.  It is now pushed out below the block instead.
     */
    async sortDrop(dataRowId, params) {
        // Odoo 20 changed the signature: sortDrop(dataRowId, {element, previous})
        // — the old (dataRowId, dataGroupId, params) triple is gone.
        await super.sortDrop(dataRowId, params);
        await this.normalizeSetBlocks();
    },
});
