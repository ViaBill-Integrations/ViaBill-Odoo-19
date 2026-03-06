# Copyright 2026 ViaBill A/S
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ViabillRefundWizard(models.TransientModel):
    """Wizard that lets the merchant specify a (partial) refund amount for a
    captured ViaBill transaction before submitting it to the ViaBill API."""

    _name = 'viabill.refund.wizard'
    _description = "ViaBill Refund Wizard"

    # The captured (done) ViaBill transaction to refund.
    transaction_id = fields.Many2one(
        comodel_name='payment.transaction',
        string="Transaction",
        default=lambda self: self._default_transaction_id(),
        readonly=True,
        required=True,
    )
    currency_id = fields.Many2one(
        related='transaction_id.currency_id',
        readonly=True,
    )
    captured_amount = fields.Monetary(
        string="Captured Amount",
        compute='_compute_captured_amount',
        readonly=True,
    )
    already_refunded_amount = fields.Monetary(
        string="Already Refunded",
        compute='_compute_already_refunded_amount',
        readonly=True,
    )
    available_amount = fields.Monetary(
        string="Maximum Refund Allowed",
        compute='_compute_available_amount',
        readonly=True,
    )
    amount_to_refund = fields.Monetary(
        string="Amount to Refund",
        compute='_compute_amount_to_refund',
        store=True,
        readonly=False,
    )
    is_amount_valid = fields.Boolean(
        compute='_compute_is_amount_valid',
    )

    # === DEFAULT === #

    def _default_transaction_id(self):
        """Return the transaction passed via context (active_id)."""
        tx_id = self.env.context.get('active_id')
        if tx_id:
            return self.env['payment.transaction'].browse(tx_id)
        return self.env['payment.transaction']

    # === COMPUTE METHODS === #

    @api.depends('transaction_id')
    def _compute_captured_amount(self):
        for wizard in self:
            tx = wizard.transaction_id
            if not tx:
                wizard.captured_amount = 0.0
                continue
            wizard.captured_amount = abs(tx.amount)

    @api.depends('transaction_id')
    def _compute_already_refunded_amount(self):
        for wizard in self:
            tx = wizard.transaction_id
            if not tx:
                wizard.already_refunded_amount = 0.0
                continue
            refund_children = tx.child_transaction_ids.filtered(
                lambda c: c.operation == 'refund' and c.state == 'done'
            )
            wizard.already_refunded_amount = sum(abs(c.amount) for c in refund_children)

    @api.depends('captured_amount', 'already_refunded_amount')
    def _compute_available_amount(self):
        for wizard in self:
            wizard.available_amount = wizard.captured_amount - wizard.already_refunded_amount

    @api.depends('available_amount')
    def _compute_amount_to_refund(self):
        """Default the refund amount to the full available amount."""
        for wizard in self:
            wizard.amount_to_refund = wizard.available_amount

    @api.depends('amount_to_refund', 'available_amount')
    def _compute_is_amount_valid(self):
        for wizard in self:
            wizard.is_amount_valid = (
                0 < wizard.amount_to_refund <= wizard.available_amount
            )

    # === ACTION === #

    def action_refund(self):
        """Submit the refund request to ViaBill for the specified amount."""
        self.ensure_one()
        if not self.is_amount_valid:
            raise ValidationError(_(
                "The refund amount must be greater than zero and not exceed %(max)s.",
                max=self.available_amount,
            ))
        return self.transaction_id.sudo().action_refund(
            amount_to_refund=self.amount_to_refund
        )
