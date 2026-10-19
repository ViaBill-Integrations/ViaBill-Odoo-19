# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import hmac
import json
import logging
import pprint

import requests

from odoo import http
from odoo.http import request
from werkzeug.exceptions import BadRequest, Forbidden

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_viabill import const

_logger = logging.getLogger(__name__)

# User-Agent sent with all ViaBill API requests.
_VIABILL_USER_AGENT = 'ViaBill-Odoo/1.0'

# Callback fields covered by the ViaBill signature (plus the signature itself).
_CALLBACK_REQUIRED_FIELDS = ('transaction', 'orderNumber', 'amount', 'currency', 'status', 'time')

# Generic message returned to the browser when the checkout request fails;
# the details are only logged.
_CHECKOUT_ERROR = "The payment could not be started. Please try again or choose another payment method."


class ViaBillController(http.Controller):
    """HTTP controller for ViaBill payment provider routes."""

    _checkout_url = '/payment/viabill/checkout'
    _return_url = '/payment/viabill/return'
    _cancel_url = '/payment/viabill/cancel'
    _callback_url = '/payment/viabill/callback'

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
        Only the transaction reference is taken from the browser: the request to
        ViaBill's checkout-authorize API is rebuilt from the transaction itself
        (amount, currency, URLs, checksum). The redirect URL is read from the
        302/301 Location header and returned as JSON to the browser JS, which
        then redirects the customer to the ViaBill gateway.

        :param dict data: Form-encoded POST parameters from the browser.
        :return: JSON response with ``redirect_url`` or ``error``.
        :rtype: werkzeug.wrappers.Response
        """
        tx_reference = data.get('transaction') or data.get('order_number') or ''
        _logger.info("ViaBill checkout proxy received for transaction %s.", tx_reference)

        tx_sudo = request.env['payment.transaction'].sudo()
        if isinstance(tx_reference, str) and tx_reference:
            tx_sudo = tx_sudo.search(
                [('reference', '=', tx_reference), ('provider_code', '=', 'viabill')],
                limit=1,
            )

        # Only transactions that have not been paid yet can be sent to ViaBill.
        if not tx_sudo or tx_sudo.state != 'draft':
            _logger.warning(
                "ViaBill checkout: no draft ViaBill transaction found for reference %s.",
                tx_reference,
            )
            return self._json_response({'redirect_url': '', 'error': _CHECKOUT_ERROR})

        provider_sudo = tx_sudo.provider_id.sudo()
        values = tx_sudo._viabill_get_checkout_values()

        checkout_url = provider_sudo._viabill_get_base_url() + const.CHECKOUT_ENDPOINT

        # The test flag follows the provider's API Mode configuration.
        test_flag = provider_sudo.viabill_api_mode == 'test'

        payload = {
            'apikey':        values['apikey'],
            'transaction':   values['transaction'],
            'order_number':  values['order_number'],
            'amount':        values['amount'],
            'currency':      values['currency'],
            'success_url':   values['success_url'],
            'cancel_url':    values['cancel_url'],
            'callback_url':  values['callback_url'],
            'sha256check':   values['sha256check'],
            'test':          test_flag,
            'protocol':      values['protocol'],
            'tbyb':          0,
        }

        _logger.info(
            "ViaBill checkout: POST URL: %s\nPayload (masked):\n%s",
            checkout_url,
            provider_sudo._viabill_mask_payload(payload),
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
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            _logger.error("ViaBill checkout: request exception: %s", exc)
            return self._json_response({'redirect_url': '', 'error': _CHECKOUT_ERROR})

        _logger.info(
            "ViaBill checkout API response: status=%s, body=%s",
            resp.status_code,
            resp.text[:300],
        )

        provider_sudo._viabill_debug_log(
            'Checkout {} → HTTP {} | response={}'.format(
                checkout_url, resp.status_code, resp.text[:500]
            )
        )

        redirect_url = ''
        error_msg = ''

        if resp.status_code in (301, 302):
            redirect_url = resp.headers.get('Location', '')
            if not self._is_allowed_redirect_url(redirect_url):
                _logger.warning("ViaBill checkout: unexpected redirect URL %s.", redirect_url)
                redirect_url = ''
                error_msg = _CHECKOUT_ERROR
            else:
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
                resp.status_code,
                error_msg,
            )
            # ViaBill's own error messages (e.g. about the customer's credit) are
            # meant for the customer; anything else gets a generic message.
            if not error_msg:
                error_msg = _CHECKOUT_ERROR

        return self._json_response({'redirect_url': redirect_url, 'error': error_msg})

    @staticmethod
    def _json_response(values):
        return request.make_response(
            json.dumps(values),
            headers=[('Content-Type', 'application/json')],
        )

    @staticmethod
    def _is_allowed_redirect_url(url):
        """Only ever send the customer to an HTTPS address of ViaBill."""
        from urllib.parse import urlparse

        if not isinstance(url, str) or not url:
            return False
        parts = urlparse(url)
        host = (parts.hostname or '').lower()
        return parts.scheme == 'https' and (host == 'viabill.com' or host.endswith('.viabill.com'))

    # =========================================================================
    # RETURN / CANCEL HANDLERS
    # =========================================================================

    def _get_transaction_from_return(self, data):
        """Return the transaction of a return/cancel URL, if its access token is valid.

        :param dict data: Query string parameters (``ref`` and ``access_token``).
        :return: The transaction, or an empty recordset.
        :rtype: recordset of `payment.transaction`
        """
        ref = data.get('ref', '')
        access_token = data.get('access_token', '')
        empty = request.env['payment.transaction'].sudo()

        if not isinstance(ref, str) or not ref:
            _logger.warning("ViaBill return/cancel: missing 'ref' query parameter.")
            return empty

        if not isinstance(access_token, str) or not payment_utils.check_access_token(access_token, ref):
            _logger.warning("ViaBill return/cancel: invalid access token for ref '%s'.", ref)
            return empty

        return empty.search([('reference', '=', ref), ('provider_code', '=', 'viabill')], limit=1)

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
        transaction reference and an access token are embedded in the URL as
        query parameters.

        :param dict data: Query string parameters (``ref``, ``access_token``).
        :return: Redirect to the payment status page.
        :rtype: werkzeug.wrappers.Response
        """
        _logger.info("ViaBill return received for ref %s.", data.get('ref', ''))

        tx_sudo = self._get_transaction_from_return(data)
        if not tx_sudo:
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

        :param dict data: Query string parameters (``ref``, ``access_token``).
        :return: Redirect to the payment status page.
        :rtype: werkzeug.wrappers.Response
        """
        _logger.info("ViaBill cancel received for ref %s.", data.get('ref', ''))

        tx_sudo = self._get_transaction_from_return(data)
        if not tx_sudo:
            return request.redirect('/payment/status')

        # Only a payment that has not been approved yet can be cancelled here;
        # an authorized payment is only changed by ViaBill's signed callback.
        if tx_sudo.state in ('draft', 'pending'):
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
        - ``signature``:    sha256 signature for verification

        All of these fields are required and the signature is always verified.

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
            except (ValueError, TypeError) as exc:
                _logger.warning("ViaBill callback: failed to parse JSON body: %s", exc)
                data = {}

        if not isinstance(data, dict):
            data = {}

        _logger.info("ViaBill callback received with data:\n%s", pprint.pformat(data))

        # All signed fields and the signature must be present as plain values.
        fields = {}
        for field in _CALLBACK_REQUIRED_FIELDS + ('signature',):
            value = data.get(field)
            if field == 'orderNumber' and value in (None, ''):
                value = data.get('order_number')
            if value is None or isinstance(value, (dict, list)) or str(value) == '':
                _logger.warning("ViaBill callback rejected: missing or invalid '%s' field.", field)
                raise BadRequest()
            fields[field] = str(value)

        order_number = fields['orderNumber']
        status = fields['status'].upper()

        # Retrieve the transaction.
        tx_sudo = request.env['payment.transaction'].sudo().search(
            [('reference', '=', order_number), ('provider_code', '=', 'viabill')],
            limit=1,
        )
        if not tx_sudo:
            _logger.warning(
                "ViaBill callback received for unknown transaction reference '%s'.",
                order_number,
            )
            raise BadRequest()

        # Verify the callback signature to ensure the notification is authentic.
        if not tx_sudo.provider_id._viabill_verify_callback_signature(
            transaction=fields['transaction'],
            order_number=order_number,
            amount=fields['amount'],
            currency=fields['currency'],
            status=fields['status'],
            time=fields['time'],
            received_signature=fields['signature'],
        ):
            _logger.warning("ViaBill callback signature mismatch for transaction %s.", order_number)
            raise Forbidden()

        # A transaction that is already linked to a ViaBill transaction can only
        # be updated by callbacks for that same ViaBill transaction.
        if tx_sudo.provider_reference and not hmac.compare_digest(
            tx_sudo.provider_reference, fields['transaction']
        ):
            _logger.warning(
                "ViaBill callback for transaction %s refers to ViaBill transaction %s, "
                "but %s is expected.",
                order_number, fields['transaction'], tx_sudo.provider_reference,
            )
            raise Forbidden()

        # Build the payment data dict and process the transaction. The amount and
        # currency are always present, so Odoo validates them against the
        # transaction.
        payment_data = {
            'reference': order_number,
            'transaction': fields['transaction'],
            'status': status,
            'amount': fields['amount'],
            'currency': fields['currency'],
            'time': fields['time'],
        }
        tx_sudo._process('viabill', payment_data)

        # Auto-capture if the provider is configured for authorize+capture
        # and the transaction was just set to 'authorized'.
        if (
            status == const.VIABILL_STATUS_APPROVED
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
