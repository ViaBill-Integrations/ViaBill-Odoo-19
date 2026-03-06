# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import hashlib
import json
import logging
from datetime import datetime

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_viabill import const

_logger = get_payment_logger(__name__, sensitive_keys=const.SENSITIVE_KEYS)

# User-Agent sent with all ViaBill API requests.
_VIABILL_USER_AGENT = 'ViaBill-Odoo/1.0'


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('viabill', "ViaBill")],
        ondelete={'viabill': 'set default'},
    )

    # ── ViaBill-specific credential fields ────────────────────────────────────

    viabill_api_key = fields.Char(
        string="API Key",
        help="The API key provided by ViaBill for your merchant account.",
        copy=False,
    )
    viabill_secret_key = fields.Char(
        string="Secret Key",
        help="The secret key provided by ViaBill, used to sign API requests.",
        copy=False,
        groups='base.group_system',
    )
    viabill_pricetag_script = fields.Text(
        string="PriceTag Script",
        help=(
            "The ViaBill PriceTag <script> tag returned by ViaBill on login/register. "
            "Paste the full <script>…</script> HTML as provided by ViaBill. "
            "This script is injected into every frontend page to enable the PriceTag widget."
        ),
        copy=False,
    )

    # ── API mode (Test vs Production) ────────────────────────────────────────

    viabill_api_mode = fields.Selection(
        selection=[
            ('test', "Test"),
            ('production', "Production / Live"),
        ],
        string="API Mode",
        default='test',
        required=True,
        help=(
            "Controls whether the 'test' flag is sent to the ViaBill API. "
            "Use 'Test' while developing or testing (even when Odoo is in Enabled/Production state). "
            "Switch to 'Production / Live' only when you are ready to process real payments."
        ),
    )

    # ── Transaction type ──────────────────────────────────────────────────────

    viabill_transaction_type = fields.Selection(
        selection=[
            ('authorize', "Authorize Only"),
            ('authorize_capture', "Authorize and Capture"),
        ],
        string="Transaction Type",
        default='authorize',
        required=True,
        help=(
            "Controls whether payments are only authorized (requiring manual capture later) "
            "or automatically captured when ViaBill confirms the payment."
        ),
    )

    # ── PriceTag display toggles ──────────────────────────────────────────────

    viabill_pricetag_product = fields.Boolean(
        string="Show PriceTag on Product Page",
        default=False,
        help="Display the ViaBill PriceTag instalment widget below the price on product pages.",
    )
    viabill_pricetag_cart = fields.Boolean(
        string="Show PriceTag on Cart Page",
        default=False,
        help="Display the ViaBill PriceTag instalment widget below the total on the cart page.",
    )
    viabill_pricetag_checkout = fields.Boolean(
        string="Show PriceTag on Checkout Page",
        default=False,
        help=(
            "Display the ViaBill PriceTag instalment widget below the total on the "
            "checkout / payment page."
        ),
    )

    viabill_pricetag_country = fields.Selection(
        selection=[
            ('', "Auto-detect (use shop's country)"),
            ('dk', "Denmark"),
            ('es', "Spain"),
            ('us', "United States"),
            ('no', "Norway"),
        ],
        string="PriceTag Country",
        default='',
        help=(
            "The country code sent to the ViaBill PriceTag widget. "
            "'Auto-detect' uses the shop company's country. "
            "Override this if the auto-detected country is incorrect."
        ),
    )

    viabill_pricetag_language = fields.Selection(
        selection=[
            ('', "Auto-detect (use browser language)"),
            ('en', "English"),
            ('es', "Spanish"),
            ('da', "Danish"),
        ],
        string="PriceTag Language",
        default='',
        help=(
            "The language code sent to the ViaBill PriceTag widget. "
            "'Auto-detect' uses the current Odoo frontend language. "
            "Override this to force a specific language for the widget."
        ),
    )

    viabill_pricetag_custom_css = fields.Text(
        string="Custom CSS",
        help=(
            "Optional CSS rules injected into every frontend page when PriceTags "
            "are active. Use this to adjust the placement and alignment of the "
            "ViaBill PriceTag widget, e.g.: "
            ".viabill-pricetag { margin-top: 8px; text-align: center; }"
        ),
    )

    viabill_pricetag_custom_js = fields.Text(
        string="Custom JavaScript",
        help=(
            "Optional JavaScript injected into every frontend page when PriceTags "
            "are active. Runs after the ViaBill PriceTag script has been loaded. "
            "Use this for advanced customisation of the widget behaviour."
        ),
    )

    # ── PriceTag CSS selectors ────────────────────────────────────────────────

    viabill_pricetag_product_selector = fields.Char(
        string="PriceTag Product Selector",
        default=".oe_price",
        help=(
            "CSS selector used by the ViaBill PriceTag widget to read the current "
            "product price on product pages. "
            "Default: .oe_price"
        ),
    )
    viabill_pricetag_cart_selector = fields.Char(
        string="PriceTag Cart Selector",
        default="strong.monetary_field",
        help=(
            "CSS selector used by the ViaBill PriceTag widget to read the order "
            "total on the cart page. "
            "Default: strong.monetary_field"
        ),
    )
    viabill_pricetag_checkout_selector = fields.Char(
        string="PriceTag Checkout Selector",
        default="strong.monetary_field",
        help=(
            "CSS selector used by the ViaBill PriceTag widget to read the order "
            "total on the checkout / payment page. "
            "Default: strong.monetary_field"
        ),
    )

    # ── Order state after authorize / capture ─────────────────────────────────

    viabill_order_state_after_authorize = fields.Selection(
        selection=[
            ('pending',    "Pending Payment"),
            ('sale',       "Sales Order / Confirmed"),
            ('done',       "Locked"),
            ('draft',      "Quotation"),
        ],
        string="Order State after Authorize",
        default='sale',
        required=True,
        help=(
            "The sale order state to set after a successful ViaBill payment "
            "authorization (when Transaction Type is 'Authorize Only'). "
            "Default: Sales Order / Confirmed."
        ),
    )

    viabill_order_state_after_capture = fields.Selection(
        selection=[
            ('pending',    "Pending Payment"),
            ('sale',       "Sales Order / Confirmed"),
            ('done',       "Locked"),
            ('draft',      "Quotation"),
        ],
        string="Order State after Capture",
        default='sale',
        required=True,
        help=(
            "The sale order state to set after a successful ViaBill payment "
            "capture (when Transaction Type is 'Authorize and Capture'). "
            "Default: Sales Order / Confirmed."
        ),
    )

    # ── Debug / Troubleshooting ───────────────────────────────────────────────

    viabill_enable_debug = fields.Boolean(
        string="Enable Debug",
        default=False,
        help=(
            "When enabled, detailed ViaBill API request/response information is "
            "stored in the Debug Log below. Disable in production to avoid storing "
            "sensitive data. The log is capped at 50 entries."
        ),
    )

    viabill_debug_log = fields.Text(
        string="Debug Log",
        readonly=True,
        help="Recent ViaBill API debug messages (last 50 entries). Cleared automatically when it exceeds 50 entries.",
    )

    # =========================================================================
    # SCHEMA INITIALISATION
    # =========================================================================

    def init(self):
        """Override of `models.Model` to ensure all ViaBill-specific columns exist.

        This method is called by Odoo after _auto_init() during every module
        upgrade (-u), regardless of version numbers or migration scripts. It
        adds any missing columns idempotently so that the ORM can read them
        immediately after the upgrade completes.
        """
        super().init()
        cr = self.env.cr
        columns = [
            ('viabill_api_key',                      'VARCHAR',  'NULL'),
            ('viabill_secret_key',                   'VARCHAR',  'NULL'),
            ('viabill_pricetag_script',              'TEXT',     'NULL'),
            ('viabill_api_mode',                     'VARCHAR',  "'test'"),
            ('viabill_transaction_type',             'VARCHAR',  "'authorize'"),
            ('viabill_pricetag_product',             'BOOLEAN',  'FALSE'),
            ('viabill_pricetag_cart',                'BOOLEAN',  'FALSE'),
            ('viabill_pricetag_checkout',            'BOOLEAN',  'FALSE'),
            ('viabill_pricetag_country',             'VARCHAR',  "''"),
            ('viabill_pricetag_language',            'VARCHAR',  "''"),
            ('viabill_pricetag_custom_css',          'TEXT',     'NULL'),
            ('viabill_pricetag_custom_js',           'TEXT',     'NULL'),
            ('viabill_pricetag_product_selector',    'VARCHAR',  "'.oe_price'"),
            ('viabill_pricetag_cart_selector',       'VARCHAR',  "'strong.monetary_field'"),
            ('viabill_pricetag_checkout_selector',   'VARCHAR',  "'strong.monetary_field'"),
            ('viabill_order_state_after_authorize',  'VARCHAR',  "'sale'"),
            ('viabill_order_state_after_capture',    'VARCHAR',  "'sale'"),
            ('viabill_enable_debug',                 'BOOLEAN',  'FALSE'),
            ('viabill_debug_log',                    'TEXT',     'NULL'),
        ]
        for col_name, col_type, default_val in columns:
            cr.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name  = 'payment_provider'
                  AND column_name = %s
                """,
                (col_name,),
            )
            if not cr.fetchone():
                _logger.info(
                    "ViaBill: adding missing column payment_provider.%s", col_name
                )
                cr.execute(
                    f"ALTER TABLE payment_provider "
                    f"ADD COLUMN {col_name} {col_type} DEFAULT {default_val}"
                )

    # =========================================================================
    # COMPUTE METHODS
    # =========================================================================

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features for ViaBill.

        ViaBill supports:
        - Manual capture (full and partial) — enabled by default so Odoo
          shows the 'Capture Amount Manually' checkbox pre-ticked.
        - Partial and full refunds.
        - No tokenization.
        """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'viabill').update({
            'support_manual_capture': 'partial',
            'support_refund': 'partial',
            'support_tokenization': False,
        })

    # =========================================================================
    # DEBUG LOGGING HELPER
    # =========================================================================

    def _viabill_debug_log(self, message):
        """Append a timestamped debug message to viabill_debug_log if debug is enabled.

        The log is stored as plain text, one entry per line, capped at 50 entries.
        Older entries are dropped when the cap is exceeded.

        :param str message: The debug message to append.
        """
        self.ensure_one()
        if not self.viabill_enable_debug:
            return
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = '[{}] {}'.format(timestamp, message)
        existing = (self.viabill_debug_log or '').strip()
        lines = existing.split('\n') if existing else []
        lines.append(entry)
        # Keep only the last 50 entries
        if len(lines) > 50:
            lines = lines[-50:]
        self.sudo().write({'viabill_debug_log': '\n'.join(lines)})

    # =========================================================================
    # CRUD METHODS
    # =========================================================================

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()
        if self.code != 'viabill':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    @api.model_create_multi
    def create(self, vals_list):
        """Override to set capture_manually=True by default for ViaBill providers."""
        for vals in vals_list:
            if vals.get('code') == 'viabill' and 'capture_manually' not in vals:
                vals['capture_manually'] = True
        return super().create(vals_list)

    # =========================================================================
    # PAYMENT FLOW METHODS
    # =========================================================================

    def _get_redirect_form_view(self, is_validation=False):
        """Override of `payment` to return the ViaBill redirect form template view.

        Odoo 19 calls this method on the provider to obtain the `ir.ui.view` record
        for the redirect form. The view's id is then used to render the QWeb template
        via `ir.qweb._render(view_id, rendering_values)`.

        Note: self.ensure_one()

        :param bool is_validation: Whether the operation is a validation.
        :return: The view of the redirect form template.
        :rtype: record of `ir.ui.view`
        """
        self.ensure_one()
        if self.code != 'viabill':
            return super()._get_redirect_form_view(is_validation=is_validation)
        return self.env.ref('payment_viabill.redirect_form')

    # =========================================================================
    # API HELPER METHODS
    # =========================================================================

    def _viabill_get_base_url(self):
        """Return the ViaBill base URL depending on the provider state.

        :return: The ViaBill base URL (test or live).
        :rtype: str
        """
        self.ensure_one()
        if self.viabill_api_mode == 'test':
            return const.VIABILL_TEST_URL
        return const.VIABILL_LIVE_URL

    def _viabill_generate_checkout_signature(
        self, api_key, amount, currency, transaction, order_number, success_url, cancel_url
    ):
        """Generate the MD5 signature for a ViaBill checkout request.

        Signature format:
        ``md5({apikey}#{amount}#{currency}#{transaction}#{order_number}#{success_url}#{cancel_url}#{secret})``

        Note: self.ensure_one()

        :param str api_key: The ViaBill API key.
        :param str amount: The payment amount as a string (e.g. "100.00").
        :param str currency: The ISO 4217 currency code (e.g. "DKK").
        :param str transaction: The Odoo transaction reference.
        :param str order_number: The Odoo order number (same as transaction).
        :param str success_url: The full success redirect URL.
        :param str cancel_url: The full cancel redirect URL.
        :return: The hex MD5 signature string.
        :rtype: str
        """
        self.ensure_one()
        raw = const.CHECKOUT_SIGNATURE_FORMAT.format(
            apikey=api_key,
            amount=amount,
            currency=currency,
            transaction=transaction,
            order_number=order_number,
            success_url=success_url,
            cancel_url=cancel_url,
            secret=self.viabill_secret_key or '',
        )
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    def _viabill_generate_capture_signature(self, amount, currency, transaction):
        """Generate the MD5 signature for a ViaBill capture or refund request.

        Signature format:
        ``md5({id}#{apikey}#{amount}#{currency}#{secret})``

        Note: self.ensure_one()

        :param str amount: The capture/refund amount as a string (negative, e.g. "-100.00").
        :param str currency: The ISO 4217 currency code.
        :param str transaction: The ViaBill transaction ID (provider_reference).
        :return: The hex MD5 signature string.
        :rtype: str
        """
        self.ensure_one()
        raw = const.CAPTURE_SIGNATURE_FORMAT.format(
            id=transaction,
            apikey=self.viabill_api_key or '',
            amount=amount,
            currency=currency,
            secret=self.viabill_secret_key or '',
        )
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    def _viabill_generate_cancel_signature(self, transaction):
        """Generate the MD5 signature for a ViaBill cancel (void) request.

        Signature format:
        ``md5({id}#{apikey}#{secret})``

        Note: self.ensure_one()

        :param str transaction: The ViaBill transaction ID (provider_reference).
        :return: The hex MD5 signature string.
        :rtype: str
        """
        self.ensure_one()
        raw = const.CANCEL_SIGNATURE_FORMAT.format(
            id=transaction,
            apikey=self.viabill_api_key or '',
            secret=self.viabill_secret_key or '',
        )
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    def _viabill_verify_callback_signature(
        self, transaction, order_number, amount, currency, status, time, received_signature
    ):
        """Verify the MD5 signature received in a ViaBill IPN callback notification.

        Expected signature format:
        ``md5({transaction}#{orderNumber}#{amount}#{currency}#{status}#{time}#{secret})``

        Note: self.ensure_one()

        :param str transaction: The ViaBill transaction ID.
        :param str order_number: The Odoo transaction reference (orderNumber in callback).
        :param str amount: The payment amount string.
        :param str currency: The ISO 4217 currency code.
        :param str status: The payment status string (e.g. "APPROVED").
        :param str time: The Unix timestamp (milliseconds) as a string.
        :param str received_signature: The signature received in the callback.
        :return: True if the signature matches, False otherwise.
        :rtype: bool
        """
        self.ensure_one()
        raw = const.CALLBACK_SIGNATURE_FORMAT.format(
            transaction=transaction,
            order_number=order_number,
            amount=amount,
            currency=currency,
            status=status,
            time=time,
            secret=self.viabill_secret_key or '',
        )
        expected = hashlib.md5(raw.encode('utf-8')).hexdigest()
        return expected == received_signature

    def _viabill_call_api(self, method, endpoint, payload=None):
        """Make a server-side JSON API call to ViaBill and return the parsed response.

        :param str method: HTTP method ('GET', 'POST', 'DELETE', etc.)
        :param str endpoint: The endpoint path (e.g. '/api/addon/woocommerce/login').
        :param dict payload: The JSON request payload.
        :return: Parsed JSON response dict.
        :rtype: dict
        :raises UserError: If the request fails or ViaBill returns an error status.
        """
        self.ensure_one()
        url = self._viabill_get_base_url() + endpoint
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': _VIABILL_USER_AGENT,
        }
        try:
            resp = requests.request(
                method,
                url,
                data=json.dumps(payload or {}),
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise UserError(_("ViaBill API request failed: %s", exc)) from exc

        _logger.info(
            "ViaBill API %s %s \u2192 HTTP %s: %s",
            method, url, resp.status_code, resp.text[:300],
        )
        self._viabill_debug_log(
            'API {} {} \u2192 HTTP {} | payload={} | response={}'.format(
                method, endpoint, resp.status_code,
                json.dumps(payload or {}),
                resp.text[:500],
            )
        )

        try:
            data = resp.json()
        except ValueError:
            raise UserError(
                _("ViaBill API returned a non-JSON response (HTTP %s): %s",
                  resp.status_code, resp.text[:200])
            )

        if resp.status_code not in (200, 201, 204):
            errors = data.get('errors', []) if isinstance(data, dict) else []
            if errors:
                msg = '; '.join(
                    e.get('error', str(e)) if isinstance(e, dict) else str(e)
                    for e in errors
                )
            else:
                msg = data.get('message', resp.text[:200]) if isinstance(data, dict) else resp.text[:200]
            raise UserError(_("ViaBill API error (HTTP %s): %s", resp.status_code, msg))

        return data

    # =========================================================================
    # MERCHANT AUTHENTICATION ACTIONS
    # =========================================================================

    def action_viabill_login(self, email, password):
        """Log in to ViaBill and store the returned credentials.

        Calls ``POST /api/addon/woocommerce/login`` with the merchant's email and
        password. On success, stores the returned ``key``, ``secret``, and
        ``pricetagScript`` on the provider record.

        :param str email: The merchant's ViaBill account email.
        :param str password: The merchant's ViaBill account password.
        :raises UserError: If the login request fails.
        """
        self.ensure_one()
        data = self._viabill_call_api(
            'POST',
            const.LOGIN_ENDPOINT,
            payload={'email': email, 'password': password},
        )
        self.sudo().write({
            'viabill_api_key': data.get('key', ''),
            'viabill_secret_key': data.get('secret', ''),
            'viabill_pricetag_script': data.get('pricetagScript', ''),
        })
        _logger.info("ViaBill login successful for provider %s.", self.id)

    def action_viabill_register(self, email, name, url, country, tax_id=None, phone=None):
        """Register a new ViaBill merchant account and store the returned credentials.

        Calls ``POST /api/addon/woocommerce/register`` with the merchant's details.
        On success, stores the returned ``key``, ``secret``, and ``pricetagScript``
        on the provider record.

        :param str email: The merchant's email address.
        :param str name: The store name.
        :param str url: The live shop URL (must start with https://).
        :param str country: Two-letter ISO 3166-1 alpha-2 country code (e.g. "DK").
        :param str tax_id: Optional tax ID / VAT number.
        :param str phone: Optional phone number.
        :raises UserError: If the registration request fails.
        """
        self.ensure_one()
        payload = {
            'email': email,
            'name': name,
            'url': url,
            'country': country.upper(),
            'affiliate': 'woocommerce',
        }
        additional_info = []
        if tax_id:
            additional_info.append('taxId:{}'.format(tax_id))
        if phone:
            additional_info.append('phone:{}'.format(phone))
        if additional_info:
            payload['additionalInfo'] = additional_info

        data = self._viabill_call_api(
            'POST',
            const.REGISTER_ENDPOINT,
            payload=payload,
        )
        self.sudo().write({
            'viabill_api_key': data.get('key', ''),
            'viabill_secret_key': data.get('secret', ''),
            'viabill_pricetag_script': data.get('pricetagScript', ''),
        })
        _logger.info("ViaBill registration successful for provider %s.", self.id)

    # =========================================================================
    # DEBUG LOG ACTIONS
    # =========================================================================

    def action_viabill_clear_debug_log(self):
        """Clear the ViaBill debug log."""
        self.ensure_one()
        self.sudo().write({'viabill_debug_log': False})
        return True

    # =========================================================================
    # CAPTURE ACTION (called from controller on authorize+capture mode)
    # =========================================================================

    def action_viabill_capture(self, transaction_id, amount, currency):
        """Capture a previously authorized ViaBill transaction.

        Calls ``POST /api/transaction/capture`` with a **negative** amount per the
        ViaBill API specification.

        :param str transaction_id: The ViaBill transaction ID (provider_reference).
        :param float amount: The amount to capture (positive value; negated internally).
        :param str currency: The ISO 4217 currency code.
        :raises UserError: If the capture request fails.
        """
        self.ensure_one()
        capture_amount = '-{:.2f}'.format(abs(float(amount)))
        signature = self._viabill_generate_capture_signature(
            amount=capture_amount,
            currency=currency,
            transaction=transaction_id,
        )
        self._viabill_call_api(
            'POST',
            const.CAPTURE_ENDPOINT,
            payload={
                'id': transaction_id,
                'apikey': self.viabill_api_key,
                'amount': capture_amount,
                'currency': currency,
                'signature': signature,
            },
        )
        _logger.info(
            "ViaBill capture successful: transaction=%s, amount=%s %s.",
            transaction_id, capture_amount, currency,
        )
