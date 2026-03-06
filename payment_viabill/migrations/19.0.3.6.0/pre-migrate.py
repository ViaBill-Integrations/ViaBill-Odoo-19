# Migration 19.0.3.6.0 — no schema changes.
# This release wires up viabill_order_state_after_authorize and
# viabill_order_state_after_capture to actually affect the sale order state
# after authorization and capture respectively.


def migrate(cr, version):
    pass
