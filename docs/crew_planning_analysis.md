# Crew Availability, Planning & Work Declaration — Functional & Technical Analysis
*Pre-implementation analysis — Backend (HR/Resource/Project/Planning/Timesheet) + Portal + WhatsApp*

> **Historical document.** `crew_planning` and `crew_portal` are implemented and installed, and the platform has since moved to Odoo 20. Section A's survey of the database ("the foundation is not installed", "custom code touches none of this domain") is no longer true, and several standard-source line references below have shifted. Where this document and the requirement docs disagree, the requirement docs and the code win.

| | |
|---|---|
| **Project** | Orentoo — Odoo 20.0 (Odoo.sh) |
| **Scope** | Generic Crew Planning extension (availability, invitations, work declaration) |
| **Core principle** | Customise the workflow **around** Odoo; standard objects stay the operational source of truth |
| **Status** | **Superseded — the modules were built.** Kept as design history; for the implemented behaviour see `crew_planning_requirements.md` and `crew_portal_requirements.md` |
| **Date** | 2026-09-07 (rev. 3); Odoo 20 API notes added 2026-09-25 |

> Terminology is neutral (**Crew Member / Crew / Crew Portal / Availability Request**), never
> "freelancer". Every crew member is an `hr.employee`; a crew member may have **only portal
> access or none**.

> **Revision 2** folds in seven corrections: (1) Availability Mode (Standard vs Explicit),
> (2) audit model renamed to `crew.availability.log` with an explicit operational-vs-knowledge
> split, (3) transient candidate selection (no persistent `selected` invitation state),
> (4) request state vs staffing separated (coverage ≠ fulfilled), (5) a **mandatory** rolling
> explicit-availability horizon, (6) approved Work Declarations are immutable with an explicit
> Reopen/adjustment workflow, (7) validated standard findings preserved.
>
> **Revision 3** renames the custom availability service to the **Crew Availability Engine** (to
> avoid confusion with the rental availability engine in `rental_set`) and consistently calls
> the authoritative source **standard resource availability**.

---

# A. Current-state assessment

**This database is a rental-only install.** The foundation for this feature is **not
installed** — only `resource`, `portal`, `portal_rating` are present. Everything needed is
**available to install** (verified in `ir_module_module`): `hr`, `hr_holidays`, `hr_skills`,
`project`, `project_enterprise`, `project_forecast`, `planning`, `sale_planning`,
`sale_project`, `sale_project_forecast`, `hr_timesheet`, `sale_timesheet`, `whatsapp`.

**Custom code touches none of this domain** — a grep across `/home/odoo/src/user` for
`planning.slot|project.task|hr.employee|account.analytic.line|hr.skill` returns nothing. There
is **no collision** with existing Orentoo modules (rental_set, sale_flow, rental_purchase, …);
crew planning is a clean additive vertical. The only shared touch-points are `sale.order.line`
(already extended by rental) and `resource` (installed) — both additive.

**What standard already gives us (large):**

| Capability | Standard mechanism (file evidence) | Verdict |
|---|---|---|
| Effective availability | `resource.calendar._work_intervals_batch` = attendance − leaves; batched, tz-aware (`resource/models/resource_calendar.py:556`) | Reuse as source of truth |
| Per-person unavailability | `resource.calendar.leaves` with `resource_id` set, else company-global (`resource_calendar_leaves.py:47`; filter at `resource_calendar.py:529-552`) | Reuse |
| Skills + ordered levels | `hr.employee.skill.level_progress` 0–100 (`hr_skills/.../hr_employee_skill.py:54`); clean `>=` domains, AND-composable | Reuse |
| Planning shift = task | `planning.slot.task_id` (`sale_project_forecast/models/planning_slot.py`), `project_id` (`project_forecast`), `sale_line_id` (`sale_planning`) | Reuse — `task_id` already exists |
| Task → SOL → billing | `task.sale_line_id` (`sale_project/.../project_task.py:26`) → `account.analytic.line._timesheet_determine_sale_line()` (`sale_timesheet/.../hr_timesheet.py:117`) → `so_line` → `qty_delivered` | Reuse — no custom billing |
| SO → Project/Task | `product.service_tracking` + `_timesheet_service_generation()` on SO confirm (`sale_project/.../sale_order_line.py`) | Reuse |
| Publish/notify shift | `planning.slot.action_send`/`_send_slot` (`planning/.../planning_slot.py:1683,2343`); portal token; **no accept step** (scheduling = confirmation) | Reuse — matches §12 |
| WhatsApp | `whatsapp.template` on any model with a phone field + `mail.thread`; `whatsapp.composer` programmatic send | Reuse |
| Portal | `portal.mixin` (access_url/token), `CustomerPortal`, `ir.access` per-partner (Odoo 20 merged `ir.model.access` + `ir.rule`), `portal.wizard` to grant access | Reuse |

