"""Hard-stop the upgrade if legacy serials violate the new uniqueness rule.

Runs *before* the module's new model code is loaded and before ``init`` tries
to build the partial unique index, so the detection SQL is inlined here (the
model helpers don't exist on the old registry yet).  It reports every conflict
with a clear message instead of a cryptic Postgres "could not create unique
index" error.  No data is changed — the user resolves the conflicts and
re-runs the upgrade.
"""
import logging

_logger = logging.getLogger(__name__)

# Two or more serial-tracked lots sharing a normalized (trimmed) name.
_SERIAL_DUP_SQL = """
    SELECT btrim(l.name) AS k
      FROM stock_lot l
      JOIN product_product p ON p.id = l.product_id
      JOIN product_template t ON t.id = p.product_tmpl_id
     WHERE t.tracking = 'serial' AND btrim(coalesce(l.name, '')) != ''
     GROUP BY btrim(l.name)
    HAVING count(*) > 1
"""

# A normalized name shared by a serial-tracked lot and a lot/batch-tracked lot.
_CROSS_DUP_SQL = """
    SELECT DISTINCT btrim(sl.name) AS k
      FROM stock_lot sl
      JOIN product_product sp ON sp.id = sl.product_id
      JOIN product_template st ON st.id = sp.product_tmpl_id
      JOIN stock_lot ll ON btrim(ll.name) = btrim(sl.name)
      JOIN product_product lp ON lp.id = ll.product_id
      JOIN product_template lt ON lt.id = lp.product_tmpl_id
     WHERE st.tracking = 'serial' AND lt.tracking = 'lot'
       AND btrim(coalesce(sl.name, '')) != ''
"""


def migrate(cr, version):
    if not version:
        return
    _logger.info("rental_serial_log: checking serials for uniqueness before "
                 "creating the unique index.")
    cr.execute(_SERIAL_DUP_SQL)
    serial_dups = [r[0] for r in cr.fetchall()]
    cr.execute(_CROSS_DUP_SQL)
    cross_dups = [r[0] for r in cr.fetchall()]
    if not serial_dups and not cross_dups:
        return

    lines = [" - Serial number %s is used by more than one serial-tracked "
             "item." % name for name in serial_dups]
    lines += [" - Serial number %s is also used as a lot/batch number." % name
              for name in cross_dups]
    from odoo.exceptions import ValidationError
    raise ValidationError(
        "Serial numbers must be unique across the whole database for "
        "serial-tracked products, and may not collide with lot/batch numbers.\n"
        "The following existing values conflict and must be resolved before "
        "this upgrade can proceed (no data was changed):\n" + "\n".join(lines))
