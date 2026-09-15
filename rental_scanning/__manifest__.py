{
    'name': 'Rental Scanning',
    'version': '19.0.1.7.0',
    'summary': 'Prepared-package & set picking via scanning (late binding)',
    'description': (
        'Assign a pre-prepared physical package (or a set barcode, or a '
        'packed crate serial) to a picking at pick time and reconcile it '
        'against the operation demand.  Strict-fit with a split prompt on '
        'overflow; works on any operation type; composes with rental sets.\n\n'
        'Also shows a reusable, non-blocking Repair warning when a serial '
        'linked to an active repair is scanned (explicit Proceed Anyway, '
        'audited via rental_serial_log).\n\n'
        'See docs/rental_scanning_requirements for the full requirements '
        '(PPB-01..14) and rationale.'
    ),
    'author': 'Pro-Designed.com',
    'website': 'https://www.pro-designed.com',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': [
        'stock',
        'stock_barcode',
        'rental_set',
        'rental_serial_log',  # repair-status check + repair_override audit
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/rental_scanning_assign_views.xml',
        'views/stock_picking_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'rental_scanning/static/src/js/repair_warning.js',
            'rental_scanning/static/src/js/rental_scanning_barcode.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
