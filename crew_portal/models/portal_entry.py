# -*- coding: utf-8 -*-
from odoo import models


class PortalEntry(models.Model):
    """Show the crew portal cards to crew members only.

    Odoo 20 drives the portal home from ``portal.entry`` records; a card is
    rendered when it is either "visible" (this hook) or when its counter is
    non-zero.  The crew cards must be reachable by a crew member even before
    they have any availability / shift / declaration, so we make them visible
    whenever the logged-in user is linked to a crew employee — and hide them
    from everybody else.
    """
    _inherit = 'portal.entry'

    _CREW_CATEGORY = 'crew_category'

    def _filter_visible_portal_cards(self):
        visible = super()._filter_visible_portal_cards()
        crew_entries = self.filtered(lambda e: e.category == self._CREW_CATEGORY)
        if not crew_entries:
            return visible
        is_crew = bool(self.env['hr.employee'].sudo().search_count(
            [('user_id', '=', self.env.uid), ('is_crew', '=', True)], limit=1))
        if is_crew:
            return visible | crew_entries
        return visible - crew_entries
