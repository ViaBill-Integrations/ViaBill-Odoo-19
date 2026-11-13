# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import ast
import functools
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from urllib.parse import quote, urlencode

import requests
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import html_sanitize

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_viabill import const

_logger = get_payment_logger(__name__, sensitive_keys=const.SENSITIVE_KEYS)

# User-Agent sent with all ViaBill API requests.
_VIABILL_USER_AGENT = 'ViaBill-Odoo/1.0'


@functools.lru_cache(maxsize=None)
def _viabill_read_po_file(filename):
    """Return the translations of one of the module's i18n/*.po files.

    Only plain msgid/msgstr entries are read (with continuation lines), which
    is all the legal note needs.

    :param str filename: The .po file name, e.g. 'da.po'.
    :return: The translations, keyed by source text.
    :rtype: dict
    """
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'i18n', filename)
    translations = {}
    if not os.path.isfile(path):
        return translations

    entry, key = {}, None
    with open(path, encoding='utf-8') as po_file:
        for line in list(po_file) + ['']:
            line = line.strip()
            if line.startswith(('msgid ', 'msgstr ')):
                key, _space, value = line.partition(' ')
                if key == 'msgid' and entry.get('msgid') and entry.get('msgstr'):
                    translations[entry['msgid']] = entry['msgstr']
                if key == 'msgid':
                    entry = {}
                entry[key] = ast.literal_eval(value)
            elif line.startswith('"') and key:
                entry[key] += ast.literal_eval(line)
            elif not line.startswith('"'):
                key = None
    if entry.get('msgid') and entry.get('msgstr'):
        translations[entry['msgid']] = entry['msgstr']
    return translations


def _viabill_sanitize_font_size(value):
    """Return a valid CSS font size, or an empty string.

    A plain number is taken as pixels.

    :param str value: The value entered by the merchant, e.g. '14px' or '0.9em'.
    :rtype: str
    """
    value = (value or '').strip().lower().replace(',', '.')
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*(px|em|rem|pt|%)?', value)
    if not match or float(match.group(1)) <= 0:
        return ''
    return match.group(1) + (match.group(2) or 'px')


def _viabill_sanitize_margin(value):
    """Return a valid CSS margin, or an empty string.

    A plain number is taken as pixels. Negative values are allowed, e.g. to
    move the legal note closer to the payment method name.

    :param str value: The value entered by the merchant, e.g. '4px' or '-12'.
    :rtype: str
    """
    value = (value or '').strip().lower().replace(',', '.')
    match = re.fullmatch(r'(-?)([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*(px|em|rem|pt|%)?', value)
    if not match:
        return ''
    if float(match.group(2)) == 0:
        return '0px'
    return match.group(1) + match.group(2) + (match.group(3) or 'px')


def _viabill_sanitize_color(value):
    """Return a valid CSS color (hex or a color name), or an empty string.

    :param str value: The value entered by the merchant, e.g. '#cc0000'.
    :rtype: str
    """
    value = (value or '').strip()
    if re.fullmatch(r'#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|[a-zA-Z]{3,20}', value):
        return value
    return ''


