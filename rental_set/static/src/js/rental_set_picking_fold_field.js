/**
 * rental_set_picking_fold_field.js
 *
 * Custom field widget for `rental_set_folded` on stock.move lines
 * inside the picking form.  Mirrors the rental_set_fold widget used
 * on sale.order.line, providing the same chevron toggle and indent
 * labels so the picker gets an identical visual experience.
 *
 *   • Set parent move  → clickable chevron (▼ / ▶)
 *   • Component move   → read-only indent label (└─, …)
 *   • Normal move      → empty cell
 */
import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class RentalSetPickingFoldField extends Component {
    static template = "rental_set.PickingFoldField";
    // Owl 3 ignores a static `props`; the schema goes through useProps().
    props = useProps({
        ...standardFieldProps,
    });

    get isSetParent() {
        return !!this.props.record.data.rental_set_is_set;
    }

    get isSetComponent() {
        return !!this.props.record.data.rental_set_is_component;
    }

    get isFolded() {
        return !!this.props.record.data.rental_set_folded;
    }

    get indentLabel() {
        return this.props.record.data.rental_set_indent_label || "";
    }

    async onToggle(ev) {
        ev.stopPropagation();
        ev.preventDefault();
        ev.target.special_click = true;
        await this.props.record.update({
            rental_set_folded: !this.isFolded,
        });
    }
}

export const rentalSetPickingFoldField = {
    component: RentalSetPickingFoldField,
    displayName: "Rental Set Picking Fold",
};

registry.category("fields").add("rental_set_picking_fold", rentalSetPickingFoldField);
registry.category("fields").add("list.rental_set_picking_fold", rentalSetPickingFoldField);
