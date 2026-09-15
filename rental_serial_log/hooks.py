import logging

_logger = logging.getLogger(__name__)


def _check_serial_duplicates(env):
    """Fresh-install guard: refuse to enable serial uniqueness if the seed /
    demo data already contains conflicting serials.  Raises with a full,
    data-preserving report (nothing is renamed, merged or deleted).

    On a normal empty DB this is a no-op.  The heavy lifting lives on the model
    so the upgrade migration and the ``init`` index build reuse the same logic.
    """
    env['stock.lot']._assert_no_serial_duplicates()
    _logger.info("rental_serial_log: serial uniqueness check passed on install.")
