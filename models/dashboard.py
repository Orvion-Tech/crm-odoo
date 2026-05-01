from datetime import timedelta

from odoo import _, api, fields, models


class CrmDashboard(models.TransientModel):
    _name = 'crm.dashboard'
    _description = 'CRM Dashboard Snapshot'
    _rec_name = 'display_name'

    display_name = fields.Char(default=lambda self: _('Dashboard'))

    open_inquiries = fields.Integer()
    active_leads = fields.Integer()
    quotations_sent = fields.Integer()
    quotations_expiring = fields.Integer()
    open_orders = fields.Integer()
    open_orders_value = fields.Monetary(currency_field='currency_id')
    unpaid_invoices = fields.Integer()
    unpaid_amount = fields.Monetary(currency_field='currency_id')
    overdue_invoices = fields.Integer()
    overdue_amount = fields.Monetary(currency_field='currency_id')
    revenue_month = fields.Monetary(currency_field='currency_id')
    won_this_month = fields.Integer()
    currency_id = fields.Many2one('res.currency')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        in_7_days = today + timedelta(days=7)
        currency = (self.env.ref('base.INR', raise_if_not_found=False)
                    or self.env.company.currency_id)

        Inq = self.env['crm.inquiry']
        Lead = self.env['crm.lead']
        Q = self.env['crm.quotation']
        SO = self.env['crm.sale.order']
        Inv = self.env['crm.invoice']

        open_orders = SO.search([('state', 'in', ('confirmed', 'in_progress', 'delivered'))])
        unpaid = Inv.search([('state', '=', 'posted'), ('payment_state', '!=', 'paid')])
        overdue = unpaid.filtered('is_overdue')
        paid_month = Inv.search([('state', '=', 'paid'), ('posted_date', '>=', month_start)])

        res.update({
            'currency_id': currency.id,
            'open_inquiries': Inq.search_count([('state', 'in', ('new', 'in_progress'))]),
            'active_leads': Lead.search_count([('is_won', '=', False), ('is_lost', '=', False)]),
            'quotations_sent': Q.search_count([('state', '=', 'sent')]),
            'quotations_expiring': Q.search_count([
                ('state', '=', 'sent'),
                ('validity_date', '>=', today),
                ('validity_date', '<=', in_7_days),
            ]),
            'open_orders': len(open_orders),
            'open_orders_value': sum(open_orders.mapped('amount_total')),
            'unpaid_invoices': len(unpaid),
            'unpaid_amount': sum(unpaid.mapped('amount_residual')),
            'overdue_invoices': len(overdue),
            'overdue_amount': sum(overdue.mapped('amount_residual')),
            'revenue_month': sum(paid_month.mapped('amount_total')),
            'won_this_month': Lead.search_count([
                ('is_won', '=', True), ('date', '>=', month_start),
            ]),
        })
        return res

    @api.model
    def action_open_dashboard(self):
        rec = self.create({})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dashboard'),
            'res_model': 'crm.dashboard',
            'res_id': rec.id,
            'view_mode': 'form',
            'view_id': self.env.ref('crm_custom.view_crm_dashboard_form').id,
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Drill-downs
    # ------------------------------------------------------------------
    def _open_window(self, model, name, domain, mode):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': mode,
            'domain': domain,
            'target': 'current',
        }

    def action_open_inquiries(self):
        return self._open_window(
            'crm.inquiry', _('Open Inquiries'),
            [('state', 'in', ('new', 'in_progress'))], 'list,form,kanban',
        )

    def action_open_leads(self):
        return self._open_window(
            'crm.lead', _('Active Leads'),
            [('is_won', '=', False), ('is_lost', '=', False)], 'kanban,list,form',
        )

    def action_open_quotations_sent(self):
        return self._open_window(
            'crm.quotation', _('Sent Quotations'),
            [('state', '=', 'sent')], 'list,form,kanban',
        )

    def action_open_quotations_expiring(self):
        today = fields.Date.context_today(self)
        in_7_days = today + timedelta(days=7)
        return self._open_window(
            'crm.quotation', _('Quotations Expiring Soon'),
            [
                ('state', '=', 'sent'),
                ('validity_date', '>=', today),
                ('validity_date', '<=', in_7_days),
            ],
            'list,form,kanban',
        )

    def action_open_orders(self):
        return self._open_window(
            'crm.sale.order', _('Open Sales Orders'),
            [('state', 'in', ('confirmed', 'in_progress', 'delivered'))],
            'list,form,kanban',
        )

    def action_open_unpaid_invoices(self):
        return self._open_window(
            'crm.invoice', _('Unpaid Invoices'),
            [('state', '=', 'posted'), ('payment_state', '!=', 'paid')],
            'list,form,kanban',
        )

    def action_open_overdue_invoices(self):
        return self._open_window(
            'crm.invoice', _('Overdue Invoices'),
            [('is_overdue', '=', True)],
            'list,form,kanban',
        )

    def action_open_revenue_month(self):
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        return self._open_window(
            'crm.invoice', _('Paid This Month'),
            [('state', '=', 'paid'), ('posted_date', '>=', month_start)],
            'list,form,kanban',
        )
