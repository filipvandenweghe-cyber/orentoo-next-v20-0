# -*- coding: utf-8 -*-
import re

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'crew_portal')
class TestCrewPortal(HttpCase):

    def setUp(self):
        super().setUp()
        self.user = self.env['res.users'].create({
            'name': 'Crew Portal User',
            'login': 'crewportal',
            'password': 'crewportal',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self.emp = self.env['hr.employee'].create({
            'name': 'Portal Crew',
            'is_crew': True,
            'crew_availability_mode': 'explicit',
            'user_id': self.user.id,
        })

    def test_portal_availability_and_planning(self):
        self.authenticate('crewportal', 'crewportal')

        # My Availability renders
        res = self.url_open('/my/availability')
        self.assertEqual(res.status_code, 200)
        self.assertIn('My Availability', res.text)

        # Register availability via the portal form (with CSRF)
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', res.text)
        self.assertTrue(m, "CSRF token must be present on the page")
        res2 = self.url_open('/my/availability/register', data={
            'csrf_token': m.group(1),
            'date_start': '2027-01-10T08:00',
            'date_end': '2027-01-10T18:00',
            'state': 'available',
        })
        self.assertEqual(res2.status_code, 200)
        window = self.env['crew.availability'].search([('employee_id', '=', self.emp.id)])
        self.assertTrue(window, "Portal registration must create an availability window")
        self.assertIn('January', res2.text, "Dates should be shown with the month spelled out")

        # My Planning renders
        res3 = self.url_open('/my/planning')
        self.assertEqual(res3.status_code, 200)
        self.assertIn('My Planning', res3.text)

    def test_remove_availability(self):
        self.authenticate('crewportal', 'crewportal')
        page = self.url_open('/my/availability')
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
        self.url_open('/my/availability/register', data={
            'csrf_token': csrf, 'date_start': '2027-02-10T08:00',
            'date_end': '2027-02-10T18:00', 'state': 'available'})
        window = self.env['crew.availability'].search([('employee_id', '=', self.emp.id)])
        self.assertTrue(window)
        page2 = self.url_open('/my/availability')
        csrf2 = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page2.text).group(1)
        self.url_open('/my/availability/window/%s/remove' % window.id,
                      data={'csrf_token': csrf2})
        self.assertFalse(self.env['crew.availability'].search(
            [('employee_id', '=', self.emp.id)]), "Window should be removed")

    def test_home_cards_crew_gated(self):
        """The crew cards are shown to crew members only.

        Odoo 20 drives the portal home from ``portal.entry`` records and
        renders every card, hiding the ones that do not apply with a CSS
        class — so the gate is ``_filter_visible_portal_cards()``, which
        crew_portal overrides, and not the presence of the title in the HTML.
        """
        crew_entries = (
            self.env.ref('crew_portal.portal_entry_crew_availability')
            + self.env.ref('crew_portal.portal_entry_crew_planning')
            + self.env.ref('crew_portal.portal_entry_crew_hours')
        )
        customer = self.env['res.users'].create({
            'name': 'Plain Customer',
            'login': 'custportal',
            'password': 'custportal',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })

        # A crew member sees them...
        visible = crew_entries.with_user(self.user)._filter_visible_portal_cards()
        self.assertEqual(visible, crew_entries)

        # ...a portal user without an is_crew employee does not.
        visible2 = crew_entries.with_user(customer)._filter_visible_portal_cards()
        self.assertFalse(visible2 & crew_entries)

        # And the portal home still renders for both.
        self.authenticate('crewportal', 'crewportal')
        home = self.url_open('/my/home')
        self.assertEqual(home.status_code, 200)
        self.assertIn('My Availability', home.text)
        self.assertIn('My Planning', home.text)

        self.authenticate('custportal', 'custportal')
        self.assertEqual(self.url_open('/my/home').status_code, 200)
