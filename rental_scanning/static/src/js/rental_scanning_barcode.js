/** @odoo-module **/

import BarcodePickingModel from "@stock_barcode/models/barcode_picking_model";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { checkAndWarnRepair } from "@rental_scanning/js/repair_warning";

/**
 * Route a scanned *source* package (a package that has contents) through the
 * server-side rental_scanning reconciliation instead of the native
 * "create a line per quant" path — which over-fills demand.  The server
 * method is strict-fit, idempotent, and honours PPB-17 (retain through
 * internal steps / dissolve at the customer).
 *
 * Everything else (empty packages, package types, put-in-pack, batches, …)
 * falls back to the native behaviour.  Any unexpected failure also falls
 * back, so this patch cannot break the Barcode app.
 */
patch(BarcodePickingModel.prototype, {
    /**
     * Intercept a scanned SET barcode (a rental-set product) and fill the
     * set's components via the server reconciliation (PPB-12).  All other
     * scans fall through to native handling.
     */
    async _processBarcode(barcode) {
        if (this.resModel === "stock.picking") {
            // Repair warning for ANY serial scan (PPB flow + native serial
            // entry).  Non-blocking: the operator may proceed, which is
            // audited; cancelling aborts this scan.  Non-serial scans return
            // "no-repair" instantly.
            const verdict = await checkAndWarnRepair(this, barcode);
            if (verdict === "cancel") {
                return;
            }
            let data = null;
            try {
                const filters = {
                    all: { company_id: [false].concat(this._getCompanyId() || []) },
                };
                data = await this._parseBarcode(barcode, filters);
            } catch (e) {
                data = null;
            }
            if (data && data.product && data.product.is_rental_set) {
                let handled = false;
                try {
                    handled = await this._rentalScanningApply(barcode);
                } catch (e) {
                    handled = false;
                }
                if (handled) {
                    return;
                }
            }
        }
        return super._processBarcode(...arguments);
    },

    async _processPackage(barcodeData) {
        const recPackage = barcodeData && barcodeData.package;
        const hasContents =
            recPackage &&
            recPackage.contained_quant_ids &&
            recPackage.contained_quant_ids.length;

        if (this.resModel === "stock.picking" && recPackage && hasContents) {
            let handled = false;
            try {
                handled = await this._rentalScanningApply(recPackage.name);
            } catch (e) {
                handled = false; // never break the app — fall back to native
            }
            if (handled) {
                barcodeData.stopped = true;
                return;
            }
        }
        return super._processPackage(...arguments);
    },

    /**
     * Call the server reconciliation for one scanned package.
     * @returns {Promise<boolean>} true if the scan was handled here.
     */
    async _rentalScanningApply(barcode, allowSplit = false) {
        // Persist any pending client-side changes first.
        await this.save();

        let result;
        try {
            result = await this.orm.call(
                "stock.picking",
                "rental_scanning_scan",
                [[this.resId], barcode],
                { allow_split: allowSplit }
            );
        } catch (error) {
            const message =
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                _t("The scanned package could not be applied.");
            this.notification(message, { type: "danger" });
            return true; // handled — do not fall back to the native overflow path
        }

        if (result && result.status === "need_split") {
            this.dialogService.add(ConfirmationDialog, {
                title: _t("Split package?"),
                body:
                    result.message ||
                    _t("This package holds more than this operation needs."),
                confirmLabel: _t("Split & continue"),
                confirm: async () => {
                    await this._rentalScanningApply(barcode, true);
                },
                cancelLabel: _t("Cancel"),
                cancel: () => {},
            });
            return true;
        }

        // applied / partial -> reload the client from the server.
        this.trigger("refresh");
        return true;
    },
});
