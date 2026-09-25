# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install', 'crew_planning')
class TestWorkDeclaration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = cls.env['crew.availability.engine']._now()
        cls.emp = cls.env['hr.employee'].create({'name': 'Wanda Worker', 'is_crew': True})
        cls.project = cls.env['project.project'].create({
            'name': 'Crew Project', 'allow_timesheets': True})
        cls.task = cls.env['project.task'].create({
            'name': 'Stage Build', 'project_id': cls.project.id})
        cls.start = cls.now - timedelta(hours=10)
        cls.end = cls.now - timedelta(hours=2)  # an 8h shift that already ended

    def _make_slot(self):
        return self.env['planning.slot'].create({
            'resource_ids': self.emp.resource_id.ids,
            'project_id': self.project.id,
            'task_id': self.task.id,
            'start_datetime': self.start,
            'end_datetime': self.end,
        })

    def _declared(self, slot, break_min=0):
        wd = slot._get_or_create_work_declaration()
        wd.write({'actual_start': self.start, 'actual_end': self.end,
                  'break_minutes': break_min})
        return wd

    def test_01_worked_hours_minus_break(self):
        wd = self._declared(self._make_slot(), break_min=30)
        self.assertAlmostEqual(wd.worked_hours, 7.5, places=2)

    def test_02_submit_approve_creates_timesheet_once(self):
        slot = self._make_slot()
        wd = self._declared(slot)
        wd.action_submit()
        self.assertEqual(wd.state, 'submitted')
        wd.action_approve()
        self.assertEqual(wd.state, 'approved')
        self.assertTrue(wd.locked)
        self.assertTrue(wd.timesheet_id)
        self.assertAlmostEqual(wd.timesheet_id.unit_amount, 8.0, places=2)
        self.assertEqual(wd.timesheet_id.task_id, self.task)
        self.assertEqual(wd.timesheet_id.employee_id, self.emp)
        ts = wd.timesheet_id
        # Idempotent: re-ensuring does not create a second timesheet.
        wd._ensure_timesheet()
        self.assertEqual(wd.timesheet_id, ts)

    def test_03_locked_declaration_is_immutable(self):
        wd = self._declared(self._make_slot())
        wd.action_submit()
        wd.action_approve()
        with self.assertRaises(UserError):
            wd.comment = 'too late'

    def test_04_reopen_removes_timesheet_and_allows_recycle(self):
        slot = self._make_slot()
        wd = self._declared(slot)
        wd.action_submit()
        wd.action_approve()
        ts = wd.timesheet_id
        self.assertFalse(wd.timesheet_financially_locked)
        wd.action_reopen()
        self.assertEqual(wd.state, 'reopened')
        self.assertFalse(wd.locked)
        self.assertFalse(wd.timesheet_id)
        self.assertFalse(ts.exists(), "The draft timesheet must be removed on reopen.")
        # a corrected declaration re-approves cleanly into a fresh timesheet
        wd.break_minutes = 60
        wd.action_submit()
        wd.action_approve()
        self.assertTrue(wd.timesheet_id)
        self.assertAlmostEqual(wd.timesheet_id.unit_amount, 7.0, places=2)

    def test_05_one_declaration_per_slot(self):
        slot = self._make_slot()
        slot._get_or_create_work_declaration()
        # The UNIQUE(slot_id) constraint must reject a second declaration. Wrap
        # the failing INSERT in a savepoint so the aborted statement does not
        # poison the test transaction, and mute odoo.sql_db so the expected
        # constraint violation is not logged at ERROR level (build-log noise).
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self.env['crew.work.declaration'].create({'slot_id': slot.id})
                self.env.flush_all()

    def test_06_cannot_submit_without_actuals(self):
        slot = self._make_slot()
        wd = slot._get_or_create_work_declaration()
        wd.write({'actual_start': False, 'actual_end': False})
        with self.assertRaises(UserError):
            wd.action_submit()

    def test_07_end_before_start_rejected(self):
        slot = self._make_slot()
        wd = slot._get_or_create_work_declaration()
        with self.assertRaises(ValidationError):
            wd.write({'actual_start': self.end, 'actual_end': self.start})

    def test_09_schedule_shift_from_task_prefills_and_links(self):
        self.task.write({'planned_date_begin': self.start,
                         'date_deadline': self.end})
        action = self.task.action_crew_schedule_shift()
        ctx = action['context']
        self.assertEqual(ctx['default_task_id'], self.task.id)
        self.assertEqual(ctx['default_project_id'], self.project.id)
        self.assertEqual(ctx['default_start_datetime'], self.start)
        self.assertEqual(ctx['default_end_datetime'], self.end)
        # a slot created from that context is linked back to the task
        slot = self.env['planning.slot'].with_context(**ctx).create({
            'resource_ids': self.emp.resource_id.ids})
        self.assertEqual(slot.task_id, self.task)
        self.assertEqual(slot.project_id, self.project)
        self.assertIn(slot, self.task.planning_slot_ids)
        self.task.invalidate_recordset(['planning_slot_count'])
        self.assertEqual(self.task.planning_slot_count, 1)

    def test_10_rejected_declaration_can_be_resubmitted(self):
        slot = self._make_slot()
        wd = self._declared(slot)
        wd.action_submit()
        wd.action_reject()
        self.assertEqual(wd.state, 'rejected')
        # the crew fixes it and resubmits -> allowed (reject means "redo")
        wd.break_minutes = 15
        wd.action_submit()
        self.assertEqual(wd.state, 'submitted')

    def test_11_reporting_measures_and_date(self):
        slot = self._make_slot()
        wd = self._declared(slot)
        # work date comes from the actual start
        self.assertEqual(wd.date, self.start.date())
        # approved_hours is 0 until approval, then equals worked_hours
        self.assertEqual(wd.approved_hours, 0.0)
        wd.action_submit()
        wd.action_approve()
        self.assertAlmostEqual(wd.approved_hours, wd.worked_hours, places=2)
        # with no actuals, the date falls back to the planned start
        wd2 = self._make_slot()._get_or_create_work_declaration()
        wd2.write({'actual_start': False, 'actual_end': False})
        self.assertEqual(wd2.date, wd2.planned_start.date())

    def test_12_reject_from_draft_and_reset_to_draft(self):
        wd = self._declared(self._make_slot())
        wd.action_reject()
        self.assertEqual(wd.state, 'rejected')
        wd.action_reset_to_draft()
        self.assertEqual(wd.state, 'draft')

    def test_13_financially_locked_blocks_reopen(self):
        slot = self._make_slot()
        wd = self._declared(slot)
        wd.action_submit()
        wd.action_approve()
        self.assertFalse(wd.timesheet_financially_locked)  # nothing billed yet
        # Force the financial lock (SOL invoiced / period locked) and check the
        # guard: reopen is refused and the approved timesheet is left intact.
        def _locked(recs):
            for r in recs:
                r.timesheet_financially_locked = True
        with patch.object(type(wd), '_compute_timesheet_financially_locked', _locked):
            wd.invalidate_recordset(['timesheet_financially_locked'])
            self.assertTrue(wd.timesheet_financially_locked)
            with self.assertRaises(UserError):
                wd.action_reopen()
        self.assertEqual(wd.state, 'approved')
        self.assertTrue(wd.timesheet_id)

    def test_08_approve_requires_project(self):
        slot = self.env['planning.slot'].create({
            'resource_ids': self.emp.resource_id.ids,
            'start_datetime': self.start, 'end_datetime': self.end})
        wd = slot._get_or_create_work_declaration()
        wd.write({'actual_start': self.start, 'actual_end': self.end})
        wd.action_submit()
        with self.assertRaises(UserError):
            wd.action_approve()
