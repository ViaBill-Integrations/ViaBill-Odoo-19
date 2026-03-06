# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': 'Payment Provider: ViaBill',
    'version': '19.0.4.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "ViaBill — Pay later.",
    'description': "ViaBill Payments",
    'depends': ['payment', 'website_sale'],
    'installable': True,
    'application': False,
    'data': [
        'security/ir.model.access.csv',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
        'data/payment_icon_data.xml',
        'views/payment_viabill_templates.xml',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'views/sale_order_views.xml',
        'views/viabill_refund_wizard_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'assets': {
        # Frontend assets: payment form interaction + PriceTags widget
        'web.assets_frontend': [
            'payment_viabill/static/src/interactions/**/*',
            'payment_viabill/static/src/js/viabill_pricetags.js',
            'payment_viabill/static/src/scss/viabill.scss',
        ],
        # Backend assets: provider configuration form login/register handlers
        'web.assets_backend': [
            'payment_viabill/static/src/backend/viabill_provider_form.js',
        ],
    },
    'author': 'ViaBill A/S',
    'license': 'LGPL-3',
}
