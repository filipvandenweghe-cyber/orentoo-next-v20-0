# Crew Portal — Requirements (as built)

Module: **`crew_portal`**. Depends: `crew_planning`, `portal`. Odoo **20.0**.

Self-service for crew members (external or internal, without backend access).

> **Security invariant (R0):** every route resolves the logged-in user's
> employee (`_crew_employee`) and reads/writes **only that person's data**.
> `sudo` is used for controlled writes but is always scoped to the resolved
> employee; a non-crew user sees the "no crew profile" page.

---

## 1. Home cards
- **R1.1** Three crew cards are shown on the portal home **only to crew members**
  (gated by an `is_crew` employee linked to the user): **My Availability**,
  **My Planning**, **My Hours**, each with a bundled icon and a counter.
  *Odoo 20 mechanism:* each card is a **`portal.entry` data record**
  (`data/portal_entry_data.xml`, `category = crew_category`, sequences 310/320/330);
  the gate is `PortalEntry._filter_visible_portal_cards()`, overridden in
  `models/portal_entry.py`. Note that Odoo 20 **renders every card in the HTML** and
  merely hides the inapplicable ones with a CSS class — so "shown / not shown" is about
  visibility, never about presence in the page source (tests must assert accordingly).
- **R1.2** Counters: availability windows · upcoming shifts · shifts awaiting a
  declaration. They are produced by the Odoo 20 controller hook
  `_prepare_portal_counter_values(counter)`, which returns a
  `(model, domain, access)` triple that the portal counts itself — the old
  `_prepare_home_portal_values(counters)` hook no longer exists. Because
  `/my/counters` counts with `limit=1` for non-alert categories, the value behaves as a
  **0/1 visibility flag** rather than an exact total.
- **R1.3** A planner can **hide standard portal cards** per crew member
  (sales/invoices/purchases/projects/tasks/timesheets/subscriptions/signatures).
  This is enforced by overriding the **`/my/counters`** route so the zeroing is
  the *last word* after every app's own counter contribution (the session
  `portal_counters` cache is refreshed too), reliably hiding those cards.

## 2. My Availability (`/my/availability`)
- **R2.1** Lists the crew member's registered availability windows (From / To /
  last updated) with a **Remove** action that recompiles the engine.
- **R2.2** **Register availability**: From / To / Available-or-Unavailable →
  fed into the Crew Availability Engine (`origin=self_portal`,
  entry-horizon enforced). On mobile/portrait these fields **stack vertically**;
  inline from tablet width up.
- **R2.3** **Open availability requests**: the crew member's pending invitations
  (future requests only, chronological) shown as cards with the request header,
  posted-at, **requested period** and indicative effort, plus **I'm available /
  Not available** actions. Each card makes clear this is *only a request for
  availability — not yet a booking*.

## 3. My Planning (`/my/planning`)
- **R3.1** Upcoming shifts assigned to the crew member (end ≥ now), showing
  start → end (no seconds), project, task and role.
- **R3.2** **"I can no longer work this shift"** with an optional reason →
  calls the backend action (policy-aware; see crew_planning R6.3). Once
  reported, the card shows a confirmation instead of the form.

## 4. My Hours (`/my/hours`)
- **R4.1** **Shifts to declare**: started shifts assigned to the crew member,
  excluding ones reported "cannot work". A shift is editable (shows the Submit
  form) only while its declaration is **draft / reopened / rejected**; the form
  prefills actual start/end from the plan and takes break minutes + a comment.
  Submitting creates/updates the declaration and submits it.
- **R4.2** The header of each to-declare card **stacks on portrait** (start →
  on line 1, end on line 2, project + description on line 3) and stays on one
  line from tablet width up.
- **R4.3** **Submitted / approved / rejected** shifts are shown **read-only**
  with their hours and status.
- **R4.4** The submit route is **guarded and idempotent**: it acts only on an
  editable, unlocked declaration; a stale or double POST on an already
  submitted/approved one is a silent no-op (never the website error page).

## 5. Localisation
- Datetimes are formatted with the month spelled out in the **user's timezone**
  (`format_datetime`); browser `datetime-local` inputs are parsed from/returned
  to the user's tz and stored as naive UTC.

## 6. Testing notes
`crew_portal` has its own `HttpCase` suite, `tests/test_crew_portal.py`
(`test_portal_availability_and_planning`, `test_remove_availability`,
`test_home_cards_crew_gated`), on top of the backing `crew_planning` model/logic tests
(counter-hiding map, declaration lifecycle and state gating, self-unassign policy).
The `/my/counters` override was additionally verified against the live routing map
(it resolves to `CrewPortal.counters`). The card-gating test asserts on
`_filter_visible_portal_cards()` rather than on the HTML, for the reason given in R1.1.
