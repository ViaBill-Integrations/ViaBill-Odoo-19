# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Migration 19.0.2.0.0 — Add new ViaBill columns introduced in v17/v18.

This covers upgrades from any version in the 19.0.1.x range.
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
    _logger.info("ViaBill migration: adding column %s %s DEFAULT %s.", col_name, col_type, default_val)
    cr.execute(f"ALTER TABLE payment_provider ADD COLUMN {col_name} {col_type} DEFAULT {default_val}")


def migrate(cr, version):
    _add_column_if_missing(cr, 'viabill_transaction_type',  'VARCHAR', "'authorize'")
    _add_column_if_missing(cr, 'viabill_pricetag_product',  'BOOLEAN', 'FALSE')
    _add_column_if_missing(cr, 'viabill_pricetag_cart',     'BOOLEAN', 'FALSE')
    _add_column_if_missing(cr, 'viabill_pricetag_checkout', 'BOOLEAN', 'FALSE')
    _logger.info("ViaBill migration 19.0.2.0.0 complete.")
