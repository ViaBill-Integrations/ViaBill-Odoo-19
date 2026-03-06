# Migration 19.0.3.9.0 — no schema changes.
# This release fixes the XPath anchors in sale_order_views.xml to use the
# Odoo 19 field name (has_authorized_transaction_ids / payment_action_void button)
# instead of the Odoo 16 field name (authorized_transaction_ids), which caused
# the Refund Transaction button to never appear on the sale order form.


def migrate(cr, version):
    pass
