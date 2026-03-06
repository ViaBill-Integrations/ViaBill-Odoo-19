/** @odoo-module **/
// Copyright 2026 ViaBill A/S
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

/**
 * ViaBill Provider Form — Backend JS
 *
 * Handles the Login and Register button clicks on the ViaBill tab of the
 * payment provider configuration form.
 *
 * Strategy: We use event delegation on the document body so that the handlers
 * work even after Odoo re-renders the form view (e.g. after saving). The
 * provider ID is read from the URL path, which is stable for the lifetime of
 * the form view.
 *
 * On success the page is reloaded so the newly saved credentials are reflected
 * in the form fields.
 */

/** @odoo-module **/

import { onMounted } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

// ── Helpers ──────────────────────────────────────────────────────────────────

function showAuthMessage(message, type) {
    const el = document.getElementById('viabill_auth_message');
    if (!el) return;
    el.style.display = message ? 'block' : 'none';
    el.className = 'alert alert-' + type + ' mt-3';
    el.textContent = message;
}

function getProviderIdFromUrl() {
    const match = window.location.pathname.match(/\/(\d+)(?:\/|$)/);
    return match ? parseInt(match[1], 10) : null;
}

function jsonRpc(url, params) {
    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({
            jsonrpc: '2.0',
            method: 'call',
            id: Math.floor(Math.random() * 1e9),
            params: params,
        }),
    })
    .then(r => r.json())
    .then(resp => {
        if (resp.error) {
            const errData = resp.error.data || {};
            throw new Error(errData.message || resp.error.message || 'RPC error');
        }
        return resp.result;
    });
}

// ── Login handler ─────────────────────────────────────────────────────────────

async function handleLogin(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const btn = document.getElementById('viabill_btn_login');
    const email = (document.getElementById('viabill_login_email') || {}).value || '';
    const password = (document.getElementById('viabill_login_password') || {}).value || '';

    if (!email || !password) {
        showAuthMessage('Please enter your ViaBill email and password.', 'danger');
        return;
    }

    const providerId = getProviderIdFromUrl();
    if (!providerId) {
        showAuthMessage('Please save the provider record first, then try again.', 'danger');
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Connecting…'; }
    showAuthMessage('', 'info');

    try {
        const result = await jsonRpc('/payment/viabill/login', {
            provider_id: providerId,
            email,
            password,
        });
        if (result && result.success) {
            showAuthMessage('Login successful! Credentials saved. Reloading…', 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            showAuthMessage((result && result.error) || 'Login failed. Please try again.', 'danger');
            if (btn) { btn.disabled = false; btn.textContent = 'Connect with ViaBill'; }
        }
    } catch (err) {
        showAuthMessage('Network error: ' + String(err), 'danger');
        if (btn) { btn.disabled = false; btn.textContent = 'Connect with ViaBill'; }
    }
}

// ── Register handler ──────────────────────────────────────────────────────────

async function handleRegister(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const btn = document.getElementById('viabill_btn_register');
    const email   = (document.getElementById('viabill_reg_email')   || {}).value || '';
    const name    = (document.getElementById('viabill_reg_name')    || {}).value || '';
    const url     = (document.getElementById('viabill_reg_url')     || {}).value || '';
    const country = (document.getElementById('viabill_reg_country') || {}).value || '';
    const taxId   = (document.getElementById('viabill_reg_tax_id')  || {}).value || '';
    const phone   = (document.getElementById('viabill_reg_phone')   || {}).value || '';

    if (!email || !name || !url || !country) {
        showAuthMessage('Please fill in all required fields (Email, Store Name, URL, Country).', 'danger');
        return;
    }
    if (!url.startsWith('https://')) {
        showAuthMessage('Live Shop URL must start with https://', 'danger');
        return;
    }
    if (country.length !== 2) {
        showAuthMessage('Country must be a 2-letter ISO code (e.g. DK, DE, US).', 'danger');
        return;
    }

    const providerId = getProviderIdFromUrl();
    if (!providerId) {
        showAuthMessage('Please save the provider record first, then try again.', 'danger');
        return;
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Creating account…'; }
    showAuthMessage('', 'info');

    try {
        const result = await jsonRpc('/payment/viabill/register', {
            provider_id: providerId,
            email, name, url,
            country: country.toUpperCase(),
            tax_id: taxId || null,
            phone: phone || null,
        });
        if (result && result.success) {
            showAuthMessage('Account created! Credentials saved. Reloading…', 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            showAuthMessage((result && result.error) || 'Registration failed. Please try again.', 'danger');
            if (btn) { btn.disabled = false; btn.textContent = 'Create ViaBill Account'; }
        }
    } catch (err) {
        showAuthMessage('Network error: ' + String(err), 'danger');
        if (btn) { btn.disabled = false; btn.textContent = 'Create ViaBill Account'; }
    }
}

// ── Attach via event delegation on document ───────────────────────────────────

document.addEventListener('click', function(event) {
    const target = event.target;
    if (!target) return;
    if (target.id === 'viabill_btn_login' || target.closest('#viabill_btn_login')) {
        handleLogin(event);
    } else if (target.id === 'viabill_btn_register' || target.closest('#viabill_btn_register')) {
        handleRegister(event);
    }
}, true); // ← capture phase, fires before Odoo's OWL handlers
