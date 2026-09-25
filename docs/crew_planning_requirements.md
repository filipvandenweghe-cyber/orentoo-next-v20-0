# Crew Planning — Requirements (as built)

Module: **`crew_planning`** (backend) — companion portal: **`crew_portal`**
(see `crew_portal_requirements.md`). Odoo **20.0**, Enterprise.

> Guiding principle: **customise the workflow AROUND Odoo, keep the standard
> objects as the source of truth.** Availability is standard
> `resource.calendar.leaves`; scheduling is standard `planning.slot`; worked
> hours are standard timesheets (`account.analytic.line`) that bill through the
> native Timesheet → Task → Sales Order Item chain. No parallel stores for
> anything Odoo already owns.

Depends: `hr`, `resource`, `planning`, `hr_skills`, `sale_project_forecast`,
`sale_timesheet`, `hr_timesheet`, `whatsapp`.

---

## 1. Crew pool & availability mode (`hr.employee`)
- **R1.1** `is_crew` marks the schedulable pool and gates the portal.
- **R1.2** `crew_availability_mode`:
  - *standard* — normal resource calendar / Time Off; positive availability is
    the working schedule, as in vanilla Odoo.
  - *explicit* — **unavailable by default**: a broad 24/7 calendar plus
    engine-managed leaves that cover every hour *except* the windows the crew
    member explicitly offered. For freelancers who only work when they opt in.
- **R1.3** Switching mode saves the prior working schedule and restores it when
  leaving *explicit* (`crew_saved_calendar_id`), so the change is reversible.

## 2. Availability engine (`crew.availability.engine`, `resource.calendar.leaves`)
- **R2.1** Availability is compiled into standard leaves flagged `crew_managed`.
  The engine **only ever touches `crew_managed` leaves** — Time Off and other
  standard leaves are never altered.
- **R2.2** Registering availability = delete-future-and-rebuild the managed
  leaves for the resource so overlapping/partial windows resolve to clean,
  non-overlapping intervals.
- **R2.3** For *explicit* crew a **mandatory rolling-horizon cron** keeps a
  blanket unavailability N months ahead (`unavailability_horizon_months`,
  default 12), so they never silently appear "free" past the last compiled day.
  Coverage is anchored to the start of day **minus one day** (`_coverage_start`).
- **R2.4** Availability may be entered only within the **entry horizon**
  (`availability_entry_horizon_months`); the portal enforces it.

## 3. Availability requests (`crew.availability.request`)
- **R3.1** A request is scoped to a **Task**, a **Project**, or a bare
  **period** (`request_type`) and prefills its window/effort from that source.
- **R3.2** Lifecycle `draft → open → closed` (+ `cancelled`). **No "fulfilled"
  state.** `availability_coverage` (sufficient/insufficient) and
  `planned_headcount` are computed, orthogonal indicators; closing is always an
  explicit planner action, never auto-triggered by coverage.
- **R3.3** Human-readable `period_label` and `indicative_hours` are exposed and
  reused by the portal, the invitation list and the e-mail template.
- **R3.4** Candidate matching: employees whose skills meet a **minimum level**
  (compared via `level_progress ≥`) **and** role, computed on the fly and
  **scoped to the request's company** (no cross-company leakage).
- **R3.5** `known_state` per candidate is **coverage-aware**:
  available / partial / unavailable / unknown, derived from actually-covered
  seconds over the requested window.

## 4. Invitations & waves (`crew.availability.invitation`, invite wizard)
- **R4.1** One persisted invitation per **(request, employee)** — a DB unique
  constraint blocks duplicates; a second wave or a reminder never creates
  another row.
- **R4.2** Lifecycle `sent → reminded* → responded → expired` (+ `cancelled`);
  **reminders mutate the existing record** (`reminder_count`).
- **R4.3** The candidate wizard is transient: Candidate = matches criteria,
  Selected = a transient line, **Invitation = persisted only on *Invite
  Selected***. Waves exclude already-invited and already-answered people.
- **R4.4** "Count known-available without sending": a planner can tally people
  already known-available without firing a new invitation.
- **R4.5** Messaging is channel-aware (**email / WhatsApp**) and **resilient**:
  a missing WhatsApp account/template is skipped with a chatter note instead of
  crashing; WhatsApp requires a phone; templates are read with `sudo`.
- **R4.6** A request whose period has already started cannot be invited into.

