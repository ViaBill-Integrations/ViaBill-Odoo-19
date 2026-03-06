# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import json
import logging
import pprint

import requests

from odoo import http
from odoo.http import request
from odoo.exceptions import UserError
from werkzeug.exceptions import Forbidden

from odoo.addons.payment_viabill import const

_logger = logging.getLogger(__name__)

# User-Agent sent with all ViaBill API requests.
_VIABILL_USER_AGENT = 'ViaBill-Odoo/1.0'


class ViaBillController(http.Controller):
    """HTTP controller for ViaBill payment provider routes."""

    _checkout_url = '/payment/viabill/checkout'
    _return_url = '/payment/viabill/return'
    _cancel_url = '/payment/viabill/cancel'
    _callback_url = '/payment/viabill/callback'
    _login_url = '/payment/viabill/login'
    _register_url = '/payment/viabill/register'

    # =========================================================================
    # CHECKOUT PROXY
    # =========================================================================

    @http.route(
        _checkout_url,
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def viabill_checkout(self, **data):
        """Proxy the ViaBill checkout authorize request server-side.

        The browser JS POSTs the form fields (URL-encoded) to this endpoint.
        We forward a JSON request to ViaBill's checkout-authorize API, read the
        redirect URL from the 302/301 Location header, and return it as JSON to
        the browser JS which then redirects the customer to the ViaBill gateway.

        :param dict data: Form-encoded POST parameters from the browser.
        :return: JSON response with ``redirect_url`` or ``error``.
        :rtype: werkzeug.wrappers.Response
        """
        _logger.info("ViaBill checkout proxy received with data:\n%s", pprint.pformat(data))

        # Retrieve the provider to get the correct base URL (test vs live).
        tx_reference = data.get('transaction') or data.get('order_number', '')
        provider_sudo = None
        if tx_reference:
            tx_sudo = request.env['payment.transaction'].sudo().search(
                [('reference', '=', tx_reference), ('provider_code', '=', 'viabill')],
                limit=1,
            )
            if tx_sudo:
                provider_sudo = tx_sudo.provider_id.sudo()

        # Determine the correct ViaBill base URL (test vs live).
        if provider_sudo:
            base_url = provider_sudo._viabill_get_base_url()
        else:
            base_url = const.VIABILL_LIVE_URL

        checkout_url = base_url + const.CHECKOUT_ENDPOINT

        # Determine test flag from the provider's API Mode configuration.
        # This allows merchants to test with any URL (e.g. ngrok) instead of
        # being restricted to localhost-based test detection.
        if provider_sudo:
            test_flag = (provider_sudo.viabill_api_mode == 'test')
        else:
            # Fallback: honour the form-submitted 'test' field if no provider found.
            test_raw = data.get('test', 'false')
            if isinstance(test_raw, bool):
                test_flag = test_raw
            else:
                test_flag = str(test_raw).strip().lower() == 'true'

        # Build the JSON payload for ViaBill from the form fields.
        payload = {
            'apikey':       data.get('apikey', ''),
            'transaction':  data.get('transaction', ''),
            'order_number': data.get('order_number', ''),
            'amount':       data.get('amount', ''),
            'currency':     data.get('currency', ''),
            'success_url':  data.get('success_url', ''),
            'cancel_url':   data.get('cancel_url', ''),
            'callback_url': data.get('callback_url', ''),
            'md5check':     data.get('md5check', ''),
            'test':         test_flag,
            'protocol':     data.get('protocol', '3.0'),
            'tbyb':         0,
        }

        _logger.info(
            "ViaBill checkout: POST URL: %s\nPayload (apikey/md5check masked):\n%s",
            checkout_url,
            {k: ('***' if k in ('apikey', 'md5check') else v) for k, v in payload.items()},
        )

        headers = {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'User-Agent': _VIABILL_USER_AGENT,
        }

        try:
            resp = requests.post(
                checkout_url,
                data=json.dumps(payload),
                headers=headers,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as exc:
            _logger.error("ViaBill checkout: request exception: %s", exc)
            return request.make_response(
                json.dumps({'redirect_url': '', 'error': str(exc)}),
                headers=[('Content-Type', 'application/json')],
            )

        _logger.info(
            "ViaBill checkout API response: status=%s, headers=%s, body=%s",
            resp.status_code,
            dict(resp.headers),
            resp.text[:300],
        )
        if provider_sudo:
            provider_sudo._viabill_debug_log(
                'Checkout {} → HTTP {} | response={}'.format(
                    checkout_url, resp.status_code, resp.text[:500]
                )
            )

        redirect_url = ''
        error_msg = ''

        if resp.status_code in (301, 302):
            redirect_url = resp.headers.get('Location', '')
            _logger.info("ViaBill checkout redirect URL: %s", redirect_url)
        else:
            try:
                error_data = resp.json()
                errors = error_data.get('errors', []) if isinstance(error_data, dict) else []
                if errors:
                    error_msg = '; '.join(
                        e.get('error', str(e)) if isinstance(e, dict) else str(e)
                        for e in errors
                    )
                else:
                    error_msg = (
                        error_data.get('message', resp.text[:200])
                        if isinstance(error_data, dict)
                        else resp.text[:200]
                    )
            except ValueError:
                error_msg = resp.text[:200]
            _logger.warning(
                "ViaBill checkout: unexpected status %s. Error: %s",
                resp.status_code, error_msg,
            )

        return request.make_response(
            json.dumps({'redirect_url': redirect_url, 'error': error_msg}),
            headers=[('Content-Type', 'application/json')],
        )

    # =========================================================================
    # RETURN / CANCEL HANDLERS
    # =========================================================================

    @http.route(
        _return_url,
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False,
    )
    def viabill_return(self, **data):
        """Handle the buyer's return from the ViaBill gateway after a successful payment.

        ViaBill redirects to this URL after the buyer completes payment. The
        transaction reference is embedded in the URL as a query parameter ``?ref=...``.

        :param dict data: Query string parameters (includes ``ref``).
        :return: Redirect to the payment status page.
        :rtype: werkzeug.wrappers.Response
        """
        _logger.info("ViaBill return received with data:\n%s", pprint.pformat(data))

        ref = data.get('ref', '')
        if not ref:
            _logger.warning("ViaBill return: missing 'ref' query parameter.")
            return request.redirect('/payment/status')

        tx_sudo = request.env['payment.transaction'].sudo().search(
            [('reference', '=', ref), ('provider_code', '=', 'viabill')],
            limit=1,
        )
        if not tx_sudo:
            _logger.warning("ViaBill return: no transaction found for ref '%s'.", ref)
            return request.redirect('/payment/status')

        # Mark the transaction as pending (waiting for IPN callback confirmation).
        # The IPN callback will update it to 'done' or 'cancel'.
        if tx_sudo.state == 'draft':
            tx_sudo._set_pending()

        return request.redirect('/payment/status')

    @http.route(
        _cancel_url,
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False,
    )
    def viabill_cancel(self, **data):
        """Handle the buyer's return from the ViaBill gateway after cancellation.

        :param dict data: Query string parameters (includes ``ref``).
        :return: Redirect to the payment status page.
        :rtype: werkzeug.wrappers.Response
        """
        _logger.info("ViaBill cancel received with data:\n%s", pprint.pformat(data))

        ref = data.get('ref', '')
        if not ref:
            _logger.warning("ViaBill cancel: missing 'ref' query parameter.")
            return request.redirect('/payment/status')

        tx_sudo = request.env['payment.transaction'].sudo().search(
            [('reference', '=', ref), ('provider_code', '=', 'viabill')],
            limit=1,
        )
        if not tx_sudo:
            _logger.warning("ViaBill cancel: no transaction found for ref '%s'.", ref)
            return request.redirect('/payment/status')

        if tx_sudo.state not in ('cancel', 'done'):
            tx_sudo._set_canceled(
                state_message="Payment cancelled by customer at ViaBill gateway."
            )

        return request.redirect('/payment/status')

    # =========================================================================
    # IPN CALLBACK
    # =========================================================================

    @http.route(
        _callback_url,
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def viabill_callback(self, **data):
        """Handle the ViaBill server-to-server IPN callback notification.

        ViaBill sends a POST request with a JSON body containing:

        - ``transaction``:  ViaBill transaction ID
        - ``orderNumber``:  Odoo transaction reference
        - ``amount``:       Payment amount as string (e.g. "280.00")
        - ``currency``:     ISO currency code (e.g. "DKK")
        - ``status``:       Payment status (e.g. "APPROVED", "CANCELLED", "REJECTED")
        - ``time``:         Unix timestamp in milliseconds
        - ``signature``:    MD5 signature for verification

        :param dict data: Form-encoded POST parameters (may be empty if ViaBill sends JSON).
        :return: An empty 200 response to acknowledge the notification.
        :rtype: werkzeug.wrappers.Response
        """
        # ViaBill sends the callback as a JSON body, not form-encoded.
        # Try to parse the raw request body as JSON first; fall back to form params.
        if not data:
            try:
                raw_body = request.httprequest.get_data(as_text=True)
                if raw_body:
                    data = json.loads(raw_body)
                    _logger.info(
                        "ViaBill callback: parsed JSON body:\n%s", pprint.pformat(data)
                    )
            except (ValueError, TypeError) as exc:
                _logger.warning("ViaBill callback: failed to parse JSON body: %s", exc)

        _logger.info("ViaBill callback received with data:\n%s", pprint.pformat(data))

        # Extract the required callback fields.
        transaction = data.get('transaction', '')
        order_number = data.get('orderNumber', '') or data.get('order_number', '')
        amount = data.get('amount', '')
        currency = data.get('currency', '')
        status = data.get('status', '')
        time_val = data.get('time', '')
        received_signature = data.get('signature', '') or data.get('md5check', '')

        if not order_number:
            _logger.warning(
                "ViaBill callback received with missing orderNumber. Full data: %s", data
            )
            return request.make_response('', status=200)

        # Retrieve the transaction.
        try:
            tx_sudo = request.env['payment.transaction'].sudo().search(
                [('reference', '=', order_number), ('provider_code', '=', 'viabill')],
                limit=1,
            )
        except Exception:
            _logger.exception("Error searching for ViaBill transaction.")
            return request.make_response('', status=200)

        if not tx_sudo:
            _logger.warning(
                "ViaBill callback received for unknown transaction reference '%s'.",
                order_number,
            )
            return request.make_response('', status=200)

        # Verify the callback signature to ensure the notification is authentic.
        if received_signature and not tx_sudo.provider_id._viabill_verify_callback_signature(
            transaction=transaction,
            order_number=order_number,
            amount=amount,
            currency=currency,
            status=status,
            time=str(time_val),
            received_signature=received_signature,
        ):
            _logger.warning(
                "ViaBill callback signature mismatch for transaction %s. "
                "Received: %s", order_number, received_signature
            )
            raise Forbidden()

        # Build the payment data dict and process the transaction.
        payment_data = {
            'reference': order_number,
            'transaction': transaction,
            'status': status.upper(),
            'amount': amount,
            'currency': currency,
            'time': str(time_val),
        }
        tx_sudo._process('viabill', payment_data)

        # Auto-capture if the provider is configured for authorize+capture
        # and the transaction was just set to 'authorized'.
        if (
            status.upper() == const.VIABILL_STATUS_APPROVED
            and tx_sudo.provider_id.viabill_transaction_type == 'authorize_capture'
            and tx_sudo.state == 'authorized'
        ):
            _logger.info(
                "ViaBill callback: auto-capturing transaction %s (authorize+capture mode).",
                tx_sudo.reference,
            )
            try:
                # Call _capture() directly (not action_capture which opens a wizard).
                # _capture() creates a child transaction, calls _send_capture_request()
                # which sends a negative amount to the ViaBill capture endpoint, and
                # transitions the parent transaction to 'done' on success.
                capture_tx = tx_sudo.sudo()._capture()
                if capture_tx.state == 'error':
                    _logger.error(
                        "ViaBill callback: auto-capture failed for transaction %s: %s",
                        tx_sudo.reference, capture_tx.state_message,
                    )
                else:
                    _logger.info(
                        "ViaBill callback: auto-capture successful for transaction %s "
                        "(child: %s, state: %s).",
                        tx_sudo.reference, capture_tx.reference, capture_tx.state,
                    )
            except Exception as exc:
                _logger.error(
                    "ViaBill callback: auto-capture failed for transaction %s: %s",
                    tx_sudo.reference, exc,
                )

        return request.make_response('', status=200)

    # =========================================================================
    # AUTHENTICATION ENDPOINTS (called from the provider form via JSON-RPC)
    # =========================================================================

    @http.route(
        _login_url,
        type='jsonrpc',
        auth='user',
        methods=['POST'],
        csrf=True,
    )
    def viabill_login(self, provider_id, email, password):
        """Handle the ViaBill merchant login from the provider configuration form.

        :param int provider_id: The ID of the payment.provider record.
        :param str email: The merchant's ViaBill account email.
        :param str password: The merchant's ViaBill account password.
        :return: Result dict with ``success`` flag and ``message`` or ``error``.
        :rtype: dict
        """
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id)
        if not provider_sudo.exists() or provider_sudo.code != 'viabill':
            return {'success': False, 'error': 'Invalid provider.'}
        try:
            provider_sudo.action_viabill_login(email=email, password=password)
            return {'success': True, 'message': 'Credentials saved successfully.'}
        except (UserError, Exception) as exc:
            return {'success': False, 'error': str(exc)}

    @http.route(
        _register_url,
        type='jsonrpc',
        auth='user',
        methods=['POST'],
        csrf=True,
    )
    def viabill_register(self, provider_id, email, name, url, country, tax_id=None, phone=None):
        """Handle the ViaBill merchant registration from the provider configuration form.

        :param int provider_id: The ID of the payment.provider record.
        :param str email: The merchant's email address.
        :param str name: The store name.
        :param str url: The live shop URL (must start with https://).
        :param str country: Two-letter ISO 3166-1 alpha-2 country code.
        :param str tax_id: Optional tax ID / VAT number.
        :param str phone: Optional phone number.
        :return: Result dict with ``success`` flag and ``message`` or ``error``.
        :rtype: dict
        """
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id)
        if not provider_sudo.exists() or provider_sudo.code != 'viabill':
            return {'success': False, 'error': 'Invalid provider.'}
        try:
            provider_sudo.action_viabill_register(
                email=email, name=name, url=url, country=country,
                tax_id=tax_id, phone=phone,
            )
            return {'success': True, 'message': 'Account created and credentials saved.'}
        except (UserError, Exception) as exc:
            return {'success': False, 'error': str(exc)}
