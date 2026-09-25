# -*- coding: utf-8 -*-
# =============================================================================
# Weather Forecast — Odoo 19.0 module
# Author  : Kooki BV (Pro-Designed.com)
# Version : 19.0.1.0.0
#
# Versioning scheme: 19.0.MAJOR.MINOR.PATCH
#   MAJOR  — breaking model/API changes
#   MINOR  — new features, backward-compatible
#   PATCH  — bug-fixes, no model changes
# =============================================================================
{
    'name': 'Weather Forecast',
    'version': '20.0.1.0.0',
    'author': 'Kooki BV (Pro-Designed.com)',
    'website': 'https://www.pro-designed.com',
    'category': 'Tools',
    'summary': 'Hourly weather forecast for the next 8 days via OpenWeather One Call API 3.0.',
    'description': (
        'Application that fetches the daily weather forecast for the coming 8 days '
        'on an hourly basis. Please note that – as with all forecasts – the closer '
        'the moment of the forecast, the more accurate it is.'
    ),
    'depends': ['base', 'mail', 'base_setup'],
    'data': [
        # Load order matters: security first, then data, then views
        'security/security.xml',
        'data/cron.xml',
        'views/res_config_settings_views.xml',
        'views/weather_location_views.xml',
        'views/menus.xml',
        'security/ir.access.csv',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