def _viabill_sanitize_extra_css(css):
    """Return the extra CSS declarations in a form that is safe to print inside
    a style attribute.

    Only declarations (e.g. "padding: 8px; border: 1px solid #ccc") are
    supported, so anything that could open a new rule or a tag is removed.

    :param str css: The value entered by the merchant.
    :rtype: str
    """
    css = css or ''
    css = re.sub(r'<[^>]*>', '', css)
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'[{}<>\\]', '', css)
    css = re.sub(r'expression\s*\(|javascript\s*:|@import', '', css, flags=re.I)
    css = re.sub(r'\s+', ' ', css)
    return css.strip(' ;')


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('viabill', "ViaBill")],
        ondelete={'viabill': 'set default'},
    )

    #  ViaBill-specific credential fields ────────────────────────────────────

    viabill_api_key = fields.Char(
        string="API Key",
        help=(
            "The API key for your ViaBill merchant account. "
            "You can find this value in your ViaBill merchant account."
        ),
        copy=False,
    )
    viabill_secret_key = fields.Char(
        string="Secret Key",
        help=(
            "The secret key for your ViaBill merchant account, used to sign API "
            "requests. You can find this value in your ViaBill merchant account."
        ),
        copy=False,
        groups='base.group_system',
    )
    viabill_pricetag_script = fields.Text(
        string="PriceTag Script",
        help=(
            "The ViaBill PriceTag script snippet from your ViaBill merchant account. "
            "Paste the full <script>…</script> HTML as provided by ViaBill "
            "(raw inline JavaScript without the tags is also accepted). "
            "This script is injected into every frontend page to enable the PriceTag widget."
        ),
        copy=False,
    )

    #  API mode (Test vs Production) ────────────────────────────────────────

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

    #  Transaction type ──────────────────────────────────────────────────────

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

    #  PriceTag display toggles ──────────────────────────────────────────────

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

    #  PriceTag CSS selectors ────────────────────────────────────────────────

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

    #  Legal note (credit warning) at the checkout ───────────────────────────
    #  It is always shown, with the default text; only its look can be changed.

    viabill_legal_note_language = fields.Selection(
        selection=[
            ('auto', "Auto"),
            ('en', "English"),
            ('da', "Danish"),
            ('es', "Spanish"),
        ],
        string="Legal Note Language",
        default='auto',
        required=True,
        help='Language of the default legal note. "Auto" uses the language of the customer.',
    )
    viabill_legal_note_default = fields.Char(
        string="Legal Note Text",
        compute='_compute_viabill_legal_note_default',
        help="The legal note shown at checkout, in the language selected below.",
    )

    # Styling of the legal note, see _viabill_get_legal_note_style().
    viabill_legal_note_font_size = fields.Char(
        string="Legal Note Font Size",
        help=(
            "Font size of the legal note, e.g. 14px, 0.9em or 90%. "
            "Leave empty to use the font size of your theme."
        ),
    )
    viabill_legal_note_font_style = fields.Selection(
        selection=[
            ('normal', "Normal"),
            ('bold', "Bold"),
            ('italic', "Italic"),
            ('bold-italic', "Bold italic"),
        ],
        string="Legal Note Font Style",
        # The same as the payment method name.
        default='normal',
        required=True,
    )
    viabill_legal_note_color = fields.Char(
        string="Legal Note Text Color",
        help=(
            "Color code of the legal note text, e.g. #cc0000. "
            "Leave empty to use the text color of your theme."
        ),
    )
    viabill_legal_note_align = fields.Selection(
        selection=[
            ('default', "Theme default"),
            ('left', "Left"),
            ('center', "Center"),
            ('right', "Right"),
        ],
        string="Legal Note Text Alignment",
        default='default',
        required=True,
    )
    viabill_legal_note_extra_css = fields.Text(
        string="Legal Note Extra CSS",
        help=(
            "Optional CSS declarations applied to the legal note box, separated by "
            "semicolons (no selectors or braces), e.g. padding: 8px 12px; "
            "background-color: #fff8e1; border-left: 3px solid #f0b849; They are "
            "applied after the options above, so they can also override them."
        ),
    )
    # The defaults keep the spacing the legal note always had at the checkout
    # (0.5em above and below); they are positive, so safe for every theme.
    viabill_legal_note_margin_top = fields.Char(
        string="Legal Note Margin Top",
        default='0.5em',
        help=(
            "Space above the legal note, e.g. 4px or 0.5em. "
            "A negative value moves it closer to the payment method name."
        ),
    )
    viabill_legal_note_margin_bottom = fields.Char(
        string="Legal Note Margin Bottom",
        default='0.5em',
        help="Space below the legal note, e.g. 4px or 0.5em.",
    )
    viabill_legal_note_margin_left = fields.Char(
        string="Legal Note Margin Left",
        default='0px',
        help="Space to the left of the legal note, e.g. 4px. A negative value moves it to the left.",
    )

    #  Order state after authorize / capture ─────────────────────────────────

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

    #  Debug / Troubleshooting ───────────────────────────────────────────────

    viabill_enable_debug = fields.Boolean(
        string="Enable Debug",
        default=False,
        groups='base.group_system',
        help=(
            "When enabled, detailed ViaBill API request/response information is "
            "stored in the Debug Log below. Disable in production to avoid storing "
            "sensitive data. The log is capped at 50 entries."
        ),
    )

    viabill_debug_log = fields.Text(
        string="Debug Log",
        readonly=True,
        groups='base.group_system',
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
    # PRICETAG CUSTOM CODE (frontend)
    # =========================================================================

    def viabill_get_custom_css_markup(self):
        """Return the custom PriceTag CSS for the <style> element of the frontend.

        The value is printed as is (so CSS such as child combinators keeps
        working), but "<" is removed, so it can never close the <style> element.

        :return: The CSS.
        :rtype: markupsafe.Markup
        """
        self.ensure_one()
        css = (self.sudo().viabill_pricetag_custom_css or '').replace('<', '')
        return Markup(css)

    def viabill_get_custom_js_markup(self):
        """Return the custom PriceTag JavaScript for the <script> element of the frontend.

        The value is printed as is, but "</" is written as "<\\/" (the same in
        JavaScript), so it can never close the <script> element.

        :return: The JavaScript.
        :rtype: markupsafe.Markup
        """
        self.ensure_one()
        js = (self.sudo().viabill_pricetag_custom_js or '').replace('</', '<\\/')
        return Markup(js)

    # =========================================================================
    # LEGAL NOTE (frontend)
    # =========================================================================

    @api.depends('viabill_legal_note_language')
    @api.depends_context('lang')
    def _compute_viabill_legal_note_default(self):
        for provider in self:
            provider.viabill_legal_note_default = provider._viabill_get_legal_note_default_text(
                provider.viabill_legal_note_language
            )

    def _viabill_get_legal_note(self):
        """Return the legal note (credit warning) to show at the checkout: the
        default text in the configured language.

        :return: The sanitized HTML.
        :rtype: markupsafe.Markup
        """
        self.ensure_one()
        provider = self.sudo()
        text = provider._viabill_get_legal_note_default_text(provider.viabill_legal_note_language)
        return Markup(html_sanitize(text))

    @api.constrains(
        'viabill_legal_note_font_size',
        'viabill_legal_note_color',
        'viabill_legal_note_margin_top',
        'viabill_legal_note_margin_bottom',
        'viabill_legal_note_margin_left',
    )
    def _check_viabill_legal_note_style(self):
        for provider in self:
            font_size = (provider.viabill_legal_note_font_size or '').strip()
            if font_size and not _viabill_sanitize_font_size(font_size):
                raise ValidationError(_(
                    "The font size of the legal note is not valid. "
                    "Use a value such as 14px, 0.9em or 90%."
                ))
            color = (provider.viabill_legal_note_color or '').strip()
            if color and not _viabill_sanitize_color(color):
                raise ValidationError(_(
                    "The text color of the legal note is not valid. "
                    "Use a color code such as #cc0000."
                ))
            for margin in (
                provider.viabill_legal_note_margin_top,
                provider.viabill_legal_note_margin_bottom,
                provider.viabill_legal_note_margin_left,
            ):
                margin = (margin or '').strip()
                if margin and not _viabill_sanitize_margin(margin):
                    raise ValidationError(_(
                        "The margin of the legal note is not valid. "
                        "Use a value such as 4px, -12px or 0.5em."
                    ))

    def _viabill_get_legal_note_class(self):
        """Return the CSS classes of the legal note.

        Without a text color of its own, the legal note uses the muted text
        color of the theme, like the "Secured by" line of the payment form.
        Bootstrap's text-muted class overrides inline colors, so it is only
        added when no text color is set.

        :rtype: str
        """
        self.ensure_one()
        if _viabill_sanitize_color(self.sudo().viabill_legal_note_color):
            return 'viabill-legal-note'
        return 'viabill-legal-note text-muted'

    def _viabill_get_legal_note_style(self):
        """Return the inline style of the legal note, built from the styling
        settings, e.g. "font-weight:600;color:#cc0000".

        :return: The CSS declarations. Not escaped: QWeb escapes the attribute.
        :rtype: str
        """
        self.ensure_one()
        provider = self.sudo()
        declarations = []

        font_size = _viabill_sanitize_font_size(provider.viabill_legal_note_font_size)
        if font_size:
            declarations.append('font-size:' + font_size)

        font_style = provider.viabill_legal_note_font_style or 'normal'
        declarations.append('font-weight:' + ('600' if 'bold' in font_style else 'normal'))
        declarations.append('font-style:' + ('italic' if 'italic' in font_style else 'normal'))

        color = _viabill_sanitize_color(provider.viabill_legal_note_color)
        if color:
            declarations.append('color:' + color)

        if provider.viabill_legal_note_align in ('left', 'center', 'right'):
            declarations.append('text-align:' + provider.viabill_legal_note_align)

        # An empty or invalid margin means the default of that field.
        for side in ('top', 'bottom', 'left'):
            name = 'viabill_legal_note_margin_' + side
            margin = _viabill_sanitize_margin(provider[name]) or self._fields[name].default(self)
            declarations.append('margin-' + side + ':' + margin)

        # The extra CSS comes last, so it can override the controls above.
        extra_css = _viabill_sanitize_extra_css(provider.viabill_legal_note_extra_css)
        if extra_css:
            declarations.append(extra_css)

        return ';'.join(declarations)

    def _viabill_get_legal_note_default_text(self, language='auto'):
        """Return the default legal note in the given language.

        "auto" uses the language of the current context. The module's own
        .po file is read directly, so the language does not have to be
        installed; other languages are translated through Odoo as usual.

        :param str language: 'auto', 'en', 'da' or 'es'.
        :return: The default legal note.
        :rtype: str
        """
        if language == 'auto' or not language:
            language = (self.env.lang or 'en_US')[:2]
        if language == 'en':
            return const.LEGAL_NOTE_DEFAULT_TEXT
        po_file = const.LEGAL_NOTE_PO_FILES.get(language)
        if po_file:
            translation = _viabill_read_po_file(po_file).get(const.LEGAL_NOTE_DEFAULT_TEXT)
            if translation:
                return translation
        return _("Warning! Borrow responsibly - it costs money.")

    # =========================================================================
    # SIGNUP
    # =========================================================================

    def _viabill_get_signup_url(self):
        """Return the ViaBill online signup form URL for this shop.

        Note: self.ensure_one()

        :return: The signup URL.
        :rtype: str
        """
        self.ensure_one()
        params = {
            'utm_source': 'odoo',
            'utm_medium': 'plugin',
            'utm_campaign': 'merchant_signup',
            'shop_url': (self.get_base_url() or '').rstrip('/'),
        }
        return const.VIABILL_SIGNUP_URL + '?' + urlencode(params, quote_via=quote)

    def action_viabill_open_signup(self):
        """Open the ViaBill online signup form in a new browser tab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self._viabill_get_signup_url(),
            'target': 'new',
        }

    # =========================================================================
    # AVAILABILITY
    # =========================================================================

    @api.model
    def _get_compatible_providers(self, *args, **kwargs):
        """Override of `payment` to hide ViaBill until it is configured.

        The ViaBill payment option is not offered at checkout until the
        merchant has entered the ViaBill account credentials (API key and
        secret key) in the provider configuration.
        """
        providers = super()._get_compatible_providers(*args, **kwargs)
        return providers.filtered(
            lambda p: p.code != 'viabill'
            or (p.sudo().viabill_api_key and p.sudo().viabill_secret_key)
        )

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
        provider = self.sudo()
        if not provider.viabill_enable_debug:
            return
        # The secret key is never part of the messages; mask it anyway, should
        # it ever be echoed back (e.g. in an API response).
        if provider.viabill_secret_key:
            message = message.replace(provider.viabill_secret_key, '***')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = '[{}] {}'.format(timestamp, message)
        existing = (provider.viabill_debug_log or '').strip()
        lines = existing.split('\n') if existing else []
        lines.append(entry)
        # Keep only the last 50 entries
        if len(lines) > 50:
            lines = lines[-50:]
        provider.write({'viabill_debug_log': '\n'.join(lines)})

    @api.model
    def _viabill_mask_payload(self, payload):
        """Return a copy of an API payload that is safe to log or store.

        The API key, signatures/checksums and secrets are replaced by '***'.

        :param dict payload: The API payload.
        :return: The masked payload.
        :rtype: dict
        """
        masked_keys = set(const.SENSITIVE_KEYS) | {'signature', 'sha256check'}
        return {
            key: ('***' if key in masked_keys and value else value)
            for key, value in (payload or {}).items()
        }

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
        self, api_key, amount, currency, transaction, order_number, success_url, cancel_url, is_test
    ):
        """Generate the SHA-256 signature for a ViaBill checkout request.

        Signature format:
            if is_test:
                sha256({apikey}#{amount}#{currency}#{transaction}#{order_number}#{success_url}#{cancel_url}#{secret})
            else:
                sha256({apikey}#{amount}#{currency}#{transaction}#{order_number}#{success_url}#{cancel_url}#{secret})#true

        Note: self.ensure_one()

        :param str api_key: The ViaBill API key.
        :param str amount: The payment amount as a string (e.g. "100.00").
        :param str currency: The ISO 4217 currency code (e.g. "DKK").
        :param str transaction: The Odoo transaction reference.
        :param str order_number: The Odoo order number (same as transaction).
        :param str success_url: The full success redirect URL.
        :param str cancel_url: The full cancel redirect URL.
        :return: The hex SHA-256 signature string.
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

        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _viabill_generate_capture_signature(self, amount, currency, transaction):
        """Generate the SHA-256 signature for a ViaBill capture or refund request.

        Signature format:
        ``sha256({id}#{apikey}#{amount}#{currency}#{secret})``

        Note: self.ensure_one()

        :param str amount: The capture/refund amount as a string (negative, e.g. "-100.00").
        :param str currency: The ISO 4217 currency code.
        :param str transaction: The ViaBill transaction ID (provider_reference).
        :return: The hex SHA-256 signature string.
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

        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _viabill_generate_cancel_signature(self, transaction):
        """Generate the SHA-256 signature for a ViaBill cancel (void) request.

        Signature format:
        ``sha256({id}#{apikey}#{secret})``

        Note: self.ensure_one()

        :param str transaction: The ViaBill transaction ID (provider_reference).
        :return: The hex SHA-256 signature string.
        :rtype: str
        """
        self.ensure_one()

        raw = const.CANCEL_SIGNATURE_FORMAT.format(
            id=transaction,
            apikey=self.viabill_api_key or '',
            secret=self.viabill_secret_key or '',
        )

        return hashlib.sha256(raw.encode('utf-8')).hexdigest()


    def _viabill_verify_callback_signature(
        self, transaction, order_number, amount, currency, status, time, received_signature
    ):
        """Verify the SHA-256 signature received in a ViaBill IPN callback notification.

        Expected signature format:
        ``sha256({transaction}#{orderNumber}#{amount}#{currency}#{status}#{time}#{secret})``

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

        secret = self.sudo().viabill_secret_key or ''
        # Without a configured secret the signature would be computable by anyone.
        if not secret or not isinstance(received_signature, str) or not received_signature:
            return False

        raw = const.CALLBACK_SIGNATURE_FORMAT.format(
            transaction=transaction,
            order_number=order_number,
            amount=amount,
            currency=currency,
            status=status,
            time=time,
            secret=secret,
        )

        expected = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        # Constant-time comparison.
        return hmac.compare_digest(expected, received_signature.strip().lower())

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
                json.dumps(self._viabill_mask_payload(payload)),
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
    # DEBUG LOG ACTIONS
    # =========================================================================

    def action_viabill_clear_debug_log(self):
        """Clear the ViaBill debug log (system administrators only)."""
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_("Only administrators can clear the ViaBill debug log."))
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
