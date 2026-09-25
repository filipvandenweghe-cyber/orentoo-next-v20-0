# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class CrewWorkDeclaration(models.Model):
    """Thin layer between a planning shift and the standard timesheet.

    The crew member declares the hours they actually worked on a shift; the
    planner approves; on approval we create the standard timesheet
    (``account.analytic.line``) exactly once. From there the native chain takes
    over — the timesheet's ``so_line`` is deduced from ``task.sale_line_id`` and
    drives ``qty_delivered`` on the Sales Order Item. After approval the
    declaration is immutable: edits never silently touch the timesheet.
    """
    _name = 'crew.work.declaration'
    _description = 'Crew Work Declaration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'planned_start desc, id desc'

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True)
    slot_id = fields.Many2one(
        'planning.slot', string="Shift", required=True, ondelete='cascade',
        index=True, tracking=True)

    # --- context, mirrored from the shift (source of truth) --------------
    # Odoo 20 turned planning.slot.resource_id / employee_id into the
    # many2many resource_ids / employee_ids (a shift may staff several
    # resources).  A work declaration stays per crew member, so we mirror the
    # shift's first resource instead of using a related field.
    employee_id = fields.Many2one(
        'hr.employee', compute='_compute_slot_resource', store=True, index=True)
    resource_id = fields.Many2one(
        'resource.resource', compute='_compute_slot_resource', store=True)
    project_id = fields.Many2one('project.project', related='slot_id.project_id', store=True)
    task_id = fields.Many2one('project.task', related='slot_id.task_id', store=True)
    sale_line_id = fields.Many2one('sale.order.line', related='slot_id.sale_line_id', store=True)
    company_id = fields.Many2one('res.company', related='slot_id.company_id', store=True, index=True)
    planned_start = fields.Datetime(related='slot_id.start_datetime', store=True)
    planned_end = fields.Datetime(related='slot_id.end_datetime', store=True)
    planned_hours = fields.Float(related='slot_id.allocated_hours', store=True, string="Planned Hours")

    @api.depends('slot_id.resource_ids', 'slot_id.employee_ids')
    def _compute_slot_resource(self):
        for decl in self:
            decl.resource_id = decl.slot_id.resource_ids[:1]
            decl.employee_id = decl.slot_id.employee_ids[:1]

    # --- declared actuals -------------------------------------------------
    actual_start = fields.Datetime(tracking=True)
    actual_end = fields.Datetime(tracking=True)
    break_minutes = fields.Integer(string="Break (min)", default=0, tracking=True)
    worked_hours = fields.Float(compute='_compute_worked_hours', store=True, tracking=True)
    approved_hours = fields.Float(
        compute='_compute_approved_hours', store=True, string="Approved Hours",
        help="Worked hours once the declaration is approved (0 otherwise); "
             "lets reporting compare approved vs. all declared hours.")
    date = fields.Date(
        compute='_compute_date', store=True, string="Work Date",
        help="Day the shift took place (actual start, else planned start); "
             "used as the time axis in reporting.")
    comment = fields.Text()

    # --- lifecycle --------------------------------------------------------
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('reopened', 'Reopened'),
        ('rejected', 'Rejected'),
    ], default='draft', required=True, tracking=True, copy=False)
    locked = fields.Boolean(copy=False, help="Set on approval; the declaration is frozen.")
    timesheet_id = fields.Many2one(
        'account.analytic.line', string="Timesheet", copy=False, readonly=True,
        ondelete='set null', help="Timesheet created on approval (write-once).")
    timesheet_financially_locked = fields.Boolean(
        compute='_compute_timesheet_financially_locked',
        help="The approved timesheet's Sales Order Item has already been "
             "(partially) invoiced, or its period is locked — the declaration "
             "can no longer be reopened; correct it with an adjustment.")

    _slot_unique = models.Constraint(
        'UNIQUE(slot_id)', "A shift already has a work declaration.")

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'crew.work.declaration') or _('New')
        return super().create(vals_list)

    @api.depends('worked_hours', 'state')
    def _compute_approved_hours(self):
        for wd in self:
            wd.approved_hours = wd.worked_hours if wd.state == 'approved' else 0.0

    @api.depends('actual_start', 'planned_start')
    def _compute_date(self):
        for wd in self:
            dt = wd.actual_start or wd.planned_start
            wd.date = dt.date() if dt else False

    @api.depends('actual_start', 'actual_end', 'break_minutes')
    def _compute_worked_hours(self):
        for wd in self:
            if wd.actual_start and wd.actual_end and wd.actual_end > wd.actual_start:
                hours = (wd.actual_end - wd.actual_start).total_seconds() / 3600.0
                wd.worked_hours = max(hours - (wd.break_minutes or 0) / 60.0, 0.0)
            else:
                wd.worked_hours = 0.0

    @api.depends('timesheet_id', 'timesheet_id.so_line',
                 'timesheet_id.so_line.qty_invoiced')
    def _compute_timesheet_financially_locked(self):
        for wd in self:
            ts = wd.timesheet_id
            locked = False
            if ts:
                so_line = ts.so_line
                if so_line and so_line.qty_invoiced:
                    locked = True
                # analytic line inside a company accounting lock period
                company = ts.company_id
                lock_date = (getattr(company, 'hard_lock_date', False)
                             or getattr(company, 'fiscalyear_lock_date', False))
                if lock_date and ts.date and ts.date <= lock_date:
                    locked = True
            wd.timesheet_financially_locked = locked

    @api.constrains('actual_start', 'actual_end')
    def _check_actuals(self):
        for wd in self:
            if wd.actual_start and wd.actual_end and wd.actual_end <= wd.actual_start:
                raise ValidationError(_("The end of work must be after its start."))

    def write(self, vals):
        # Approved (locked) declarations are immutable except for the controlled
        # lifecycle fields written by the actions below.
        lifecycle = {'state', 'locked', 'timesheet_id'}
        if not (set(vals) <= lifecycle):
            for wd in self:
                if wd.locked:
                    raise UserError(_(
                        "Work declaration %s is approved and locked. Reopen it "
                        "(if not yet billed) or file an adjustment.", wd.name))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Timesheet bridge
    # ------------------------------------------------------------------
    def _prepare_timesheet_vals(self):
        self.ensure_one()
        return {
            'name': self.comment or (self.task_id.display_name or _("Crew work")),
            'project_id': self.project_id.id,
            'task_id': self.task_id.id,
            'employee_id': self.employee_id.id,
            'unit_amount': self.worked_hours,
            'date': (self.actual_start or self.planned_start or fields.Datetime.now()).date(),
            'company_id': (self.company_id or self.env.company).id,
        }

    def _ensure_timesheet(self):
        """Create the standard timesheet exactly once (idempotent)."""
        for wd in self:
            if wd.timesheet_id:
                continue
            if not wd.project_id:
                raise UserError(_(
                    "The shift of %s has no project, so no timesheet can be "
                    "created. Set a project/task on the shift first.", wd.name))
            if wd.worked_hours <= 0:
                raise UserError(_(
                    "Declare the worked hours on %s before approving.", wd.name))
            wd.timesheet_id = self.env['account.analytic.line'].create(
                wd._prepare_timesheet_vals())

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    # States from which the crew may (re)submit: a fresh draft, a reopened
    # declaration (approved one sent back for correction) or a rejected one
    # (planner asked for a redo). Submitted/approved are out of the crew's hands.
    _SUBMITTABLE_STATES = ('draft', 'reopened', 'rejected')

    def action_submit(self):
        for wd in self:
            if wd.state not in self._SUBMITTABLE_STATES:
                raise UserError(_(
                    "This declaration cannot be submitted in its current state "
                    "(%s).", wd.state))
            if not (wd.actual_start and wd.actual_end):
                raise UserError(_("Enter the actual start and end before submitting."))
            wd.state = 'submitted'
        return True

    def action_approve(self):
        for wd in self:
            if wd.state != 'submitted':
                raise UserError(_("Only a submitted declaration can be approved."))
            wd._ensure_timesheet()
            wd.write({'state': 'approved', 'locked': True})
        return True

    def action_reject(self):
        for wd in self:
            if wd.state not in ('submitted', 'draft', 'reopened'):
                raise UserError(_("This declaration cannot be rejected."))
            wd.state = 'rejected'
        return True

    def action_reset_to_draft(self):
        for wd in self:
            if wd.state != 'rejected':
                raise UserError(_("Only a rejected declaration can be reset to draft."))
            wd.state = 'draft'
        return True

    def action_reopen(self):
        """Explicit, permissioned reopen of an approved declaration: only while
        it is not financially locked. Detaches and removes the draft timesheet
        so a corrected declaration can re-approve cleanly."""
        for wd in self:
            if wd.state != 'approved':
                raise UserError(_("Only an approved declaration can be reopened."))
            if wd.timesheet_financially_locked:
                raise UserError(_(
                    "The timesheet of %s is already billed or in a locked "
                    "period. Correct it with an adjustment declaration instead "
                    "of reopening.", wd.name))
            ts = wd.timesheet_id
            wd.write({'state': 'reopened', 'locked': False, 'timesheet_id': False})
            if ts:
                ts.unlink()  # restores qty_delivered on the SOL
        return True
