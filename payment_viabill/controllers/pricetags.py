# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ViaBillPricetagsController(http.Controller):
    """Controller for ViaBill PriceTags configuration endpoint.

    This endpoint is called by the frontend JS to retrieve the PriceTags
    configuration for the active ViaBill provider.
    """

    _pricetags_config_url = '/payment/viabill/pricetags/config'

    @http.route(
        _pricetags_config_url,
        type='jsonrpc',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def viabill_pricetags_config(self):
        """Return the PriceTags configuration for the active ViaBill provider.

        :return: dict with keys: scriptUrl, enableProduct, enableCart, enableCheckout
        :rtype: dict
        """
        provider = request.env['payment.provider'].sudo().search(
            [('code', '=', 'viabill'), ('state', '!=', 'disabled')],
            limit=1,
        )
        if not provider:
            return {
                'scriptUrl': '',
                'enableProduct': False,
                'enableCart': False,
                'enableCheckout': False,
            }

        return {
            'scriptUrl': provider.viabill_pricetag_script or '',
            'enableProduct': bool(provider.viabill_pricetag_product),
            'enableCart': bool(provider.viabill_pricetag_cart),
            'enableCheckout': bool(provider.viabill_pricetag_checkout),
        }