# B. Gap analysis (per requirement)

| Req | Requirement | Classification |
|---|---|---|
| §2 | Standard = source of truth | Standard (constraint) |
| §3 | Availability Mode (Standard vs Explicit); positive availability | Small extension — mode field + **Crew Availability Engine** mapping declarations to `resource.calendar.leaves` (see D) |
| §3b | **Mandatory** rolling explicit-availability horizon | Custom, **mandatory** scheduled mechanism (see D.4) |
| §4A/B/C | Request from Project / Task / Period | Custom (request model) + prefill from standard |
| §4D | Crew self-service availability | Custom portal page → same Crew Availability Engine |
| §4E | Planner enters availability | Small extension — button on employee → leaves service |
| §5 | Audit log of availability changes | Custom (`crew.availability.log`, audit only) |
| §6 | Don't re-ask known periods; available/partial/declined | Custom (log drives targeting; standard resource availability stays authoritative) |
| §7 | Validity rules + "can no longer work" | Small extension + custom workflow (replace portal self-unassign) |
| §8 | Candidate selection by skill/level/role | Standard + config (skills domains) wrapped in a **transient** wizard |
| §9 | Invitation waves | Custom (invitation model + wave tracking; invitation created only on Invite) |
| §10 | Request overview + KPIs (coverage vs staffing) | Custom (views on request/invitation) |
| §11 | Email + WhatsApp invitations, reminders | Standard (mail + whatsapp) + custom orchestration |
| §12 | Assignment = `planning.slot`; scheduling = confirmation | Standard planning; small notification wording tweak |
| §13–15 | Project→Task→Slot→Timesheet→SOL | Standard (reuse whole chain) |
| §16 | Work Declaration layer → Timesheet (immutable after approval) | Custom (thin model) + standard timesheet write |
| §17 | Crew Portal | Custom controllers/pages reusing standard data |
| §18 | Portal security | Standard (`ir.access` + controllers + tokens) |

**Net:** ~70% standard/config; the custom part is the *workflow shell* (requests, invitations,
availability knowledge log, work declaration, portal) — the "customise around Odoo, not the
engines" principle.

# C. Proposed architecture

**Reused (unchanged):** `resource.calendar`, `resource.calendar.leaves`, `resource.resource`,
`hr.employee`, `hr.employee.skill`/`hr.skill.level`, `planning.slot` (+ `task_id`/`project_id`/
`sale_line_id`), `project.task`/`project.project`, `account.analytic.line`, `sale.order.line`,
`whatsapp.template`/`composer`, `portal.mixin`.

**Extended (thin `_inherit`):**
- `hr.employee` — crew flags (`is_crew`, crew type) and **`crew_availability_mode`**
  (`standard` / `explicit`, see D.1); "enter availability" action (portal `employee_token`
  already exists).
- `planning.slot` — "report I can't work" action + link to work declarations; optional
  `crew_request_id` for precise staffing counts; **task_id reused, not added.**
- `resource.resource` — helper delegating to the leaves service.
- `sale.order.line` / `project.task` — only if needed for prefill (likely nothing).

**New custom models:** `crew.availability.request`, `crew.availability.invitation`,
**`crew.availability.log`** (audit/declaration — renamed from `crew.availability`),
`crew.work.declaration`.

**Wizards (transient):** candidate-selection/invite (persists nothing — an invitation is only
created on *Invite Selected*); "enter availability on behalf"; "report can't work".

**Controllers / portal pages (`crew_portal`):** `/my/availability`, `/my/planning`,
`/my/hours`, `/my/profile` (phased) — crew-namespaced to avoid the existing `/my/timesheets`
and `/my/tasks` routes.

