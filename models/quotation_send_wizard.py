import json
import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_WA_GRAPH_URL = 'https://graph.facebook.com/v19.0/{phone_number_id}/messages'


class QuotationSendWizard(models.TransientModel):
    _name = 'crm.quotation.send.wizard'
    _description = 'Send Quotation Wizard'

    quotation_id = fields.Many2one(
        'crm.quotation', string='Quotation', required=True, ondelete='cascade',
    )
    channel = fields.Selection(
        [('email', 'Email'), ('whatsapp', 'WhatsApp'), ('both', 'Both')],
        string='Channel',
        required=True,
        default='email',
    )
    email_to = fields.Char(string='Email To')
    phone = fields.Char(string='WhatsApp Number')
    message_body = fields.Html(string='Message', sanitize=False)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        quotation_id = self.env.context.get('default_quotation_id') or self.env.context.get('active_id')
        if not quotation_id:
            return defaults
        q = self.env['crm.quotation'].browse(quotation_id)
        defaults.setdefault('quotation_id', q.id)
        defaults.setdefault('email_to', q.email or (q.partner_id and q.partner_id.email) or '')
        defaults.setdefault('phone', q.phone or (q.partner_id and q.partner_id.phone) or '')
        defaults.setdefault('message_body', q._build_send_body())
        return defaults

    def action_confirm(self):
        self.ensure_one()
        q = self.quotation_id
        sent_via = []

        if self.channel in ('email', 'both'):
            if not self.email_to:
                raise UserError(_('Email channel selected but no email address provided.'))
            q._send_via_email(self.email_to, self.message_body)
            sent_via.append('email')

        if self.channel in ('whatsapp', 'both'):
            if not self.phone:
                raise UserError(_('WhatsApp channel selected but no phone number provided.'))
            q._send_via_whatsapp(self.phone, self.message_body)
            sent_via.append('whatsapp')

        if sent_via:
            q.state = 'sent'
            q.message_post(
                body=_('Quotation sent via: %s.') % ', '.join(sent_via),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return {'type': 'ir.actions.act_window_close'}
