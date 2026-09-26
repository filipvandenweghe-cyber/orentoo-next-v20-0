{
    'name': 'Rental Sets',
    'version': '20.0.1.45.0',
    'summary': 'Extend products with Rental Set capabilities',
    'description': (
        'Rental Sets are normal Odoo products that can expand into hidden '
        'internal components for rental, sales, inventory and warehouse '
        'operations.'
    ),
    'author': 'Custom',
    'category': 'Inventory/Products',
    'license': 'LGPL-3',

    'depends': [
        'product',
        'sale',
        'sale_stock',
        'sale_stock_renting',
        'stock',
        'sale_renting',
        # 'website_sale',  # not installed in this environment
        # 'barcode',       # not installed in this environment
    ],

    'data': [
        # Security

        # Views
        'views/res_config_settings_views.xml',
        'views/product_template_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
        'views/stock_picking_type_views.xml',
        'views/rental_availability_report_views.xml',
        'views/product_catalog_views.xml',

        # Wizards
        'views/rental_set_add_component_wizard.xml',

        # Reports
        'report/report_deliveryslip.xml',
        'report/report_set_customer_documents.xml',

        'security/ir.access.csv',
    ],

    'assets': {
        'web.assets_backend': [
            'rental_set/static/src/js/rental_set_fold_field.xml',
            'rental_set/static/src/js/rental_set_fold_field.js',
            'rental_set/static/src/js/rental_set_list_renderer.js',
            'rental_set/static/src/js/rental_set_picking_fold_field.xml',
            'rental_set/static/src/js/rental_set_picking_fold_field.js',
            'rental_set/static/src/js/rental_set_picking_renderer.js',
            'rental_set/static/src/js/rental_set_qty_widget.xml',
            'rental_set/static/src/js/rental_set_qty_widget.js',
            'rental_set/static/src/js/availability_matrix.xml',
            'rental_set/static/src/js/availability_matrix.js',
            'rental_set/static/src/scss/rental_set.scss',
            'rental_set/static/src/scss/availability_matrix.scss',
        ],
    },

    'demo': [
        'data/rental_set_demo.xml',
    ],

    'installable': True,
    'application': False,
    'auto_install': False,

    'post_init_hook': '_enable_rental_pickings',
}
