# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError


class PlanningSlot(models.Model):
    """Crew extension of a planning shift.

    Requirements:
    - §14: one operational shift represents one Task — adds ``task_id`` (native
      slots link only to project / SOL) and a "Schedule Shift" path from a task.
    - Links a shift to the availability request it staffs (``crew_request_id``)
      for coverage/staffing KPIs.
    - "I can no longer work this shift": flags ``crew_unavailable_reported`` +
      reason and notifies the planner. Whether it also unassigns the crew
      (making it an open shift) honours the STANDARD Planning policy
      (Settings > Planning > "Employee Unavailabilities" == unassign) and the
      same deadline / past-shift guards Odoo uses for native self-unassign
      (``_crew_may_self_unassign``).
    - Re-assigning a real resource clears the stale "reported" flag (``write``
      override), so the shift is a fresh, live assignment in the portal again.
    - Owns at most one ``crew.work.declaration`` (``_get_or_create_...``).
    """
    _inherit = 'planning.slot'

    # §14 — one operational Planning Shift represents one Task. Standard Odoo 19
    # planning.slot has project_id/sale_line_id but NOT task_id, so we add it.
    task_id = fields.Many2one(
        'project.task', string="Task",
        domain="[('project_id', '=?', project_id)]",
        index='btree_not_null')
    crew_request_id = fields.Many2one(
        'crew.availability.request', string="Availability Request",
        index='btree_not_null', copy=False,
        help="Availability request this shift staffs (for coverage/staffing KPIs).")
    crew_unavailable_reported = fields.Boolean(
        string="Crew Reported Unavailable", copy=False,
        help="The assigned crew member reported they can no longer perform this "
             "shift. The assignment is kept until the planner handles it.")
    crew_unavailable_reason = fields.Char(string="Reason", copy=False)

    def write(self, vals):
        # Re-assigning a real crew member to the shift (in the backend, after a
        # portal "can no longer work") clears the stale "reported unavailable"
        # flag, so the portal treats the shift as a fresh, live assignment
        # again. Skip when the write already manages the flag itself (e.g. the
        # report action) or when the shift is being unassigned.
        #
        # Odoo 20: planning.slot.resource_id became the many2many resource_ids,
        # whose write value is a command list — truthy even when it CLEARS the
        # assignment.  So decide on the resulting assignment, after super().
        touches_resources = (
            'resource_ids' in vals and 'crew_unavailable_reported' not in vals
        )
        res = super().write(vals)
        if touches_resources:
            reassigned = self.filtered(
                lambda slot: slot.resource_ids and slot.crew_unavailable_reported)
            if reassigned:
                reassigned.write({
                    'crew_unavailable_reported': False,
                    'crew_unavailable_reason': False,
                })
        return res

    work_declaration_ids = fields.One2many(
        'crew.work.declaration', 'slot_id', string="Work Declarations")
    work_declaration_count = fields.Integer(compute='_compute_work_declaration_count')

    def _compute_work_declaration_count(self):
        data = dict(self.env['crew.work.declaration']._read_group(
            [('slot_id', 'in', self.ids)], ['slot_id'], ['__count']))
        for slot in self:
            slot.work_declaration_count = data.get(slot, 0)

    def _get_or_create_work_declaration(self):
        """Return the single work declaration of this shift, creating a draft
        one (prefilled from the plan) if none exists yet."""
        self.ensure_one()
        wd = self.work_declaration_ids[:1]
        if not wd:
            wd = self.env['crew.work.declaration'].create({
                'slot_id': self.id,
                'actual_start': self.start_datetime,
                'actual_end': self.end_datetime,
            })
        return wd

    def action_open_work_declaration(self):
        self.ensure_one()
        wd = self._get_or_create_work_declaration()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crew.work.declaration',
            'res_id': wd.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _crew_may_self_unassign(self):
        """Whether reporting "cannot work" may self-unassign the crew member.
        Honours the standard Planning policy (Settings > Planning >
        "Employee Unavailabilities" == 'unassign') and the same deadline/past
        guards Odoo uses for its native self-unassign action."""
        self.ensure_one()
        return bool(self.allow_self_unassign
                    and not self.is_unassign_deadline_passed
                    and not self.is_past)

    def action_crew_report_cannot_work(self, reason=False):
        """Crew reports they can no longer perform this shift. Always flag it
        with the reason and notify the planner (never delete the shift). Whether
        the crew member is unassigned (making it an OPEN shift) depends on the
        company's Planning policy: only self-unassign when the standard
        'Unassign themselves from shifts' policy is active and the deadline has
        not passed — otherwise the assignment is kept for the planner to handle
        (reassign / arrange a switch)."""
        for slot in self:
            emp_name = ", ".join(slot.employee_ids.mapped('display_name')) or _("Crew member")
            slot.crew_unavailable_reported = True
            if reason:
                slot.crew_unavailable_reason = reason
            may_unassign = slot._crew_may_self_unassign()
            if may_unassign:
                body = _(
                    "%(emp)s can no longer work the shift %(start)s → %(end)s; it "
                    "has been unassigned and is now an open shift.",
                    emp=emp_name, start=slot.start_datetime, end=slot.end_datetime)
            else:
                body = _(
                    "%(emp)s reported they can no longer work the shift "
                    "%(start)s → %(end)s. The assignment has been kept — please "
                    "reassign it or arrange a switch.",
                    emp=emp_name, start=slot.start_datetime, end=slot.end_datetime)
            if reason:
                body += _(" Reason: %s", reason)
            if slot.crew_request_id:
                slot.crew_request_id.message_post(body=body)
            if may_unassign:
                slot.resource_ids = [Command.clear()]  # unassign -> open shift
        return True

    @api.onchange('task_id')
    def _onchange_task_id(self):
        if self.task_id:
            self.project_id = self.task_id.project_id

    @api.constrains('task_id', 'project_id')
    def _check_task_in_project(self):
        for slot in self:
            if slot.task_id and slot.project_id and slot.task_id.project_id != slot.project_id:
                raise ValidationError(_(
                    "The task %(task)s does not belong to project %(project)s.",
                    task=slot.task_id.display_name,
                    project=slot.project_id.display_name))