**Scheduled actions:**
- **Mandatory:** *explicit-availability horizon roll + idempotent consistency repair* (D.4).
- invitation reminder cron; optional close-expired-requests.

# D. Crew availability — technical design (standard resource availability stays the source of truth)

> **Naming:** the **Crew Availability Engine** is our thin custom service — Availability Mode
> handling + the `_apply_availability(...)` leaves mapper + the mandatory horizon cron. It only
> **writes standard `resource.calendar.leaves`**; it is **not** itself a source of availability
> and must not be confused with the rental availability engine (`rental_set`). The authoritative
> source for staffing remains **standard Odoo resource availability** (`_work_intervals_batch`).

## D.1 Availability Mode

A neutral setting decides *how* a crew member's operational availability is maintained — not
who they are (never "freelancer").

**Field:** `hr.employee.crew_availability_mode` — Selection
`[('standard','Standard Working Schedule'), ('explicit','Explicit Availability')]`, default
**`standard`**. It lives on `hr.employee` (a business/HR decision); the Crew Availability Engine
reaches standard resource availability via `employee.resource_id`.

- **Standard Working Schedule** — normal Odoo behaviour: the resource's working
  `resource.calendar` + normal Time Off leaves. Available by schedule unless on leave. This is
  the mode for **internal employees who only use the Crew Portal** but are otherwise normally
  scheduled. No blanket leave.
- **Explicit Availability** — the broad-calendar + per-resource **blanket-unavailability**
  leaves mechanism: unavailable unless a hole has been punched by an explicit declaration.

**Staffing stays standard in both modes:** both modes express availability *only* through
`resource.calendar` attendance + `resource.calendar.leaves`. `_work_intervals_batch`,
`auto_plan_ids()`, `allocated_hours` and conflict detection read that identically and are
**mode-agnostic**. The mode only selects which *leaves-maintenance policy* the crew service
applies (do nothing vs maintain blanket coverage). Auto Plan and the candidate
"available in window?" check never branch on mode.

**Candidate selection consequence:** the wizard's "available in window" filter uses operational
availability the same way for everyone — a `standard` crew member is available by working
schedule; an `explicit` crew member is available only where registered (unknown windows are
blanket-covered → not available). The *availability knowledge* log (D.3) is used separately to
decide **whom to ask**, never whom standard resource availability considers available.

## D.2 Mechanism (Explicit mode)

Broad shared "Crew" `resource.calendar` (generous / 24×7 attendance) + per-resource
`resource.calendar.leaves`. Positive availability = **absence** of a leave inside the broad
attendance. Alternatives rejected (all investigated): *no calendar* → always available (wrong
default); *empty-attendance calendar* → leaves only subtract and attendances are weekly-recurring
so single-date availability is impossible; *leaves* do not add availability
where no attendance exists. Only **broad attendance + carve leaves** works with standard
resource availability (`_leave_intervals_batch` already filters by `resource_id` and merges
overlaps).

The **Crew Availability Engine** — a single idempotent service
`_apply_availability(resource, start, end, state, origin, refs)` — owns all leave
create/split/merge: store **UTC**, compute in **resource.tz** — Odoo 20 removed
`resource.calendar.tz`, so a calendar follows `res.company.tz` and the resource tz is the
only per-person timezone. Handle **DST** `pytz` fold/gap explicitly, normalise overlaps to non-overlapping intervals
before writing. It only ever writes standard `resource.calendar.leaves`; it computes no
availability of its own. *Caveat:* a 24×7 base calendar makes
`allocated_percentage` meaningless for utilisation reporting — acceptable for event crew
(flagged).

## D.3 Two clearly separated concepts

- **Operational availability** = standard Odoo resource availability (`resource.calendar` +
  `.leaves`). **Authoritative for staffing / Auto-Plan.** Nothing custom is consulted here.
- **Availability knowledge / declaration** = **`crew.availability.log`** (renamed from
  `crew.availability`). Pure audit: *what the crew member or planner explicitly communicated*,
  used only to decide **whether to ask again** and to trace origin. It is written **from**
  availability actions and records the leaves it produced, but is **never read by staffing** and
  must never become a parallel availability source.

