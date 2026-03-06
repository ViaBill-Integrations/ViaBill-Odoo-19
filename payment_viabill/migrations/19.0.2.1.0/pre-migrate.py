# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""
Migration 19.0.2.1.0 — Add ViaBill-specific columns to payment_provider.

This script runs BEFORE the ORM loads the updated model definitions, so it
must use raw SQL to add the new columns. The ORM will then find the columns
already present and skip its own ALTER TABLE calls.

New columns added in this version:
  - viabill_transaction_type   VARCHAR (selection: 'authorize' / 'authorize_capture')
  - viabill_pricetag_product   BOOLEAN
  - viabill_pricetag_cart      BOOLEAN
  - viabill_pricetag_checkout  BOOLEAN

Columns that were already present in earlier versions (no action needed):
  - viabill_api_key
  - viabill_secret_key
  - viabill_pricetag_script
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Add new ViaBill columns to payment_provider if they do not already exist."""

    # Each entry: (column_name, SQL type, default_value_sql)
    columns = [
        ('viabill_transaction_type', 'VARCHAR', "'authorize'"),
        ('viabill_pricetag_product', 'BOOLEAN', 'FALSE'),
        ('viabill_pricetag_cart',    'BOOLEAN', 'FALSE'),
        ('viabill_pricetag_checkout','BOOLEAN', 'FALSE'),
    ]

    for col_name, col_type, default_val in columns:
        # Check whether the column already exists to make the migration idempotent.
        cr.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'payment_provider'
              AND column_name = %s
            """,
            (col_name,),
        )
        if cr.fetchone():
            _logger.info(
                "ViaBill migration: column payment_provider.%s already exists, skipping.",
                col_name,
            )
            continue

        _logger.info(
            "ViaBill migration: adding column payment_provider.%s %s DEFAULT %s.",
            col_name, col_type, default_val,
        )
        cr.execute(
            f"""
            ALTER TABLE payment_provider
            ADD COLUMN {col_name} {col_type} DEFAULT {default_val}
            """
        )

    _logger.info("ViaBill migration 19.0.2.1.0 complete.")
