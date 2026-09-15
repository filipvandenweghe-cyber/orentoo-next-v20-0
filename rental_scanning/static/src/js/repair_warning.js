/** @odoo-module **/

import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Reusable Repair warning for serial scans.
 *
 * Non-blocking in business terms: if the scanned serial is linked to an
 * ACTIVE repair (confirmed / under_repair), the operator is asked to confirm.
 * "Proceed Anyway" continues the scan AND logs an audit entry
 * (rental.serial.log `repair_override`); "Cancel" aborts the scan.
 *
 * Works for any serial scan (the PPB flow and native serial entry) because it
 * only needs the scanned string and the Barcode model's `orm` /
 * `dialogService`.
 *
 * @param {Object} model  a BarcodePickingModel instance (has `orm`,
 *                         `dialogService`, `resId`)
 * @param {String} barcode the scanned string
 * @returns {Promise<"no-repair"|"proceed"|"cancel">}
 */
export async function checkAndWarnRepair(model, barcode) {
    let info;
    try {
        info = await model.orm.call("stock.lot", "rsl_repair_warning", [barcode]);
    } catch (e) {
        return "no-repair"; // the warning must never break scanning
    }
    if (!info || !info.has_repair) {
        return "no-repair";
    }
    return new Promise((resolve) => {
        model.dialogService.add(ConfirmationDialog, {
            title: _t("Serial under repair"),
            body: info.message || _t("This serial is linked to an active repair."),
            confirmLabel: _t("Proceed Anyway"),
            confirm: async () => {
                try {
                    await model.orm.call("rental.serial.log", "rsl_log_repair_override", [
                        barcode,
                        model.resId || false,
                        info.repair_id || false,
                    ]);
                } catch (e) {
                    // an audit failure must not block the operator
                }
                resolve("proceed");
            },
            cancelLabel: _t("Cancel"),
            cancel: () => resolve("cancel"),
        });
    });
}
