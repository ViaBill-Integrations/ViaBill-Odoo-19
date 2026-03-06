# Migration 19.0.3.7.0 — no schema changes.
# This release adds the sale.order model extension (sale_order.py) with the
# viabill_done_transaction_ids computed field and payment_action_viabill_refund()
# method, plus the corresponding sale_order_views.xml that adds the
# "Refund Transaction" button to the sale order header.


def migrate(cr, version):
    pass
