import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'[\w.+-]+@[\w.-]+\.\w+')


def _extract_primary_email(from_header):
    """Pull the first bare email out of an RFC 2822 From header."""
    if not from_header:
        return ''
    match = _EMAIL_RE.search(from_header)
    return match.group(0).lower() if match else ''


class Inquiry(models.Model):
    _name = 'crm.inquiry'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Customer Inquiry'
    _order = 'date desc, id desc'
    _rec_name = 'name'
    _mail_post_access = 'read'
    _primary_email = 'email'

    reference = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    name = fields.Char(string='Customer Name', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True)
    phone = fields.Char(string='Phone', tracking=True)
    email = fields.Char(string='Email', tracking=True)
    description = fields.Html(string='Description')

    date = fields.Date(string='Inquiry Date', default=fields.Date.context_today)
    user_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        default=lambda self: self.env.user,
        tracking=True,
    )
    priority = fields.Selection(
        [('0', 'Low'), ('1', 'Normal'), ('2', 'High')],
        string='Priority',
        default='1',
        tracking=True,
    )
    state = fields.Selection(
        [
            ('new', 'New'),
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='new',
        required=True,
        tracking=True,
    )

    source = fields.Selection(
        [
            ('website', 'Website'),
            ('phone', 'Phone'),
            ('walk_in', 'Walk-in'),
            ('referral', 'Referral'),
            ('exhibition', 'Exhibition'),
            ('email', 'Email'),
            ('whatsapp', 'WhatsApp'),
        ],
        string='Source',
        default='phone',
        tracking=True,
    )
    customer_type = fields.Selection(
        [
            ('builder', 'Builder'),
            ('architect', 'Architect'),
            ('hotel', 'Hotel'),
            ('office', 'Office'),
            ('residential', 'Residential'),
            ('dealer', 'Dealer'),
        ],
        string='Customer Type',
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.INR', raise_if_not_found=False) or self.env.company.currency_id,
    )
    expected_revenue = fields.Monetary(
        string='Expected Revenue',
        currency_field='currency_id',
        tracking=True,
    )
    expected_closing_date = fields.Date(string='Expected Closing')
    city = fields.Char(string='City')
    country_id = fields.Many2one('res.country', string='Country')
    quantity_estimate = fields.Integer(string='Quantity Estimate')

    whatsapp_message_id = fields.Char(
        string='WhatsApp Message ID',
        copy=False,
        index=True,
        help='Last processed WhatsApp message id; used for deduplication.',
    )

    duplicate_count = fields.Integer(
        string='Possible Duplicates',
        compute='_compute_duplicate_count',
    )

    lead_ids = fields.One2many('crm.lead', 'inquiry_id', string='Converted Leads')
    lead_count = fields.Integer(string='Leads', compute='_compute_lead_count')

    @api.depends('lead_ids')
    def _compute_lead_count(self):
        for rec in self:
            rec.lead_count = len(rec.lead_ids)

    def action_convert_to_lead(self):
        self.ensure_one()
        if self.state == 'cancelled':
            raise UserError(_('Cannot convert a cancelled inquiry.'))
        Lead = self.env['crm.lead']
        lead = Lead.create({
            'name': self.name,
            'inquiry_id': self.id,
            'partner_id': self.partner_id.id,
            'contact_name': self.partner_id.name or self.name,
            'phone': self.phone,
            'email': self.email,
            'description': self.description,
            'user_id': self.user_id.id,
            'priority': self.priority,
            'source': self.source,
            'customer_type': self.customer_type,
            'city': self.city,
            'country_id': self.country_id.id,
            'currency_id': self.currency_id.id,
            'expected_revenue': self.expected_revenue,
            'expected_closing_date': self.expected_closing_date,
        })
        if self.state == 'new':
            self.state = 'in_progress'
        self.message_post(
            body=_('Converted to lead %s.') % lead.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lead'),
            'res_model': 'crm.lead',
            'res_id': lead.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_leads(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Leads'),
            'res_model': 'crm.lead',
            'view_mode': 'list,form',
            'domain': [('inquiry_id', '=', self.id)],
            'context': {'default_inquiry_id': self.id},
        }

    @api.depends('phone', 'email')
    def _compute_duplicate_count(self):
        for rec in self:
            if not rec.id:
                rec.duplicate_count = 0
                continue
            domain = [('id', '!=', rec.id)]
            matches = []
            if rec.phone:
                matches.append(('phone', '=', rec.phone))
            if rec.email:
                matches.append(('email', '=ilike', rec.email))
            if not matches:
                rec.duplicate_count = 0
                continue
            if len(matches) == 2:
                domain += ['|'] + matches
            else:
                domain += matches
            rec.duplicate_count = self.search_count(domain)

    def action_view_duplicates(self):
        self.ensure_one()
        domain = [('id', '!=', self.id)]
        matches = []
        if self.phone:
            matches.append(('phone', '=', self.phone))
        if self.email:
            matches.append(('email', '=ilike', self.email))
        if len(matches) == 2:
            domain += ['|'] + matches
        else:
            domain += matches
        return {
            'type': 'ir.actions.act_window',
            'name': _('Possible Duplicates'),
            'res_model': 'crm.inquiry',
            'view_mode': 'list,form',
            'domain': domain,
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.inquiry') or 'New'
        return super().create(vals_list)

    @api.model
    def _cron_flag_stale_inquiries(self):
        """Post an internal note on inquiries stuck in 'new' for 3+ days."""
        threshold = fields.Date.today() - timedelta(days=3)
        stale = self.search([('state', '=', 'new'), ('date', '<', threshold)])
        for inq in stale:
            inq.message_post(
                body=_('Inquiry has been in "New" state for more than 3 days. Please follow up.'),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        _logger.info('Stale inquiry cron: flagged %d inquiries', len(stale))

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.name = self.partner_id.name
            self.phone = self.partner_id.phone
            self.email = self.partner_id.email

    def action_in_progress(self):
        self.write({'state': 'in_progress'})

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset(self):
        self.write({'state': 'new'})

    # ------------------------------------------------------------------
    # Incoming email → inquiry
    # ------------------------------------------------------------------
    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Create an inquiry from an incoming email routed via mail.alias.

        Odoo's mail router calls this when an email hits an alias whose
        alias_model_id points at crm.inquiry.
        """
        defaults = {
            'name': msg_dict.get('subject') or _('New Email Inquiry'),
            'email': _extract_primary_email(msg_dict.get('from') or msg_dict.get('email_from') or ''),
            'description': msg_dict.get('body') or '',
            'source': 'email',
            'state': 'new',
        }
        if custom_values:
            defaults.update(custom_values)

        # Link/create partner from the sender's email
        author_id = msg_dict.get('author_id')
        if author_id:
            defaults['partner_id'] = author_id
        elif defaults.get('email'):
            Partner = self.env['res.partner']
            partner = Partner.search([('email', '=ilike', defaults['email'])], limit=1)
            if not partner:
                partner = Partner.create({
                    'name': msg_dict.get('from_name') or defaults['email'],
                    'email': defaults['email'],
                })
            defaults['partner_id'] = partner.id
            if not defaults.get('name') or defaults['name'] == _('New Email Inquiry'):
                defaults['name'] = partner.name

        inquiry = super().message_new(msg_dict, custom_values=defaults)
        _logger.info('Email inquiry created: %s from=%s', inquiry.reference, defaults.get('email'))
        return inquiry

    def message_update(self, msg_dict, update_vals=None):
        """Reply on an existing inquiry: just log it; do not mutate the record."""
        return super().message_update(msg_dict, update_vals=update_vals)

    @api.model
    def _create_from_whatsapp(self, payload):
        """Entry point called by the WhatsApp webhook controller."""
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {}) or {}
                contacts = {c.get('wa_id'): c for c in value.get('contacts', []) or []}
                for msg in value.get('messages', []) or []:
                    self._process_whatsapp_message(msg, contacts)

    @api.model
    def _process_whatsapp_message(self, msg, contacts):
        wa_msg_id = msg.get('id')
        if not wa_msg_id:
            return
        if self.search_count([('whatsapp_message_id', '=', wa_msg_id)]):
            return

        phone = msg.get('from')
        contact = contacts.get(phone, {}) or {}
        name = (contact.get('profile') or {}).get('name') or phone or 'WhatsApp Contact'
        msg_type = msg.get('type', 'text')
        body = (msg.get('text') or {}).get('body') or f'[{msg_type}]'

        open_inq = self.search([
            ('phone', '=', phone),
            ('state', 'in', ('new', 'in_progress')),
        ], limit=1)

        if open_inq:
            open_inq.message_post(
                body=f'<b>WhatsApp</b>: {body}',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            open_inq.whatsapp_message_id = wa_msg_id
            return open_inq

        Partner = self.env['res.partner']
        partner = Partner.search([('phone', '=', phone)], limit=1) or Partner.create({
            'name': name,
            'phone': phone,
        })

        inquiry = self.create({
            'name': name,
            'phone': phone,
            'partner_id': partner.id,
            'description': f'<p>{body}</p>',
            'source': 'whatsapp',
            'state': 'new',
            'whatsapp_message_id': wa_msg_id,
        })
        _logger.info('WhatsApp inquiry created: %s (wa_msg_id=%s)', inquiry.reference, wa_msg_id)
        return inquiry
