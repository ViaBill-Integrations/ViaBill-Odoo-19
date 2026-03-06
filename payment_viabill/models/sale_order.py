# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    viabill_done_transaction_ids = fields.Many2many(
        comodel_name='payment.transaction',
        string="Done ViaBill Transactions",
        compute='_compute_viabill_done_transaction_ids',
    )

    @api.depends(
        'transaction_ids.state',
        'transaction_ids.provider_code',
        'transaction_ids.child_transaction_ids.state',
        'transaction_ids.child_transaction_ids.operation',
    )
    def _compute_viabill_done_transaction_ids(self):
        """Compute the set of ViaBill transactions that have been captured (state == 'done')
        and still have a refundable balance (i.e. not fully refunded yet).

        This field controls the visibility of the "Refund Transaction" button in the
        sale order header. The button is hidden once the full captured amount has been
        refunded.
        """
        for order in self:
            refundable = self.env['payment.transaction']
            for tx in order.transaction_ids.filtered(
                lambda t: t.provider_code == 'viabill' and t.state == 'done'
            ):
                refunded = sum(
                    abs(c.amount)
                    for c in tx.child_transaction_ids
                    if c.operation == 'refund' and c.state == 'done'
                )
                if refunded < abs(tx.amount):
                    refundable |= tx
            order.viabill_done_transaction_ids = refundable

    def payment_action_viabill_refund(self):
        """Open the ViaBill refund wizard for the first refundable captured transaction.

        The wizard lets the merchant specify a partial or full refund amount before
        the request is submitted to the ViaBill API.
        """
        self.ensure_one()
        done_txs = self.viabill_done_transaction_ids
        if not done_txs:
            raise UserError(_("There are no captured ViaBill transactions to refund."))
        tx = done_txs[0]
        return {
            'name': _('Refund ViaBill Transaction'),
            'type': 'ir.actions.act_window',
            'res_model': 'viabill.refund.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_id': tx.id,
                'active_model': 'payment.transaction',
            },
        }
