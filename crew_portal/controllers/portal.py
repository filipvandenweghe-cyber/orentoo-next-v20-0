# -*- coding: utf-8 -*-
from datetime import datetime

import pytz

from odoo import _, fields, http
from odoo.http import request
from odoo.tools import format_datetime
from odoo.addons.portal.controllers.portal import CustomerPortal


class CrewPortal(CustomerPortal):
    """Own-records-only crew self-service. Every route resolves the logged-in
    user's employee and reads/writes ONLY that person's data (sudo is used for
    the controlled writes, but always scoped to the resolved employee)."""

    # ------------------------------------------------------------------
    def _crew_employee(self):
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', request.env.user.id)], limit=1)

    def _fmt_dt(self, value):
        """Localized datetime with the month spelled out, in the user's tz."""
        if not value:
            return ''
        return format_datetime(request.env, value, tz=request.env.user.tz or 'UTC',
                               dt_format='d MMMM y HH:mm')

    def _parse_portal_dt(self, value):
        """A browser datetime-local value ('YYYY-MM-DDTHH:MM') is naive local
        time; convert it to the naive UTC datetimes Odoo stores."""
        if not value:
            return False
        naive = datetime.strptime(value.replace('T', ' ')[:16], '%Y-%m-%d %H:%M')
        tz = pytz.timezone(request.env.user.tz or 'UTC')
        return tz.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)

    def _dt_local_input(self, value):
        """Format a stored (naive UTC) datetime as a 'YYYY-MM-DDTHH:MM' string
        in the user's tz, for a datetime-local input default value."""
        if not value:
            return ''
        tz = pytz.timezone(request.env.user.tz or 'UTC')
        local = pytz.utc.localize(value).astimezone(tz)
        return local.strftime('%Y-%m-%dT%H:%M')

    # ------------------------------------------------------------------
    def _prepare_portal_counter_values(self, counter):
        """Feed the crew badge counters.

        Odoo 20 replaced the ``_prepare_home_portal_values(counters)`` hook by
        ``_prepare_portal_counter_values(counter)``, which returns the
        (model, domain, access) triple the portal counts itself.
        """
        if counter in ('crew_availability_count', 'crew_planning_count', 'crew_hours_count'):
            emp = self._crew_employee()
            if not emp:
                return False, False, False
            if counter == 'crew_availability_count':
                return 'crew.availability', [('employee_id', '=', emp.id)], 'sudo'
            if not emp.resource_id:
                return False, False, False
            if counter == 'crew_planning_count':
                return 'planning.slot', [
                    ('resource_ids', 'in', emp.resource_id.ids),
                    ('end_datetime', '>=', fields.Datetime.now()),
                ], 'sudo'
            # crew_hours_count: started shifts still awaiting a declaration —
            # the domain equivalent of _crew_hours_slots()['to_declare'].
            return 'planning.slot', [
                ('resource_ids', 'in', emp.resource_id.ids),
                ('start_datetime', '<=', fields.Datetime.now()),
                ('crew_unavailable_reported', '=', False),
                '|', ('work_declaration_ids', '=', False),
                     ('work_declaration_ids.state', 'in', self._CREW_EDITABLE_WD_STATES),
            ], 'sudo'
        return super()._prepare_portal_counter_values(counter)

    @http.route()
    def counters(self, counters, **kw):
        """Force the configured cards to 0 for this crew member so they stay
        hidden on the portal home.

        This must run *after* the full counter chain: other apps (sale,
        account, purchase, ...) return their own (model, domain, access) from
        ``_prepare_portal_counter_values``, and the portal counts them here.
        Overriding the ``/my/counters`` route — which only the base ``portal``
        defines — makes our zeroing the last word, and we refresh the session
        cache the template reads so the card is hidden on the very next render
        too.  In Odoo 20 a card without its own ``is_config_card`` flag is
        shown only when its counter is non-zero, so this still hides it.
        """
        res = super().counters(counters, **kw)
        emp = self._crew_employee()
        if emp:
            hidden = emp._crew_portal_hidden_counters()
            if any(key in res for key in hidden):
                for key in hidden:
                    if key in res:
                        res[key] = 0
                cache = request.session.get('portal_counters', {}).copy()
                cache.update({k: bool(v) for k, v in res.items() if k.endswith('_count')})
                request.session['portal_counters'] = cache
        return res

    # ------------------------------------------------------------------
    # My Availability
    # ------------------------------------------------------------------
    @http.route(['/my/availability'], type='http', auth='user', website=True)
    def portal_my_availability(self, **kw):
        emp = self._crew_employee()
        if not emp:
            return request.render('crew_portal.portal_not_crew', {'page_name': 'crew'})
        windows = request.env['crew.availability'].sudo().search(
            [('employee_id', '=', emp.id)], order='date_start')
        today = fields.Date.context_today(request.env.user)
        invitations = request.env['crew.availability.invitation'].sudo().search(
            [('employee_id', '=', emp.id), ('response', '=', 'pending')]
        ).filtered(
            lambda i: i.request_id.date_start and i.request_id.date_start.date() >= today
        ).sorted(lambda i: i.request_id.date_start)  # chronological by start date
        win_rows = [{
            'id': w.id,
            'start': self._fmt_dt(w.date_start),
            'end': self._fmt_dt(w.date_end),
            'updated': self._fmt_dt(w.write_date),
        } for w in windows]
        inv_rows = [{
            'id': inv.id,
            'header': (inv.request_id.task_id.display_name
                       or inv.request_id.project_id.display_name
                       or inv.request_id.name or _("Availability request")),
            'period': inv.request_id.period_label,
            'posted': self._fmt_dt(inv.sent_on or inv.create_date),
            'indicative_hours': inv.request_id.indicative_hours,
            'planning_id': inv.request_id.name,
        } for inv in invitations]
        today = fields.Date.context_today(request.env.user).isoformat()
        return request.render('crew_portal.portal_my_availability', {
            'page_name': 'crew_availability',
            'employee': emp,
            'win_rows': win_rows,
            'inv_rows': inv_rows,
            'default_start': '%sT06:00' % today,   # 06:00 today (local), editable
            'default_end': '%sT18:00' % today,     # 18:00 today (local), editable
        })

    @http.route(['/my/availability/register'], type='http', auth='user',
                methods=['POST'], website=True)
    def portal_register_availability(self, **post):
        emp = self._crew_employee()
        start = self._parse_portal_dt(post.get('date_start'))
        end = self._parse_portal_dt(post.get('date_end'))
        if emp and emp.resource_id and start and end and start < end:
            request.env['crew.availability.engine'].sudo().apply_availability(
                emp.resource_id.sudo(), start, end,
                available=(post.get('state', 'available') == 'available'),
                origin='self_portal', employee=emp, enforce_entry_horizon=True)
        return request.redirect('/my/availability')

    @http.route(['/my/availability/window/<int:window_id>/remove'], type='http',
                auth='user', methods=['POST'], website=True)
    def portal_remove_availability(self, window_id, **post):
        emp = self._crew_employee()
        window = request.env['crew.availability'].sudo().browse(window_id)
        if emp and window.exists() and window.employee_id.id == emp.id:
            resource = window.resource_id
            window.unlink()
            request.env['crew.availability.engine'].sudo()._recompile(resource)
        return request.redirect('/my/availability')

    @http.route(['/my/invitation/<int:inv_id>/respond'], type='http', auth='user',
                methods=['POST'], website=True)
    def portal_respond_invitation(self, inv_id, **post):
        emp = self._crew_employee()
        inv = request.env['crew.availability.invitation'].sudo().browse(inv_id)
        if emp and inv.exists() and inv.employee_id.id == emp.id:
            if post.get('response') == 'available':
                inv.action_set_available()
            elif post.get('response') == 'unavailable':
                inv.action_set_unavailable()
        return request.redirect('/my/availability')

    # ------------------------------------------------------------------
    # My Planning
    # ------------------------------------------------------------------
    @http.route(['/my/planning'], type='http', auth='user', website=True)
    def portal_my_planning(self, **kw):
        emp = self._crew_employee()
        if not emp:
            return request.render('crew_portal.portal_not_crew', {'page_name': 'crew'})
        Slot = request.env['planning.slot'].sudo()
        slots = Slot.search([
            ('resource_ids', 'in', emp.resource_id.ids),
            ('end_datetime', '>=', fields.Datetime.now()),
        ], order='start_datetime') if emp.resource_id else Slot.browse()
        slot_rows = [{
            'id': s.id,
            'start': self._fmt_dt(s.start_datetime),
            'end': self._fmt_dt(s.end_datetime),
            'project': s.project_id.display_name,
            'task': s.task_id.display_name,
            'role': s.role_id.display_name,
            'reported': s.crew_unavailable_reported,
        } for s in slots]
        return request.render('crew_portal.portal_my_planning', {
            'page_name': 'crew_planning',
            'employee': emp,
            'slot_rows': slot_rows,
        })

    @http.route(['/my/planning/<int:slot_id>/cannot-work'], type='http', auth='user',
                methods=['POST'], website=True)
    def portal_cannot_work(self, slot_id, **post):
        emp = self._crew_employee()
        slot = request.env['planning.slot'].sudo().browse(slot_id)
        if emp and slot.exists() and emp.id in slot.employee_ids.ids:
            slot.action_crew_report_cannot_work(post.get('reason'))
        return request.redirect('/my/planning')

    # ------------------------------------------------------------------
    # My Hours (work declarations)
    # ------------------------------------------------------------------
    # A declaration is editable by the crew (shows the Submit form) while it is
    # a draft, a reopened one (sent back for correction) or a rejected one (the
    # planner asked for a redo). Submitted/approved are awaiting/handled by the
    # planner and are shown read-only.
    _CREW_EDITABLE_WD_STATES = ('draft', 'reopened', 'rejected')

    def _crew_hours_slots(self, emp):
        """Partition the crew member's started shifts into those still needing
        a declaration ('to_declare') and those already handled ('done'). A shift
        the crew reported they could not work is excluded."""
        result = {'to_declare': request.env['planning.slot'].sudo().browse(),
                  'done': request.env['planning.slot'].sudo().browse()}
        if not (emp and emp.resource_id):
            return result
        slots = request.env['planning.slot'].sudo().search([
            ('resource_ids', 'in', emp.resource_id.ids),
            ('start_datetime', '<=', fields.Datetime.now()),
            ('crew_unavailable_reported', '=', False),
        ], order='start_datetime desc')
        for slot in slots:
            wd = slot.work_declaration_ids[:1]
            if wd and wd.state not in self._CREW_EDITABLE_WD_STATES:
                result['done'] |= slot
            else:
                result['to_declare'] |= slot
        return result

    @http.route(['/my/hours'], type='http', auth='user', website=True)
    def portal_my_hours(self, **kw):
        emp = self._crew_employee()
        if not emp:
            return request.render('crew_portal.portal_not_crew', {'page_name': 'crew'})
        parts = self._crew_hours_slots(emp)
        todo_rows = [{
            'id': s.id,
            'start': self._fmt_dt(s.start_datetime),
            'end': self._fmt_dt(s.end_datetime),
            'project': s.project_id.display_name,
            'task': s.task_id.display_name,
            'default_start': self._dt_local_input(s.work_declaration_ids[:1].actual_start
                                                  or s.start_datetime),
            'default_end': self._dt_local_input(s.work_declaration_ids[:1].actual_end
                                                or s.end_datetime),
            'break_minutes': s.work_declaration_ids[:1].break_minutes or 0,
            'comment': s.work_declaration_ids[:1].comment or '',
        } for s in parts['to_declare']]
        done_rows = [{
            'start': self._fmt_dt(s.start_datetime),
            'end': self._fmt_dt(s.end_datetime),
            'project': s.project_id.display_name,
            'task': s.task_id.display_name,
            'worked_hours': round(s.work_declaration_ids[:1].worked_hours, 2),
            'state': dict(s.work_declaration_ids[:1]._fields['state'].selection).get(
                s.work_declaration_ids[:1].state),
        } for s in parts['done']]
        return request.render('crew_portal.portal_my_hours', {
            'page_name': 'crew_hours',
            'employee': emp,
            'todo_rows': todo_rows,
            'done_rows': done_rows,
        })

    @http.route(['/my/hours/<int:slot_id>/declare'], type='http', auth='user',
                methods=['POST'], website=True)
    def portal_declare_hours(self, slot_id, **post):
        emp = self._crew_employee()
        slot = request.env['planning.slot'].sudo().browse(slot_id)
        if emp and slot.exists() and emp.id in slot.employee_ids.ids:
            start = self._parse_portal_dt(post.get('actual_start'))
            end = self._parse_portal_dt(post.get('actual_end'))
            if start and end and start < end:
                wd = slot._get_or_create_work_declaration()
                # Only act on an editable declaration; a stale/double POST on an
                # already submitted/approved one is a no-op (never a crash).
                if not wd.locked and wd.state in self._CREW_EDITABLE_WD_STATES:
                    try:
                        break_min = int(post.get('break_minutes') or 0)
                    except (TypeError, ValueError):
                        break_min = 0
                    wd.write({
                        'actual_start': start,
                        'actual_end': end,
                        'break_minutes': max(break_min, 0),
                        'comment': post.get('comment') or False,
                    })
                    wd.action_submit()
        return request.redirect('/my/hours')
