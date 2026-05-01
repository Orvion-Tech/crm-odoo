from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wa_phone_number_id = fields.Char(
        string='WhatsApp Phone Number ID',
        config_parameter='crm_custom.wa_phone_number_id',
    )
    wa_access_token = fields.Char(
        string='WhatsApp Access Token',
        config_parameter='crm_custom.wa_access_token',
    )
    wa_verify_token = fields.Char(
        string='WhatsApp Verify Token',
        config_parameter='crm_custom.wa_verify_token',
        help='Arbitrary token you set on Meta webhook config; used for GET handshake.',
    )
    wa_app_secret = fields.Char(
        string='WhatsApp App Secret',
        config_parameter='crm_custom.wa_app_secret',
        help='Meta app secret, used to validate X-Hub-Signature-256 on incoming webhooks.',
    )
