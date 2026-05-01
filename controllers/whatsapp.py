import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppWebhook(http.Controller):
    """Meta WhatsApp Cloud API webhook endpoint.

    GET  /crm_custom/whatsapp/webhook  -> verification handshake
    POST /crm_custom/whatsapp/webhook  -> incoming message events
    """

    _WEBHOOK_URL = '/crm_custom/whatsapp/webhook'

    @http.route(_WEBHOOK_URL, type='http', auth='public',
                methods=['GET'], csrf=False)
    def verify(self, **params):
        ICP = request.env['ir.config_parameter'].sudo()
        expected = ICP.get_param('crm_custom.wa_verify_token') or ''
        mode = params.get('hub.mode')
        token = params.get('hub.verify_token')
        challenge = params.get('hub.challenge', '')

        if mode == 'subscribe' and token and hmac.compare_digest(token, expected):
            return challenge
        _logger.warning('WhatsApp webhook verification failed: mode=%s', mode)
        return request.make_response('Forbidden', status=403)

    @http.route(_WEBHOOK_URL, type='http', auth='public',
                methods=['POST'], csrf=False)
    def incoming(self, **kw):
        raw = request.httprequest.data or b''
        provided_sig = request.httprequest.headers.get('X-Hub-Signature-256', '')
        ICP = request.env['ir.config_parameter'].sudo()
        app_secret = ICP.get_param('crm_custom.wa_app_secret') or ''

        if not app_secret:
            _logger.error('WhatsApp webhook: app_secret not configured')
            return request.make_response('Server misconfigured', status=500)

        expected_sig = 'sha256=' + hmac.new(
            app_secret.encode('utf-8'), raw, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, provided_sig):
            _logger.warning('WhatsApp webhook: signature mismatch')
            return request.make_response('Forbidden', status=403)

        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except ValueError:
            _logger.exception('WhatsApp webhook: invalid JSON')
            return request.make_response('Bad Request', status=400)

        try:
            request.env['crm.inquiry'].sudo()._create_from_whatsapp(payload)
        except Exception:
            _logger.exception('WhatsApp webhook: processing failed')
            # Return 200 so Meta doesn't retry endlessly on our bugs;
            # logs capture the failure for offline inspection.

        return request.make_response('OK', status=200)
