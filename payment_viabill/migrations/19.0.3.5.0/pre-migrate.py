# Migration 19.0.3.5.0 — no schema changes.
# This release fixes:
#   1. Debug log now records all API calls (checkout proxy + capture/void/refund).
#   2. Refund Transaction button added to the payment.transaction form header.
#   3. Authorize+capture mode: IPN callback now correctly calls _capture() after
#      setting the transaction to 'authorized', using the same capture API path
#      as the manual Capture Transaction button.
#   4. Void Transaction: clearer error message when ViaBill reports the transaction
#      is already captured (TEST_CAPTURED / CAPTURED state).


def migrate(cr, version):
    pass
