# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'crew_planning')
class TestCrewRequests(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env['crew.availability.engine']
        cls.now = cls.engine._now()

        # Skills: one type "Sound" with 3 ordered levels
        cls.skill_type = cls.env['hr.skill.type'].create({'name': 'Audio'})
        cls.lvl_beg, cls.lvl_int, cls.lvl_adv = cls.env['hr.skill.level'].create([
            {'name': 'Beginner', 'level_progress': 0, 'skill_type_id': cls.skill_type.id},
            {'name': 'Intermediate', 'level_progress': 50, 'skill_type_id': cls.skill_type.id},
            {'name': 'Advanced', 'level_progress': 100, 'skill_type_id': cls.skill_type.id},
        ])
        cls.skill_sound = cls.env['hr.skill'].create({
            'name': 'Sound', 'skill_type_id': cls.skill_type.id})
        cls.skill_light = cls.env['hr.skill'].create({
            'name': 'Lighting', 'skill_type_id': cls.skill_type.id})

        cls.role_sound = cls.env['planning.role'].create({'name': 'Sound Tech'})

        cls.emp_adv = cls._make_emp('Ava Advanced', 'explicit', cls.skill_sound, cls.lvl_adv, cls.role_sound)
        cls.emp_int = cls._make_emp('Ivo Intermediate', 'standard', cls.skill_sound, cls.lvl_int, cls.role_sound)
        cls.emp_light = cls._make_emp('Lena Lighting', 'standard', cls.skill_light, cls.lvl_adv, cls.role_sound)
        cls.emp_noemail = cls._make_emp('Noel NoEmail', 'standard', cls.skill_sound, cls.lvl_adv, cls.role_sound, email=False)

        cls.d1 = cls.now + timedelta(days=20)
        cls.d2 = cls.d1 + timedelta(hours=10)

    @classmethod
    def _make_emp(cls, name, mode, skill, level, role, email=True):
        vals = {'name': name, 'is_crew': True, 'crew_availability_mode': mode}
        if email:
            vals['work_email'] = name.replace(' ', '.').lower() + '@example.com'
            vals['mobile_phone'] = '+3247' + str(abs(hash(name)) % 10000000).zfill(7)
        emp = cls.env['hr.employee'].create(vals)
        cls.env['hr.employee.skill'].create({
            'employee_id': emp.id,
            'skill_type_id': skill.skill_type_id.id,
            'skill_id': skill.id,
            'skill_level_id': level.id,
        })
        emp.resource_id.default_role_id = role
        return emp

    def _make_request(self, **kw):
        vals = {
            'request_type': 'period',
            'date_start': self.d1,
            'date_end': self.d2,
            'headcount_needed': 1,
            'skill_requirement_ids': [(0, 0, {
                'skill_type_id': self.skill_type.id,
                'skill_id': self.skill_sound.id,
                'min_skill_level_id': self.lvl_int.id,
            })],
        }
        vals.update(kw)
        return self.env['crew.availability.request'].create(vals)

    def _wizard(self, req):
        wiz = self.env['crew.invite.wizard'].create({'request_id': req.id})
        wiz.action_refresh()
        return wiz

    # ------------------------------------------------------------------
    def test_01_sequence_name(self):
        req = self._make_request()
        self.assertTrue(req.name.startswith('CAR/'), "Request should get a sequence name.")

    def test_02_candidate_matching_skill_and_role(self):
        req = self._make_request(role_id=self.role_sound.id)
        cands = req._match_candidate_employees()
        self.assertIn(self.emp_adv, cands)      # Sound Advanced >= Intermediate
        self.assertIn(self.emp_int, cands)      # Sound Intermediate >= Intermediate
        self.assertNotIn(self.emp_light, cands)  # no Sound skill

    def test_03_min_level_excludes_below(self):
        req = self._make_request()
        req.skill_requirement_ids.min_skill_level_id = self.lvl_adv  # require Advanced
        cands = req._match_candidate_employees()
        self.assertIn(self.emp_adv, cands)
        self.assertNotIn(self.emp_int, cands)  # Intermediate < Advanced

    def test_04_invite_waves_and_exclusion(self):
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)
        self.assertEqual(len(wiz.line_ids), 3)  # Ava, Ivo, Noel(no-email)
        wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_adv).selected = True
        wiz.action_invite_selected()
        self.assertEqual(len(req.invitation_ids), 1)
        self.assertEqual(req.invitation_ids.wave, 1)
        # wave 2: everyone is still listed, but the invited one is flagged and
        # its selection toggle is disabled; new people can still be added.
        wiz2 = self._wizard(req)
        self.assertEqual(len(wiz2.line_ids), 3)
        adv_line = wiz2.line_ids.filtered(lambda l: l.employee_id == self.emp_adv)
        self.assertTrue(adv_line.already_invited)
        int_line = wiz2.line_ids.filtered(lambda l: l.employee_id == self.emp_int)
        self.assertFalse(int_line.already_invited)
        int_line.selected = True
        wiz2.action_invite_selected()
        self.assertEqual(len(req.invitation_ids), 2)
        self.assertEqual(max(req.invitation_ids.mapped('wave')), 2)

    def test_05_no_duplicate_invitation(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        req = self._make_request()
        self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_adv.id})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            with self.cr.savepoint():
                self.env['crew.availability.invitation'].create({
                    'request_id': req.id, 'employee_id': self.emp_adv.id})
                self.env.flush_all()

    def test_06_reminder_no_new_invitation(self):
        req = self._make_request()
        inv = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_adv.id})
        inv.action_remind()
        self.assertEqual(inv.reminder_count, 1)
        self.assertEqual(inv.state, 'reminded')
        self.assertEqual(len(req.invitation_ids), 1)

    def test_07_response_available_feeds_engine(self):
        req = self._make_request()
        inv = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_adv.id})
        inv.action_set_available()
        self.assertEqual(inv.response, 'available')
        # explicit crew -> a window/hole must now exist for the period
        win = self.env['crew.availability'].search([
            ('resource_id', '=', self.emp_adv.resource_id.id),
            ('date_start', '<', self.d2), ('date_end', '>', self.d1)])
        self.assertTrue(win, "Available response must register availability via the engine.")

    def test_08_response_standard_crew_no_engine(self):
        req = self._make_request()
        inv = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_int.id})  # standard mode
        inv.action_set_available()
        self.assertEqual(inv.response, 'available')
        self.assertFalse(self.env['crew.availability'].search([
            ('resource_id', '=', self.emp_int.resource_id.id)]),
            "Standard-schedule crew must not get engine windows.")

    def test_09_coverage_vs_staffing(self):
        req = self._make_request(headcount_needed=2)
        for emp in (self.emp_adv, self.emp_int):
            self.env['crew.availability.invitation'].create({
                'request_id': req.id, 'employee_id': emp.id}).action_set_available()
        self.assertEqual(req.available_count, 2)
        self.assertEqual(req.availability_coverage, 'sufficient')
        # staffing is separate: no slots yet
        self.assertEqual(req.planned_headcount, 0)
        self.assertEqual(req.staffing_display, '0 / 2')
        # add a planning slot linked to the request -> staffing rises, coverage unchanged
        self.env['planning.slot'].create({
            'resource_ids': self.emp_adv.resource_id.ids,
            'crew_request_id': req.id,
            'start_datetime': self.d1, 'end_datetime': self.d2})
        req.invalidate_recordset(['planned_headcount', 'staffing_display'])
        self.assertEqual(req.planned_headcount, 1)
        self.assertEqual(req.availability_coverage, 'sufficient')

    def test_12_invite_without_selection_blocks(self):
        from odoo.exceptions import UserError
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)  # candidates present, none selected
        with self.assertRaises(UserError):
            wiz.action_invite_selected()

    def test_14_period_label_single_and_multi_day(self):
        single = self.env['crew.availability.request'].create({
            'request_type': 'period',
            'date_start': '2026-11-02 08:00:00', 'date_end': '2026-11-02 17:00:00'})
        self.assertTrue(single.period_label.lower().startswith('for '),
                        "Single day should read 'for <date>'.")
        self.assertNotIn(' to ', single.period_label)
        multi = self.env['crew.availability.request'].create({
            'request_type': 'period',
            'date_start': '2026-11-02 08:00:00', 'date_end': '2026-11-05 17:00:00'})
        self.assertTrue(multi.period_label.lower().startswith('from '))
        self.assertIn(' to ', multi.period_label)

    def test_13_invite_without_email_blocks(self):
        from odoo.exceptions import UserError
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)
        wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_noemail).selected = True
        with self.assertRaises(UserError):
            wiz.action_invite_selected()  # channel = email, no work_email

    def test_18_count_known_available_without_sending(self):
        # A crew member already known-available for the period is shown in the
        # wizard and can be "taken along" to the count WITHOUT any message being
        # sent — even if they have no email/phone.
        self.env['crew.availability'].create({
            'resource_id': self.emp_noemail.resource_id.id,
            'employee_id': self.emp_noemail.id,
            'company_id': self.env.company.id,
            'date_start': self.d1, 'date_end': self.d2})
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)  # channel defaults to email
        line = wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_noemail)
        self.assertEqual(line.known_state, 'available')
        line.selected = True
        wiz.action_invite_selected()  # no email needed: counted, not sent
        inv = req.invitation_ids.filtered(lambda i: i.employee_id == self.emp_noemail)
        self.assertEqual(inv.response, 'available')
        self.assertEqual(inv.state, 'responded')
        self.assertFalse(inv.sent_on)
        self.assertEqual(req.available_count, 1)

    def test_19_partial_coverage_counts_as_partial(self):
        # A window covering only part of a long request period => 'partial',
        # counted in partial_count (NOT available_count), so coverage stays honest.
        w0 = self.now + timedelta(days=15)
        self.env['crew.availability'].create({
            'resource_id': self.emp_noemail.resource_id.id,
            'employee_id': self.emp_noemail.id,
            'company_id': self.env.company.id,
            'date_start': w0, 'date_end': w0 + timedelta(days=1)})
        req = self.env['crew.availability.request'].create({
            'request_type': 'period',
            'date_start': self.now + timedelta(days=10),
            'date_end': self.now + timedelta(days=40),
            'role_id': self.role_sound.id, 'headcount_needed': 1,
            'skill_requirement_ids': [(0, 0, {
                'skill_type_id': self.skill_type.id, 'skill_id': self.skill_sound.id,
                'min_skill_level_id': self.lvl_int.id})]})
        req.action_open()
        self.assertEqual(req._employee_known_state(self.emp_noemail), 'partial')
        wiz = self._wizard(req)
        line = wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_noemail)
        self.assertEqual(line.known_state, 'partial')
        line.selected = True
        wiz.action_invite_selected()
        inv = req.invitation_ids.filtered(lambda i: i.employee_id == self.emp_noemail)
        self.assertEqual(inv.response, 'partial')
        self.assertEqual(req.partial_count, 1)
        self.assertEqual(req.available_count, 0)
        self.assertEqual(req.availability_coverage, 'insufficient')

    def test_20_partial_with_contact_is_counted_and_resent(self):
        # A partially-available crew member who is reachable should be counted as
        # partial AND re-asked (invitation sent) to fill the gaps.
        w0 = self.now + timedelta(days=15)
        self.env['crew.availability'].create({
            'resource_id': self.emp_adv.resource_id.id,
            'employee_id': self.emp_adv.id,
            'company_id': self.env.company.id,
            'date_start': w0, 'date_end': w0 + timedelta(days=1)})
        req = self.env['crew.availability.request'].create({
            'request_type': 'period',
            'date_start': self.now + timedelta(days=10),
            'date_end': self.now + timedelta(days=40),
            'role_id': self.role_sound.id, 'headcount_needed': 1,
            'skill_requirement_ids': [(0, 0, {
                'skill_type_id': self.skill_type.id, 'skill_id': self.skill_sound.id,
                'min_skill_level_id': self.lvl_int.id})]})
        req.action_open()
        wiz = self._wizard(req)  # channel email; emp_adv has email
        line = wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_adv)
        self.assertEqual(line.known_state, 'partial')
        line.selected = True
        wiz.action_invite_selected()
        inv = req.invitation_ids.filtered(lambda i: i.employee_id == self.emp_adv)
        self.assertEqual(inv.response, 'partial')   # counted as partial
        self.assertEqual(inv.state, 'sent')         # AND re-asked
        self.assertTrue(inv.sent_on)
        self.assertEqual(req.partial_count, 1)

    def test_21_partial_decline_is_unknown_full_decline_is_unavailable(self):
        emp = self.emp_noemail
        # a 1-day decline inside a 1-month request, no availability -> still ask
        log = self.env['crew.availability.log'].create({
            'employee_id': emp.id, 'resource_id': emp.resource_id.id,
            'company_id': self.env.company.id,
            'date_start': '2026-09-10 08:00:00', 'date_end': '2026-09-11 08:00:00',
            'declared_state': 'unavailable', 'origin': 'planner'})
        req = self.env['crew.availability.request'].create({
            'request_type': 'period',
            'date_start': '2026-09-01 00:00:00', 'date_end': '2026-09-30 00:00:00'})
        self.assertEqual(req._employee_known_state(emp), 'unknown')
        # decline covering the WHOLE period -> unavailable
        log.write({'date_start': '2026-08-01 00:00:00', 'date_end': '2026-10-01 00:00:00'})
        self.assertEqual(req._employee_known_state(emp), 'unavailable')

    def test_24_portal_hidden_counters(self):
        emp = self.env['hr.employee'].create({
            'name': 'Hider', 'is_crew': True,
            'crew_portal_hide_sales': True, 'crew_portal_hide_invoices': True,
            'crew_portal_hide_purchases': True, 'crew_portal_hide_timesheets': True})
        keys = emp._crew_portal_hidden_counters()
        self.assertTrue({'order_count', 'quotation_count', 'invoice_count',
                         'purchase_count', 'timesheet_count'} <= keys)
        emp.crew_portal_hide_sales = False
        self.assertNotIn('order_count', emp._crew_portal_hidden_counters())
        self.assertIn('invoice_count', emp._crew_portal_hidden_counters())

    def test_23_grant_portal_access(self):
        from odoo.exceptions import UserError
        emp = self.env['hr.employee'].create({
            'name': 'Grantee', 'is_crew': True, 'work_email': 'grantee@example.com'})
        emp.action_crew_grant_portal()
        self.assertTrue(emp.user_id, "A related user must be created/linked.")
        self.assertTrue(emp.user_id.share, "The granted user must be a Portal user.")
        self.assertEqual(emp.user_id.login, 'grantee@example.com')
        # granting again is blocked
        with self.assertRaises(UserError):
            emp.action_crew_grant_portal()
        # no work email -> blocked
        emp2 = self.env['hr.employee'].create({'name': 'NoMail', 'is_crew': True})
        with self.assertRaises(UserError):
            emp2.action_crew_grant_portal()

    def test_25_cannot_invite_past_started_request(self):
        from odoo.exceptions import UserError
        past = self.env['crew.availability.request'].create({
            'request_type': 'period', 'role_id': self.role_sound.id,
            'date_start': self.now - timedelta(days=5),
            'date_end': self.now - timedelta(days=1)})
        past.action_open()
        wiz = self._wizard(past)
        with self.assertRaises(UserError):
            wiz.action_invite_selected()

    def test_22_report_cannot_work_unassigns_and_notifies(self):
        # Company policy allows crew to unassign themselves.
        self.env.company.planning_employee_unavailabilities = 'unassign'
        req = self._make_request(role_id=self.role_sound.id)
        slot = self.env['planning.slot'].create({
            'resource_ids': self.emp_adv.resource_id.ids,
            'crew_request_id': req.id,
            'start_datetime': self.d1, 'end_datetime': self.d2})
        self.assertTrue(slot.allow_self_unassign)
        before = len(req.message_ids)
        slot.action_crew_report_cannot_work('Sick')
        self.assertTrue(slot.crew_unavailable_reported)
        self.assertEqual(slot.crew_unavailable_reason, 'Sick')
        self.assertFalse(slot.resource_ids, "Crew must be unassigned (open shift).")
        self.assertTrue(slot.exists(), "The shift itself must NOT be deleted.")
        self.assertGreater(len(req.message_ids), before,
                           "The planner must be notified via the request chatter.")

    def test_26_report_cannot_work_switch_policy_keeps_assignment(self):
        # Company policy is "switch" (the Odoo default): crew may NOT self
        # unassign. Reporting still flags + notifies, but keeps the assignment.
        self.env.company.planning_employee_unavailabilities = 'switch'
        req = self._make_request(role_id=self.role_sound.id)
        slot = self.env['planning.slot'].create({
            'resource_ids': self.emp_adv.resource_id.ids,
            'crew_request_id': req.id,
            'start_datetime': self.d1, 'end_datetime': self.d2})
        self.assertFalse(slot.allow_self_unassign)
        before = len(req.message_ids)
        slot.action_crew_report_cannot_work('Sick')
        self.assertTrue(slot.crew_unavailable_reported)
        self.assertEqual(slot.crew_unavailable_reason, 'Sick')
        self.assertEqual(slot.resource_ids, self.emp_adv.resource_id,
                         "Assignment must be kept under the 'switch' policy.")
        self.assertGreater(len(req.message_ids), before,
                           "The planner must still be notified.")

    def test_27_reassignment_clears_cannot_work_flag(self):
        self.env.company.planning_employee_unavailabilities = 'unassign'
        req = self._make_request(role_id=self.role_sound.id)
        slot = self.env['planning.slot'].create({
            'resource_ids': self.emp_adv.resource_id.ids,
            'crew_request_id': req.id,
            'start_datetime': self.d1, 'end_datetime': self.d2})
        slot.action_crew_report_cannot_work('Sick')
        self.assertFalse(slot.resource_ids)
        self.assertTrue(slot.crew_unavailable_reported)
        # planner reassigns the shift in the backend -> flag/reason cleared
        slot.resource_ids = self.emp_adv.resource_id
        self.assertFalse(slot.crew_unavailable_reported,
                         "Re-assigning a crew member must clear the stale flag.")
        self.assertFalse(slot.crew_unavailable_reason)

    def test_28_invitation_exposes_request_period(self):
        req = self._make_request()
        inv = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_adv.id, 'channel': 'email'})
        self.assertTrue(inv.period_label)
        self.assertEqual(inv.period_label, req.period_label)

    def test_29_self_unassign_blocked_past_deadline(self):
        # Unassign policy is on, but the unassignment deadline has passed:
        # the report must KEEP the assignment (only flag + notify).
        self.env.company.planning_employee_unavailabilities = 'unassign'
        self.env.company.planning_self_unassign_days_before = 30
        req = self._make_request(role_id=self.role_sound.id)
        slot = self.env['planning.slot'].create({
            'resource_ids': self.emp_adv.resource_id.ids,
            'crew_request_id': req.id,
            'start_datetime': self.d1, 'end_datetime': self.d2})  # d1 = now + 20d
        self.assertTrue(slot.is_unassign_deadline_passed)
        self.assertFalse(slot._crew_may_self_unassign())
        slot.action_crew_report_cannot_work('too late')
        self.assertEqual(slot.resource_ids, self.emp_adv.resource_id,
                         "Past the deadline -> assignment kept.")
        self.assertTrue(slot.crew_unavailable_reported)

    def test_17_send_whatsapp_after_email(self):
        from odoo.exceptions import UserError
        req = self._make_request()
        inv = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_adv.id, 'channel': 'email'})
        inv.action_send()                 # emailed
        inv.action_send_whatsapp_now()    # emp_adv has phone; resilient, no crash
        # a crew member without a phone can't be WhatsApp'd
        inv2 = self.env['crew.availability.invitation'].create({
            'request_id': req.id, 'employee_id': self.emp_noemail.id})
        with self.assertRaises(UserError):
            inv2.action_send_whatsapp_now()

    def test_15_whatsapp_requires_phone(self):
        from odoo.exceptions import UserError
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)
        wiz.channel = 'whatsapp'
        wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_noemail).selected = True
        with self.assertRaises(UserError):
            wiz.action_invite_selected()  # no phone -> blocked

    def test_16_whatsapp_invite_resilient_when_unconfigured(self):
        # emp_adv has a phone; with no WhatsApp account configured the invite
        # must still succeed (nudge skipped with a chatter note), not crash.
        req = self._make_request(role_id=self.role_sound.id)
        req.action_open()
        wiz = self._wizard(req)
        wiz.channel = 'whatsapp'
        wiz.line_ids.filtered(lambda l: l.employee_id == self.emp_adv).selected = True
        wiz.action_invite_selected()
        self.assertEqual(len(req.invitation_ids), 1)
        self.assertEqual(req.invitation_ids.state, 'sent')
        self.assertEqual(req.invitation_ids.channel, 'whatsapp')

    def test_11_candidates_scoped_to_request_company(self):
        other = self.env['res.company'].create({'name': 'Crew Other Co'})
        emp_other = self.env['hr.employee'].create({
            'name': 'Otto Other', 'company_id': other.id})
        self.env['hr.employee.skill'].create({
            'employee_id': emp_other.id, 'skill_type_id': self.skill_type.id,
            'skill_id': self.skill_sound.id, 'skill_level_id': self.lvl_adv.id})
        req = self._make_request()  # company = env.company, not `other`
        cands = req._match_candidate_employees()
        self.assertIn(self.emp_adv, cands)
        self.assertNotIn(emp_other, cands,
                         "Candidates must be scoped to the request's company (multi-company).")

    def test_10_exclude_already_answered(self):
        # a candidate who already declared availability for the period is not re-asked
        self.env['crew.availability.log'].create({
            'employee_id': self.emp_adv.id,
            'resource_id': self.emp_adv.resource_id.id,
            'date_start': self.d1, 'date_end': self.d2,
            'declared_state': 'available', 'origin': 'planner',
        })
        req = self._make_request()
        cands = req._match_candidate_employees(exclude_answered=True)
        self.assertNotIn(self.emp_adv, cands)
        cands_all = req._match_candidate_employees(exclude_answered=False)
        self.assertIn(self.emp_adv, cands_all)
