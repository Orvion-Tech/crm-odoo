from datetime import date, timedelta
from collections import OrderedDict
from markupsafe import Markup

from odoo import _, api, fields, models


class CrmDashboard(models.TransientModel):
    _name = 'crm.dashboard'
    _description = 'CRM Dashboard Snapshot'
    _rec_name = 'display_name'

    display_name = fields.Char(default='Dashboard')

    # KPI counters
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

    # Rich blocks (rendered HTML)
    pipeline_funnel_html = fields.Html(sanitize=False, readonly=True)
    revenue_chart_html = fields.Html(sanitize=False, readonly=True)
    top_products_html = fields.Html(sanitize=False, readonly=True)
    recent_leads_html = fields.Html(sanitize=False, readonly=True)
    recent_quotations_html = fields.Html(sanitize=False, readonly=True)
    recent_invoices_html = fields.Html(sanitize=False, readonly=True)
    low_stock_html = fields.Html(sanitize=False, readonly=True)
    my_activities_html = fields.Html(sanitize=False, readonly=True)

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
            'display_name': _('CRM Dashboard'),
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
            'pipeline_funnel_html': self._build_pipeline_funnel(currency),
            'revenue_chart_html': self._build_revenue_chart(currency, today),
            'top_products_html': self._build_top_products(currency),
            'recent_leads_html': self._build_recent_leads(),
            'recent_quotations_html': self._build_recent_quotations(currency),
            'recent_invoices_html': self._build_recent_invoices(currency, today),
            'low_stock_html': self._build_low_stock(),
            'my_activities_html': self._build_my_activities(today),
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
            # 'main' resets the breadcrumb stack so refresh / re-open does not
            # accumulate "Unnamed" entries from previous TransientModel records.
            'target': 'main',
        }

    # ------------------------------------------------------------------
    # HTML block builders
    # ------------------------------------------------------------------
    def _money(self, currency, amount):
        symbol = (currency.symbol if currency else '') or ''
        try:
            return '%s %s' % (symbol, '{:,.2f}'.format(float(amount or 0)))
        except Exception:
            return '%s %s' % (symbol, amount)

    def _empty(self, msg):
        return Markup(
            '<div class="o_crm_dash_empty">%s</div>'
        ) % msg

    def _build_pipeline_funnel(self, currency):
        Lead = self.env['crm.lead']
        Stage = self.env['crm.lead.stage']
        stages = Stage.search([('is_won', '=', False), ('is_lost', '=', False)], order='sequence, id')
        if not stages:
            return self._empty(_('No pipeline stages configured.'))
        rows = []
        max_count = 0
        for st in stages:
            cnt = Lead.search_count([('stage_id', '=', st.id)])
            rows.append((st.name, cnt))
            if cnt > max_count:
                max_count = cnt
        if max_count == 0:
            return self._empty(_('No leads in any open stage.'))
        bars = []
        for name, cnt in rows:
            pct = int(round((cnt / max_count) * 100)) if max_count else 0
            bars.append(
                '<div class="o_crm_dash_bar_row">'
                '<div class="o_crm_dash_bar_label">%s</div>'
                '<div class="o_crm_dash_bar_track">'
                '<div class="o_crm_dash_bar_fill" style="width:%d%%;"></div>'
                '</div>'
                '<div class="o_crm_dash_bar_value">%d</div>'
                '</div>' % (name, pct, cnt)
            )
        return Markup('<div class="o_crm_dash_bars">%s</div>') % Markup(''.join(bars))

    def _build_revenue_chart(self, currency, today):
        Inv = self.env['crm.invoice']
        # Build buckets for the last 6 calendar months including current.
        buckets = OrderedDict()
        cursor = today.replace(day=1)
        months = []
        for i in range(5, -1, -1):
            y = cursor.year
            m = cursor.month - i
            while m <= 0:
                m += 12
                y -= 1
            months.append(date(y, m, 1))
        for d in months:
            buckets[(d.year, d.month)] = 0.0
        # End of last bucket = first of next month
        last = months[-1]
        nxt_y, nxt_m = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
        end = date(nxt_y, nxt_m, 1)
        invs = Inv.search([
            ('state', '=', 'paid'),
            ('posted_date', '>=', months[0]),
            ('posted_date', '<', end),
        ])
        for inv in invs:
            d = inv.posted_date
            if not d:
                continue
            key = (d.year, d.month)
            if key in buckets:
                buckets[key] += float(inv.amount_total or 0)
        max_val = max(buckets.values()) if buckets else 0
        if max_val <= 0:
            return self._empty(_('No paid invoices in the last 6 months.'))
        cells = []
        for (y, m), v in buckets.items():
            label = date(y, m, 1).strftime('%b')
            h = max(int(round((v / max_val) * 140)), 2) if max_val else 2
            cells.append(
                '<div class="o_crm_dash_col">'
                '<div class="o_crm_dash_col_value">%s</div>'
                '<div class="o_crm_dash_col_bar" style="height:%dpx;"></div>'
                '<div class="o_crm_dash_col_label">%s</div>'
                '</div>' % (self._money(currency, v), h, label)
            )
        return Markup('<div class="o_crm_dash_columns">%s</div>') % Markup(''.join(cells))

    def _build_top_products(self, currency):
        # Aggregate from confirmed/in-progress/delivered/done sale order lines.
        self.env.cr.execute(
            """
            SELECT l.product_id,
                   p.name AS product_name,
                   SUM(l.quantity) AS qty,
                   SUM(l.subtotal) AS revenue
            FROM crm_sale_order_line l
            JOIN crm_sale_order o ON o.id = l.order_id
            JOIN crm_product p ON p.id = l.product_id
            WHERE o.state IN ('confirmed','in_progress','delivered','done')
            GROUP BY l.product_id, p.name
            ORDER BY revenue DESC NULLS LAST
            LIMIT 8
            """
        )
        rows = self.env.cr.fetchall()
        if not rows:
            return self._empty(_('No products sold yet.'))
        body = []
        for _pid, name, qty, revenue in rows:
            body.append(
                '<tr>'
                '<td>%s</td>'
                '<td class="text-end">%g</td>'
                '<td class="text-end o_crm_dash_money">%s</td>'
                '</tr>' % (name or '-', qty or 0, self._money(currency, revenue or 0))
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>Product</th>'
            '<th class="text-end">Qty Sold</th>'
            '<th class="text-end">Revenue</th>'
            '</tr></thead>'
            '<tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

    def _build_recent_leads(self):
        Lead = self.env['crm.lead']
        leads = Lead.search(
            [('is_won', '=', False), ('is_lost', '=', False)],
            limit=8, order='date desc, id desc',
        )
        if not leads:
            return self._empty(_('No active leads.'))
        body = []
        for l in leads:
            stage = (l.stage_id.name if l.stage_id else '') or '-'
            owner = (l.user_id.name if l.user_id else '') or '-'
            d = l.date.strftime('%d %b') if l.date else '-'
            body.append(
                '<tr>'
                '<td><a href="#" data-action="open" data-model="crm.lead" data-id="%d">%s</a></td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td class="text-end">%s</td>'
                '</tr>' % (l.id, l.name or l.reference, stage, owner, d)
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>Lead</th><th>Stage</th><th>Owner</th><th class="text-end">Date</th>'
            '</tr></thead><tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

    def _build_recent_quotations(self, currency):
        Q = self.env['crm.quotation']
        qs = Q.search([('state', 'in', ('sent', 'draft'))], limit=8, order='date desc, id desc')
        if not qs:
            return self._empty(_('No recent quotations.'))
        body = []
        for q in qs:
            customer = (q.partner_id.name if q.partner_id else q.contact_name) or '-'
            valid = q.validity_date.strftime('%d %b') if q.validity_date else '-'
            body.append(
                '<tr>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td><span class="o_crm_dash_pill o_crm_dash_pill_%s">%s</span></td>'
                '<td class="text-end">%s</td>'
                '<td class="text-end o_crm_dash_money">%s</td>'
                '</tr>' % (
                    q.reference or '-', customer, q.state, q.state.title(),
                    valid, self._money(currency, q.amount_untaxed or 0),
                )
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>Ref</th><th>Customer</th><th>Status</th>'
            '<th class="text-end">Valid Till</th><th class="text-end">Amount</th>'
            '</tr></thead><tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

    def _build_recent_invoices(self, currency, today):
        Inv = self.env['crm.invoice']
        invs = Inv.search(
            [('state', '=', 'posted'), ('payment_state', '!=', 'paid')],
            limit=8, order='due_date asc, id desc',
        )
        if not invs:
            return self._empty(_('No unpaid invoices.'))
        body = []
        for inv in invs:
            customer = (inv.partner_id.name if inv.partner_id else inv.contact_name) or '-'
            due = inv.due_date.strftime('%d %b') if inv.due_date else '-'
            cls = 'o_crm_dash_pill_overdue' if inv.is_overdue else 'o_crm_dash_pill_unpaid'
            label = _('Overdue') if inv.is_overdue else inv.payment_state.title()
            body.append(
                '<tr>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td><span class="o_crm_dash_pill %s">%s</span></td>'
                '<td class="text-end">%s</td>'
                '<td class="text-end o_crm_dash_money">%s</td>'
                '</tr>' % (
                    inv.reference or '-', customer, cls, label,
                    due, self._money(currency, inv.amount_residual or 0),
                )
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>Ref</th><th>Customer</th><th>Status</th>'
            '<th class="text-end">Due</th><th class="text-end">Outstanding</th>'
            '</tr></thead><tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

    def _build_low_stock(self):
        Inv = self.env['crm.inventory']
        rows = Inv.search([('is_low_stock', '=', True)], limit=10, order='quantity asc')
        if not rows:
            return self._empty(_('All stock levels healthy.'))
        body = []
        for r in rows:
            product = r.product_id.name or '-'
            wh = r.warehouse_id.display_name or '-'
            body.append(
                '<tr>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td class="text-end">%g</td>'
                '<td class="text-end">%g</td>'
                '</tr>' % (product, wh, r.quantity or 0, r.reorder_min or 0)
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>Product</th><th>Warehouse</th>'
            '<th class="text-end">On Hand</th><th class="text-end">Reorder At</th>'
            '</tr></thead><tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

    def _build_my_activities(self, today):
        Activity = self.env['mail.activity']
        acts = Activity.search(
            [('user_id', '=', self.env.uid)],
            limit=10, order='date_deadline asc, id asc',
        )
        if not acts:
            return self._empty(_('You have no scheduled activities. ✓'))
        body = []
        for a in acts:
            kind = (a.activity_type_id.name if a.activity_type_id else '') or _('To Do')
            summary = a.summary or (a.note and a.note[:80]) or '-'
            ref_label = a.res_name or '-'
            due = a.date_deadline.strftime('%d %b') if a.date_deadline else '-'
            cls = ''
            if a.date_deadline and a.date_deadline < today:
                cls = 'o_crm_dash_pill_overdue'
                due_label = _('Overdue · %s') % due
            elif a.date_deadline == today:
                cls = 'o_crm_dash_pill_today'
                due_label = _('Today')
            else:
                cls = 'o_crm_dash_pill_unpaid'
                due_label = due
            body.append(
                '<tr>'
                '<td><span class="o_crm_dash_pill %s">%s</span></td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '<td>%s</td>'
                '</tr>' % (cls, due_label, kind, summary, ref_label)
            )
        return Markup(
            '<table class="o_crm_dash_table">'
            '<thead><tr>'
            '<th>When</th><th>Type</th><th>Summary</th><th>Linked Record</th>'
            '</tr></thead><tbody>%s</tbody></table>'
        ) % Markup(''.join(body))

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

    def action_open_low_stock(self):
        return self._open_window(
            'crm.inventory', _('Low Stock'),
            [('is_low_stock', '=', True)], 'list,form',
        )

    def action_open_my_activities(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('My Activities'),
            'res_model': 'mail.activity',
            'view_mode': 'list,form',
            'domain': [('user_id', '=', self.env.uid)],
            'target': 'current',
        }

    def action_refresh(self):
        """Re-render the dashboard with fresh data."""
        return self.action_open_dashboard()
