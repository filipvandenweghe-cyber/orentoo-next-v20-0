# -*- coding: utf-8 -*-
{
    'name': "Crew Planning",
    'summary': "Crew availability engine, requests, invitations and work declarations "
               "on top of standard Odoo Resource / Planning / Project / Timesheet.",
    'description': """
Crew Planning (Phase 1 — Crew Availability Engine)
==================================================

Generic crew scheduling built **around** standard Odoo, not replacing its engines.

* **Availability Mode** on the employee: *Standard Working Schedule* (normal Odoo
  resource calendar / Time Off) or *Explicit Availability* (unavailable unless a
  window has been registered).
* **Crew Availability Engine** — a thin service that compiles registered positive
  availability windows into standard ``resource.calendar.leaves`` on a broad 24/7
  crew calendar. Standard Odoo resource availability (`_work_intervals_batch`)
  stays the single source of truth for staffing / Auto-Plan.
* **Mandatory rolling horizon** — a daily cron keeps blanket unavailability
  covering [today, today + horizon] and repairs consistency idempotently, so an
  Explicit-Availability crew member can never become accidentally available.
* **Availability audit log** (`crew.availability.log`) — records what was declared,
  by whom and from where; never read by staffing.

Later phases add availability requests, invitation waves, the Crew Portal, and the
work-declaration → timesheet flow.
""",
    'author': "Orentoo",
    'website': "https://www.orentoo.com",
    'category': 'Human Resources/Planning',
    'version': '20.0.4.17.0',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'resource',
        'planning',
        'hr_skills',
        'sale_project_forecast',
        'sale_timesheet',
        'hr_timesheet',
        'whatsapp',
    ],
    'data': [
        'data/crew_calendar_data.xml',
        'data/config_parameters.xml',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'data/mail_template_data.xml',
        'data/whatsapp_template_data.xml',
        'wizard/crew_enter_availability_views.xml',
        'wizard/crew_invite_wizard_views.xml',
        'views/crew_availability_views.xml',
        'views/crew_availability_log_views.xml',
        'views/crew_availability_request_views.xml',
        'views/crew_availability_invitation_views.xml',
        'views/planning_slot_views.xml',
        'views/crew_work_declaration_views.xml',
        'views/project_views.xml',
        'views/hr_employee_views.xml',
        'views/res_config_settings_views.xml',
        'views/crew_menus.xml',
        'security/ir.access.csv',
    ],
    'demo': [
        'demo/crew_demo.xml',
    ],
    'installable': True,
    'application': True,
}