Standard resource availability cannot distinguish *unknown* vs *definitely-unavailable* (both
are a "leave"). That distinction lives in `crew.availability.log`, which drives §6 "don't
re-ask" — never in operational availability.

## D.4 Mandatory explicit-availability horizon

Because `resource.calendar.leaves` have finite `date_from`/`date_to`, the blanket unavailability
must be **actively kept rolling forward**, or an `explicit` crew member silently becomes
available once coverage runs out. This is **mandatory**, not an optional consistency cron.

- **Config (per company / `ir.config_parameter`), all numbers configurable:**
  - `crew_planning.unavailability_horizon_months` (e.g. **12**) — how far ahead blanket coverage is
    guaranteed;
  - `crew_planning.availability_entry_horizon_months` (e.g. **6**) — separate *business rule* for how far
    ahead a crew member may **enter** availability (§7 validity);
  - hard invariant **`unavailability_horizon ≥ availability_entry_horizon`** (validated). Beyond
    the entry horizon a crew member cannot have registered availability, yet blanket coverage
    still extends further, so they can never leak into "available".
- **On enabling Explicit mode:** create blanket unavailability `[now → today + horizon]`.
- **Scheduled action (mandatory, daily):** for every `explicit` crew member, **idempotently
  extend** blanket coverage to `today + horizon`, and **repair** consistency =
  `blanket_unavailable − registered_available_holes − declared_unavailable`, re-normalising
  overlaps/gaps so no accidental hole appears. Re-running changes nothing if already correct.
- **Mode transitions:** `standard → explicit` seeds coverage; `explicit → standard` removes the
  blanket leaves (normal schedule resumes). Both routed through the same service.
- **Invariant:** an `explicit` resource is available **only** inside registered holes **and**
  only within the covered horizon; the cron must always keep coverage ahead of the planning
  window.

Planning `auto_plan_ids()`, `allocated_hours` and conflict detection read `_work_intervals_batch`
and respect these leaves automatically — **no parallel availability source.**

# E. Data model (custom)

**`crew.availability.request`** — `name`, `request_type` (task/project/period), `project_id`,
`task_id`, `date_start`, `date_end`, `role_id`, `skill_requirement_ids`, `headcount_needed`,
`state` `[draft, open, closed, cancelled]`, `company_id`. **Computed, orthogonal indicators
(not states):** `available_count` (available/partial responses), `availability_coverage`
`[insufficient, sufficient]` (= `available_count ≥ headcount_needed`), `planned_headcount`
(count of standard `planning.slot`s staffing this requirement — by `task_id` for task requests,
or optional `slot.crew_request_id`), `staffing_display` = `planned_headcount / headcount_needed`.
Coverage and Staffing are shown as **distinct** figures; a request is **never** "fulfilled" on
availability alone.

**`crew.availability.invitation`** — created **only** on "Invite Selected". `request_id`,
`employee_id`, `wave` (int), `channel` (email/whatsapp/both), `state`
`[sent, reminded, responded, expired, cancelled]` (**no persistent `selected`**), `sent_on`,
`last_reminder_on`, `reminder_count`, `response` (available/partial/unavailable/pending),
`response_start`/`response_end` (partial), `mail_message_ids`. Uniqueness
**`(request_id, employee_id)`** blocks duplicate invitations across waves and re-inviting. A
reminder mutates the existing record (`reminder_count`, `last_reminder_on`), never creating
another.

**`crew.availability.log`** (audit/declaration only) — `employee_id`/`resource_id`,
`date_start`, `date_end`, `declared_state` (available/unavailable), `origin` (self_portal/
task_request/project_request/period_request/planner), `request_id`, `project_id`, `task_id`,
`changed_by`, timestamp, `previous_state`, `new_state`, `leave_ids` (the operational leaves it
produced). Append-only; **never read by staffing**.

**`crew.work.declaration`** — `slot_id` (unique), `employee_id`/`project_id`/`task_id`/
`sale_line_id` (related from slot), `planned_start/end/duration` (from slot), `actual_start`,
`actual_end`, `break_minutes`, `worked_hours` (computed), `comment`, `state`
`[draft, submitted, approved, reopened, rejected]`, `locked` (bool, set on approval),
`timesheet_id` (the created `account.analytic.line`, for idempotency),
`timesheet_financially_locked` (computed — see F).

