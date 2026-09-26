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
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class RentalSetPickingFoldField extends Component {
    static template = "rental_set.PickingFoldField";
    // Owl 3 ignores a static `props`; the schema goes through useProps().
    props = useProps({
        ...standardFieldProps,
    });

    /**
     * RS-42/RS-47: one caret, and only where there is something to fold.
     * ``rental_set_is_set`` is already computed as "is a set AND has
     * components", so it doubles as the has-children test here.
     */
    get hasChildren() {
        return !!this.props.record.data.rental_set_is_set;
    }

    get isFolded() {
        return !!this.props.record.data.rental_set_folded;
    }

    get toggleTitle() {
        return this.isFolded ? _t("Show components") : _t("Hide components");
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