## 5. Responses → availability (`crew.availability.invitation`, log)
- **R5.1** Response `pending → available | partial | unavailable`.
- **R5.2** An *available*/*partial* answer from **explicit-mode** crew punches
  the matching hole in the managed leaves via the engine (partial punches only
  the offered sub-window). **Standard-mode** crew feed nothing (their calendar
  already expresses availability).
- **R5.3** Every availability change is recorded append-only in
  `crew.availability.log` (who / when / declared state / origin — there is no
  previous→new pair), used for audit only — it is
  **never read by staffing**.

## 6. Scheduling (`planning.slot`)
- **R6.1** §14 — **one operational shift represents one Task**: `task_id` is
  **re-declared** on `planning.slot` to narrow its domain (Odoo 20 ships the field
  natively via `project_forecast`/`sale_project_forecast`), with a "Schedule Shift"
  action from the task that opens a **prefilled** new slot (project, task, SOL,
  planned window, allocated hours); the planner picks the resource and saves.
- **R6.2** A shift may link to the request it staffs (`crew_request_id`) for
  coverage/staffing KPIs.
- **R6.3** "I can no longer work this shift" (from backend or portal): flags
  `crew_unavailable_reported` + reason and notifies the planner via the request
  chatter — **only when the shift is linked to a request** (`crew_request_id`); an
  unlinked shift notifies nobody. Whether it **also unassigns** the crew (making it an
  open shift) honours the **standard Planning policy** (Settings → Planning → *Shift
  Changes*, `planning_employee_unavailabilities == 'unassign'`) plus the same
  **deadline / past-shift**
  guards Odoo uses for native self-unassign (`_crew_may_self_unassign`). Under
  the *switch* policy the assignment is kept for the planner to handle.
- **R6.4** Re-assigning a real resource to a shift **clears the stale
  "reported" flag/reason** (`write` override), so the portal treats it as a
  fresh, live assignment again.

## 7. Work declaration → timesheet → SOL (`crew.work.declaration`)
- **R7.1** Exactly **one declaration per shift** (`UNIQUE(slot_id)`), mirroring
  the shift context (employee / project / task / sale_line / company / planned
  window) as the source of truth.
- **R7.2** Declared actuals: `actual_start`, `actual_end`, `break_minutes` →
  computed `worked_hours`.
- **R7.3** Lifecycle `draft → submitted → approved`, plus `reopened` and
  `rejected`. **Submittable states = draft, reopened, rejected** (reject means
  "please redo"); submitted/approved are out of the crew's hands.
- **R7.4** **Approval creates the standard timesheet exactly once** (idempotent
  via `timesheet_id`) and locks the declaration; the native machinery deduces
  `so_line` from `task.sale_line_id` and updates `qty_delivered`. Requires a
  project and positive hours.
- **R7.5** After approval the declaration is **immutable** (write guard); only
  the controlled lifecycle fields may change.
- **R7.6** **Reopen** (planner/manager) is allowed **only while not financially
  locked** — `timesheet_financially_locked` is true when the SOL is already
  (partly) invoiced or the timesheet's period is accounting-locked
  (`hard_lock_date`/`fiscalyear_lock_date`). Reopen detaches and deletes the
  draft timesheet (restoring `qty_delivered`) so a corrected declaration
  re-approves cleanly. When financially locked, corrections go through a new
  adjustment declaration — never a silent edit.

## 8. Reporting
- **R8.1** A **pivot** (and graph) on declarations: default rows **crew member →
  status**, columns the **work date by time unit** (month by default,
  expandable day/week/quarter/year). A stored `date` (actual start, else
  planned start) backs the time axis.
- **R8.2** Two measures: **All Hours** (`worked_hours`, any state) and
  **Approved Hours** (`approved_hours` = worked hours only when approved), so
  approved-vs-all (the not-yet-approved pipeline) is visible at a glance.

## 9. Menu & navigation
- **R9.1** Crew app menu: **1) Availability** (Invitations, Availability
  Requests, Availability Windows, Availability Log) · **2) Work Declarations** ·
  **3) Reporting** (Crew Hours) · **4) Configuration**.
- **R9.2** Clicking the Crew app lands on **Availability Windows** (explicit
  root action), independent of submenu order.

## 10. Security & multi-company
- ACLs: HR users / Planning users read-write the crew models and create/approve
  work declarations; HR managers may delete declarations. Crew (portal) users
  never touch these models directly — the portal writes with scoped `sudo`.
- Candidate matching and invitations are scoped to the request's company
  (cross-company crew handled as two employee records — "Option A").

## 11. Testing
`tests/test_crew_availability.py`, `tests/test_crew_requests.py`,
`tests/test_work_declaration.py` — engine/mode, matching, waves & exclusion,
coverage-vs-staffing, partial/decline semantics, WhatsApp resilience, portal
counter hiding, self-unassign policies (unassign / switch / past-deadline),
reassignment flag clearing, schedule-from-task, and the full declaration
lifecycle (write-once timesheet, immutability, reopen/recycle, reject→resubmit,
reporting measures).

## 12. Odoo 20 migration notes
- **A shift can staff several resources.** `planning.slot.resource_id` / `employee_id`
  became the many2many **`resource_ids` / `employee_ids`**. Consequences here:
  - the "re-assignment clears the stale *cannot work* flag" rule (R6.4) is decided
    **after** `super().write()`, on the *resulting* assignment — an m2m command list is
    truthy even when it **clears** the assignment, so testing the vals alone would wrongly
    clear the flag on unassign;
  - unassigning is `resource_ids = [Command.clear()]`;
  - the planner notification names **all** assigned employees.
- **`crew.work.declaration` mirrors the first resource.** `employee_id` / `resource_id` are
  now *computed stored* mirrors of `slot.employee_ids[:1]` / `slot.resource_ids[:1]`, not
  related fields. A declaration therefore remains one-per-shift even on a multi-resource
  shift — **open point:** decide the intended behaviour when a shift staffs several people.
- **Working schedules**: `resource.calendar.tz` was removed — a calendar now follows its
  company's timezone (`res.company.tz`). `resource.calendar.attendance` lost `name` and
  computes `day_period`. `resource.calendar.leaves.time_type` became **`count_as`**
  (`absence` / `working_time`); the engine writes `count_as='absence'`.
- **Availability interval API**: `_work_intervals_batch(resources=…)` became
  `resources_per_tz=resource._get_resources_per_tz()`.
- **Config parameters** are read with the typed getters (`get_int`); `get_param` /
  `set_param` no longer exist. The keys are `crew_planning.unavailability_horizon_months`
  and `crew_planning.availability_entry_horizon_months`.
- **Security**: `ir.model.access` + `ir.rule` merged into a single `ir.access`
  (`security/ir.access.csv`).