**Skill requirement line** (embedded) — `skill_id`, `min_skill_level_id` (compared via
`level_progress >=`).

**Candidate-selection wizard** — `TransientModel` with transient lines; persists nothing.
Candidate = matches criteria (computed on the fly); Selected = transient wizard line;
Invitation = persisted only on *Invite Selected*; Reminder = communication on an existing
invitation.

# F. State machines

- **Availability Request:** `draft → open → closed` (+ `cancelled`). **No "fulfilled".**
  `availability_coverage` (insufficient/sufficient) and `planned_headcount` are computed,
  orthogonal indicators — not states. Closing is explicit, never auto-triggered by coverage.
- **Invitation:** `sent → reminded* → responded → expired` (+ `cancelled`). **No persistent
  `selected`.** Reminder = transition on the existing record.
- **Availability Response:** `pending → available | partial | unavailable`.
- **Availability Knowledge (`crew.availability.log`):** append-only audit; not a staffing state
  machine.
- **Work Declaration (immutable after approval):**
  - `draft ↔ submitted` — freely editable.
  - `submitted → approved` — create/link the standard timesheet **exactly once** (guarded by
    `timesheet_id`); set `locked = True`. **After approval, declaration edits do NOT touch the
    timesheet.**
  - `approved → reopened → draft` — an **explicit, permissioned** Reopen (planner/manager),
    **only if** `timesheet_financially_locked` is False; it detaches/removes the draft timesheet
    so a corrected declaration can re-approve cleanly.
  - `approved` **+ financially locked** (timesheet's SOL already invoiced for these hours /
    analytic line in a locked period / linked posted move) → **Reopen blocked**; correction goes
    through an explicit **adjustment declaration** (a new compensating WD), never a silent edit.
  - `submitted/draft → rejected → draft`.
  - Preserved chain: **Work Declaration → approval → standard Timesheet → Task → Sales Order
    Item.**

# G. End-to-end workflows

1. **Task-based invitation:** Task → "Request Availability" (prefills project/task/start/end/SOL)
   → transient candidate wizard (skills/role) → select wave-1 → **Invite Selected** (invitation
   records created; email/WA: *"availability only, not scheduled"*) → responses become
   `crew.availability.log` + operational holes in leaves → planner schedules → `planning.slot`
   (task_id set) → publish notifies (*"you're scheduled…"*).
2. **Project-based:** identical, prefilled from project period; task chosen later on the slot.
3. **General period:** request with no project/task; pure availability harvesting.
4. **Self-service:** crew opens `/my/availability`, marks available/unavailable → same service →
   log `origin=self_portal`.
5. **Planner-entered:** button on employee/resource → same service → `origin=planner`.
6. **Multi-wave:** wave-1 of 10 invited; reopen wizard → previously-invited **excluded** →
   wave-2; reminders mutate existing invitations, never duplicate.
7. **Planning notification:** standard `action_send`; reword template to "scheduled, tell us ASAP
   if you can't"; **no** second confirmation.
8. **Work Declaration → Timesheet → SOL:** slot → crew confirms/edits actuals in `/my/hours` →
   submit → planner approves → write `account.analytic.line(task_id, unit_amount, employee_id)`
   **once**, lock the declaration → standard `_timesheet_determine_sale_line()` sets `so_line`
   from `task.sale_line_id` → `qty_delivered` on the correct SOL. Corrections use Reopen
   (unlocked) or an adjustment WD (financially locked). **No manual project/task/SOL reselection;
   no silent re-billing.**

# H. Risks & edge cases

- **Overlapping/partial availability** — resolved to non-overlapping intervals in the single
  leaves service; partial responses punch a hole only for the offered sub-window.
- **DST/timezones** — store UTC, compute in resource tz; `pytz` fold/gap is the main sharp edge.
- **Coverage lapse (Explicit mode)** — mitigated by the **mandatory** horizon cron + idempotent
  repair; invariant `unavailability_horizon ≥ entry_horizon` guarantees no leak past the entry
  window.
- **Mode transition** — `standard↔explicit` must seed/clear blanket coverage via the service.
- **Changed task/slot dates** — written leaves stay; slot changes raise standard
  `publication_warning`; the request keeps its own window (no retro-rewrite of the log).
