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
 * • `sortDrop` override — uses DB resIds (not OWL datapoint IDs) throughout
 *   because sale_management's sortDrop calls leaveEditMode() BEFORE the
 *   actual resequence, which can trigger a record reload and assign new
 *   datapoint IDs to existing records.  resIds are stable across reloads.
 *
 *   – Set parent dragged  → after the parent's sequence is saved, every
 *     descendant (DFS tree order) is resequenced to follow it.
 *
 *   – Component dragged   → after the drop the new position is validated.
 *     If the component landed outside its parent's range (isWithinParentRange),
 *     the move is immediately reverted.
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
     * Collect all descendant DB IDs (resIds) of a set record in DFS tree
     * order, matching their current visual sequence.
     *
     * We use resIds (DB integers) rather than OWL datapoint IDs because
     * sale_management.sortDrop calls leaveEditMode() before the resequence,
     * which may reload records and assign fresh datapoint IDs.
     *
     * @param  {Object} parentRecord  OWL record with is_set=true and a resId
     * @returns {number[]}  flat array of DB IDs in tree order
     */
    collectSetDescendantResIds(parentRecord) {
        const result = [];

        // Build parentResId → [childResId, …] index
        const childResIdsByParentResId = new Map();
        for (const record of this.props.list.records) {
            if (!record.resId) continue; // skip unsaved records
            const parentResId = record.data.set_parent_line_id?.id;
            if (!parentResId) continue;
            if (!childResIdsByParentResId.has(parentResId)) {
                childResIdsByParentResId.set(parentResId, []);
            }
            childResIdsByParentResId.get(parentResId).push(record.resId);
        }

        // Sort each sibling group by current visual position
        for (const siblings of childResIdsByParentResId.values()) {
            siblings.sort((a, b) => {
                const ai = this.props.list.records.findIndex((r) => r.resId === a);
                const bi = this.props.list.records.findIndex((r) => r.resId === b);
                return ai - bi;
            });
        }

        // DFS: parent → its children (in order) → their children, etc.
        const dfs = (resId) => {
            for (const childResId of childResIdsByParentResId.get(resId) || []) {
                result.push(childResId);
                dfs(childResId);
            }
        };

        dfs(parentRecord.resId);
        return result;
    },

    /**
     * Check that a component record is still adjacent to its parent after a
     * drop.  Walks backwards from the component's position:
     *   • If the parent record is found first            → valid ✓
     *   • If a sibling (same set_parent_line_id)  is found → keep walking ✓
     *   • Anything else first                            → invalid ✗
     *
     * Uses resId comparisons (stable DB integers) throughout.
     *
     * @param {Object} movedRecord  current OWL record for the moved component
     * @param {number} parentResId  DB id of the expected parent line
     * @returns {boolean}
     */
    isWithinParentRange(movedRecord, parentResId) {
        const records = this.props.list.records;
        const movedIdx = records.indexOf(movedRecord);

        for (let i = movedIdx - 1; i >= 0; i--) {
            const rec = records[i];
            if (rec.resId === parentResId) return true;               // found parent ✓
            if (rec.data.set_parent_line_id?.id === parentResId) continue; // sibling ✓
            return false; // something else → out of range ✗
        }
        return false;
    },

    /**
     * @override
     * Extends the base sortDrop with Rental Set rules, using resId-based
     * record lookup throughout to survive potential record reloads.
     *
     * 1. Set parent moved → resequence all descendants (DFS tree order) to
     *    follow, using fresh record lookups after each await.
     *
     * 2. Component moved → validate position; if it landed outside its
     *    parent's range resequence it back to its original position.
     */
    async sortDrop(dataRowId, params) {
        // Odoo 20 changed the signature: sortDrop(dataRowId, {element, previous})
        // — the old (dataRowId, dataGroupId, params) triple is gone.
        const records = this.props.list.records;

        // Identify the moved record
        const movedRecord = records.find((r) => String(r.id) === String(dataRowId));

        // Capture stable DB IDs BEFORE the drop
        // (datapoint IDs may be reassigned after leaveEditMode + reload)
        const movedResId = movedRecord?.resId || null;
        const isSetParent = !!movedRecord?.data.is_set;
        const movedParentResId = movedRecord?.data.is_set_component
            ? movedRecord.data.set_parent_line_id?.id
            : null;

        // Remember record before the moved row (for component revert)
        const movedIdx = movedRecord ? records.indexOf(movedRecord) : -1;
        const prevResId = movedIdx > 0 ? records[movedIdx - 1].resId : null;

        // Collect descendant DB IDs before the drop
        const descendantResIds = isSetParent
            ? this.collectSetDescendantResIds(movedRecord)
            : [];

        // ── Standard drop ─────────────────────────────────────────────────
        await super.sortDrop(dataRowId, params);

        // ── Set parent: move descendants to follow ────────────────────────
        if (descendantResIds.length) {
            // Re-find parent by resId — its datapoint ID may have changed
            const parentRecord = movedResId
                ? this.props.list.records.find((r) => r.resId === movedResId)
                : null;
            let lastId = parentRecord ? String(parentRecord.id) : String(dataRowId);

            for (const resId of descendantResIds) {
                // Fresh lookup by resId after each await (list may have re-sorted)
                const childRecord = this.props.list.records.find((r) => r.resId === resId);
                if (!childRecord) continue;
                // Odoo 20: StaticList.resequence([movedId], targetId) — the
                // moved id is an array and there is no options argument.
                await this.props.list.resequence([String(childRecord.id)], lastId);
                lastId = String(childRecord.id);
            }
            return;
        }

        // ── Component: revert if dropped outside its parent range ─────────
        if (movedParentResId && movedResId) {
            const currentMovedRecord = this.props.list.records.find(
                (r) => r.resId === movedResId
            );
            if (
                currentMovedRecord &&
                !this.isWithinParentRange(currentMovedRecord, movedParentResId)
            ) {
                const prevRecord = prevResId
                    ? this.props.list.records.find((r) => r.resId === prevResId)
                    : null;
                await this.props.list.resequence(
                    [String(currentMovedRecord.id)],
                    prevRecord ? String(prevRecord.id) : null
                );
            }
        }
    },
});
