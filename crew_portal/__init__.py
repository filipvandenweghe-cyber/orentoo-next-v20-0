# -*- coding: utf-8 -*-
"""Crew Portal — own-records-only self-service for crew members.

Every route resolves the logged-in user's employee and reads/writes ONLY that
person's data (sudo is used for the controlled writes, always scoped to the
resolved employee). Surfaces: My Availability (register windows, answer
invitations), My Planning (upcoming shifts, "can no longer work"), My Hours
(declare worked hours). See ``docs/crew_portal_requirements.md``.
"""
from . import controllers
from . import models
