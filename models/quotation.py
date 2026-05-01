import json
import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_WA_GRAPH_URL = 'https://graph.facebook.com/v19.0/{phone_number_id}/messages'


class Quotation(models.Model):
    _name = 'crm.quotation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'CRM Quotation'
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

    lead_id = fields.Many2one(
        'crm.lead',
        string='Source Lead',
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

    date = fields.Date(string='Quotation Date', default=fields.Date.context_today, tracking=True)
    validity_date = fields.Date(
        string='Valid Until',
        default=lambda self: fields.Date.context_today(self) + timedelta(days=15),
        tracking=True,
    )
    is_expired = fields.Boolean(string='Expired', compute='_compute_is_expired', store=True)

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('accepted', 'Accepted'),
            ('rejected', 'Rejected'),
            ('expired', 'Expired'),
            ('superseded', 'Superseded'),
            ('ordered', 'Ordered'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
    )

    sale_order_ids = fields.One2many(
        'crm.sale.order', 'quotation_id', string='Linked Orders',
    )
    sale_order_count = fields.Integer(
        string='Orders', compute='_compute_sale_order_count'
    )

    parent_id = fields.Many2one(
        'crm.quotation',
        string='Revises',
        copy=False,
        ondelete='set null',
        help='If this is a revision, points at the previous version.',
    )
    root_id = fields.Many2one(
        'crm.quotation',
        string='Original',
        compute='_compute_root_id',
        store=True,
        recursive=True,
    )
    child_revision_ids = fields.One2many(
        'crm.quotation', 'parent_id', string='Direct Revisions',
    )
    revision_count = fields.Integer(
        string='Total Revisions',
        compute='_compute_revision_count',
    )
    revision_number = fields.Integer(
        string='Revision',
        default=1,
        copy=False,
        readonly=True,
    )
    is_locked = fields.Boolean(
        string='Locked',
        compute='_compute_is_locked',
        store=True,
    )

    line_ids = fields.One2many('crm.quotation.line', 'quotation_id', string='Lines', copy=True)
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.INR', raise_if_not_found=False) or self.env.company.currency_id,
    )
    amount_untaxed = fields.Monetary(
        string='Total',
        currency_field='currency_id',
        compute='_compute_amount',
        store=True,
        tracking=True,
    )

    terms_conditions = fields.Html(string='Terms & Conditions')
    note = fields.Html(string='Internal Note')

    @api.depends('line_ids.subtotal')
    def _compute_amount(self):
        for q in self:
            q.amount_untaxed = sum(q.line_ids.mapped('subtotal'))

    @api.depends('validity_date', 'state')
    def _compute_is_expired(self):
        today = fields.Date.context_today(self)
        for q in self:
            q.is_expired = (
                q.state not in ('accepted', 'rejected', 'expired', 'superseded')
                and q.validity_date
                and q.validity_date < today
            )

    @api.depends('parent_id', 'parent_id.root_id')
    def _compute_root_id(self):
        for q in self:
            node = q
            while node.parent_id:
                node = node.parent_id
            q.root_id = node

    @api.depends('root_id')
    def _compute_revision_count(self):
        for q in self:
            if not q.root_id:
                q.revision_count = 0
                continue
            q.revision_count = self.search_count([
                ('root_id', '=', q.root_id.id),
                ('id', '!=', q.id),
            ])

    @api.depends('state')
    def _compute_is_locked(self):
        locked_states = ('sent', 'accepted', 'rejected', 'expired', 'superseded', 'ordered')
        for q in self:
            q.is_locked = q.state in locked_states

    @api.depends('sale_order_ids')
    def _compute_sale_order_count(self):
        for q in self:
            q.sale_order_count = len(q.sale_order_ids)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.contact_name = self.partner_id.name
            self.phone = self.partner_id.phone
            self.email = self.partner_id.email

    @api.onchange('lead_id')
    def _onchange_lead_id(self):
        if not self.lead_id:
            return
        lead = self.lead_id
        if not self.name:
            self.name = lead.name
        self.partner_id = lead.partner_id
        self.contact_name = lead.contact_name
        self.phone = lead.phone
        self.email = lead.email
        self.user_id = lead.user_id
        self.currency_id = lead.currency_id
        self.line_ids = [(5, 0, 0)] + [
            (0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.quantity,
                'unit_price': l.unit_price,
                'sequence': l.sequence,
            })
            for l in lead.line_ids
        ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.quotation') or 'New'
        return super().create(vals_list)

    def action_send(self):
        """Open the wizard that asks the salesperson which channel to send via."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('Cannot send an empty quotation: add at least one line.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Send Quotation'),
            'res_model': 'crm.quotation.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_quotation_id': self.id},
        }

    def _greeting_name(self):
        """Best-effort customer salutation: contact name → partner name → fallback."""
        self.ensure_one()
        candidate = self.contact_name or (self.partner_id and self.partner_id.name) or ''
        # If the only thing we have is an email address, soften it to "Customer"
        if '@' in candidate:
            return _('Customer')
        return candidate or _('Customer')

    def _build_send_body(self):
        """Full HTML body used by the wizard. Editable per-send."""
        self.ensure_one()
        company = self.env.company
        symbol = self.currency_id.symbol or self.currency_id.name or ''
        salesperson = self.user_id
        sales_email = salesperson.email or company.email or ''
        sales_phone = salesperson.partner_id.phone or company.phone or ''

        # ----- line rows -----
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

        # ----- terms (if any) -----
        terms_block = ''
        if self.terms_conditions:
            terms_block = (
                '<div style="margin-top:24px;padding:16px;background:#f8f9ff;'
                'border-left:3px solid #f59e0b;">'
                '<h4 style="margin:0 0 8px 0;color:#3b3a73;">Terms &amp; Conditions</h4>'
                '<div style="color:#444;font-size:14px;">%s</div></div>'
            ) % self.terms_conditions

        # ----- date format -----
        try:
            date_str = self.date.strftime('%d %B %Y') if self.date else '-'
        except Exception:
            date_str = str(self.date or '-')
        try:
            validity_str = self.validity_date.strftime('%d %B %Y') if self.validity_date else '-'
        except Exception:
            validity_str = str(self.validity_date or '-')

        return (
            '<div style="font-family:Arial,sans-serif;max-width:720px;margin:0 auto;'
            'border:1px solid #e2e3f5;border-radius:12px;overflow:hidden;">'

            # --- header ---
            '<div style="background:linear-gradient(90deg,#3b3a73 0%%,#2a2956 100%%);'
            'padding:24px 28px;color:white;">'
            '<div style="font-size:11px;letter-spacing:1.5px;color:#f59e0b;'
            'text-transform:uppercase;font-weight:600;">Quotation</div>'
            '<h1 style="margin:4px 0 0 0;font-size:26px;font-weight:700;">%(ref)s</h1>'
            '<div style="margin-top:6px;font-size:14px;opacity:0.85;">%(company)s</div>'
            '</div>'

            # --- greeting + summary ---
            '<div style="padding:24px 28px;">'
            '<p style="margin:0 0 16px 0;font-size:15px;">Dear <b>%(greeting)s</b>,</p>'
            '<p style="margin:0 0 20px 0;color:#444;">'
            'Thank you for your interest. Please find below our detailed quotation.'
            '</p>'

            # --- info table ---
            '<table style="width:100%%;border-collapse:collapse;margin-bottom:24px;'
            'background:#f8f9ff;border-radius:8px;">'
            '<tr>'
            '<td style="padding:10px 14px;color:#666;font-size:13px;">Quotation No.</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;">%(ref)s</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Date</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(date)s</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Valid Until</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;color:#f59e0b;border-top:1px solid #e2e3f5;">%(validity)s</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:10px 14px;color:#666;font-size:13px;border-top:1px solid #e2e3f5;">Salesperson</td>'
            '<td style="padding:10px 14px;text-align:right;font-weight:600;border-top:1px solid #e2e3f5;">%(salesperson)s</td>'
            '</tr>'
            '</table>'

            # --- lines ---
            '<h3 style="margin:0 0 12px 0;color:#3b3a73;font-size:16px;'
            'text-transform:uppercase;letter-spacing:0.5px;">Quotation Details</h3>'
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
            'color:#3b3a73;border-top:2px solid #f59e0b;">'
            '<b>TOTAL</b></td>'
            '<td style="padding:14px 10px;text-align:right;font-size:18px;font-weight:700;'
            'color:#3b3a73;border-top:2px solid #f59e0b;">%(symbol)s %(total).2f</td>'
            '</tr></tfoot>'
            '</table>'

            '%(terms)s'

            # --- signature ---
            '<div style="margin-top:32px;padding-top:20px;border-top:1px solid #e2e3f5;">'
            '<p style="margin:0;color:#444;">Reply to this email if you have any questions '
            'or want to discuss further.</p>'
            '<p style="margin:18px 0 0 0;">Best regards,</p>'
            '<p style="margin:4px 0 0 0;font-weight:700;color:#3b3a73;">%(salesperson)s</p>'
            '<p style="margin:0;color:#666;font-size:13px;">'
            '%(sales_email)s%(sep)s%(sales_phone)s'
            '</p>'
            '</div>'
            '</div>'

            # --- footer ---
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
            'date': date_str,
            'validity': validity_str,
            'salesperson': salesperson.name or '',
            'sales_email': sales_email,
            'sales_phone': sales_phone,
            'sep': ' · ' if (sales_email and sales_phone) else '',
            'lines': lines_html,
            'symbol': symbol,
            'total': self.amount_untaxed or 0,
            'terms': terms_block,
            'company_addr': ', '.join(filter(None, [
                company.street, company.city,
                company.state_id.name, company.country_id.name,
            ])) or '',
            'company_contact': ' · '.join(filter(None, [
                company.email, company.phone, company.website,
            ])) or '',
        }

    def _render_quotation_pdf(self):
        """Render the quotation PDF and return (filename, raw-bytes), or (None, None)."""
        self.ensure_one()
        # Odoo 19: call on the model, not on the report instance.
        # Returns (pdf_bytes, content_type) tuple.
        pdf_bytes, _content_type = self.env['ir.actions.report']._render_qweb_pdf(
            'crm_custom.action_report_quotation', res_ids=[self.id]
        )
        if not pdf_bytes:
            return None, None
        filename = 'Quotation_%s.pdf' % (self.reference or 'New').replace('/', '_')
        return filename, pdf_bytes

    def _send_via_email(self, email_to, body_html):
        self.ensure_one()
        Partner = self.env['res.partner']
        recipient = self.partner_id or Partner.search(
            [('email', '=ilike', email_to)], limit=1
        )
        if not recipient:
            # Use a friendlier default name than the email itself
            new_name = self.contact_name or (
                email_to.split('@')[0].replace('.', ' ').title()
            )
            recipient = Partner.create({'name': new_name, 'email': email_to})
        elif not recipient.email:
            recipient.email = email_to

        # Generate PDF and attach it. Surface render errors so they're debuggable.
        import base64
        attachment_ids = []
        try:
            filename, pdf_bytes = self._render_quotation_pdf()
        except Exception as e:
            _logger.exception('Quotation PDF render failed for %s', self.reference)
            raise UserError(_(
                'Could not generate the quotation PDF: %s\n\n'
                'The most common causes are:\n'
                '  - wkhtmltopdf not installed (run: which wkhtmltopdf)\n'
                '  - wkhtmltopdf is the unpatched version (apt one); '
                'install the patched build from wkhtmltopdf.org for full layout support\n'
                '  - Company Document Layout not configured: '
                'Settings → Companies → Document Layout'
            ) % e) from e
        if pdf_bytes:
            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'datas': base64.b64encode(pdf_bytes),
                'res_model': self._name,
                'res_id': self.id,
                'type': 'binary',
                'mimetype': 'application/pdf',
            })
            attachment_ids.append(attachment.id)

        # Make the message come from the salesperson, not OdooBot
        author_partner = self.user_id.partner_id or self.env.user.partner_id
        company_name = self.env.company.name or ''
        subject = _('Quotation %(ref)s — %(company)s') % {
            'ref': self.reference, 'company': company_name,
        }

        self.message_post(
            body=body_html,
            subject=subject,
            partner_ids=[recipient.id],
            attachment_ids=attachment_ids,
            author_id=author_partner.id,
            email_from=self.user_id.email_formatted or author_partner.email_formatted or None,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _send_via_whatsapp(self, phone, body_html):
        """POST a free-form text message to Meta WhatsApp Cloud API.

        Note: Meta only allows free-form text inside the 24h customer-service
        window. Outside the window, an approved message template is required
        — that path is not implemented here.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        phone_number_id = ICP.get_param('crm_custom.wa_phone_number_id')
        access_token = ICP.get_param('crm_custom.wa_access_token')
        if not phone_number_id or not access_token:
            raise UserError(_(
                'WhatsApp credentials are not configured. '
                'Set them in Settings → CRM Custom → WhatsApp.'
            ))

        # Strip HTML for WhatsApp (it only accepts plain text in text messages)
        from odoo.tools.mail import html2plaintext
        text_body = html2plaintext(body_html or '')

        # Normalize phone: strip spaces/dashes, drop leading '+'
        wa_to = ''.join(ch for ch in (phone or '') if ch.isdigit())
        if not wa_to:
            raise UserError(_('Invalid WhatsApp phone number.'))

        url = _WA_GRAPH_URL.format(phone_number_id=phone_number_id)
        payload = {
            'messaging_product': 'whatsapp',
            'to': wa_to,
            'type': 'text',
            'text': {'body': text_body},
        }
        headers = {
            'Authorization': 'Bearer %s' % access_token,
            'Content-Type': 'application/json',
        }
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        except requests.RequestException as e:
            _logger.exception('WhatsApp send failed for %s', self.reference)
            raise UserError(_('WhatsApp request failed: %s') % e) from e

        if resp.status_code >= 400:
            _logger.warning('WhatsApp send error %s: %s', resp.status_code, resp.text)
            raise UserError(_(
                'WhatsApp API returned %(code)s: %(body)s\n\n'
                'Tip: Meta only accepts free-form text within 24 hours of the '
                'customer\'s last message. Outside that window, a pre-approved '
                'template is required.'
            ) % {'code': resp.status_code, 'body': resp.text[:500]})

        data = resp.json()
        wa_msg_id = ((data.get('messages') or [{}])[0]).get('id')
        self.message_post(
            body=_('<b>WhatsApp sent</b> to %s (msg id %s):<br/>%s') % (
                wa_to, wa_msg_id or '-', body_html,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def action_accept(self):
        """Mark accepted AND auto-create a draft SO."""
        self.ensure_one()
        self.write({'state': 'accepted'})
        return self._create_sale_order(open_form=True)

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_create_order(self):
        """Manual: spawn another SO from an already-accepted/ordered quotation."""
        self.ensure_one()
        if self.state not in ('accepted', 'ordered'):
            raise UserError(_('Only accepted quotations can spawn orders.'))
        return self._create_sale_order(open_form=True)

    def _create_sale_order(self, open_form=False):
        self.ensure_one()
        SO = self.env['crm.sale.order']
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
        order = SO.create({
            'name': self.name,
            'quotation_id': self.id,
            'partner_id': self.partner_id.id,
            'contact_name': self.contact_name,
            'phone': self.phone,
            'email': self.email,
            'user_id': self.user_id.id,
            'currency_id': self.currency_id.id,
            'line_ids': line_vals,
        })
        self.message_post(
            body=_('Order %s created from this quotation.') % order.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        if open_form:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Sales Order'),
                'res_model': 'crm.sale.order',
                'res_id': order.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return order

    def action_view_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sales Orders'),
            'res_model': 'crm.sale.order',
            'view_mode': 'list,form',
            'domain': [('quotation_id', '=', self.id)],
            'context': {'default_quotation_id': self.id},
        }

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_create_revision(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(_(
                'Draft quotations can be edited directly — no revision needed.'
            ))
        root = self.root_id or self
        tree = self.search([('root_id', '=', root.id)])
        next_n = max(tree.mapped('revision_number') or [1]) + 1

        new = self.copy(default={
            'parent_id': self.id,
            'revision_number': next_n,
            'state': 'draft',
            'reference': '%s-R%d' % (root.reference, next_n),
            'validity_date': False,
            'date': False,
        })
        self.state = 'superseded'
        self.message_post(
            body=_('Superseded by revision %s.') % new.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        new.message_post(
            body=_('Created as revision of %s.') % self.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revision'),
            'res_model': 'crm.quotation',
            'res_id': new.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_original(self):
        self.ensure_one()
        if not self.parent_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Original'),
            'res_model': 'crm.quotation',
            'res_id': self.root_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_revisions(self):
        self.ensure_one()
        root = self.root_id or self
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revisions of %s') % root.reference,
            'res_model': 'crm.quotation',
            'view_mode': 'list,form',
            'domain': [('root_id', '=', root.id), ('id', '!=', self.id)],
        }

    @api.model
    def _cron_expire_quotations(self):
        """Flip quotations past validity_date to 'expired' (skips accepted/rejected)."""
        today = fields.Date.today()
        stale = self.search([
            ('state', 'in', ('draft', 'sent')),
            ('validity_date', '<', today),
        ])
        for q in stale:
            q.state = 'expired'
            q.message_post(
                body=_('Quotation auto-expired (past validity date %s).') % q.validity_date,
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        _logger.info('Quotation expire cron: expired %d quotations', len(stale))


class QuotationLine(models.Model):
    _name = 'crm.quotation.line'
    _description = 'Quotation Line'
    _order = 'sequence, id'

    quotation_id = fields.Many2one(
        'crm.quotation', string='Quotation', required=True, ondelete='cascade',
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
    currency_id = fields.Many2one(related='quotation_id.currency_id', store=True)

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
