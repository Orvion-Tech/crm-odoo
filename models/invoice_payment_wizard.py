from odoo import _, api, fields, models
from odoo.exceptions import UserError


class InvoicePaymentWizard(models.TransientModel):
    _name = 'crm.invoice.payment.wizard'
    _description = 'Register Invoice Payment'

    invoice_id = fields.Many2one(
        'crm.invoice', string='Invoice', required=True, readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='invoice_id.currency_id',
        readonly=True,
    )
    amount_residual = fields.Monetary(
        related='invoice_id.amount_residual',
        string='Outstanding',
        readonly=True,
    )
    payment_date = fields.Date(
        string='Payment Date',
        default=fields.Date.context_today,
        required=True,
    )
    amount = fields.Monetary(
        string='Amount',
        currency_field='currency_id',
        required=True,
    )
    method = fields.Selection(
        [
            ('cash', 'Cash'),
            ('cheque', 'Cheque'),
            ('bank', 'Bank Transfer'),
            ('upi', 'UPI / Online'),
            ('card', 'Card'),
            ('other', 'Other'),
        ],
        string='Method',
        default='bank',
        required=True,
    )
    reference = fields.Char(
        string='Reference',
        help='Cheque number, UTR, transaction id, etc.',
    )
    note = fields.Char(string='Note')

    @api.constrains('amount')
    def _check_amount_positive(self):
        for w in self:
            if (w.amount or 0) <= 0:
                raise UserError(_('Payment amount must be greater than zero.'))

    def action_register(self):
        self.ensure_one()
        inv = self.invoice_id
        if inv.state != 'posted':
            raise UserError(_('Only posted invoices can receive payments.'))
        if self.amount > inv.amount_residual + 0.001:
            raise UserError(_(
                'Payment amount (%(paid).2f) is greater than the outstanding balance (%(due).2f).'
            ) % {'paid': self.amount, 'due': inv.amount_residual})
        self.env['crm.invoice.payment'].create({
            'invoice_id': inv.id,
            'payment_date': self.payment_date,
            'amount': self.amount,
            'method': self.method,
            'reference': self.reference,
            'note': self.note,
        })
        return {'type': 'ir.actions.act_window_close'}
