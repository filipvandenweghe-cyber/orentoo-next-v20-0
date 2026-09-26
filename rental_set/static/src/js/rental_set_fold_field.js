/**
 * rental_set_fold_field.js
 *
 * Custom field widget for `set_components_folded` — the set hierarchy gutter.
 *
 *   • Row WITH components → one caret button (arrow_drop_down open /
 *     arrow_right folded) that toggles `set_components_folded`, so the
 *     renderer hides / shows the descendant rows.  While folded it also
 *     shows the component count, so a collapsed set never hides rows
 *     silently (RS-45).
 *
 *   • Every other row     → empty cell.
 *
 * Nesting depth is NOT drawn here: the renderer indents the product cell
 * per level (RS-40).  No box-drawing characters are used (RS-41).
 */
import { Component, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class RentalSetFoldField extends Component {
    static template = "rental_set.FoldField";
    // Owl 3 ignores a static `props`; the schema goes through useProps().
    props = useProps({
        ...standardFieldProps,
    });

    // ── Helpers ────────────────────────────────────────────────────────────

    /**
     * RS-42: a caret is drawn only where there is something to fold.  A set
     * line whose components have all been deleted gets no marker.
     */
    get hasChildren() {
        return !!this.props.record.data.is_set && this.childCount > 0;
    }

    get childCount() {
        return this.props.record.data.set_child_count || 0;
    }

    get isFolded() {
        return !!this.props.record.data.set_components_folded;
    }

    get toggleTitle() {
        return this.isFolded ? _t("Show components") : _t("Hide components");
    }

    get hiddenTitle() {
        return _t("%s components hidden", this.childCount);
    }

    // ── Interaction ────────────────────────────────────────────────────────

    async onToggle(ev) {
        ev.stopPropagation();
        ev.preventDefault();
        // Tell onCellClicked to skip its logic if it somehow fires despite
        // stopPropagation (safety net — covers edge cases in Odoo's event
        // dispatch chain). We intentionally ignore `props.readonly` here
        // because the fold state is pure UI and should always be toggleable.
        ev.target.special_click = true;
        await this.props.record.update({
            set_components_folded: !this.isFolded,
        });
    }
}

export const rentalSetFoldField = {
    component: RentalSetFoldField,
    displayName: "Rental Set Fold",
};

// Register both the generic and the list-specific variant
registry.category("fields").add("rental_set_fold", rentalSetFoldField);
registry.category("fields").add("list.rental_set_fold", rentalSetFoldField);
