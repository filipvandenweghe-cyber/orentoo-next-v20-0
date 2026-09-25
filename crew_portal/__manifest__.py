# -*- coding: utf-8 -*-
{
    'name': "Crew Portal",
    'summary': "Self-service portal for crew: availability, planning and "
               "'can no longer work' reporting.",
    'description': """
Crew Portal (Phase 4)
=====================

Portal for crew members (external or internal, without backend access):

* **My Availability** — see registered availability, register available /
  unavailable periods (fed into the standard resource availability via the
  Crew Availability Engine), and answer open availability requests.
* **My Planning** — upcoming shifts, and a "report I can no longer work this
  shift" action that notifies the planner without silently dropping the
  assignment.

Access is strictly own-records-only: each portal controller resolves the
logged-in user's employee and only ever reads/writes that person's data.
""",
    'author': "Orentoo",
    'website': "https://www.orentoo.com",
    'category': 'Human Resources/Planning',
    'version': '20.0.1.17.0',
    'license': 'LGPL-3',
    'depends': [
        'crew_planning',
        'portal',
    ],
    'data': [
        'data/portal_entry_data.xml',
        'views/crew_portal_templates.xml',
    ],
    'installable': True,
    'application': False,
}
