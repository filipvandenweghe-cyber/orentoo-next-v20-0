{
    'name': 'eCommerce Rental Stock - Sets',
    'author': 'Pro-Designed.com',
    'category': 'Website/Website',
    'summary': 'Stock availability checks for rental sets on eCommerce',
    'version': '20.0.1.0.0',
    'description': """
Bridge module that adds component-based stock availability checks for
rental sets on the eCommerce frontend.

When a rental set is displayed or added to the cart, availability is
computed from the set's leaf components rather than from the (virtual)
set product itself.
    """,
    'depends': [
        'rental_set',
        'website_sale_stock_renting',
    ],
    'auto_install': True,
    'license': 'LGPL-3',
}
