/**
 * rental_set_fold_field.js
 *
 * Custom field widget for `set_components_folded`.
 * Renders a single interactive column that serves two roles:
 *
 *   • Set parent row  → clickable chevron (▼ / ▶) that toggles
 *     `set_components_folded` on the record, causing the renderer
 *     to hide / show descendant component rows.
 *
 *   • Component row   → read-only indent label (└─, └─▶ …) that
 *     visualises the nesting depth without being interactive.
 *
 *   • Normal row      → empty cell (no column space wasted).
 */
import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class RentalSetFoldField extends Component {
    static template = "rental_set.FoldField";
    // Owl 3 ignores a static `props`; the schema goes through useProps().
    props = useProps({
        ...standardFieldProps,
    });

    // ── Helpers ────────────────────────────────────────────────────────────

    get isSetParent() {
        return !!this.props.record.data.is_set;
    }

    get isSetComponent() {
        return !!this.props.record.data.is_set_component;
    }

    get isFolded() {
        return !!this.props.record.data.set_components_folded;
    }

    /** Unicode box-drawing prefix computed server-side. */
    get indentLabel() {
        return this.props.record.data.set_indent_label || "";
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
