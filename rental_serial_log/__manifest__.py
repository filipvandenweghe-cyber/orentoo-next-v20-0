{
    'name': 'Rental Serial Log',
    'version': '20.0.1.1.0',
    'summary': 'Per-serial rental traceability: delivered / returned / repaired',
    'description': (
        'A persisted usage log per serial-tracked lot.  Records, at rental '
        'delivery validation, which client and sales order the serial was '
        'used for and the package it was in (with a contents snapshot); at '
        'return; and when it enters / leaves Repair.  Surfaced as a Rental '
        'History on the Lot/Serial form.\n\n'
        'Also enforces instance-wide uniqueness of serial numbers for '
        'serial-tracked products (normalized, case-sensitive), with a '
        'cross-type block so a lot/batch cannot reuse a serial string.  See '
        'docs/serial_uniqueness_requirements.\n\n'
        'Rental returns accept only serials that were delivered on the order '
        'and never create a new serial (overridable returnable-serial '
        'resolver for later phases); plus an RPC serial repair-status check '
        'and a repair-override audit event backing the scan warning.  See '
        'docs/rental_return_serial_requirements.'
    ),
    'author': 'Pro-Designed.com',
    'website': 'https://www.pro-designed.com',
    'category': 'Sales/Rental',
    'license': 'LGPL-3',
    'depends': [
        'sale_stock_renting',  # rental + stock (is_rental_order)
        'repair',              # repair.order
    ],
    'data': [
        'data/serial_duplicate_audit.xml',
        'views/rental_serial_log_views.xml',
        'views/stock_lot_views.xml',
        'security/ir.access.csv',
    ],
    'post_init_hook': '_check_serial_duplicates',
    'installable': True,
    'application': False,
    'auto_install': False,
}
