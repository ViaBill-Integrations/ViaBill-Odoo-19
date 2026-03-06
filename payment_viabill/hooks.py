# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).

import logging

from odoo.addons.payment import setup_provider, reset_payment_provider

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Called after fresh install. setup_provider creates the ViaBill provider record."""
    setup_provider(env, 'viabill')


def uninstall_hook(env):
    """Called before uninstall. Resets the ViaBill provider to the 'none' provider."""
    reset_payment_provider(env, 'viabill')
