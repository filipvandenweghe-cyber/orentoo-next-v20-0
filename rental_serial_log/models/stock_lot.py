import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# Name of the partial unique index enforcing instance-wide serial uniqueness.
_SERIAL_UNIQUE_INDEX = 'stock_lot_serial_unique_key_uniq'


def _normalize_serial(name):
    """Return the normalized (whitespace-trimmed) form of a serial/lot name.

    Case-sensitive by design: "abc" and "ABC" are distinct serials.  Returns
    an empty string for a falsy/blank name so callers can treat it as "no key".
    """
    return (name or '').strip()


class StockLot(models.Model):
    _inherit = 'stock.lot'

    rental_log_ids = fields.One2many(
        'rental.serial.log', 'lot_id', string='Rental History')

    # Instance-wide uniqueness key for *serial*-tracked products only.  It is
    # the normalized (trimmed, case-sensitive) serial name and is NULL for
    # anything that is not serial-tracked, so lot/batch semantics are left
    # entirely unchanged.  A partial unique index (built in ``init``) makes the
    # populated values unique across the whole database — every company and
    # every product — so a physical serial number identifies exactly one lot.
    serial_unique_key = fields.Char(
        string='Serial Uniqueness Key',
        compute='_compute_serial_unique_key', store=True,
        index=False, copy=False,
        help="Normalized serial number used to enforce instance-wide "
             "uniqueness. Only set for serial-tracked products.")

    @api.depends('name', 'product_id.tracking')
    def _compute_serial_unique_key(self):
        for lot in self:
            key = _normalize_serial(lot.name)
            if lot.product_id.tracking == 'serial' and key:
                lot.serial_unique_key = key
            else:
                lot.serial_unique_key = False

    def init(self):
        """Create the partial unique index backing serial uniqueness.

        DB-level enforcement is race-safe and inherently cross-company.  The
        index only covers non-NULL keys, i.e. serial-tracked lots.  Existing
        duplicates would make ``CREATE UNIQUE INDEX`` fail with a cryptic
        error, so we guard by detecting them first and raising a clear message
        (install/upgrade also runs the same detection earlier — see the
        migration script and ``post_init_hook``).
        """
        cr = self.env.cr
        cr.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname = %s",
            (_SERIAL_UNIQUE_INDEX,))
        if cr.fetchone():
            return
        self._assert_no_serial_duplicates()
        cr.execute(SQL(
            "CREATE UNIQUE INDEX %s ON %s (serial_unique_key) "
            "WHERE serial_unique_key IS NOT NULL",
            SQL.identifier(_SERIAL_UNIQUE_INDEX),
            SQL.identifier(self._table),
        ))
        _logger.info("rental_serial_log: created serial uniqueness index %s",
                     _SERIAL_UNIQUE_INDEX)

    # ── duplicate detection (install / upgrade hard-stop) ────────────────────

    @api.model
    def _find_serial_duplicates(self):
        """Return the existing violations of the instance-wide serial rule.

        Two kinds of conflict, both computed on the *normalized* name so that
        e.g. ``"SN1 "`` and ``"SN1"`` are treated as the same string:

          * two or more serial-tracked lots sharing a normalized name;
          * a serial-tracked name equal to a lot/batch-tracked name (Option B:
            a batch may not reuse a serial's string, so a scanned serial is
            never ambiguous).

        Returns a list of ``(normalized_name, kind)`` tuples where ``kind`` is
        ``'serial'`` or ``'cross-type'``.  Empty list means clean.  Reads with
        ``sudo`` so cross-company duplicates are seen.
        """
        cr = self.env.cr
        # Serial-vs-serial: same normalized name used by >1 serial lot.
        cr.execute("""
            SELECT btrim(l.name) AS k
              FROM stock_lot l
              JOIN product_product p ON p.id = l.product_id
              JOIN product_template t ON t.id = p.product_tmpl_id
             WHERE t.tracking = 'serial' AND btrim(coalesce(l.name, '')) != ''
             GROUP BY btrim(l.name)
            HAVING count(*) > 1
        """)
        serial_dups = [(row[0], 'serial') for row in cr.fetchall()]

        # Cross-type: a normalized name used by both a serial lot and a
        # lot/batch-tracked lot.
        cr.execute("""
            SELECT DISTINCT btrim(sl.name) AS k
              FROM stock_lot sl
              JOIN product_product sp ON sp.id = sl.product_id
              JOIN product_template st ON st.id = sp.product_tmpl_id
              JOIN stock_lot ll ON btrim(ll.name) = btrim(sl.name)
              JOIN product_product lp ON lp.id = ll.product_id
              JOIN product_template lt ON lt.id = lp.product_tmpl_id
             WHERE st.tracking = 'serial'
               AND lt.tracking = 'lot'
               AND btrim(coalesce(sl.name, '')) != ''
        """)
        cross_dups = [(row[0], 'cross-type') for row in cr.fetchall()]
        return serial_dups + cross_dups

    @api.model
    def _serial_duplicate_lot_ids(self):
        """Return the ids of every lot involved in a serial-uniqueness conflict.

        Powers the administrator audit action: it resolves the abstract
        conflicts of ``_find_serial_duplicates`` down to the concrete lots so an
        admin can open, inspect and fix them.  Covers both serial-vs-serial and
        cross-type (serial vs lot/batch) conflicts.  Reads raw SQL so it is
        instance-wide across companies.
        """
        cr = self.env.cr
        # Serial lots whose normalized name is shared by more than one serial.
        cr.execute("""
            SELECT l.id
              FROM stock_lot l
              JOIN product_product p ON p.id = l.product_id
              JOIN product_template t ON t.id = p.product_tmpl_id
             WHERE t.tracking = 'serial' AND btrim(coalesce(l.name, '')) != ''
               AND btrim(l.name) IN (
                    SELECT btrim(l2.name)
                      FROM stock_lot l2
                      JOIN product_product p2 ON p2.id = l2.product_id
                      JOIN product_template t2 ON t2.id = p2.product_tmpl_id
                     WHERE t2.tracking = 'serial'
                       AND btrim(coalesce(l2.name, '')) != ''
                     GROUP BY btrim(l2.name) HAVING count(*) > 1)
        """)
        ids = {row[0] for row in cr.fetchall()}
        # Both sides of a serial-vs-lot/batch (cross-type) collision.
        cr.execute("""
            SELECT sl.id, ll.id
              FROM stock_lot sl
              JOIN product_product sp ON sp.id = sl.product_id
              JOIN product_template st ON st.id = sp.product_tmpl_id
              JOIN stock_lot ll ON btrim(ll.name) = btrim(sl.name)
              JOIN product_product lp ON lp.id = ll.product_id
              JOIN product_template lt ON lt.id = lp.product_tmpl_id
             WHERE st.tracking = 'serial' AND lt.tracking = 'lot'
               AND btrim(coalesce(sl.name, '')) != ''
        """)
        for serial_id, batch_id in cr.fetchall():
            ids.add(serial_id)
            ids.add(batch_id)
        return sorted(ids)

    @api.model
    def _assert_no_serial_duplicates(self):
        """Raise a clear, data-preserving error if legacy duplicates exist."""
        dups = self._find_serial_duplicates()
        if not dups:
            return
        lines = []
        for name, kind in dups:
            if kind == 'serial':
                lines.append(_(" - Serial number %(sn)s is used by more than "
                               "one serial-tracked item.", sn=name))
            else:
                lines.append(_(" - Serial number %(sn)s is also used as a "
                               "lot/batch number.", sn=name))
        raise ValidationError(_(
            "Serial numbers must be unique across the whole database for "
            "serial-tracked products, and may not collide with lot/batch "
            "numbers.\nThe following existing values conflict and must be "
            "resolved before this feature can be enabled (no data was "
            "changed):\n%(lines)s",
            lines="\n".join(lines)))

    # ── write-time enforcement (friendly message + cross-type, Option B) ─────

    @api.constrains('name', 'product_id')
    def _check_serial_unique_key(self):
        """Block new serial-uniqueness violations with a readable message.

        The partial unique index is the race-safe source of truth for the
        serial-vs-serial case; this constraint additionally (a) turns that into
        a friendly, non-leaking message and (b) enforces the cross-type rule
        (batch name vs serial key) which a single index cannot express without
        forbidding legitimate batch-vs-batch repeats.

        Lookups use raw SQL (not the ORM) on purpose: an ORM ``search`` would
        flush the record being validated, tripping the DB unique index with a
        cryptic ``IntegrityError`` before this friendly message is raised.  Raw
        SQL is also instance-wide (no multi-company record rules) yet only ever
        returns existence, so it never leaks another company's data.
        """
        for lot in self:
            key = _normalize_serial(lot.name)
            if not key:
                continue
            if lot.product_id.tracking == 'serial':
                if self._serial_key_taken(key, exclude_id=lot.id):
                    raise ValidationError(_(
                        "Serial number %(sn)s already exists in the system. "
                        "Serial numbers must be unique.", sn=key))
                # A lot/batch already uses this string (Option B).
                if self._batch_name_exists(key, exclude_id=lot.id):
                    raise ValidationError(_(
                        "Serial number %(sn)s is already used as a lot/batch "
                        "number. It cannot also be a serial number.", sn=key))
            elif lot.product_id.tracking == 'lot':
                # A serial already uses this string (Option B, reverse).
                if self._serial_key_taken(key, exclude_id=lot.id):
                    raise ValidationError(_(
                        "Lot/batch number %(sn)s is already used as a serial "
                        "number. It cannot also be a lot/batch number.",
                        sn=key))

    @api.model
    def _serial_key_taken(self, key, exclude_id=False):
        """True if another lot already carries ``key`` as its serial key."""
        self.env.cr.execute(
            "SELECT 1 FROM stock_lot WHERE serial_unique_key = %s "
            "AND id != %s LIMIT 1", (key, exclude_id or 0))
        return bool(self.env.cr.fetchone())

    @api.model
    def _batch_name_exists(self, key, exclude_id=False):
        """True if a lot/batch-tracked lot has ``key`` as its normalized name.

        Compared on ``btrim(name)`` in SQL so any surrounding-whitespace
        variant is caught, and instance-wide (raw query, so multi-company
        record rules don't hide a conflict in another company).  Returns a
        boolean only, so it never leaks another company's data.
        """
        self.env.cr.execute("""
            SELECT 1
              FROM stock_lot l
              JOIN product_product p ON p.id = l.product_id
              JOIN product_template t ON t.id = p.product_tmpl_id
             WHERE t.tracking = 'lot'
               AND btrim(l.name) = %s
               AND l.id != %s
             LIMIT 1
        """, (key, exclude_id or 0))
        return bool(self.env.cr.fetchone())
