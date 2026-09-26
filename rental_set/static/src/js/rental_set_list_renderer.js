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
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useOwnedDialogs } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/** Indentation step per nesting level, capped so a deep set cannot eat the
 *  description column (RS-11/RS-40). Mirrors o_rental_set_indent_* in SCSS. */
const MAX_INDENT_LEVEL = 4;

patch(SaleOrderLineListRenderer.prototype, {

    setup() {
        super.setup();
        this.setParentMap = new Map();
        this.addDialog = useOwnedDialogs();
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
                // RS-52: never delete rows the user cannot see.  A collapsed
                // set hides its components, so state what will go before the
                // cascade runs.
                if (!(await this._confirmSetDeletion(record, descendants))) {
                    return;
                }
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
     * Ask before removing a whole Rental Set (RS-52/RS-53).
     *
     * The wording follows what will actually happen: on a draft/sent order the
     * lines are deleted; on a confirmed order Odoo forbids deleting lines, so
     * `sale.order.line.unlink` zeroes the components instead and cancels their
     * moves.
     *
     * @param  {Object}   record       the set line being deleted
     * @param  {Object[]} descendants  its descendants, DFS order
     * @returns {Promise<boolean>} true when the user confirmed
     */
    _confirmSetDeletion(record, descendants) {
        const setName = record.data.product_id?.display_name || _t("this set");
        const draft = ["draft", "sent"].includes(record.data.state);
        const lines = descendants
            .map((rec) => {
                const level = Math.max((rec.data.set_level || 1) - 1, 0);
                const name = rec.data.product_id?.display_name || "";
                const qty = rec.data.product_uom_qty;
                return `${"    ".repeat(level)}• ${name} — ${qty}`;
            })
            .join("\n");
        const intro = draft
            ? _t(
                  "%(name)s will be removed together with the %(count)s lines it contains:",
                  { name: setName, count: descendants.length }
              )
            : _t(
                  "%(name)s will be emptied (quantities set to 0) together with the %(count)s lines it contains:",
                  { name: setName, count: descendants.length }
              );
        return new Promise((resolve) => {
            this.addDialog(
                ConfirmationDialog,
                {
                    title: draft ? _t("Remove this rental set?") : _t("Empty this rental set?"),
                    body: `${intro}\n\n${lines}`,
                    confirmLabel: draft ? _t("Remove set") : _t("Empty set"),
                    confirmClass: "btn-primary",
                    confirm: () => resolve(true),
                    cancelLabel: _t("Cancel"),
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
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
        // RS-43: subordinate a set line the way Odoo 20 subordinates a section
        // in this same list — a tint + weight ladder, two levels deep.
        if (record.data.is_set) {
            classNames = record.data.is_set_component
                ? `${classNames} o_rental_set_parent_nested`
                : `${classNames} o_rental_set_parent`;
        }
        return classNames;
    },

    /**
     * @override
     * RS-40: show nesting depth by indenting the product cell, the way core
     * indents its hierarchical list (`account_account_list_view` adds
     * `o_list_indent_#{i}` from getCellClass).  Like core, the indentation is
     * dropped as soon as the user sorts, filters or groups the list, because
     * the rows are then no longer in parent → child order and an indent would
     * claim a hierarchy that is not on screen.
     */
    getCellClass(column, record) {
        let classNames = super.getCellClass(column, record);
        if (
            column.name === "product_and_description" &&
            record.data.is_set_component &&
            this._rentalSetIndentAllowed()
        ) {
            const level = Math.min(record.data.set_level || 1, MAX_INDENT_LEVEL);
            classNames = `${classNames} o_rental_set_indent_${level}`;
        }
        return classNames;
    },

    /** False while the list is reordered, filtered or grouped (see above). */
    _rentalSetIndentAllowed() {
        const list = this.props.list;
        return !(
            (list.orderBy || []).length ||
            (list.domain || []).length ||
            (list.groupBy || []).length
        );
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
