# Migration 19.0.3.8.0 — no schema changes.
# This release fixes the order state after capture by hooking into
# _update_source_transaction_state() instead of _post_process(), since Odoo
# promotes the parent transaction to 'done' via _update_state() directly,
# bypassing _post_process().  The Refund Transaction button on the sale order
# now correctly appears after a successful capture.


def migrate(cr, version):
    pass
