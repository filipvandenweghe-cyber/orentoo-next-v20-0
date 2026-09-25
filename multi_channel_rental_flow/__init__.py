# Multi-Channel Rental Flow
# ========================
#
# This module provides a multi-channel ordering flow (website, kiosk,
# backend) that bundles rental items, event tickets, add-ons and
# services into a unified dossier with single-payment checkout.
#
# ===================================================================
# Business Requirements Index
# ===================================================================
#
# --- Product Configuration (PC) ---
# PC01  Products are configured at product.product (variant) level.
# PC02  Each variant has a master toggle: use_in_multi_channel_rental_flow.
# PC03  Item role selection: rental, event_ticket, addon, service, flex_option.
# PC04  Channel flags: available_in_multi_channel_website, _kiosk.
# PC05  Behavior flags: requires_timeslot, requires_duration, allow_quantity_selection.
# PC06  Display flag: use_product_image_in_flow (default True).
# PC07  Role onchange sets sensible defaults (rental → timeslot+duration).
# PC08  Disabling master toggle clears channel flags.
# PC09  Template shows full config for single-variant, summary for multi-variant.
#
# --- Profile Configuration (PF) ---
# PF01  Profile represents a kiosk, website or backend channel.
# PF02  Profile links to warehouse, pricelist, company.
# PF03  Profile links to POS config (kiosk) or website.
# PF04  Product assortment via eCommerce categories and POS categories.
# PF05  Explicit product include/exclude lists.
# PF06  Feature toggles: rental, event tickets, addons, services.
# PF07  Guest checkout modes: login_required, guest_with_email, guest_minimal.
# PF08  Configurable slot interval (minutes) and advance days.
# PF09  Fallback duration options when no coefficient table applies.
# PF10  Dynamic pricing color thresholds (low/high %) and 5 color fields.
# PF11  Payment providers, mode (online/terminal/POS/both), demo toggle.
# PF12  Printer mode: ePOS IP, IoT Box, browser, none — reuses pos.printer.
# PF13  Print behavior: receipt on payment, tickets on payment, combined.
# PF14  Effective product domain helper: _get_available_products().
#
# --- Dossier Model (DO) ---
# DO01  Dossier = one customer, one currency, one payment.
# DO02  Auto-sequence: DOS/YYYY/NNNNN or RD prefix.
# DO03  Description field for easy identification (project use).
# DO04  Display name = number + description concatenation.
# DO05  Source: backend, website, kiosk.
# DO06  State machine: draft → confirmed → payment_pending → paid → done.
# DO07  Additional states: payment_failed, payment_expired, payment_exception, cancelled.
# DO08  Chatter with state tracking.
# DO09  M2M sale_order_ids: manually linkable + auto-generated.
# DO10  Linked order total computed from sale orders.
# DO11  Cancel cascades to items, slots, and generated orders.
# DO12  Reset to draft (manager only) restores cancelled items/slots.
# DO13  Profile onchange sets warehouse, pricelist, source.
# DO14  Partner onchange fills email.
#
# --- Dossier Slot (DS) ---
# DS01  One slot = one rental timeslot (start + end datetime).
# DS02  Slot start must be before end (constraint).
# DS03  Warehouse per slot, defaults from dossier.
# DS04  Computed display name: "DD/MM/YYYY HH:MM – HH:MM".
# DS05  One slot generates one sale/rental order.
# DS06  Slot states: draft → order_created → confirmed → cancelled.
#
# --- Dossier Item (DI) ---
# DI01  Item = one product line in the dossier.
# DI02  Item role: rental, event_ticket, addon, service, flex_option.
# DI03  Optional slot link (non-slot items grouped separately).
# DI04  Quantity must be positive (constraint).
# DI05  Price preview: price_unit, price_subtotal, coefficient, dynamic factor.
# DI06  Availability state: unknown, available, unavailable, outside_opening_hours.
# DI07  Color code for dynamic pricing display.
# DI08  Event fields as integer IDs (soft dependency safe).
# DI09  Link to generated sale.order.line.
# DI10  Product onchange sets role from product configuration.
# DI11  Pricing onchange computes preview: base × coefficient × dynamic multiplier.
# DI12  Item states: draft → priced → generated → confirmed → cancelled / exception.
#
# --- Sale Order Integration (SO) ---
# SO01  sale.order extended with mcrf_dossier_id (M2O) for generated orders.
# SO02  sale.order M2M mcrf_dossier_ids for manual + generated linking.
# SO03  mcrf_dossier_display: stored computed "number — description".
# SO04  Dossier column on sales order list and rental order list.
# SO05  Dossier field in Other Info tab (Sales column) on order form.
# SO06  sale.order.line extended with mcrf_dossier_item_id back-link.
# SO07  payment.transaction extended with mcrf_dossier_id.
#
# --- Pricing Service (PR) ---
# PR01  Central service: all pricing in backend, no JS duplication.
# PR02  Rental price: reuses rental_coefficient_dynamic_pricing engine.
# PR03  Price formula: base_price × coefficient × dynamic_multiplier.
# PR04  Addon/service price: standard pricelist or lst_price.
# PR05  Event ticket price: product lst_price (event_sale pricing later).
# PR06  Duration options from coefficient table lines (skip as_from=0).
# PR07  Fallback durations: profile options → system default (1,2,4,8h).
# PR08  Slot generation from warehouse opening hours at profile interval.
# PR09  Fallback slots: 08:00–18:00 when no calendar configured.
# PR10  Event slots: uses event.slot (multi-slot) or event date (single).
# PR11  Forecasted stock per slot for storable products.
# PR12  Non-storable consumables treated as always available.
# PR13  Day availability: available/partial/unavailable/closed with counts.
# PR14  Price calendar: hourly (3-day) or daily (7-day) window.
# PR15  Color codes from profile thresholds (low/normal/high/unavailable/closed).
# PR16  Slot timezone & past-slot cutoff. Slot start times are computed and
#       displayed in the warehouse opening-hours calendar timezone
#       (_get_flow_timezone; falls back to the user tz, then UTC). Fallback
#       08:00–18:00 slots are localized to that tz and stored as UTC, so the
#       picker and the basket/checkout show the same wall-clock time. Slots
#       whose start is already in the past are dropped (_filter_past_slots),
#       an absolute-instant comparison in UTC. The basket display
#       (rental.dossier.item._get_schedule_display) uses the same tz.
# PR17  Latest start slot limited by duration.  profile.slot_end_limit_mode
#       controls how the last selectable start time relates to the warehouse
#       closing time for the selected duration:
#         • none            — last start is the last opening-hours slot.
#         • duration        — last start = closing − selected duration.
#         • next_contingent — last start = closing − next shorter duration
#                             option (falls back to the selected duration when
#                             none is shorter).
#       Applied in _get_slot_preview (the picker) and _get_day_availability_state
#       (the day calendar) via _apply_end_time_limit; events are unaffected.
#       The past-slot cutoff (PR16) and this end-limit compose: for today, once
#       every remaining start is in the past or beyond the cut-off the slot list
#       is empty and the kiosk shows "No more availability" (vs "No slots
#       available." for a future date with no slots).
#
# --- Order Generation (OG) ---
# OG01  One rental slot generates one sale.order with rental dates at order level.
# OG02  Rental dates: slot start → order.rental_start_date, slot end → order.rental_return_date.
# OG03  Rental lines created with in_rental_app context → is_rental=True.
# OG04  Non-slot items grouped into a separate non-rental order.
# OG05  Event ticket lines set event_id/event_ticket_id (event_sale compatible).
# OG06  Generated orders linked via mcrf_dossier_id + M2M.
# OG07  Idempotent: regeneration reuses existing draft orders, no duplicate lines.
# OG08  Sync: updates quantity, removes lines for cancelled items.
# OG09  Cancel: only generated orders cancelled, manual orders preserved.
# OG10  "Generate Orders" button on dossier form.
#
# --- Payment Preparation (PP) ---
# PP01  Confirm-before-payment: orders confirmed before payment starts.
# PP02  All-or-nothing: if any check fails, no orders stay confirmed.
# PP03  Flow: validate → generate → soft-check → confirm → verify-reservation → payment_pending.
# PP04  Forecast is a SOFT WARNING only — does not block the flow.
#       Forecasted stock can be slightly inaccurate; never leave rentable
#       stock on the table due to forecast quirks.
# PP05  Order confirmation is NOT a hard gate — Odoo's action_confirm
#       never raises on insufficient stock; it creates partially-available moves.
# PP06  Stock reservation verification is the HARD GATE: after confirmation,
#       check actual move states. If any pickup move is partially_available
#       or confirmed (not fully assigned), stock is truly insufficient → rollback.
# PP07  Rollback on failure: cancel generated orders, reset item/slot states.
#       Rollback ONLY touches orders generated by the dossier (mcrf_dossier_id
#       is set). Manually linked orders are NEVER cancelled by the rollback.
# PP08  Event capacity: seats_available and sold_out (soft check).
# PP09  Services/addons: always available (no stock check).
# PP10  Idempotent: safe to call multiple times.
# PP11  State guards: rejects paid, done, cancelled dossiers.
# PP12  "Prepare for Payment" button on dossier form.
# PP13  Full datetime precision: hourly/minute rentals work correctly
#       with Odoo's stock move reservation system.
#
# --- Payment Orchestration (PY) ---
# PY01  One dossier = one payment transaction for the total.
# PY02  Transaction linked to dossier and all generated sale orders.
# PY03  Payment provider resolved: profile default → demo → any enabled.
# PY04  payment_pending_until set from profile timeout config.
# PY05  action_payment_success: marks paid, keeps orders, triggers email (website).
# PY06  action_payment_failed: cancels generated orders, sets payment_failed.
# PY07  action_payment_expired: cancels generated orders, sets payment_expired.
# PY08  Cron expires pending dossiers after payment_pending_until.
# PY09  payment.transaction._post_process override for dossier-level logic.
# PY10  All payment methods idempotent (safe to call multiple times).
#
# --- Event Ticket Integration (EV) ---
# EV01  Event ticket items use event.event, event.event.ticket, event.slot.
# EV02  Dossier item has Many2one event_id, event_ticket_id, event_slot_id.
# EV03  Slot preview for events follows event.slot (multi-slot) or event date.
# EV04  Days without event availability are grey/unselectable.
# EV05  Event capacity checked during prepare-for-payment (soft warning).
# EV06  Event registrations linked to dossier after order confirmation.
#       event_sale's _init_registrations creates registrations on confirm;
#       _link_event_registrations then tags them with mcrf_dossier_id.
# EV07  Payment success keeps registrations open (Odoo sets state from SO).
# EV08  Payment failure/SO cancel → registrations auto-cancelled by event_sale.
# EV09  Standard Odoo Event backend and ticket purchase remain unaffected.
#
# --- Confirmation Email & Lookup (CE) ---
# CE01  Website payment success sends confirmation email automatically.
# CE02  Kiosk payment success does NOT send confirmation email.
# CE03  Email includes: dossier number, order references, timeslots,
#       items summary, event tickets, total paid, payment reference.
# CE04  Email includes kiosk lookup instructions: dossier number + email.
# CE05  Email explains that dossier groups underlying orders.
# CE06  Duplicate payment callbacks do not send duplicate emails
#       (confirmation_sent flag checked before sending).
# CE07  Backend users can manually resend via button.
# CE08  Confirmation fields: confirmation_sent, confirmation_sent_at.
# CE09  Mail template: multi_channel_rental_flow.email_template_dossier_confirmation.
# CE10  Lookup credentials: dossier/order number + email address.
#       QR/barcode optional later, not required.
#
# --- Ticket Payload & Printing (TK) ---
# TK01  Lookup by dossier number + email address.
# TK02  Lookup by sale/rental order number + email address.
# TK03  Only paid dossiers return printable tickets.
# TK04  One ticket per non-event order line per timeslot.
# TK05  Event tickets use event.registration info (barcode, slot, attendee).
# TK06  Ticket payload includes: dossier, order, product, timeslot, qty, event info.
# TK07  QWeb PDF report for dossier tickets (backend download/print).
# TK08  Kiosk uses POS/ePOS/IoT printer via profile's pos_printer_id.
# TK09  Per-order ticket payload available.
# TK10  Per-registration ticket payload available.
# TK11  mcrf_tickets_printed Boolean on sale.order — tracks print status.
#       Backend can uncheck to allow reprinting (manager only).
# TK12  Print Tickets button on dossier form (PDF report).
# TK13  Allow Reprint button resets printed flag (manager only).
# TK14  Ticket lookup API: /multi_channel_rental/api/ticket_lookup (public).
# TK15  Mark printed API: /multi_channel_rental/api/mark_printed (public).
#
# --- Kiosk Lookup & Print UI (KL) ---
# KL01  Public kiosk page at /rental-kiosk/<profile_id>.
# KL02  Lookup form: dossier/order number + email address.
# KL03  Only paid dossiers return printable tickets.
# KL04  Ticket cards displayed with product, timeslot, event info.
# KL05  Print via Epson ePOS (reuses pos.printer.printer_ip).
# KL06  Print via IoT Box/proxy (removed in Odoo 20: pos.printer has no proxy_ip).
# KL07  Browser print fallback when printer_mode = 'browser'.
# KL08  Mark-as-printed after successful kiosk print.
# KL09  Respects profile.enable_ticket_lookup_printing.
# KL10  Minimal personal data shown on public screen.
# KL11  Standard POS and POS self-order remain unaffected.
#
# --- Kiosk Ordering Flow (KO) ---
# KO01  Kiosk order page at /rental-kiosk/<profile_id>/order.
# KO02  Product grid with images and categories from profile.
# KO03  Category navigation: eCommerce and POS categories.
# KO04  Rental: quantity + duration + datepicker + slot selection.
# KO05  Duration options from coefficient table or profile fallback.
# KO06  Calendar with day availability states and colors from profile.
# KO07  Slots with prices, availability, color codes.
# KO08  Event: event/ticket selection with event_sale integration.
# KO09  Addon/service: quantity + add to basket.
# KO10  Basket management: add/remove items, total.
# KO11  Checkout: prepare-for-payment + demo payment + print tickets.
# KO12  Auto-print on success via ePOS/IoT/browser.
# KO13  Respects profile.enable_dossier_ordering.
# KO14  Kiosk Order URL on profile form.
# KO15  All pricing computed server-side (no JS price calculations).
# KO16  Selection summary. The product detail page shows what is being added
#       (product, event, duration, date, start time) just above "Add to
#       Basket", and the basket/checkout lists the same labeled schedule
#       (date, start time, duration) per line — times in the warehouse
#       calendar timezone (see PR16). Backed by
#       rental.dossier.item._get_schedule_display / _format_duration.
# KO17  Timeslot guard. A rental that requires a timeslot cannot be added
#       without one: the client hides/blocks "Add to Basket" until a slot is
#       selected (and re-selects the slot after a quantity change so the
#       button can never fire mid-reload), and the server rejects the add via
#       service._basket_add_rejection. Prevents slot-less, base-priced lines
#       with no date/time in the basket.
#
# --- Website Flow (WF) ---
# WF01  Website flow at /rental-flow (separate from /shop).
# WF02  Uses website.layout — follows active website theme.
# WF03  Product catalog with images, eCommerce categories.
# WF04  Product assortment from website profile.
# WF05  Product detail: qty, duration, datepicker, slot selection.
# WF06  Event ticket and addon/service support.
# WF07  Dossier basket with qty +/-, remove, totals incl. tax.
# WF08  Checkout form with name + email, dossier payment.
# WF09  Confirmation page with kiosk lookup instructions.
# WF10  Session-based dossier (mcrf_dossier_id in session).
# WF11  /shop and standard eCommerce remain untouched.
# WF12  Same backend services as kiosk (no pricing duplication).
#
# --- Soft Dependencies (SD) ---
# SD01  pos_iot / iot: IoT Box printer support (runtime detection).
# SD02  rental_set: rental set product expansion (runtime detection).
#
# --- Standard Flow Protection (SF) ---
# SF01  Standard /shop eCommerce remains untouched.
# SF02  Standard POS remains usable.
# SF03  Standard POS self-order/kiosk remains usable.
# SF04  Standard Event backend remains usable.
# SF05  Odoo demo payment provider remains usable for testing.
#
# ===================================================================

from . import models
from . import services
from . import controllers
