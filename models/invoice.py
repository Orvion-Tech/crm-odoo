import base64
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Days added to invoice_date for each payment_terms value.
_PAYMENT_TERM_DAYS = {
    'advance': 0,
    'on_delivery': 0,
    'net_15': 15,
    'net_30': 30,
    'net_45': 45,
    'net_60': 60,
}


class Invoice(models.Model):
    _name = 'crm.invoice'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'CRM Invoice'
    _order = 'invoice_date desc, id desc'
    _rec_name = 'reference'

    reference = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    name = fields.Char(string='Title', tracking=True)

    sale_order_id = fields.Many2one(
        'crm.sale.order',
        string='Source Order',
        copy=False,
        ondelete='set null',
        tracking=True,
    )
    quotation_id = fields.Many2one(
        related='sale_order_id.quotation_id',
        string='Source Quotation',
        store=True,
    )
    partner_id = fields.Many2one('res.partner', string='Customer', tracking=True)
    contact_name = fields.Char(string='Contact Name', tracking=True)
    phone = fields.Char(string='Phone')
    email = fields.Char(string='Email')

    user_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        default=lambda self: self.env.user,
        tracking=True,
    )

    invoice_date = fields.Date(
        string='Invoice Date',
        default=fields.Date.context_today,
        tracking=True,
    )
    posted_date = fields.Date(string='Posted On', readonly=True, copy=False)
    due_date = fields.Date(
        string='Due Date',
        compute='_compute_due_date',
        store=True,
        readonly=False,
        tracking=True,
        help='Auto-computed from invoice date + payment terms; can be overridden.',
    )

    payment_terms = fields.Selection(
        [
            ('advance', 'Advance Payment'),
            ('on_delivery', 'Pay on Delivery'),
            ('net_15', 'Net 15 days'),
            ('net_30', 'Net 30 days'),
            ('net_45', 'Net 45 days'),
            ('net_60', 'Net 60 days'),
            ('custom', 'Custom'),
        ],
        string='Payment Terms',
        default='net_30',
        tracking=True,
    )
    payment_terms_custom = fields.Char(string='Custom Terms')

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('posted', 'Posted'),
            ('paid', 'Paid'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
    )

    line_ids = fields.One2many(
        'crm.invoice.line', 'invoice_id', string='Lines', copy=True,
    )
    payment_ids = fields.One2many(
        'crm.invoice.payment', 'invoice_id', string='Payments',
    )

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.INR', raise_if_not_found=False) or self.env.company.currency_id,
    )
    amount_total = fields.Monetary(
        string='Total',
        currency_field='currency_id',
        compute='_compute_amount',
        store=True,
        tracking=True,
    )
    amount_paid = fields.Monetary(
        string='Paid',
        currency_field='currency_id',
        compute='_compute_amount_paid',
        store=True,
    )
    amount_residual = fields.Monetary(
        string='Amount Due',
        currency_field='currency_id',
        compute='_compute_amount_paid',
        store=True,
    )
    payment_state = fields.Selection(
        [
            ('unpaid', 'Unpaid'),
            ('partial', 'Partially Paid'),
            ('paid', 'Paid'),
        ],
        string='Payment Status',
        compute='_compute_amount_paid',
        store=True,
        tracking=True,
    )

    is_overdue = fields.Boolean(
        string='Overdue',
        compute='_compute_is_overdue',
        store=True,
        help='Posted invoice past its due date with unpaid balance.',
    )

    terms_conditions = fields.Html(string='Terms & Conditions')
    internal_note = fields.Html(string='Internal Note')

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('line_ids.subtotal')
    def _compute_amount(self):
        for inv in self:
            inv.amount_total = sum(inv.line_ids.mapped('subtotal'))

    @api.depends('payment_ids.amount', 'amount_total', 'state')
    def _compute_amount_paid(self):
        for inv in self:
            paid = sum(inv.payment_ids.mapped('amount'))
            inv.amount_paid = paid
            inv.amount_residual = (inv.amount_total or 0) - paid
            if inv.amount_total <= 0:
                inv.payment_state = 'unpaid'
            elif paid <= 0:
                inv.payment_state = 'unpaid'
            elif paid + 0.001 < inv.amount_total:
                inv.payment_state = 'partial'
            else:
                inv.payment_state = 'paid'

    @api.depends('invoice_date', 'payment_terms')
    def _compute_due_date(self):
        for inv in self:
            if not inv.invoice_date:
                inv.due_date = False
                continue
            if inv.payment_terms == 'custom':
                # Don't auto-overwrite if user has set a custom date.
                if not inv.due_date:
                    inv.due_date = inv.invoice_date
                continue
            days = _PAYMENT_TERM_DAYS.get(inv.payment_terms, 0)
            inv.due_date = inv.invoice_date + timedelta(days=days)

    @api.depends('state', 'payment_state', 'due_date')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for inv in self:
            inv.is_overdue = bool(
                inv.state == 'posted'
                and inv.payment_state != 'paid'
                and inv.due_date and inv.due_date < today
            )

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.contact_name = self.partner_id.name
            self.phone = self.partner_id.phone
            self.email = self.partner_id.email

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if not self.sale_order_id:
            return
        so = self.sale_order_id
        if not self.name:
            self.name = so.name
        self.partner_id = so.partner_id
        self.contact_name = so.contact_name
        self.phone = so.phone
        self.email = so.email
        self.user_id = so.user_id
        self.currency_id = so.currency_id
        self.payment_terms = so.payment_terms
        self.payment_terms_custom = so.payment_terms_custom
        self.terms_conditions = so.terms_conditions
        self.line_ids = [(5, 0, 0)] + [
            (0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.quantity,
                'unit_price': l.unit_price,
                'discount': l.discount,
                'sequence': l.sequence,
            })
            for l in so.line_ids
        ]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.invoice') or 'New'
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_post(self):
        for inv in self:
            if inv.state != 'draft':
                raise UserError(_('Only draft invoices can be posted.'))
            if not inv.line_ids:
                raise UserError(_('Cannot post an empty invoice: add at least one line.'))
            inv.write({
                'state': 'posted',
                'posted_date': fields.Date.today(),
            })
            try:
                inv._send_invoice_email()
            except Exception as e:
                _logger.exception('Invoice email failed for %s', inv.reference)
                inv.message_post(
                    body=_('Invoice posted, but the email could not be sent: %s') % e,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

    def action_send(self):
        """Manual resend — surfaces errors so the salesperson sees them."""
        self.ensure_one()
        self._send_invoice_email()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sent'),
                'message': _('Invoice email sent.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_register_payment(self):
        self.ensure_one()
        if self.state != 'posted':
            raise UserError(_('Only posted invoices can receive payments.'))
        if self.payment_state == 'paid':
            raise UserError(_('Invoice is already fully paid.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Register Payment'),
            'res_model': 'crm.invoice.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_id': self.id,
                'default_amount': self.amount_residual,
                'default_currency_id': self.currency_id.id,
            },
        }

    def action_mark_paid(self):
        """Helper: settle the residual in one click using the wizard defaults."""
        self.ensure_one()
        if self.amount_residual <= 0:
            raise UserError(_('Nothing to settle.'))
        self.env['crm.invoice.payment'].create({
            'invoice_id': self.id,
            'amount': self.amount_residual,
            'payment_date': fields.Date.today(),
            'method': 'other',
            'reference': _('Manual settle'),
        })

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        for inv in self:
            if inv.payment_ids:
                raise UserError(_(
                    'Cannot reset to draft: this invoice has recorded payments. '
                    'Delete the payments first.'
                ))
            if inv.state not in ('posted', 'cancelled'):
                continue
            inv.write({'state': 'draft', 'posted_date': False})

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Source Order'),
            'res_model': 'crm.sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payments'),
            'res_model': 'crm.invoice.payment',
            'view_mode': 'list,form',
            'domain': [('invoice_id', '=', self.id)],
            'context': {'default_invoice_id': self.id},
        }

    # ------------------------------------------------------------------
    # Cron: nag on overdue invoices
    # ------------------------------------------------------------------
    @api.model
    def _cron_flag_overdue_invoices(self):
        today = fields.Date.today()
        overdue = self.search([
            ('state', '=', 'posted'),
            ('payment_state', '!=', 'paid'),
            ('due_date', '<', today),
        ])
        for inv in overdue:
            inv.message_post(
                body=_('Invoice is overdue. Amount due: %(amount)s %(symbol)s. Please follow up.') % {
                    'amount': '%.2f' % (inv.amount_residual or 0),
                    'symbol': inv.currency_id.symbol or '',
                },
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        _logger.info('Overdue invoice cron: flagged %d invoices', len(overdue))

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def _greeting_name(self):
        self.ensure_one()
        candidate = self.contact_name or (self.partner_id and self.partner_id.name) or ''
        if '@' in candidate:
            return _('Customer')
        return candidate or _('Customer')

    def _build_invoice_body(self):
        self.ensure_one()
        company = self.env.company
        symbol = self.currency_id.symbol or self.currency_id.name or ''
        salesperson = self.user_id
        sales_email = salesperson.email or company.email or ''
        sales_phone = salesperson.partner_id.phone or company.phone or ''

        rows = []
        for line in self.line_ids:
            disc_cell = '%s%%' % ('%g' % line.discount) if line.discount else '—'
            rows.append(
                '<tr>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;">%s</td>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;color:#666;">%s</td>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;text-align:right;">%g</td>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;text-align:right;">%s %.2f</td>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;text-align:right;">%s</td>'
                '<td style="padding:10px;border-bottom:1px solid #e2e3f5;text-align:right;font-weight:600;">%s %.2f</td>'
                '</tr>' % (
                    line.product_id.name or '',
                    line.description or '',
                    line.quantity or 0,
                    symbol, line.unit_price or 0,
                    disc_cell,
                    symbol, line.subtotal or 0,
                )
            )
        lines_html = ''.join(rows) or (
            '<tr><td colspan="6" style="padding:20px;text-align:center;color:#888;">'
            '(no line items)</td></tr>'
        )

        try:
            inv_str = (self.invoice_date or fields.Date.today()).strftime('%d %B %Y')
        except Exception:
            inv_str = str(self.invoice_date or fields.Date.today())
        try:
            due_str = self.due_date.strftime('%d %B %Y') if self.due_date else _('On receipt')
        except Exception:
            due_str = str(self.due_date or '-')

        payment_label = dict(self._fields['payment_terms'].selection).get(self.payment_terms, '')
        if self.payment_terms == 'custom' and self.payment_terms_custom:
            payment_label = self.payment_terms_custom

        terms_block = ''
        if self.terms_conditions:
            terms_block = (
                '<div style="margin-top:18px;padding:14px;background:#f8f9ff;'
                'border-left:3px solid #f59e0b;border-radius:4px;">'
                '<div style="font-size:11px;letter-spacing:1px;color:#3b3a73;'
                'text-transform:uppercase;font-weight:600;">Terms &amp; Conditions</div>'
                '<div style="margin-top:6px;color:#444;font-size:13px;">%s</div></div>'
            ) % self.terms_conditions

        return (
            '<div style="font-family:Arial,sans-serif;max-width:720px;margin:0 auto;'
            'border:1px solid #e2e3f5;border-radius:12px;overflow:hidden;">'

            '<div style="background:linear-gradient(90deg,#3b3a73 0%%,#2a2956 100%%);'
            'padding:24px 28px;color:white;">'
            '<div style="font-size:11px;letter-spacing:1.5px;color:#ffd591;'
            'text-transform:uppercase;font-weight:600;">Invoice</div>'
            '<h1 style="margin:4px 0 0 0;font-size:26px;font-weight:700;">%(ref)s</h1>'
            '<div style="margin-top:6px;font-size:14px;opacity:0.9;">%(company)s</div>'
            '</div>'

            '<div style="padding:24px 28px;">'
            '<p style="margin:0 0 16px 0;font-size:15px;">Dear <b>%(greeting)s</b>,</p>'
            '<p style="margin:0 0 20px 0;color:#444;">'
            'Please find your invoice below. The full document is also attached as a PDF. '
            'Kindly process payment by the due date indicated.'
            '</p>'

            '<table style="width:100%%;border-collapse:collapse;margin-bottom:20px;'
            'background:#f8f9ff;border-radius:8px;">'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;">Invoice No.</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;">%(ref)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Invoice Date</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(invdate)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Due Date</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:700;color:#dc2626;border-top:1px solid #e2e3f5;">%(due)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Payment Terms</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(payment)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Salesperson</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(salesperson)s</td></tr>'
            '</table>'

            '<h3 style="margin:0 0 12px 0;color:#3b3a73;font-size:16px;'
            'text-transform:uppercase;letter-spacing:0.5px;">Items</h3>'
            '<table style="width:100%%;border-collapse:collapse;font-size:14px;">'
            '<thead style="background:#3b3a73;color:white;">'
            '<tr>'
            '<th style="padding:10px;text-align:left;font-size:12px;letter-spacing:0.5px;">Product</th>'
            '<th style="padding:10px;text-align:left;font-size:12px;letter-spacing:0.5px;">Description</th>'
            '<th style="padding:10px;text-align:right;font-size:12px;letter-spacing:0.5px;">Qty</th>'
            '<th style="padding:10px;text-align:right;font-size:12px;letter-spacing:0.5px;">Unit Price</th>'
            '<th style="padding:10px;text-align:right;font-size:12px;letter-spacing:0.5px;">Discount</th>'
            '<th style="padding:10px;text-align:right;font-size:12px;letter-spacing:0.5px;">Subtotal</th>'
            '</tr></thead>'
            '<tbody>%(lines)s</tbody>'
            '<tfoot>'
            '<tr style="background:#f8f9ff;">'
            '<td colspan="5" style="padding:14px 10px;text-align:right;font-size:15px;'
            'color:#3b3a73;border-top:2px solid #f59e0b;"><b>AMOUNT DUE</b></td>'
            '<td style="padding:14px 10px;text-align:right;font-size:18px;font-weight:700;'
            'color:#dc2626;border-top:2px solid #f59e0b;">%(symbol)s %(total).2f</td>'
            '</tr></tfoot>'
            '</table>'

            '%(terms_block)s'

            '<div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e3f5;">'
            '<p style="margin:0;color:#444;">Thank you for your business. Please reply to this email '
            'with any questions about this invoice.</p>'
            '<p style="margin:18px 0 0 0;">Best regards,</p>'
            '<p style="margin:4px 0 0 0;font-weight:700;color:#3b3a73;">%(salesperson)s</p>'
            '<p style="margin:0;color:#666;font-size:13px;">%(sales_email)s%(sep)s%(sales_phone)s</p>'
            '</div>'
            '</div>'

            '<div style="background:#f8f9ff;padding:16px 28px;text-align:center;'
            'font-size:12px;color:#666;border-top:1px solid #e2e3f5;">'
            '<div style="font-weight:600;color:#3b3a73;">%(company)s</div>'
            '<div style="margin-top:4px;">%(company_addr)s</div>'
            '<div style="margin-top:4px;">%(company_contact)s</div>'
            '</div>'

            '</div>'
        ) % {
            'ref': self.reference,
            'company': company.name or '',
            'greeting': self._greeting_name(),
            'invdate': inv_str,
            'due': due_str,
            'payment': payment_label,
            'salesperson': salesperson.name or '',
            'sales_email': sales_email,
            'sales_phone': sales_phone,
            'sep': ' · ' if (sales_email and sales_phone) else '',
            'lines': lines_html,
            'symbol': symbol,
            'total': self.amount_total or 0,
            'terms_block': terms_block,
            'company_addr': ', '.join(filter(None, [
                company.street, company.city,
                company.state_id.name, company.country_id.name,
            ])) or '',
            'company_contact': ' · '.join(filter(None, [
                company.email, company.phone, company.website,
            ])) or '',
        }

    def _render_invoice_pdf(self):
        self.ensure_one()
        pdf_bytes, _content_type = self.env['ir.actions.report']._render_qweb_pdf(
            'crm_custom.action_report_invoice', res_ids=[self.id]
        )
        if not pdf_bytes:
            return None, None
        filename = 'Invoice_%s.pdf' % (self.reference or 'New').replace('/', '_')
        return filename, pdf_bytes

    def _send_invoice_email(self):
        self.ensure_one()
        email_to = self.email or (self.partner_id and self.partner_id.email)
        if not email_to:
            raise UserError(_('Customer has no email address — cannot send invoice.'))

        Partner = self.env['res.partner']
        recipient = self.partner_id or Partner.search(
            [('email', '=ilike', email_to)], limit=1
        )
        if not recipient:
            new_name = self.contact_name or email_to.split('@')[0].replace('.', ' ').title()
            recipient = Partner.create({'name': new_name, 'email': email_to})
        elif not recipient.email:
            recipient.email = email_to

        attachment_ids = []
        try:
            filename, pdf_bytes = self._render_invoice_pdf()
        except Exception as e:
            _logger.exception('Invoice PDF render failed for %s', self.reference)
            raise UserError(_(
                'Could not generate the invoice PDF: %s\n\n'
                'Check wkhtmltopdf is the patched build and that '
                'Settings → Companies → Document Layout is configured.'
            ) % e) from e
        if pdf_bytes:
            att = self.env['ir.attachment'].create({
                'name': filename,
                'datas': base64.b64encode(pdf_bytes),
                'res_model': self._name,
                'res_id': self.id,
                'type': 'binary',
                'mimetype': 'application/pdf',
            })
            attachment_ids.append(att.id)

        author = self.user_id.partner_id or self.env.user.partner_id
        company_name = self.env.company.name or ''
        subject = _('Invoice: %(ref)s — %(company)s') % {
            'ref': self.reference, 'company': company_name,
        }
        self.message_post(
            body=self._build_invoice_body(),
            subject=subject,
            partner_ids=[recipient.id],
            attachment_ids=attachment_ids,
            author_id=author.id,
            email_from=self.user_id.email_formatted or author.email_formatted or None,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )


class InvoiceLine(models.Model):
    _name = 'crm.invoice.line'
    _description = 'Invoice Line'
    _order = 'sequence, id'

    invoice_id = fields.Many2one(
        'crm.invoice', string='Invoice', required=True, ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('crm.product', string='Product', required=True)
    description = fields.Char(string='Description')
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    unit_price = fields.Monetary(string='Unit Price', currency_field='currency_id', required=True)
    discount = fields.Float(
        string='Discount %',
        default=0.0,
        digits=(5, 2),
        help='Percentage discount on this line (0-100).',
    )
    subtotal = fields.Monetary(
        string='Subtotal',
        currency_field='currency_id',
        compute='_compute_subtotal',
        store=True,
    )
    currency_id = fields.Many2one(related='invoice_id.currency_id', store=True)

    @api.depends('quantity', 'unit_price', 'discount')
    def _compute_subtotal(self):
        for line in self:
            gross = (line.quantity or 0) * (line.unit_price or 0)
            line.subtotal = gross * (1 - (line.discount or 0) / 100.0)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.unit_price = self.product_id.list_price
            if not self.description:
                self.description = self.product_id.name


class InvoicePayment(models.Model):
    _name = 'crm.invoice.payment'
    _description = 'Invoice Payment'
    _order = 'payment_date desc, id desc'
    _rec_name = 'reference'

    invoice_id = fields.Many2one(
        'crm.invoice', string='Invoice', required=True, ondelete='cascade',
    )
    partner_id = fields.Many2one(related='invoice_id.partner_id', store=True)
    currency_id = fields.Many2one(related='invoice_id.currency_id', store=True)
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
        for p in self:
            if (p.amount or 0) <= 0:
                raise UserError(_('Payment amount must be greater than zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Recompute payment_state via invalidation; tracking is on invoice fields.
        for p in records:
            inv = p.invoice_id
            inv.message_post(
                body=_('Payment recorded: %(amt)s %(sym)s via %(method)s%(ref)s') % {
                    'amt': '%.2f' % (p.amount or 0),
                    'sym': inv.currency_id.symbol or '',
                    'method': dict(p._fields['method'].selection).get(p.method, ''),
                    'ref': (' (ref: %s)' % p.reference) if p.reference else '',
                },
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            # Auto-flip invoice state to 'paid' once fully settled.
            if inv.state == 'posted' and inv.payment_state == 'paid':
                inv.write({'state': 'paid'})
        return records

    def unlink(self):
        invoices = self.mapped('invoice_id')
        result = super().unlink()
        # If a refund/correction drops it below paid, revert state.
        for inv in invoices:
            if inv.state == 'paid' and inv.payment_state != 'paid':
                inv.write({'state': 'posted'})
        return result
