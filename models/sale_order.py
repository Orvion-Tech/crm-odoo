import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _name = 'crm.sale.order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'CRM Sales Order'
    _order = 'date desc, id desc'
    _rec_name = 'reference'

    reference = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    name = fields.Char(string='Title', tracking=True)

    quotation_id = fields.Many2one(
        'crm.quotation',
        string='Source Quotation',
        copy=False,
        ondelete='set null',
        tracking=True,
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

    date = fields.Date(
        string='Order Date',
        default=fields.Date.context_today,
        tracking=True,
    )
    confirmation_date = fields.Date(string='Confirmed On', readonly=True, copy=False)
    expected_delivery_date = fields.Date(string='Expected Delivery', tracking=True)

    delivery_address = fields.Text(string='Delivery Address')
    warehouse_id = fields.Many2one(
        'crm.warehouse',
        string='Source Warehouse',
        default=lambda self: self.env['crm.warehouse'].default_warehouse(),
        tracking=True,
        help='Stock will be deducted from this warehouse on delivery.',
    )
    stock_move_ids = fields.One2many(
        'crm.stock.move', 'sale_order_id', string='Stock Movements',
    )
    payment_terms = fields.Selection(
        [
            ('advance', 'Advance Payment'),
            ('on_delivery', 'Pay on Delivery'),
            ('net_15', 'Net 15 days'),
            ('net_30', 'Net 30 days'),
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
            ('confirmed', 'Confirmed'),
            ('in_progress', 'In Progress'),
            ('delivered', 'Delivered'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
    )

    line_ids = fields.One2many('crm.sale.order.line', 'order_id', string='Lines', copy=True)
    invoice_ids = fields.One2many(
        'crm.invoice', 'sale_order_id', string='Invoices',
    )
    invoice_count = fields.Integer(
        string='Invoices', compute='_compute_invoice_count',
    )

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for o in self:
            o.invoice_count = len(o.invoice_ids)
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

    terms_conditions = fields.Html(string='Terms & Conditions')
    internal_note = fields.Html(string='Internal Note')

    @api.depends('line_ids.subtotal')
    def _compute_amount(self):
        for o in self:
            o.amount_total = sum(o.line_ids.mapped('subtotal'))

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.contact_name = self.partner_id.name
            self.phone = self.partner_id.phone
            self.email = self.partner_id.email

    @api.onchange('quotation_id')
    def _onchange_quotation_id(self):
        if not self.quotation_id:
            return
        q = self.quotation_id
        if not self.name:
            self.name = q.name
        self.partner_id = q.partner_id
        self.contact_name = q.contact_name
        self.phone = q.phone
        self.email = q.email
        self.user_id = q.user_id
        self.currency_id = q.currency_id
        self.line_ids = [(5, 0, 0)] + [
            (0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.quantity,
                'unit_price': l.unit_price,
                'discount': l.discount,
                'sequence': l.sequence,
            })
            for l in q.line_ids
        ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.sale.order') or 'New'
        return super().create(vals_list)

    def action_confirm(self):
        for o in self:
            if not o.line_ids:
                raise UserError(_('Cannot confirm an empty order: add at least one line.'))
            if not (o.delivery_address and o.delivery_address.strip()):
                raise UserError(_(
                    'Delivery address is required to confirm an order. '
                    'Fill it in the "Shipping" section first.'
                ))
            o.write({
                'state': 'confirmed',
                'confirmation_date': fields.Date.today(),
            })
            if o.quotation_id and o.quotation_id.state == 'accepted':
                o.quotation_id.state = 'ordered'
                o.quotation_id.message_post(
                    body=_('Locked: linked to confirmed order %s.') % o.reference,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            # Auto-send confirmation email. If it fails (no email, SMTP issue,
            # PDF render error), we still want the order to remain confirmed —
            # post the failure as a chatter note so the salesperson can retry.
            try:
                o._send_confirmation_email()
            except Exception as e:
                _logger.exception('Order confirmation email failed for %s', o.reference)
                o.message_post(
                    body=_('Order confirmed, but the confirmation email could not be sent: %s') % e,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

    def action_send_confirmation(self):
        """Manual resend — surfaces errors so the salesperson sees them."""
        self.ensure_one()
        self._send_confirmation_email()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sent'),
                'message': _('Order confirmation email sent.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_start(self):
        self.write({'state': 'in_progress'})

    def action_deliver(self):
        for o in self:
            if not o.warehouse_id:
                raise UserError(_(
                    'Pick a Source Warehouse before marking this order delivered.'
                ))
            # Pre-flight: refuse if any line is short on stock.
            shortages = []
            Inv = self.env['crm.inventory']
            for line in o.line_ids:
                on_hand = Inv.get_quantity(line.product_id, o.warehouse_id)
                if on_hand < (line.quantity or 0):
                    shortages.append((
                        line.product_id.name, on_hand, line.quantity,
                    ))
            if shortages:
                msg = _(
                    'Cannot deliver — stock is short at %(wh)s:\n%(rows)s'
                ) % {
                    'wh': o.warehouse_id.display_name,
                    'rows': '\n'.join(
                        '  • %s — on hand %g, needed %g' % row for row in shortages
                    ),
                }
                raise UserError(msg)

            # Generate one OUT move per line and confirm it.
            Move = self.env['crm.stock.move']
            for line in o.line_ids:
                if not line.product_id or not line.quantity:
                    continue
                move = Move.create({
                    'move_type': 'out',
                    'product_id': line.product_id.id,
                    'warehouse_id': o.warehouse_id.id,
                    'quantity': line.quantity,
                    'sale_order_id': o.id,
                    'note': _('Auto-generated on Mark Delivered for %s') % o.reference,
                })
                move.action_done()
            o.message_post(
                body=_('Delivered from %(wh)s — %(n)d stock move(s) recorded.') % {
                    'wh': o.warehouse_id.display_name,
                    'n': len(o.line_ids),
                },
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            o.state = 'delivered'

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        for o in self:
            if o.state != 'confirmed':
                raise UserError(_(
                    'Only confirmed orders can be reset to draft. Cancel first if already in fulfillment.'
                ))
        self.write({'state': 'draft', 'confirmation_date': False})

    def action_view_quotation(self):
        self.ensure_one()
        if not self.quotation_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Source Quotation'),
            'res_model': 'crm.quotation',
            'res_id': self.quotation_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_invoice(self):
        """Spawn a draft invoice from this order. Multiple invoices allowed."""
        self.ensure_one()
        if self.state not in ('confirmed', 'in_progress', 'delivered', 'done'):
            raise UserError(_('Order must be confirmed before invoicing.'))
        if not self.line_ids:
            raise UserError(_('Cannot invoice an empty order.'))
        Invoice = self.env['crm.invoice']
        line_vals = [
            (0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.quantity,
                'unit_price': l.unit_price,
                'discount': l.discount,
                'sequence': l.sequence,
            })
            for l in self.line_ids
        ]
        invoice = Invoice.create({
            'name': self.name,
            'sale_order_id': self.id,
            'partner_id': self.partner_id.id,
            'contact_name': self.contact_name,
            'phone': self.phone,
            'email': self.email,
            'user_id': self.user_id.id,
            'currency_id': self.currency_id.id,
            'payment_terms': self.payment_terms,
            'payment_terms_custom': self.payment_terms_custom,
            'terms_conditions': self.terms_conditions,
            'line_ids': line_vals,
        })
        self.message_post(
            body=_('Invoice %s created from this order.') % invoice.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice'),
            'res_model': 'crm.invoice',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices'),
            'res_model': 'crm.invoice',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }

    # ------------------------------------------------------------------
    # Confirmation email
    # ------------------------------------------------------------------
    def _greeting_name(self):
        self.ensure_one()
        candidate = self.contact_name or (self.partner_id and self.partner_id.name) or ''
        if '@' in candidate:
            return _('Customer')
        return candidate or _('Customer')

    def _build_confirmation_body(self):
        self.ensure_one()
        company = self.env.company
        symbol = self.currency_id.symbol or self.currency_id.name or ''
        salesperson = self.user_id
        sales_email = salesperson.email or company.email or ''
        sales_phone = salesperson.partner_id.phone or company.phone or ''

        # Lines
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

        # Date helpers
        try:
            confirm_str = (self.confirmation_date or fields.Date.today()).strftime('%d %B %Y')
        except Exception:
            confirm_str = str(self.confirmation_date or fields.Date.today())
        try:
            delivery_str = self.expected_delivery_date.strftime('%d %B %Y') if self.expected_delivery_date else _('To be confirmed')
        except Exception:
            delivery_str = str(self.expected_delivery_date or '-')

        # Payment terms label
        payment_label = dict(self._fields['payment_terms'].selection).get(self.payment_terms, '')
        if self.payment_terms == 'custom' and self.payment_terms_custom:
            payment_label = self.payment_terms_custom

        # Delivery address block (only if filled)
        delivery_block = ''
        if self.delivery_address:
            delivery_block = (
                '<div style="margin-top:18px;padding:14px;background:#f8f9ff;'
                'border-left:3px solid #3b3a73;border-radius:4px;">'
                '<div style="font-size:11px;letter-spacing:1px;color:#3b3a73;'
                'text-transform:uppercase;font-weight:600;">Delivery Address</div>'
                '<div style="margin-top:6px;color:#444;font-size:14px;white-space:pre-wrap;">%s</div>'
                '</div>'
            ) % self.delivery_address

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

            # Header
            '<div style="background:linear-gradient(90deg,#16a34a 0%%,#0f7c39 100%%);'
            'padding:24px 28px;color:white;">'
            '<div style="font-size:11px;letter-spacing:1.5px;color:#fff8e1;'
            'text-transform:uppercase;font-weight:600;">Order Confirmed</div>'
            '<h1 style="margin:4px 0 0 0;font-size:26px;font-weight:700;">%(ref)s</h1>'
            '<div style="margin-top:6px;font-size:14px;opacity:0.9;">%(company)s</div>'
            '</div>'

            # Greeting
            '<div style="padding:24px 28px;">'
            '<p style="margin:0 0 16px 0;font-size:15px;">Dear <b>%(greeting)s</b>,</p>'
            '<p style="margin:0 0 20px 0;color:#444;">'
            'Your order has been <b>confirmed</b>. Thank you for choosing %(company)s. '
            'A summary is below; the full order document is also attached as a PDF.'
            '</p>'

            # Info table
            '<table style="width:100%%;border-collapse:collapse;margin-bottom:20px;'
            'background:#f8f9ff;border-radius:8px;">'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;">Order No.</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;">%(ref)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Confirmed On</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(confirmed)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Expected Delivery</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;color:#3b3a73;border-top:1px solid #e2e3f5;">%(delivery)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Payment Terms</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(payment)s</td></tr>'
            '<tr><td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Salesperson</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(salesperson)s</td></tr>'
            '</table>'

            # Lines
            '<h3 style="margin:0 0 12px 0;color:#3b3a73;font-size:16px;'
            'text-transform:uppercase;letter-spacing:0.5px;">Order Items</h3>'
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
            'color:#3b3a73;border-top:2px solid #f59e0b;"><b>TOTAL</b></td>'
            '<td style="padding:14px 10px;text-align:right;font-size:18px;font-weight:700;'
            'color:#3b3a73;border-top:2px solid #f59e0b;">%(symbol)s %(total).2f</td>'
            '</tr></tfoot>'
            '</table>'

            '%(delivery_block)s'
            '%(terms_block)s'

            # Signature
            '<div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e3f5;">'
            '<p style="margin:0;color:#444;">We will keep you updated on the fulfillment status. '
            'Reply to this email if you have any questions.</p>'
            '<p style="margin:18px 0 0 0;">Best regards,</p>'
            '<p style="margin:4px 0 0 0;font-weight:700;color:#3b3a73;">%(salesperson)s</p>'
            '<p style="margin:0;color:#666;font-size:13px;">%(sales_email)s%(sep)s%(sales_phone)s</p>'
            '</div>'
            '</div>'

            # Footer
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
            'confirmed': confirm_str,
            'delivery': delivery_str,
            'payment': payment_label,
            'salesperson': salesperson.name or '',
            'sales_email': sales_email,
            'sales_phone': sales_phone,
            'sep': ' · ' if (sales_email and sales_phone) else '',
            'lines': lines_html,
            'symbol': symbol,
            'total': self.amount_total or 0,
            'delivery_block': delivery_block,
            'terms_block': terms_block,
            'company_addr': ', '.join(filter(None, [
                company.street, company.city,
                company.state_id.name, company.country_id.name,
            ])) or '',
            'company_contact': ' · '.join(filter(None, [
                company.email, company.phone, company.website,
            ])) or '',
        }

    def _render_order_pdf(self):
        self.ensure_one()
        pdf_bytes, _content_type = self.env['ir.actions.report']._render_qweb_pdf(
            'crm_custom.action_report_sale_order', res_ids=[self.id]
        )
        if not pdf_bytes:
            return None, None
        filename = 'Order_%s.pdf' % (self.reference or 'New').replace('/', '_')
        return filename, pdf_bytes

    def _send_confirmation_email(self):
        self.ensure_one()
        import base64
        email_to = self.email or (self.partner_id and self.partner_id.email)
        if not email_to:
            raise UserError(_('Customer has no email address — cannot send confirmation.'))

        # Recipient partner
        Partner = self.env['res.partner']
        recipient = self.partner_id or Partner.search(
            [('email', '=ilike', email_to)], limit=1
        )
        if not recipient:
            new_name = self.contact_name or email_to.split('@')[0].replace('.', ' ').title()
            recipient = Partner.create({'name': new_name, 'email': email_to})
        elif not recipient.email:
            recipient.email = email_to

        # PDF
        attachment_ids = []
        try:
            filename, pdf_bytes = self._render_order_pdf()
        except Exception as e:
            _logger.exception('Order PDF render failed for %s', self.reference)
            raise UserError(_(
                'Could not generate the order PDF: %s\n\n'
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
        subject = _('Order Confirmed: %(ref)s — %(company)s') % {
            'ref': self.reference, 'company': company_name,
        }
        self.message_post(
            body=self._build_confirmation_body(),
            subject=subject,
            partner_ids=[recipient.id],
            attachment_ids=attachment_ids,
            author_id=author.id,
            email_from=self.user_id.email_formatted or author.email_formatted or None,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )


class SaleOrderLine(models.Model):
    _name = 'crm.sale.order.line'
    _description = 'Sales Order Line'
    _order = 'sequence, id'

    order_id = fields.Many2one(
        'crm.sale.order', string='Order', required=True, ondelete='cascade',
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
    currency_id = fields.Many2one(related='order_id.currency_id', store=True)

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
