# -*- coding: utf-8 -*-
{
    'name': "Rental Purchase (Hired Equipment)",
    'summary': "Hire supplier-owned equipment through a Purchase Order without acquiring it.",
    'description': """
Rental Purchase / Hired Equipment
=================================

Adds a *Rental Purchase* option to Purchase Orders. When enabled, the purchase
represents equipment **hired** from the supplier rather than bought:

* the standard incoming receipt is reused, but the received goods stay
  **owned by the supplier** (standard Odoo consignment / stock ownership);
* a **rental return** to the supplier location is prepared automatically,
  honouring the warehouse's configured outgoing steps (1/2/3-step);
* the return can only reserve the supplier-owned stock that was received
  (never unrelated company-owned stock);
* vendor bills post to a configurable, company-dependent **Rental Purchase
  Expense Account** (on the product category, with a company-level fallback)
  instead of increasing inventory value.

Normal Purchase Orders are completely unaffected. Intercompany rental
synchronisation is handled by the separate ``rental_purchase_intercompany``
module. This core module works with any external (non-Odoo) supplier.
""",
    'author': "Orentoo",
    'website': "https://www.orentoo.com",
    'category': 'Inventory/Purchase',
    'version': '20.0.1.3.0',
    'license': 'LGPL-3',
    'depends': [
        'purchase_stock',
        'stock_account',
    ],
    'data': [
        'views/purchase_order_views.xml',
        'views/product_category_views.xml',
        'views/res_config_settings_views.xml',
        'views/report_purchase.xml',
    ],
    'installable': True,
    'application': False,
}
