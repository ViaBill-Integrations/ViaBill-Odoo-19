/** @odoo-module **/
// Copyright 2026 ViaBill A/S
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

/**
 * ViaBill PriceTags
 *
 * How it works:
 *   1. The QWeb template `payment_viabill.pricetag_assets` injects a hidden
 *      <div id="o_viabill_pricetag_config"> into every frontend page body.
 *      It carries the ViaBill PriceTag script tag and per-page enable flags.
 *
 *   2. The QWeb template `payment_viabill.pricetag_product` injects a
 *      <div class="viabill-pricetag" data-view="product"> directly after the
 *      product price block on product pages.
 *
 *   3. The QWeb template `payment_viabill.pricetag_cart_checkout` injects
 *      two separate divs after the order total row on both cart and checkout
 *      pages (via website_sale.total):
 *        - #o_viabill_pricetag_cart    with data-view="basket"  (cart page)
 *        - #o_viabill_pricetag_checkout with data-view="payment" (checkout page)
 *      Both have class "o_viabill_cart_only" / "o_viabill_checkout_only" so
 *      this script can show only the correct one on each page.
 *
 *   4. This script:
 *      a. Reads the config element.
 *      b. On the cart page: shows #o_viabill_pricetag_cart, hides checkout div.
 *      c. On the checkout page: shows #o_viabill_pricetag_checkout, hides cart div.
 *      d. Loads the ViaBill PriceTag script asynchronously.
 *
 * Official ViaBill PriceTags API: https://viabill.io/api/pricetag/
 */

(function () {
    'use strict';

    // ── 1. Read the global config element ────────────────────────────────────

    var configEl = document.getElementById('o_viabill_pricetag_config');
    if (!configEl) {
        // PriceTag script not configured or provider disabled — nothing to do.
        return;
    }

    var scriptTag      = configEl.dataset.scriptTag      || '';
    var enableProduct  = configEl.dataset.enableProduct  === '1';
    var enableCart     = configEl.dataset.enableCart     === '1';
    var enableCheckout = configEl.dataset.enableCheckout === '1';

    if (!scriptTag) {
        return;
    }

    // ── 2. Detect the current page type ──────────────────────────────────────

    var path = window.location.pathname;

    var isCheckout = /\/shop\/(checkout|payment|confirm_order)/.test(path);
    var isCart     = !isCheckout && /\/shop\/cart/.test(path);

    // ── 3. Show/hide the cart and checkout pricetag divs ─────────────────────

    var cartEl     = document.getElementById('o_viabill_pricetag_cart');
    var checkoutEl = document.getElementById('o_viabill_pricetag_checkout');

    if (cartEl) {
        // Show on cart page only (if enabled), hide everywhere else.
        cartEl.style.display = (isCart && enableCart) ? '' : 'none';
    }

    if (checkoutEl) {
        // Show on checkout page only (if enabled), hide everywhere else.
        checkoutEl.style.display = (isCheckout && enableCheckout) ? '' : 'none';
    }

    // ── 4. Check whether there is anything to render on this page ────────────

    var hasProductTag  = enableProduct  && document.querySelector('.viabill-pricetag[data-view="product"]');
    var hasCartTag     = isCart     && enableCart     && cartEl     && cartEl.style.display     !== 'none';
    var hasCheckoutTag = isCheckout && enableCheckout && checkoutEl && checkoutEl.style.display !== 'none';

    if (!hasProductTag && !hasCartTag && !hasCheckoutTag) {
        return;
    }

    // ── 5. Load the ViaBill PriceTag script ──────────────────────────────────

    if (document.querySelector('script[data-viabill-pricetag-loaded]')) {
        return; // Already loaded (e.g. by a previous navigation in an SPA).
    }

    var content = scriptTag.trim();

    if (content.toLowerCase().indexOf('<script') === 0) {
        // The stored value is a full <script>...</script> tag.
        // Extract the inner JS and execute it — this preserves the exact
        // async loader pattern that ViaBill provides.
        var match = content.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
        if (match && match[1]) {
            try {
                // eslint-disable-next-line no-new-func
                (new Function(match[1]))();
            } catch (e) {
                console.error('ViaBill PriceTags: failed to execute script tag:', e);
            }
        }
    } else {
        // Plain URL — create a <script src="..."> element.
        var srcUrl = content;
        var srcMatch = content.match(/src=["']([^"']+)["']/i);
        if (srcMatch) {
            srcUrl = srcMatch[1];
        }
        if (srcUrl) {
            var script = document.createElement('script');
            script.type = 'text/javascript';
            script.async = true;
            script.src = srcUrl;
            var first = document.getElementsByTagName('script')[0];
            if (first && first.parentNode) {
                first.parentNode.insertBefore(script, first);
            } else {
                document.head.appendChild(script);
            }
        }
    }

    // Mark as loaded so we don't double-load in SPA navigations.
    var marker = document.createElement('script');
    marker.setAttribute('data-viabill-pricetag-loaded', '1');
    marker.type = 'text/javascript';
    document.head.appendChild(marker);

})();
