# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from pytz import utc

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'crew_planning')
class TestCrewAvailability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env['crew.availability.engine']
        cls.crew_cal = cls.env.ref('crew_planning.resource_calendar_crew')
        cls.now = cls.engine._now()

    def _new_crew(self, mode='explicit', name='Crew Test'):
        return self.env['hr.employee'].create({
            'name': name,
            'is_crew': True,
            'crew_availability_mode': mode,
        })

    def _crew_leaves(self, resource):
        return self.env['resource.calendar.leaves'].search([
            ('resource_id', '=', resource.id), ('crew_managed', '=', True)])

    def _leave_overlaps(self, resource, start, end):
        return bool(self.env['resource.calendar.leaves'].search([
            ('resource_id', '=', resource.id), ('crew_managed', '=', True),
            ('date_from', '<', end), ('date_to', '>', start)]))

    def _work_intervals(self, resource, start, end):
        # Odoo 20: _work_intervals_batch takes resources_per_tz (a mapping
        # built by resource._get_resources_per_tz()) instead of resources.
        res = resource.calendar_id._work_intervals_batch(
            utc.localize(start), utc.localize(end),
            resources_per_tz=resource._get_resources_per_tz())
        return list(res.get(resource.id, []))

    # ------------------------------------------------------------------
    def test_01_enable_seeds_blanket_unavailable(self):
        emp = self._new_crew()
        resource = emp.resource_id
        self.assertEqual(resource.calendar_id, self.crew_cal,
                         "Explicit crew must run on the 24/7 crew calendar.")
        self.assertTrue(self._crew_leaves(resource), "Blanket coverage missing.")
        # covered up to ~ horizon (12 months)
        far = self._crew_leaves(resource).sorted('date_to')[-1].date_to
        self.assertGreater(far, self.now + relativedelta(months=11))
        # standard engine sees them unavailable right now
        soon = self.now + timedelta(days=1)
        self.assertFalse(self._work_intervals(resource, soon, soon + timedelta(hours=2)),
                         "An unconfirmed explicit crew member must read as unavailable.")

    def test_02_available_punches_hole(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s = self.now + timedelta(days=10)
        e = s + timedelta(hours=8)
        self.engine.apply_availability(resource, s, e, available=True,
                                       origin='planner', employee=emp)
        self.assertFalse(self._leave_overlaps(resource, s, e),
                         "Registered availability must be a hole in the blanket.")
        self.assertTrue(self.env['crew.availability'].search([
            ('resource_id', '=', resource.id)]), "Window record missing.")
        # standard engine now sees availability inside the window
        self.assertTrue(self._work_intervals(resource, s, e),
                        "Standard resource availability must expose the hole.")

    def test_03_unavailable_recloses_hole(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s = self.now + timedelta(days=10)
        e = s + timedelta(hours=8)
        self.engine.apply_availability(resource, s, e, True, employee=emp)
        self.engine.apply_availability(resource, s, e, False, employee=emp)
        self.assertTrue(self._leave_overlaps(resource, s, e),
                        "Declaring unavailable must reinstate blanket coverage.")
        self.assertFalse(self.env['crew.availability'].search([
            ('resource_id', '=', resource.id)]))

    def test_04_overlapping_windows_merge(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s1 = self.now + timedelta(days=5)
        self.engine.apply_availability(resource, s1, s1 + timedelta(hours=48), True, employee=emp)
        self.engine.apply_availability(resource, s1 + timedelta(hours=24),
                                       s1 + timedelta(hours=72), True, employee=emp)
        wins = self.env['crew.availability'].search([('resource_id', '=', resource.id)])
        self.assertEqual(len(wins), 1, "Overlapping windows must merge into one.")
        self.assertEqual(wins.date_start, s1)
        self.assertEqual(wins.date_end, s1 + timedelta(hours=72))

    def test_05_roll_repairs_and_preserves_holes(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s = self.now + timedelta(days=3)
        e = s + timedelta(hours=6)
        self.engine.apply_availability(resource, s, e, True, employee=emp)
        # simulate a coverage lapse: wipe all crew-managed leaves
        self._crew_leaves(resource).unlink()
        self.assertFalse(self._crew_leaves(resource))
        # mandatory roll must rebuild coverage AND keep the registered hole
        self.engine.roll_and_repair(resource)
        self.assertTrue(self._crew_leaves(resource), "Roll must restore coverage.")
        self.assertFalse(self._leave_overlaps(resource, s, e),
                         "Roll must preserve the registered availability hole.")
        far = self._crew_leaves(resource).sorted('date_to')[-1].date_to
        self.assertGreater(far, self.now + relativedelta(months=11))

    def test_06_roll_is_idempotent(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s = self.now + timedelta(days=7)
        self.engine.apply_availability(resource, s, s + timedelta(hours=8), True, employee=emp)
        self.engine.roll_and_repair(resource)
        before = self._crew_leaves(resource).mapped(lambda l: (l.date_from, l.date_to))
        self.engine.roll_and_repair(resource)
        after = self._crew_leaves(resource).mapped(lambda l: (l.date_from, l.date_to))
        self.assertEqual(sorted(before), sorted(after),
                         "Repair must be idempotent.")

    def test_06b_horizon_end_is_stable_within_the_day(self):
        """The rolling horizon must not carry the current second.

        ``roll_and_repair`` rebuilds the future leaves on every run, so a
        horizon computed as the bare ``now + N months`` made the repair rewrite
        the last leave with a ``date_to`` a few seconds later each time — real
        churn, and a test (test_06 above) that only failed when a second
        happened to tick between the two runs.  This guard is deterministic.
        """
        now = self.now
        self.assertEqual(
            self.engine._horizon_end(now),
            self.engine._horizon_end(now + timedelta(seconds=1)),
            "Two runs a second apart must agree on the horizon.")
        horizon = self.engine._horizon_end(now)
        self.assertEqual(
            (horizon.hour, horizon.minute, horizon.second), (0, 0, 0),
            "The horizon is snapped to a day boundary.")
        self.assertGreaterEqual(
            horizon, now + relativedelta(months=12),
            "Snapping rounds UP, so coverage is never shorter than N months.")

    def test_07_disable_restores_schedule(self):
        emp = self._new_crew()
        resource = emp.resource_id
        original = emp.crew_saved_calendar_id
        emp.crew_availability_mode = 'standard'
        self.assertFalse(self._crew_leaves(resource),
                         "Leaving Explicit mode must drop crew-managed leaves.")
        if original:
            self.assertEqual(resource.calendar_id, original)

    def test_08_standard_mode_untouched(self):
        emp = self._new_crew(mode='standard')
        resource = emp.resource_id
        self.assertFalse(self._crew_leaves(resource))
        self.engine.roll_and_repair()  # global roll must not touch standard crew
        self.assertFalse(self._crew_leaves(resource))

    def test_09_dst_spanning_window(self):
        # A future EU spring-forward (2027-03-28); leaves are absolute UTC so the
        # hole must still be exactly represented with no crash.
        emp = self._new_crew()
        resource = emp.resource_id
        s = datetime(2027, 3, 27, 20, 0, 0)
        e = datetime(2027, 3, 29, 6, 0, 0)
        self.assertLess(e, self.engine._horizon_end(),
                        "DST test window must fall inside the coverage horizon.")
        self.engine.apply_availability(resource, s, e, True, employee=emp)
        self.assertFalse(self._leave_overlaps(resource, s, e))
        self.assertTrue(self._work_intervals(resource, s, e),
                        "The DST-spanning window must read as available.")

    def test_10_horizon_invariant_enforced(self):
        with self.assertRaises(ValidationError):
            self.env['res.config.settings'].create({
                'crew_unavailability_horizon_months': 6,
                'crew_availability_entry_horizon_months': 12,
            })

    def test_12_today_is_covered_before_now(self):
        # Coverage is anchored to the start of the day, so "today up to now"
        # must NOT leak as available for an explicit crew member.
        emp = self._new_crew()
        resource = emp.resource_id
        day_start = datetime.combine(self.now.date(), datetime.min.time())
        self.assertFalse(
            self._work_intervals(resource, day_start, self.now + timedelta(hours=1)),
            "Today (including before 'now') must read as unavailable by default.")

    def test_11_log_records_declaration(self):
        emp = self._new_crew()
        resource = emp.resource_id
        s = self.now + timedelta(days=9)
        self.engine.apply_availability(resource, s, s + timedelta(hours=4),
                                       True, origin='planner', employee=emp)
        log = self.env['crew.availability.log'].search([
            ('employee_id', '=', emp.id)], limit=1)
        self.assertTrue(log)
        self.assertEqual(log.declared_state, 'available')
        self.assertEqual(log.origin, 'planner')
