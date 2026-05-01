from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    crm_inquiry_ids = fields.One2many('crm.inquiry', 'partner_id', string='CRM Inquiries')
    crm_lead_ids = fields.One2many('crm.lead', 'partner_id', string='CRM Leads')
    crm_quotation_ids = fields.One2many('crm.quotation', 'partner_id', string='CRM Quotations')
    crm_sale_order_ids = fields.One2many('crm.sale.order', 'partner_id', string='CRM Sales Orders')
    crm_invoice_ids = fields.One2many('crm.invoice', 'partner_id', string='CRM Invoices')

    crm_inquiry_count = fields.Integer(compute='_compute_crm_counts')
    crm_lead_count = fields.Integer(compute='_compute_crm_counts')
    crm_quotation_count = fields.Integer(compute='_compute_crm_counts')
    crm_sale_order_count = fields.Integer(compute='_compute_crm_counts')
    crm_invoice_count = fields.Integer(compute='_compute_crm_counts')

    crm_outstanding_amount = fields.Monetary(
        string='Outstanding',
        compute='_compute_crm_money',
        currency_field='crm_currency_id',
    )
    crm_total_revenue = fields.Monetary(
        string='Lifetime Revenue',
        compute='_compute_crm_money',
        currency_field='crm_currency_id',
    )
    crm_currency_id = fields.Many2one(
        'res.currency',
        compute='_compute_crm_money',
        string='CRM Currency',
    )

    @api.depends(
        'crm_inquiry_ids', 'crm_lead_ids', 'crm_quotation_ids',
        'crm_sale_order_ids', 'crm_invoice_ids',
    )
    def _compute_crm_counts(self):
        for p in self:
            p.crm_inquiry_count = len(p.crm_inquiry_ids)
            p.crm_lead_count = len(p.crm_lead_ids)
            p.crm_quotation_count = len(p.crm_quotation_ids)
            p.crm_sale_order_count = len(p.crm_sale_order_ids)
            p.crm_invoice_count = len(p.crm_invoice_ids)

    @api.depends('crm_invoice_ids.amount_residual', 'crm_invoice_ids.state', 'crm_invoice_ids.amount_total')
    def _compute_crm_money(self):
        currency = (self.env.ref('base.INR', raise_if_not_found=False)
                    or self.env.company.currency_id)
        for p in self:
            invoices = p.crm_invoice_ids
            unpaid = invoices.filtered(
                lambda i: i.state == 'posted' and i.payment_state != 'paid'
            )
            paid = invoices.filtered(lambda i: i.state == 'paid')
            p.crm_currency_id = currency
            p.crm_outstanding_amount = sum(unpaid.mapped('amount_residual'))
            p.crm_total_revenue = sum(paid.mapped('amount_total'))

    # ------------------------------------------------------------------
    # Drill-downs
    # ------------------------------------------------------------------
    def _open_crm_window(self, model, name, mode='list,form'):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': mode,
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
            'target': 'current',
        }

    def action_view_crm_inquiries(self):
        return self._open_crm_window('crm.inquiry', _('Inquiries'))

    def action_view_crm_leads(self):
        return self._open_crm_window('crm.lead', _('Leads'), mode='kanban,list,form')

    def action_view_crm_quotations(self):
        return self._open_crm_window('crm.quotation', _('Quotations'))

    def action_view_crm_sale_orders(self):
        return self._open_crm_window('crm.sale.order', _('Sales Orders'))

    def action_view_crm_invoices(self):
        return self._open_crm_window('crm.invoice', _('Invoices'))

    def action_view_crm_outstanding(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Outstanding Invoices'),
            'res_model': 'crm.invoice',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.id),
                ('state', '=', 'posted'),
                ('payment_state', '!=', 'paid'),
            ],
            'context': {'default_partner_id': self.id},
            'target': 'current',
        }
