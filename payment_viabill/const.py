# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

# ViaBill API base URLs
VIABILL_LIVE_URL = 'https://secure.viabill.com'
VIABILL_TEST_URL = 'https://secure.viabill.com'

# ViaBill addon name used in API endpoints
VIABILL_ADDON_NAME = 'woocommerce'

# ViaBill API endpoint paths
CHECKOUT_ENDPOINT = '/api/checkout-authorize/addon/' + VIABILL_ADDON_NAME
LOGIN_ENDPOINT = '/api/addon/' + VIABILL_ADDON_NAME + '/login'
REGISTER_ENDPOINT = '/api/addon/' + VIABILL_ADDON_NAME + '/register'
CAPTURE_ENDPOINT = '/api/transaction/capture'
REFUND_ENDPOINT = '/api/transaction/refund'
CANCEL_ENDPOINT = '/api/transaction/cancel'
RENEW_ENDPOINT = '/api/transaction/renew'

# ViaBill callback status codes sent to the callback/notification URL
VIABILL_STATUS_APPROVED = 'APPROVED'
VIABILL_STATUS_CANCELLED = 'CANCELLED'
VIABILL_STATUS_REJECTED = 'REJECTED'
VIABILL_STATUS_PENDING = 'PENDING'
VIABILL_STATUS_FAILED = 'FAILED'

# Mapping from ViaBill callback statuses to Odoo transaction states
STATUS_MAPPING = {
    'pending': [VIABILL_STATUS_PENDING],
    'authorized': [],  # ViaBill does not have a distinct authorized state via callback
    'done': [VIABILL_STATUS_APPROVED],
    'cancel': [VIABILL_STATUS_CANCELLED, VIABILL_STATUS_REJECTED],
    'error': [VIABILL_STATUS_FAILED],
}

# Signature format strings
# Checkout: md5(apikey#amount#currency#transaction#orderNumber#successUrl#cancelUrl#secret)
CHECKOUT_SIGNATURE_FORMAT = '{apikey}#{amount}#{currency}#{transaction}#{order_number}#{success_url}#{cancel_url}#{secret}'

# Callback: md5(transaction#orderNumber#amount#currency#status#time#secret)
CALLBACK_SIGNATURE_FORMAT = '{transaction}#{order_number}#{amount}#{currency}#{status}#{time}#{secret}'

# Capture/Refund: md5(id#apikey#amount#currency#secret)
CAPTURE_SIGNATURE_FORMAT = '{id}#{apikey}#{amount}#{currency}#{secret}'

# Cancel: md5(id#apikey#secret)
CANCEL_SIGNATURE_FORMAT = '{id}#{apikey}#{secret}'

# Sensitive keys to be masked in logs
SENSITIVE_KEYS = ['secret', 'apikey', 'viabill_secret_key', 'viabill_api_key', 'password']

# Default payment method codes for ViaBill
DEFAULT_PAYMENT_METHOD_CODES = ['viabill']
