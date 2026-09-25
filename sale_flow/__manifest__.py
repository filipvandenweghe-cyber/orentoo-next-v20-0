{
    'name': 'Sale Flow',
    'version': '20.0.1.8.3',
    'summary': 'Commercial baseline vs. logistics reality for sale/rental orders',
    'description': (
        'Sale Flow separates the confirmed commercial agreement (baseline '
        'quantity, price, discount, subtotal) from the operational logistics '
        'reality (delivered, returned, lost, broken) via a central '
        'sale.flow.line model and a service layer.\n\n'
        'Features: post-confirmation change tracking as deltas, delivered-qty '
        'protection, invoice warning levels (orange/red), return '
        'reconciliation, lost/broken wizard with fee charges, handling of '
        'products added by logistics during delivery, rental status fixes, '
        'auto-reconcile of ordered vs delivered qty, and multi-step '
        '(Pick/Pack/Ship) delivery support. Integrates optionally with '
        'rental_set.'
    ),
    'author': 'Custom',
    'category': 'Sales/Sales',
    'license': 'LGPL-3',

    'depends': [
        'sale',
        'sale_stock',
        'sale_renting',
        'stock',
        'product',
        'mail',
    ],

    'data': [
        # Security

        # Data
        'data/sale_flow_data.xml',

        # Views
        'views/sale_flow_line_views.xml',
        'views/sale_order_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',

        # Wizards
        'wizard/sale_flow_lost_broken_wizard_views.xml',

        'security/ir.access.csv',
    ],

    'installable': True,
    'application': False,
    'auto_install': False,
}
