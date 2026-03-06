/** @odoo-module **/
// Copyright 2026 ViaBill A/S
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import { patch } from '@web/core/utils/patch';
import { PaymentForm } from '@payment/interactions/payment_form';

/**
 * ViaBill Payment Form Interaction
 *
 * ViaBill uses a two-step redirect flow:
 *   1. The server renders a hidden form with all checkout parameters.
 *   2. This JS intercepts _processRedirectFlow BEFORE the base code submits the form.
 *   3. It reads the checkout proxy URL and all field values from the rendered form.
 *   4. It POSTs the data to the local Odoo proxy (/payment/viabill/checkout) via fetch().
 *   5. The proxy calls ViaBill's API server-side and returns {"redirect_url": "..."}.
 *   6. This JS redirects window.location.href to the ViaBill gateway URL.
 *
 * By returning early (not calling super), we prevent Odoo's base code from
 * auto-submitting the form directly to the checkout URL.
 */
patch(PaymentForm.prototype, {

    // #=== PAYMENT FLOW ===#

    /**
     * Override to force the ViaBill payment flow to 'redirect'.
     *
     * @override method from @payment/interactions/payment_form
     */
    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'viabill') {
            return super._initiatePaymentFlow(...arguments);
        }
        await super._initiatePaymentFlow(
            providerCode, paymentOptionId, paymentMethodCode, 'redirect'
        );
    },

    /**
     * Override to intercept the ViaBill redirect form and use the AJAX proxy flow.
     *
     * Odoo's base _processRedirectFlow injects the redirect form HTML into the DOM
     * and then auto-submits it. We override this method to intercept BEFORE the
     * form is submitted, read the form fields, POST them to our local proxy, and
     * redirect the browser to the ViaBill gateway URL returned by the proxy.
     *
     * We do NOT call super() so the base auto-submit never happens.
     *
     * @override method from @payment/interactions/payment_form
     */
    async _processRedirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'viabill') {
            return super._processRedirectFlow(...arguments);
        }

        // processingValues.redirect_form_html contains the rendered QWeb template HTML.
        // Inject it into a temporary off-screen container so we can read the field values.
        const tempContainer = document.createElement('div');
        tempContainer.style.display = 'none';
        tempContainer.innerHTML = processingValues.redirect_form_html || '';
        document.body.appendChild(tempContainer);

        const redirectForm = tempContainer.querySelector('form[name="o_payment_redirect_form"]');
        if (!redirectForm) {
            console.error('ViaBill: redirect form not found in rendered HTML.');
            document.body.removeChild(tempContainer);
            return;
        }

        const proxyUrl = redirectForm.getAttribute('action');
        if (!proxyUrl) {
            console.error('ViaBill: redirect form has no action URL.');
            document.body.removeChild(tempContainer);
            return;
        }

        // Serialize the form fields as URL-encoded body for the proxy POST.
        const formData = new FormData(redirectForm);
        const params = [];
        for (const [key, value] of formData.entries()) {
            params.push(encodeURIComponent(key) + '=' + encodeURIComponent(value));
        }
        const body = params.join('&');

        // Clean up the temporary container.
        document.body.removeChild(tempContainer);

        try {
            const response = await fetch(proxyUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                },
                body: body,
            });

            if (!response.ok) {
                console.error('ViaBill: proxy endpoint returned HTTP', response.status);
                return;
            }

            const data = await response.json();

            if (data.redirect_url) {
                // Redirect the customer's browser to the ViaBill hosted gateway.
                window.location.href = data.redirect_url;
            } else {
                const errorMsg = data.error || 'ViaBill checkout failed: no redirect URL returned.';
                console.error('ViaBill:', errorMsg);
            }
        } catch (err) {
            console.error('ViaBill: error calling proxy endpoint:', err);
        }
    },

});
