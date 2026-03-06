# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Migration 19.0.3.3.0 — Add viabill_api_mode column.

Adds the API Mode selection field (test / production) introduced in v33.
Existing records default to 'test' so that behaviour is unchanged after upgrade.
"""
import logging

_logger = logging.getLogger(__name__)


def _add_column_if_missing(cr, col_name, col_type, default_val):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'payment_provider' AND column_name = %s",
        (col_name,),
    )
    if cr.fetchone():
        _logger.info("ViaBill migration: column %s already exists, skipping.", col_name)
        return
    _logger.info(
        "ViaBill migration: adding column %s %s DEFAULT %s.",
        col_name, col_type, default_val,
    )
    cr.execute(
        f"ALTER TABLE payment_provider ADD COLUMN {col_name} {col_type} DEFAULT {default_val}"
    )


def migrate(cr, version):
    _add_column_if_missing(cr, 'viabill_api_mode', 'VARCHAR', "'test'")
    _logger.info("ViaBill migration 19.0.3.3.0 complete.")
