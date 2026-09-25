{
    'name': 'Coefficient & Dynamic Pricing',
    'version': '20.0.1.2.0',
    'summary': 'Dynamic and degressive pricing for rental orders',
    'description': (
        'This module allows rental prices to use duration coefficients '
        'and dynamic pricing factors. Rental order line prices are computed as: '
        'base rental price x coefficient x dynamic pricing multiplier.'
    ),
    'author': 'Kooki BV / Pro-Designed.com',
    'website': 'https://www.pro-designed.com',
    'category': 'Sales/Sales',
    'license': 'LGPL-3',

    'depends': [
        'sale',
        'sale_renting',
        'sale_stock_renting',
        'stock',
        'product',
        'rental_set',
    ],

    'data': [
        # Security

        # Data
        'data/rental_coefficient_data.xml',

        # Views
        'views/rental_coefficient_type_views.xml',
        'views/rental_coefficient_table_views.xml',
        'views/rental_dynamic_pricing_table_views.xml',
        'views/product_template_views.xml',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/menu.xml',

        'security/ir.access.csv',
    ],

    'demo': [
        'data/demo_data.xml',
    ],

    'installable': True,
    'application': False,
    'auto_install': False,
}
