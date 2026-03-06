# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Migration 19.0.3.4.0 — Fix capture transaction state (no schema changes).

This version fixes a logic bug where the capture child transaction was incorrectly
set to 'authorized' instead of 'done' after a successful manual capture.
No database schema changes are required.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("ViaBill migration 19.0.3.4.0 complete (no schema changes).")