- **"Can no longer work" after planning** — must **not** use standard portal self-unassign (it
  silently blanks `resource_id`). Custom action keeps the slot, flags it, notifies the planner
  (§7/§12).
- **Cancellation after planning** — never auto-cancel a published slot on availability withdrawal.
- **Inactive employee** — archive resource → auto-excluded from `_work_intervals_batch` and
  candidate search.
- **Duplicate email/WhatsApp** — `(request, employee)` uniqueness + wave/reminder counters; WA
  needs an **approved** template + `whatsapp.account`.
- **Duplicate timesheets** — one WD per slot + `timesheet_id` back-reference → approval is
  write-once; post-approval edits never touch the timesheet.
- **Approved-WD immutability / financial lock** — Reopen blocked when the timesheet is
  invoiced / in a locked period / on a posted move; correction via an explicit adjustment WD.
- **Request never appears "staffed" on availability alone** — Coverage and Staffing kept
  separate (E/F).
- **Performance (thousands of crew)** — leaves batched/indexed; candidate search is a skills
  domain; blanket-leave punching/rolling is O(windows), not O(crew²).

# I. Recommended module structure

- **`crew_planning`** (backend core): the Crew Availability Engine (both modes) + mandatory
  horizon cron, audit log, requests, invitations, transient candidate wizard, work declaration,
  `planning.slot` glue, WhatsApp orchestration, KPIs.
  *Depends:* `planning`, `sale_project_forecast`, `sale_timesheet`, `hr_skills`, `hr_timesheet`,
  `whatsapp`, `resource`.
- **`crew_portal`** (portal): controllers, pages, portal `ir.access`/security, "report can't work".
  *Depends:* `crew_planning`, `portal`.
- **`orentoo_crew`** (optional, thin): org-specific config, calendar seeding, wording, data —
  keeps `crew_planning`/`crew_portal` generic and reusable.

*(Names provisional. The heavy standard dependency tree is unavoidable given the greenfield DB —
a deliberate "reuse standard" cost, not custom weight.)*

# J. Implementation phases

**No new phases vs rev. 1; two items are re-scoped earlier/mandatory and two are refinements:**

- **Phase 0 — Foundation:** install/configure the standard stack (hr, project, planning,
  sale_project_forecast, sale_timesheet, hr_skills, hr_timesheet, whatsapp, portal); seed the
  shared Crew calendar; confirm `planning.slot.task_id`/billing chain live.
- **Phase 1 — Crew Availability Engine:** `crew_availability_mode` + the two-mode service branch +
  the **mandatory horizon roll & idempotent repair cron** (moved up from "polish") + audit log
  + backend "enter availability" (§4E) + unit tests (overlap/DST/horizon).
- **Phase 2 — Requests & invitations:** request models (task/project/period), **transient**
  candidate wizard (no `selected` state), waves, **email** invitations, overview with
  **coverage-vs-staffing** split, KPIs.
- **Phase 3 — WhatsApp** channel + reminders.
- **Phase 4 — Crew Portal:** My Availability, My Planning (+ "can't work"), security rules.
- **Phase 5 — Work Declaration → Timesheet:** WD model, approval → timesheet (write-once),
  **lock-on-approval + Reopen/adjustment** workflow + financial-lock guard, My Hours.
- **Phase 6 — Validity rules & polish:** future-window limits (entry horizon), cut-off hours,
  dashboards, WA template approval.

# K. Preserved standard findings (confirmed, subject to re-confirmation once installed)

- `planning.slot.task_id` exists via the standard dependency stack (`sale_project_forecast`);
- **one operational Planning Shift = one Task**;
- **Task determines the Sales Order Item**;
- standard **Timesheets** drive delivered quantity / billing;
- **scheduling/publication = "you have been scheduled"; no second acceptance**;
- reuse standard **WhatsApp** infrastructure.

## Open decisions before Phase 0

1. **Footprint:** greenfield DB means installing the **full enterprise Project / Planning /
   Timesheet / HR / WhatsApp stack** — proceed on this dev branch, or validate on a separate
   branch/build first?
2. **Availability spike:** proceed with the recommended broad-calendar + blanket-leave design,
   or spike a per-crew-calendar variant for comparison first?
3. **Module naming/split:** confirm `crew_planning` + `crew_portal` (+ optional `orentoo_crew`).
