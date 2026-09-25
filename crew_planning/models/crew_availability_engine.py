# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# ---------------------------------------------------------------------------
# Pure interval helpers (naive UTC datetimes, as stored on Datetime fields)
# ---------------------------------------------------------------------------


def _merge(intervals):
    """Coalesce a list of (start, end) into a minimal, sorted, non-overlapping
    (and adjacency-merged) list."""
    out = []
    for s, e in sorted(i for i in intervals if i[0] < i[1]):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _subtract(base, holes):
    """Return ``base`` intervals minus every ``holes`` interval."""
    result = []
    holes = _merge(holes)
    for bs, be in _merge(base):
        cur = bs
        for hs, he in holes:
            if he <= cur or hs >= be:
                continue
            if hs > cur:
                result.append((cur, min(hs, be)))
            cur = max(cur, he)
            if cur >= be:
                break
        if cur < be:
            result.append((cur, be))
    return [(s, e) for s, e in result if s < e]


class CrewAvailabilityEngine(models.AbstractModel):
    """Thin service that compiles registered positive availability windows
    (``crew.availability``) into standard ``resource.calendar.leaves`` on a
    broad 24/7 crew calendar. Standard Odoo resource availability
    (`_work_intervals_batch`) remains the single source of truth for staffing;
    this engine only *writes* leaves and computes no availability of its own.
    """
    _name = 'crew.availability.engine'
    _description = 'Crew Availability Engine'

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _param_int(self, key, default):
        # Odoo 20 replaced ir.config_parameter.get_param() by typed getters
        # (get_str / get_int / get_bool / get_float).
        return max(self.env['ir.config_parameter'].sudo().get_int(key, default), 0)

    def _unavailability_months(self):
        return self._param_int('crew_planning.unavailability_horizon_months', 12)

    def _entry_months(self):
        return self._param_int('crew_planning.availability_entry_horizon_months', 6)

    def _now(self):
        return fields.Datetime.now()

    def _horizon_end(self, now=None):
        now = now or self._now()
        return now + relativedelta(months=self._unavailability_months())

    def _coverage_start(self, now=None):
        """Blanket unavailability is anchored to the START of the day (with a
        one-day buffer) rather than the exact instant ``now``, so the current
        day is fully covered in any timezone. Otherwise "today up to now" would
        leak as available (there would be no leave before the moment coverage
        was seeded)."""
        now = now or self._now()
        return datetime.combine(now.date(), time.min) - timedelta(days=1)

    def _entry_limit(self, now=None):
        now = now or self._now()
        return now + relativedelta(months=self._entry_months())

    def _crew_calendar(self):
        cal = self.env.ref('crew_planning.resource_calendar_crew', raise_if_not_found=False)
        if not cal:
            raise UserError(_("The 24/7 Crew calendar is missing (crew_planning.resource_calendar_crew)."))
        return cal

    # ------------------------------------------------------------------
    # Mode transitions (called from hr.employee)
    # ------------------------------------------------------------------
    def _enable_explicit(self, employees):
        for emp in employees:
            resource = emp.resource_id
            if not resource:
                continue
            crew_cal = self._crew_calendar()
            if resource.calendar_id != crew_cal:
                # remember the working schedule so Standard mode can restore it
                if not emp.crew_saved_calendar_id:
                    emp.crew_saved_calendar_id = resource.calendar_id
                resource.calendar_id = crew_cal
            self._recompile(resource)

    def _disable_explicit(self, employees):
        for emp in employees:
            resource = emp.resource_id
            if not resource:
                continue
            self._crew_leaves(resource).unlink()
            if emp.crew_saved_calendar_id:
                resource.calendar_id = emp.crew_saved_calendar_id
                emp.crew_saved_calendar_id = False

    # ------------------------------------------------------------------
    # Availability declaration (single entry point)
    # ------------------------------------------------------------------
    def apply_availability(self, resource, start, end, available,
                           origin='planner', employee=None, log=True,
                           enforce_entry_horizon=False):
        """Register (un)availability for ``[start, end]`` and recompile leaves.

        available=True  -> the window becomes a hole in the blanket (rentable);
        available=False -> the window is removed from availability (blanket
                           coverage applies again). No scrap / no engine of its
                           own — only standard leaves are written.
        """
        resource.ensure_one()
        now = self._now()
        start = max(start, now)
        end = min(end, self._horizon_end(now))
        if enforce_entry_horizon:
            end = min(end, self._entry_limit(now))
        if start >= end:
            return
        windows = self._get_windows(resource)
        if available:
            windows = _merge(windows + [(start, end)])
        else:
            windows = _subtract(windows, [(start, end)])
        self._set_windows(resource, employee or resource.employee_id, windows)
        if log:
            self._log(resource, start, end, available, origin, employee)
        self._recompile(resource)

    # ------------------------------------------------------------------
    # Rolling horizon + idempotent repair (mandatory cron)
    # ------------------------------------------------------------------
    def roll_and_repair(self, resources=None):
        """Re-compile every Explicit-Availability crew resource so blanket
        unavailability always reaches today + horizon and stays consistent.
        Fully idempotent: re-running changes nothing if already correct."""
        if resources is None:
            resources = self.env['hr.employee'].sudo().search(
                [('crew_availability_mode', '=', 'explicit'),
                 ('resource_id', '!=', False)]).resource_id
        self._recompile(resources)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _crew_leaves(self, resource):
        return self.env['resource.calendar.leaves'].sudo().search([
            ('resource_id', '=', resource.id),
            ('crew_managed', '=', True),
        ])

    def _get_windows(self, resource):
        recs = self.env['crew.availability'].sudo().search([
            ('resource_id', '=', resource.id)])
        return _merge([(r.date_start, r.date_end) for r in recs])

    def _set_windows(self, resource, employee, intervals):
        Win = self.env['crew.availability'].sudo()
        Win.search([('resource_id', '=', resource.id)]).unlink()
        for s, e in _merge(intervals):
            Win.create({
                'resource_id': resource.id,
                'employee_id': employee.id if employee else False,
                'company_id': resource.company_id.id,
                'date_start': s,
                'date_end': e,
            })

    def _recompile(self, resources):
        """Compile leaves = ([now, horizon] − availability windows) for each
        resource, by deleting future crew-managed leaves and rebuilding them.
        Deterministic and idempotent; past leaves are kept as history."""
        Leaves = self.env['resource.calendar.leaves'].sudo()
        for resource in resources:
            now = self._now()
            coverage_start = self._coverage_start(now)
            horizon = self._horizon_end(now)
            # keep the windows table bounded: drop fully-past availability
            self.env['crew.availability'].sudo().search([
                ('resource_id', '=', resource.id),
                ('date_end', '<=', coverage_start)]).unlink()
            windows = self._get_windows(resource)
            desired = _subtract([(coverage_start, horizon)], windows)
            # rebuild crew-managed coverage from the day anchor forward, keeping
            # older leaves as history
            self._crew_leaves(resource).filtered(
                lambda l: l.date_to > coverage_start).unlink()
            calendar = resource.calendar_id or self._crew_calendar()
            for s, e in desired:
                Leaves.create({
                    'name': _("Crew unavailability"),
                    'resource_id': resource.id,
                    'calendar_id': calendar.id,
                    'company_id': resource.company_id.id,
                    'date_from': s,
                    'date_to': e,
                    # Odoo 20 replaced resource.calendar.leaves.time_type
                    # ('leave'/'other') by count_as ('absence'/'working_time').
                    'count_as': 'absence',
                    'crew_managed': True,
                })

    def _log(self, resource, start, end, available, origin, employee):
        self.env['crew.availability.log'].sudo().create({
            'employee_id': (employee or resource.employee_id).id or False,
            'resource_id': resource.id,
            'company_id': resource.company_id.id,
            'date_start': start,
            'date_end': end,
            'declared_state': 'available' if available else 'unavailable',
            'origin': origin,
            'user_id': self.env.user.id,
        })
