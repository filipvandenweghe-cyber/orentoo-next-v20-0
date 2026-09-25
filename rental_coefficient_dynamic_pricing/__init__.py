# =============================================================================
# Coefficient & Dynamic Pricing — Business Requirements Index
# =============================================================================
#
# PRICING FORMULA
# ---------------
# final_price = base_rental_price x coefficient x dynamic_multiplier x qty
#
# base_rental_price : Odoo-native pricelist result for ONE rental period.
#                     Falls back to Sales Price when no pricelist rule matches.
#                     Odoo 20 removed product.pricing (rental pricing moved to
#                     product.rent_periodicity + the pricelist) and prices a
#                     rental line for the WHOLE period, so the adapter divides
#                     the period count back out -- see RE01.
# coefficient       : looked up from a rental.coefficient.table by duration.
# dynamic_multiplier: weighted-average factor_percentage / 100 from a
#                     rental.dynamic.pricing.table across the rental period.
# qty               : sale order line product_uom_qty (handled by Odoo).
#
# COEFFICIENT REQUIREMENTS
# -------------------------
# RC01  Coefficient types categorise coefficient tables (e.g. Standard,
#       Professional, Weekend, Campaign, Special Customer Agreement).
# RC02  A default "Standard" coefficient type is created on module install.
# RC03  Coefficient type names are unique per company.
# RC04  Coefficient tables link to exactly one coefficient type.
# RC05  A coefficient table defines a duration_unit (minute, hour, day, week,
#       month) and a list of coefficient lines.
# RC06  Each coefficient line specifies an "as from duration" threshold
#       (>= 0) and a coefficient value (> 0).
# RC07  Duplicate "as from duration" values within the same table are blocked
#       (SQL UNIQUE constraint).
# RC08  Coefficient lines are automatically sorted by as_from_duration.
# RC09  Lookup: get_coefficient_for_duration(duration_int) returns the
#       coefficient of the line with the highest as_from_duration that does
#       not exceed the given duration.  Returns False when no line matches.
# RC10  At most one coefficient table per company + coefficient type may be
#       marked as "standard" (the default fallback table).
# RC11  Resolution order for selecting which coefficient table applies to a
#       rental line (future — not yet implemented):
#         1. Product + Warehouse + Customer  ->  specific table
#         2. Product + Warehouse             ->  table
#         3. Warehouse                       ->  default table
#         4. Customer                        ->  customer table
#         5. Global standard table for the coefficient type
#
# DYNAMIC PRICING REQUIREMENTS
# -----------------------------
# RD01  Dynamic pricing tables contain date/time-ranged factor lines.
# RD02  Factor percentage is entered as a percentage (100 = no change,
#       120 = +20 %, 80 = -20 %).  Must be > 0.
# RD03  Lines within the same table may not overlap (Python constraint).
# RD04  Start datetime must be strictly before end datetime (SQL CHECK).
# RD05  A rental period may span multiple factor lines and/or uncovered gaps.
# RD06  Uncovered parts of the rental period default to 100 %.
# RD07  get_weighted_factor_percentage(start_dt, end_dt) returns the
#       time-weighted average factor percentage across the rental period.
#       Weighting is by seconds of overlap.
# RD08  When no factor line overlaps the rental period, returns 100.0.
# RD09  The dynamic multiplier used in the pricing formula is
#       factor_percentage / 100.
# RD10  selection_calendar field (start_hour / start_day) is reserved for
#       future calendar-aware granularity rounding.
#
# PRODUCT / WAREHOUSE / CUSTOMER CONFIGURATION
# -----------------------------------------------
# RP01  rental.product.warehouse.pricing.config links a product template
#       to a warehouse with a dynamic pricing table and coefficient tables.
# RP02  Duplicate product + warehouse configurations are blocked
#       (SQL UNIQUE constraint).
# RP03  The warehouse determines the company; dynamic pricing tables and
#       coefficient tables are domain-filtered to that company.
# RP04  product.template gains a "Coefficient & Dynamic Pricing" tab
#       (visible only for rentable products) with One2many config lines.
# RP05  _get_applicable_pricing_config(warehouse) returns the product-level
#       config for the given warehouse.  Designed for future product-category
#       fallback without rewriting the pricing engine.
# RP06  _get_applicable_dynamic_pricing_table(warehouse) returns the
#       dynamic pricing table from the applicable config.
# RP07  _get_applicable_coefficient_tables(warehouse) returns the
#       coefficient tables from the applicable config.
# RP08  res.partner gains an allowed_coefficient_table_ids Many2many field
#       shown on the Sales & Purchase tab below the pricelist.
# RP09  _get_customer_allowed_coefficient_tables(company) returns the
#       partner's allowed tables optionally filtered by company.
# RP10  res.partner.use_dynamic_pricing boolean (default True).
#       When disabled, dynamic pricing factor is always 100 % for the
#       customer.  Checked in _compute_dynamic_pricing_values().
#
# PRICING ENGINE (rental.pricing.service)
# ------------------------------------------
# RE01  _get_base_rental_price_for_line(line) retrieves the Odoo-native
#       base rental price and normalises it to ONE period via
#       _to_single_period_price(line, price).  THESE TWO METHODS ARE THE
#       ONLY COUPLING POINT TO ODOO'S RENTAL PRICING -- REVIEW THEM AFTER
#       EVERY ODOO UPGRADE.  Odoo 20: _get_pricelist_price() returns the
#       price of the whole rental period; the coefficient table applies the
#       duration itself, so the period count is divided back out or the
#       duration would be counted twice.  The normalisation is applied to
#       EVERY base: standalone line, fixed set parent and component sum.
# RE02  _compute_duration_integer(start, end, unit) converts a rental
#       period to a rounded-up integer in the table's duration unit.
#       Minimum 1 for any positive period.
# RE03  _get_applicable_coefficient_table(product, partner, warehouse)
#       selects the single best table:  product/warehouse ∩ customer →
#       lowest sequence → standard fallback → empty.
# RE04  _get_coefficient_for_context(...) returns the coefficient value.
#       Fallback when no table/line matches: coefficient = duration (not 1).
# RE05  _get_dynamic_factor_percentage_for_context(...) returns the
#       weighted-average factor percentage for the rental period.
# RE06  _get_dynamic_multiplier_for_context(...) returns percentage / 100.
# RE07  _compute_final_rental_price(base, coeff, multiplier) combines
#       base × coefficient × dynamic_multiplier.
#
# SALE ORDER LINE INTEGRATION
# -----------------------------
# RI01  _compute_price_unit() override: after super() sets the native
#       base rental price, applies coefficient × dynamic_multiplier.
#       For newly created lines, create() calls
#       _apply_coefficient_dynamic_pricing() which uses write().
# RI02  Base-price adapter: _get_pricelist_price() with context flag
#       skip_coefficient_dynamic_pricing retrieves the Odoo-native price,
#       then _to_single_period_price() scales it to one period (see RE01).
#       REVIEW AFTER EVERY ODOO UPGRADE.
# RI03  Stored detail fields on sale.order.line: base_rental_price,
#       applied_coefficient, applied_dynamic_factor_percentage,
#       applied_dynamic_multiplier, manual override flags.
# RI04  Manual override: onchange sets manual_*_override flag and
#       recalculates price from stored base × coeff × multiplier.
# RI05  Period/product/partner changes clear manual overrides and
#       trigger full recalculation.  Quantity changes do not.
# RI06  Manual unit-price override.  Editing price_unit by hand sets
#       manual_price_override (detected via a divergence between
#       price_unit and technical_price_unit, which the engine always keeps
#       equal).  While set, the coefficient/dynamic engine does NOT
#       overwrite the unit price on quantity changes — the hand-typed price
#       is preserved.  The flag is cleared (engine regains control) when the
#       product, rental period or partner changes, when the coefficient or
#       dynamic factor is edited, or on a forced recomputation
#       (force_price_recomputation).  Odoo 20 removed the "Update Rental
#       Prices" button and sale.order.show_update_duration; the force path is
#       now order._recompute_rental_prices().
# RI07  Idempotent recomputation — no coefficient compounding.  Each
#       recomputation derives the final price from the Odoo-native base
#       (base × coefficient × dynamic_multiplier), never from the
#       already-adjusted price_unit.  The engine writes price_unit and
#       technical_price_unit together under the sale_write_from_compute
#       context so they stay in sync; otherwise super()._compute_price_unit
#       would mistake the line for a manually priced one, skip the base
#       reset, and let the engine multiply the already-adjusted price by the
#       coefficient again — compounding the price on every quantity or
#       period change.
# RI08  Auto-recompute rental prices on save.  sale.order.write() refreshes
#       the coefficient/duration/dynamic driven unit price of rental lines
#       whenever a price-relevant field changes (period, customer, pricelist,
#       or any order-line edit) on a draft/sent quotation.  Since Odoo 20
#       removed the "Update Rental Prices" button this is the only automatic
#       path; the partner onchange also recomputes softly (RI05).  It runs
#       WITHOUT force, so lines
#       with a hand-typed price (technical_price_unit != price_unit, i.e.
#       manual_price_override) are preserved; only the explicit button
#       force-resets them.  Re-entrancy is guarded by the rental_save_recompute
#       context flag, and a button-driven force recomputation is skipped.
#
# RENTAL_SET COMPATIBILITY
# --------------------------
# RF01  Coefficient × dynamic is applied to the set parent price only;
#       component lines remain at price_unit = 0.
#       Fixed-mode: coefficient applied to the fixed pricelist price.
#       Sum-mode:   coefficient applied to the sum of component prices.
#       set_allocated_price on components is recalculated after coefficient
#       so allocations sum to the coefficient-adjusted parent total.
#       >>> If rental_set changes _allocate_fixed_prices or
#           _allocate_sum_prices, review _recalculate_set_allocations. <<<
# RF03  Component-change reprice.  When a sum-mode set's component qty/product
#       changes, rental_set resets the parent to the RAW component sum via a
#       new hook (_reapply_set_derived_pricing).  This module overrides that
#       hook to reapply coefficient × dynamic to the fresh sum (base = raw sum,
#       so no compounding — RI07) and rescale the component allocations, so
#       price_unit = SUM(components) × coefficient × dynamic holds after an
#       edit, exactly as on create.  A manually-priced parent
#       (manual_price_override) is frozen: _recompute_set_price_from_components
#       is skipped and the price is kept as typed.
#       >>> Depends on rental_set calling _reapply_set_derived_pricing after
#           its sum recompute; review if that hook changes. <<<
#
# FUTURE REQUIREMENTS (not yet implemented)
# -------------------------------------------
# RF02  Website: extend _get_additionnal_combination_info() to show the
#       adjusted price on the product configurator / rental calendar.
#
# SECURITY REQUIREMENTS
# ----------------------
# RS01  Internal users (group_sale_salesman) have read access to all
#       coefficient and dynamic pricing models.
# RS02  Sales managers (group_sale_manager) have full CRUD on all
#       coefficient and dynamic pricing models.
#
# =============================================================================

from . import models
from . import services
