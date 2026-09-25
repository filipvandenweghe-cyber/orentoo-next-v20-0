# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class CrewAvailabilityRequest(models.Model):
    """A planner's call for availability over a period — the hub of the
    invitation flow.

    Requirements:
    - Scoped to a Task, a Project, or a bare period (``request_type``); prefills
      its window/effort from that source.
    - Lifecycle ``draft -> open -> closed`` (+ ``cancelled``). There is NO
      "fulfilled" state: ``availability_coverage`` (sufficient/insufficient) and
      ``planned_headcount`` are computed, orthogonal *indicators* — closing is
      always an explicit planner decision, never auto-triggered by coverage.
    - Candidate selection (skills at/above a minimum level + role) is computed
      on the fly and scoped to the request's company; invitations are persisted
      only when the planner actually invites, in ordered *waves* that exclude
      already-invited/answered people.
    - Exposes human-readable ``period_label`` / ``indicative_hours`` reused by
      the portal, invitation list and e-mails.
    """
    _name = 'crew.availability.request'
    _description = 'Crew Availability Request'
    _inherit = ['mail.thread']
    _order = 'create_date desc, id desc'

    name = fields.Char(default='New', copy=False, readonly=True)
    request_type = fields.Selection([
        ('task', 'Task'),
        ('project', 'Project'),
        ('period', 'Period'),
    ], required=True, default='task', tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True)

    project_id = fields.Many2one('project.project', tracking=True)
    task_id = fields.Many2one(
        'project.task', domain="[('project_id', '=?', project_id)]", tracking=True)
    date_start = fields.Datetime(required=True, tracking=True)
    date_end = fields.Datetime(required=True, tracking=True)
    role_id = fields.Many2one('planning.role', string="Planning Role")
    headcount_needed = fields.Integer(string="Headcount Needed", default=1)
    indicative_hours = fields.Float(
        string="Indicative Effort (h)",
        help="Rough effort shown to crew so they can judge whether to make "
             "themselves available. Prefilled from the task when applicable. "
             "This is only an indication — the actual assigned hours live on the "
             "Planning shift / timesheet, not here.")
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)

    skill_requirement_ids = fields.One2many(
        'crew.availability.request.skill', 'request_id', string="Required Skills")
    invitation_ids = fields.One2many(
        'crew.availability.invitation', 'request_id', string="Invitations")

    # --- KPIs (availability) ---
    invited_count = fields.Integer(compute='_compute_kpis')
    available_count = fields.Integer(compute='_compute_kpis')
    partial_count = fields.Integer(compute='_compute_kpis')
    unavailable_count = fields.Integer(compute='_compute_kpis')
    pending_count = fields.Integer(compute='_compute_kpis')
    availability_coverage = fields.Selection([
        ('insufficient', 'Insufficient'),
        ('sufficient', 'Sufficient'),
    ], compute='_compute_kpis', string="Availability Coverage")
    # --- KPIs (staffing — separate from availability) ---
    planned_headcount = fields.Integer(compute='_compute_planned_headcount')
    staffing_display = fields.Char(compute='_compute_planned_headcount', string="Staffing")

    # Day-level, human-friendly period for the invitation ("for <date>" on a
    # single day, "from <date> to <date>" across days) — no messy exact times.
    period_label = fields.Char(compute='_compute_period_label')

    @api.depends('date_start', 'date_end')
    def _compute_period_label(self):
        for req in self:
            if not (req.date_start and req.date_end):
                req.period_label = False
                continue
            start = fields.Datetime.context_timestamp(req, req.date_start).date()
            end = fields.Datetime.context_timestamp(req, req.date_end).date()
            if start == end:
                req.period_label = _("for %s", format_date(self.env, start))
            else:
                req.period_label = _("from %s to %s",
                                     format_date(self.env, start),
                                     format_date(self.env, end))

    @api.depends('invitation_ids.response', 'headcount_needed')
    def _compute_kpis(self):
        for req in self:
            invs = req.invitation_ids
            req.invited_count = len(invs)
            req.available_count = len(invs.filtered(lambda i: i.response == 'available'))
            req.partial_count = len(invs.filtered(lambda i: i.response == 'partial'))
            req.unavailable_count = len(invs.filtered(lambda i: i.response == 'unavailable'))
            req.pending_count = len(invs.filtered(lambda i: i.response == 'pending'))
            req.availability_coverage = (
                'sufficient' if req.available_count >= req.headcount_needed
                else 'insufficient')

    @api.depends('task_id', 'invitation_ids')
    def _compute_planned_headcount(self):
        Slot = self.env['planning.slot']
        for req in self:
            domain = [('crew_request_id', '=', req.id)]
            if req.task_id:
                domain = ['|', ('crew_request_id', '=', req.id),
                          ('task_id', '=', req.task_id.id)]
            slots = Slot.search(domain + [('resource_ids', '!=', False)])
            req.planned_headcount = len(slots.resource_ids)
            req.staffing_display = "%s / %s" % (req.planned_headcount, req.headcount_needed)

    # ------------------------------------------------------------------
    @api.onchange('task_id')
    def _onchange_task_id(self):
        if self.task_id:
            self.project_id = self.task_id.project_id
            if self.task_id.planned_date_begin:
                self.date_start = self.task_id.planned_date_begin
            if self.task_id.date_deadline:
                self.date_end = self.task_id.date_deadline
            if self.task_id.allocated_hours:
                self.indicative_hours = self.task_id.allocated_hours

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'crew.availability.request') or 'New'
        return super().create(vals_list)

    def _origin_code(self):
        self.ensure_one()
        return {
            'task': 'task_request',
            'project': 'project_request',
            'period': 'period_request',
        }.get(self.request_type, 'period_request')

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_open(self):
        for req in self:
            if req.date_start >= req.date_end:
                raise UserError(_("The start must be before the end."))
        self.write({'state': 'open'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})

    # ------------------------------------------------------------------
    # Candidate matching
    # ------------------------------------------------------------------
    def _match_candidate_employees(self, exclude_answered=True, exclude_invited=True):
        self.ensure_one()
        Emp = self.env['hr.employee']
        # Scope to the request's company (+ company-less) so we never reach
        # across companies into records the user can't access (multi-company).
        emps = Emp.search([
            ('active', '=', True),
            ('company_id', 'in', [self.company_id.id, False]),
        ])
        if self.role_id:
            emps = emps.filtered(lambda e: self.role_id in (
                e.resource_id.role_ids | e.resource_id.default_role_id))
        for req in self.skill_requirement_ids:
            emps = emps.filtered(lambda e: any(
                s.skill_id == req.skill_id and s.level_progress >= req.min_level_progress
                for s in e.employee_skill_ids))
        if exclude_invited:
            emps -= self.invitation_ids.employee_id
        if exclude_answered and self.date_start and self.date_end:
            answered = self.env['crew.availability.log'].search([
                ('date_start', '<', self.date_end),
                ('date_end', '>', self.date_start),
                ('employee_id', 'in', emps.ids),
            ]).employee_id
            emps -= answered
        return emps

    @staticmethod
    def _covered_seconds(intervals, start, end):
        """Seconds of [start, end] covered by the (start, end) intervals,
        merging overlaps and clipping to the period."""
        clipped = sorted((max(s, start), min(e, end)) for s, e in intervals
                         if e > start and s < end)
        covered, cur_s, cur_e = 0.0, None, None
        for s, e in clipped:
            if cur_e is None or s > cur_e:
                if cur_e is not None:
                    covered += (cur_e - cur_s).total_seconds()
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        if cur_e is not None:
            covered += (cur_e - cur_s).total_seconds()
        return covered

    def _employee_known_state(self, employee):
        """What we already KNOW about this employee's availability for the
        request period (independent of any invitation):

        * 'available'   — registered availability covers the WHOLE period;
        * 'partial'     — registered availability covers only part of it;
        * 'unavailable' — declared unavailable for the WHOLE period, no availability;
        * 'unknown'     — nothing usable on record, or only a partial decline
                          (so we should still ask about the rest).
        """
        self.ensure_one()
        if not (self.date_start and self.date_end):
            return 'unknown'
        start, end = self.date_start, self.date_end
        total = (end - start).total_seconds()

        wins = self.env['crew.availability'].search([
            ('employee_id', '=', employee.id),
            ('date_start', '<', end), ('date_end', '>', start)])
        avail = self._covered_seconds(
            [(w.date_start, w.date_end) for w in wins], start, end)
        if total and avail >= total - 1:
            return 'available'
        if avail > 0:
            return 'partial'

        # No positive availability — only a FULL decline counts as unavailable;
        # a partial decline leaves the rest open, so we still ask.
        unlogs = self.env['crew.availability.log'].search([
            ('employee_id', '=', employee.id),
            ('declared_state', '=', 'unavailable'),
            ('date_start', '<', end), ('date_end', '>', start)])
        unavail = self._covered_seconds(
            [(l.date_start, l.date_end) for l in unlogs], start, end)
        if total and unavail >= total - 1:
            return 'unavailable'
        return 'unknown'

    def action_find_candidates(self):
        self.ensure_one()
        if self.state == 'draft':
            self.action_open()
        # Create a PERSISTED wizard with real candidate lines so the per-line
        # selection is stored reliably (a brand-new unsaved transient record
        # can lose the toggle on button-save, esp. with a cached view).
        wizard = self.env['crew.invite.wizard'].create({'request_id': self.id})
        wizard.action_refresh()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Find & Invite Candidates"),
            'res_model': 'crew.invite.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_view_invitations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Invitations"),
            'res_model': 'crew.availability.invitation',
            'view_mode': 'list,form',
            'domain': [('request_id', '=', self.id)],
            'context': {'default_request_id': self.id},
        }


class CrewAvailabilityRequestSkill(models.Model):
    _name = 'crew.availability.request.skill'
    _description = 'Crew Availability Request — Required Skill'

    request_id = fields.Many2one('crew.availability.request', required=True, ondelete='cascade')
    skill_type_id = fields.Many2one('hr.skill.type', required=True)
    skill_id = fields.Many2one(
        'hr.skill', required=True, domain="[('skill_type_id', '=', skill_type_id)]")
    min_skill_level_id = fields.Many2one(
        'hr.skill.level', required=True, string="Minimum Level",
        domain="[('skill_type_id', '=', skill_type_id)]")
    min_level_progress = fields.Integer(
        related='min_skill_level_id.level_progress', store=True)

    @api.onchange('skill_type_id')
    def _onchange_skill_type(self):
        if self.skill_id.skill_type_id != self.skill_type_id:
            self.skill_id = False
        if self.min_skill_level_id.skill_type_id != self.skill_type_id:
            self.min_skill_level_id = False
