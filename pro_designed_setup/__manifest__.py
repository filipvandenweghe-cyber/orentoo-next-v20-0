{
    'name': 'Pro-Designed.com Company Setup',
    'version': '20.0.1.0.1',
    'category': 'Accounting/Localizations',
    'author': 'Pro-Designed.com',
    'summary': 'Sets up the Pro-Designed.com Belgian company (EUR, Belgian PCMN chart, VAT, fiscal positions).',
    'description': """
Pro-Designed.com Company Setup
==============================
Creates and configures a Belgian company named **Pro-Designed.com** on install:

* Country: Belgium, VAT: BE0715939182
* Currency: EUR
* Chart of accounts: Belgium - Companies (PCMN)
* Fiscal positions: loaded from the Belgian localization
  (Domestic, Intra-Community, EU B2C, Extra-Community, Co-Contractant, Non Deductible)

The setup runs in an idempotent ``post_init_hook`` so it survives Odoo.sh rebuilds
once this module is committed and installed on the branch.
""",
    # multi_channel_rental_flow (which pulls in rental_coefficient_dynamic_pricing)
    # is listed so this module loads *after* the kayak rental-pricing demo data
    # exists — the post_init_hook duplicates that config into this company's
    # PRO warehouse. See _replicate_rental_pricing in hooks.py.
    'depends': ['l10n_be', 'stock', 'multi_channel_rental_flow'],
    'demo': [
        'demo/restore_chart_marker.xml',
    ],
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
