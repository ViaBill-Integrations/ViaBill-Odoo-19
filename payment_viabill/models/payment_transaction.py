# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import json
import logging
import pprint

import requests

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_viabill import const
from odoo.addons.payment_viabill.controllers.main import ViaBillController

_logger = get_payment_logger(__name__, sensitive_keys=const.SENSITIVE_KEYS)

# User-Agent sent with all ViaBill API requests.
_VIABILL_USER_AGENT = 'ViaBill-Odoo/1.0'


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # === BUSINESS METHODS - PAYMENT FLOW === #

    def _get_specific_rendering_values(self, processing_values):
        """Override of `payment` to return ViaBill-specific rendering values.

        This method is called by the base payment flow to obtain the values
        needed to render the redirect form template. It must live on the
        transaction model so that `tx._get_specific_rendering_values()` finds it.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic processing values of the transaction.
        :return: The dict of provider-specific rendering values.
        :rtype: dict
        """
        if self.provider_code != 'viabill':
            return super()._get_specific_rendering_values(processing_values)

        base_url = self.provider_id.get_base_url()
        # Force https:// to avoid browser mixed-content warnings when Odoo sits behind a
        # reverse proxy (e.g. ngrok) that terminates TLS but forwards plain HTTP internally.
        if base_url.startswith('http://'):
            base_url = 'https://' + base_url[len('http://'):]
        base_url = base_url.rstrip('/')

        amount_str = '{:.2f}'.format(processing_values['amount'])
        currency_code = self.env['res.currency'].browse(
            processing_values['currency_id']
        ).name

        reference = processing_values['reference']
        # Embed the reference as a query parameter so the return/cancel handlers
        # can identify the transaction even when ViaBill sends no body parameters.
        success_url = '{}{}'.format(
            base_url + ViaBillController._return_url,
            '?ref={}'.format(reference),
        )
        cancel_url = '{}{}'.format(
            base_url + ViaBillController._cancel_url,
            '?ref={}'.format(reference),
        )
        callback_url = base_url + ViaBillController._callback_url

        signature = self.provider_id._viabill_generate_checkout_signature(
            api_key=self.provider_id.viabill_api_key,
            amount=amount_str,
            currency=currency_code,
            transaction=reference,
            order_number=reference,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        # The form posts to the local Odoo proxy endpoint, which then calls ViaBill server-side.
        checkout_url = base_url + ViaBillController._checkout_url

        # The test flag is driven by the provider state: 'test' → True, 'enabled' → False.
        is_test = self.provider_id.state == 'test'

        return {
            'checkout_url': checkout_url,
            'protocol': '3.0',
            'apikey': self.provider_id.viabill_api_key,
            'transaction': reference,
            'order_number': reference,
            'amount': amount_str,
            'currency': currency_code,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'callback_url': callback_url,
            'md5check': signature,
            # 'test' is sent as the string 'true'/'false'; the proxy converts it to JSON boolean.
            'test': 'true' if is_test else 'false',
        }

    def _viabill_make_api_request(self, method, endpoint, payload):
        """Make a direct HTTP request to the ViaBill API.

        :param str method: HTTP method ('POST', 'DELETE', etc.)
        :param str endpoint: The full endpoint path (e.g. '/api/transaction/capture')
        :param dict payload: The JSON payload to send.
        :return: Parsed JSON response dict.
        :rtype: dict
        :raises ValidationError: If the request fails or returns an error status.
        """
        self.ensure_one()
        url = self.provider_id._viabill_get_base_url() + endpoint
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': _VIABILL_USER_AGENT,
        }
        try:
            resp = requests.request(
                method, url,
                data=json.dumps(payload),
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ValidationError(
                _("ViaBill API request failed: %s", exc)
            ) from exc

        _logger.info(
            "ViaBill API %s %s → HTTP %s: %s",
            method, url, resp.status_code, resp.text[:300],
        )
        self.provider_id._viabill_debug_log(
            'TX API {} {} → HTTP {} | payload={} | response={}'.format(
                method, endpoint, resp.status_code,
                json.dumps(payload),
                resp.text[:500],
            )
        )
        if resp.status_code not in (200, 201, 204):
            try:
                error_data = resp.json()
                errors = error_data.get('errors', []) if isinstance(error_data, dict) else []
                if errors:
                    msg = '; '.join(
                        e.get('error', str(e)) if isinstance(e, dict) else str(e)
                        for e in errors
                    )
                else:
                    msg = error_data.get('message', resp.text[:200]) if isinstance(error_data, dict) else resp.text[:200]
            except ValueError:
                msg = resp.text[:200]
            raise ValidationError(
                _("ViaBill API error (HTTP %s): %s", resp.status_code, msg)
            )

        try:
            return resp.json() if resp.text else {}
        except ValueError:
            return {}

    def _send_capture_request(self):
        """Override of `payment` to send a capture request to ViaBill.

        ViaBill capture amounts are sent as negative values per the API specification.
        The child capture transaction does not have provider_reference set by Odoo, so we
        read it from the parent (source) authorized transaction.
        """
        if self.provider_code != 'viabill':
            return super()._send_capture_request()

        amount_str = '{:.2f}'.format(abs(self.amount))
        # ViaBill expects the capture amount as a negative value.
        capture_amount = '-{}'.format(amount_str)
        currency_code = self.currency_id.name
        # The child transaction does not inherit provider_reference; read from the parent.
        transaction_id = (
            self.provider_reference
            or (self.source_transaction_id and self.source_transaction_id.provider_reference)
        )

        signature = self.provider_id._viabill_generate_capture_signature(
            amount=capture_amount,
            currency=currency_code,
            transaction=transaction_id,
        )

        payload = {
            'id': transaction_id,
            'apikey': self.provider_id.viabill_api_key,
            'amount': capture_amount,
            'currency': currency_code,
            'signature': signature,
        }

        try:
            self._viabill_make_api_request('POST', const.CAPTURE_ENDPOINT, payload)
        except ValidationError as error:
            self._set_error(str(error))
            return

        payment_data = {
            'reference': self.reference,
            'status': const.VIABILL_STATUS_APPROVED,
            'transaction': transaction_id,
        }
        self._process('viabill', payment_data)

    def _send_void_request(self):
        """Override of `payment` to send a void/cancel request to ViaBill."""
        if self.provider_code != 'viabill':
            return super()._send_void_request()

        # The child void transaction does not inherit provider_reference; read from the parent.
        transaction_id = (
            self.provider_reference
            or (self.source_transaction_id and self.source_transaction_id.provider_reference)
        )
        signature = self.provider_id._viabill_generate_cancel_signature(
            transaction=transaction_id,
        )

        payload = {
            'id': transaction_id,
            'apikey': self.provider_id.viabill_api_key,
            'signature': signature,
        }

        try:
            self._viabill_make_api_request('POST', const.CANCEL_ENDPOINT, payload)
        except ValidationError as error:
            error_str = str(error)
            # ViaBill returns HTTP 400 with 'Cannot cancel an authorize entry in state
            # TEST_CAPTURED' (or CAPTURED) when the transaction has already been captured.
            # In this case, a void is not possible; the merchant must use Refund instead.
            if 'CAPTURED' in error_str.upper():
                self._set_error(_(
                    "Cannot void this transaction: it has already been captured by ViaBill. "
                    "Please use the Refund Transaction button instead."
                ))
            else:
                self._set_error(error_str)
            return

        payment_data = {
            'reference': self.reference,
            'status': const.VIABILL_STATUS_CANCELLED,
            'transaction': transaction_id,
        }
        self._process('viabill', payment_data)

    def _send_refund_request(self):
        """Override of `payment` to send a refund request to ViaBill.

        Refund amounts are sent as negative values per the ViaBill API specification.
        """
        if self.provider_code != 'viabill':
            return super()._send_refund_request()

        # The refund transaction amount is already negative in Odoo; take the absolute value.
        # ViaBill expects refund amounts as POSITIVE values (unlike captures which are negative).
        viabill_amount = '{:.2f}'.format(abs(self.amount))
        currency_code = self.currency_id.name
        # Refund against the source (original) transaction's provider reference.
        transaction_id = self.source_transaction_id.provider_reference

        signature = self.provider_id._viabill_generate_capture_signature(
            amount=viabill_amount,
            currency=currency_code,
            transaction=transaction_id,
        )

        payload = {
            'id': transaction_id,
            'apikey': self.provider_id.viabill_api_key,
            'amount': viabill_amount,
            'currency': currency_code,
            'signature': signature,
        }

        try:
            self._viabill_make_api_request('POST', const.REFUND_ENDPOINT, payload)
        except ValidationError as error:
            self._set_error(str(error))
            return

        payment_data = {
            'reference': self.reference,
            'status': const.VIABILL_STATUS_APPROVED,
            'transaction': transaction_id,
        }
        self._process('viabill', payment_data)
        # Trigger post-processing for refund transactions immediately.
        self.env.ref('payment.cron_post_process_payment_tx')._trigger()

    # === BUSINESS METHODS - PROCESSING === #

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of `payment` to extract the transaction reference from ViaBill data.

        :param str provider_code: The code of the provider handling the transaction.
        :param dict payment_data: The payment data sent by ViaBill.
        :return: The transaction reference.
        :rtype: str
        """
        if provider_code != 'viabill':
            return super()._extract_reference(provider_code, payment_data)
        # ViaBill sends the reference as 'orderNumber' in callbacks and 'reference' in return URLs.
        return (
            payment_data.get('reference')
            or payment_data.get('orderNumber')
            or payment_data.get('order_number')
        )

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency from ViaBill payment data.

        :param dict payment_data: The payment data sent by ViaBill.
        :return: The amount data dict, or None to skip amount validation.
        :rtype: dict|None
        """
        if self.provider_code != 'viabill':
            return super()._extract_amount_data(payment_data)

        amount_str = payment_data.get('amount')
        currency_code = payment_data.get('currency')

        if not amount_str or not currency_code:
            # ViaBill return URL and internal capture/void flows may not include amount data;
            # skip validation in those cases.
            return None

        try:
            amount = float(amount_str)
        except (TypeError, ValueError):
            return None

        return {
            'amount': amount,
            'currency_code': currency_code.upper(),
        }

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on ViaBill payment data.

        :param dict payment_data: The payment data sent by ViaBill.
        :return: None
        """
        if self.provider_code != 'viabill':
            return super()._apply_updates(payment_data)

        _logger.info(
            "Processing ViaBill payment data for transaction %s:\n%s",
            self.reference, pprint.pformat(payment_data)
        )

        # Update the provider reference if a ViaBill transaction ID is present.
        viabill_transaction_id = payment_data.get('transaction')
        if viabill_transaction_id:
            self.provider_reference = viabill_transaction_id

        # Map the ViaBill status to the Odoo transaction state.
        status = payment_data.get('status', '').upper()

        if not status:
            self._set_error(_("Received ViaBill payment data with missing status."))
        elif status in const.STATUS_MAPPING['pending']:
            self._set_pending()
        elif status in const.STATUS_MAPPING['done']:
            # Determine whether this is a capture child transaction.
            # Capture child transactions are created by Odoo's _capture() method with
            # source_transaction_id pointing to the authorized parent. They must always
            # transition to 'done', not back to 'authorized'.
            is_capture_child = bool(
                self.source_transaction_id
                and self.source_transaction_id.state == 'authorized'
                and self.operation not in ('refund',)
            )
            if is_capture_child or self.operation == 'refund':
                # Capture child or refund: always transition to done.
                self._set_done()
                if self.operation == 'refund':
                    self.env.ref('payment.cron_post_process_payment_tx')._trigger()
            elif self.operation not in ('validation',):
                # Initial authorization (both authorize-only and authorize+capture modes):
                # always set to 'authorized' first. The IPN callback will trigger an
                # immediate auto-capture for authorize+capture mode via Odoo's standard
                # _capture() mechanism, which will then transition to 'done'.
                self._set_authorized()
            else:
                self._set_done()
        elif status in const.STATUS_MAPPING['cancel']:
            self._set_canceled()
        elif status in const.STATUS_MAPPING['error']:
            error_message = payment_data.get(
                'errorMessage', _("ViaBill reported a payment error.")
            )
            self._set_error(error_message)
        else:
            _logger.warning(
                "Received unknown ViaBill payment status '%s' for transaction %s.",
                status, self.reference
            )
            self._set_error(
                _("Received data with unknown ViaBill payment status: %s.", status)
            )

    # === BUSINESS METHODS - ORDER STATE MANAGEMENT === #

    def _set_authorized(self, *, state_message=None, extra_allowed_states=()):
        """Override of `sale` to apply the configured order state after ViaBill authorization.

        The ``sale`` module's ``_set_authorized()`` always confirms the sale order
        (``action_confirm``). We call ``super()`` to let that happen, then adjust the
        resulting order state to match ``viabill_order_state_after_authorize``.
        """
        txs_to_process = super()._set_authorized(
            state_message=state_message,
            extra_allowed_states=extra_allowed_states,
        )
        for tx in txs_to_process.filtered(lambda t: t.provider_code == 'viabill'):
            target_state = tx.provider_id.viabill_order_state_after_authorize
            if not target_state or target_state == 'sale':
                # Default: sale module already confirmed the order — nothing to do.
                continue
            for order in tx.sale_order_ids:
                if target_state == 'draft' and order.state not in ('draft', 'sent'):
                    # Cannot revert a confirmed order to draft via the standard flow;
                    # log a warning and skip.
                    _logger.warning(
                        "ViaBill: cannot revert sale order %s to 'draft' after authorization.",
                        order.name,
                    )
                    continue
                if target_state == 'done' and order.state == 'sale':
                    order.action_lock()
                elif target_state == 'pending' and order.state == 'sale':
                    # Set back to sent (quotation sent) which represents "pending payment".
                    order.with_context(tracking_disable=True).write({'state': 'sent'})
        return txs_to_process

    def _update_source_transaction_state(self):
        """Override of `payment` to apply the configured order state after ViaBill capture.

        Odoo promotes the parent (authorized) transaction to ``done`` via
        ``_update_state()`` directly — bypassing ``_set_done()`` and therefore
        ``_post_process()``.  We hook here instead so that
        ``viabill_order_state_after_capture`` is applied at the exact moment the
        parent transaction reaches ``done``.
        """
        super()._update_source_transaction_state()
        for child_tx in self.filtered(
            lambda t: t.provider_code == 'viabill'
            and t.source_transaction_id
            and t.source_transaction_id.state == 'done'
            and t.operation not in ('refund', 'validation')
        ):
            parent_tx = child_tx.source_transaction_id
            target_state = parent_tx.provider_id.viabill_order_state_after_capture
            if not target_state or target_state == 'sale':
                continue
            for order in parent_tx.sale_order_ids:
                if target_state == 'done' and order.state == 'sale':
                    order.action_lock()
                elif target_state == 'pending' and order.state == 'sale':
                    order.with_context(tracking_disable=True).write({'state': 'sent'})
                elif target_state == 'draft':
                    _logger.warning(
                        "ViaBill: cannot revert sale order %s to 'draft' after capture.",
                        order.name,
                    )

    def _post_process(self):
        """Override of `payment` to apply the configured order state after ViaBill capture.

        This handles the auto-capture path in authorize+capture mode where the
        parent transaction is set to ``done`` via ``_set_done()`` directly (not
        via ``_update_source_transaction_state``).
        """
        super()._post_process()
        if self.provider_code != 'viabill' or self.state != 'done':
            return
        # Only apply to the root (non-child) transaction so we don't process twice.
        if self.source_transaction_id:
            return
        target_state = self.provider_id.viabill_order_state_after_capture
        if not target_state or target_state == 'sale':
            return
        for order in self.sale_order_ids:
            if target_state == 'done' and order.state == 'sale':
                order.action_lock()
            elif target_state == 'pending' and order.state == 'sale':
                order.with_context(tracking_disable=True).write({'state': 'sent'})
            elif target_state == 'draft':
                _logger.warning(
                    "ViaBill: cannot revert sale order %s to 'draft' after capture.",
                    order.name,
                )
