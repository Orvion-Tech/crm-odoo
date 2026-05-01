import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class LeadStage(models.Model):
    _name = 'crm.lead.stage'
    _description = 'Lead Pipeline Stage'
    _order = 'sequence, id'

    name = fields.Char(string='Stage Name', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    fold = fields.Boolean(
        string='Folded in Kanban',
        help='Fold this stage by default in the kanban view (Won/Lost).',
    )
    is_won = fields.Boolean(string='Won Stage')
    is_lost = fields.Boolean(string='Lost Stage')


class Lead(models.Model):
    _name = 'crm.lead'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'CRM Lead'
    _order = 'priority desc, date desc, id desc'
    _rec_name = 'name'

    reference = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    name = fields.Char(string='Lead Name', required=True, tracking=True)

    inquiry_id = fields.Many2one(
        'crm.inquiry',
        string='Source Inquiry',
        copy=False,
        ondelete='set null',
        tracking=True,
    )

    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True)
    contact_name = fields.Char(string='Contact Name', tracking=True)
    phone = fields.Char(string='Phone', tracking=True)
    email = fields.Char(string='Email', tracking=True)

    description = fields.Html(string='Internal Notes')

    date = fields.Date(string='Lead Date', default=fields.Date.context_today, tracking=True)
    expected_closing_date = fields.Date(string='Expected Closing', tracking=True)

    user_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        default=lambda self: self.env.user,
        tracking=True,
    )
    team_id = fields.Many2one('res.users', string='Sales Team Lead')

    stage_id = fields.Many2one(
        'crm.lead.stage',
        string='Stage',
        ondelete='restrict',
        group_expand='_read_group_stage_ids',
        default=lambda self: self.env['crm.lead.stage'].search([], limit=1),
        tracking=True,
    )
    is_won = fields.Boolean(related='stage_id.is_won', store=True)
    is_lost = fields.Boolean(related='stage_id.is_lost', store=True)

    priority = fields.Selection(
        [('0', 'Low'), ('1', 'Normal'), ('2', 'High')],
        string='Priority',
        default='1',
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
    city = fields.Char(string='City')
    country_id = fields.Many2one('res.country', string='Country')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.INR', raise_if_not_found=False) or self.env.company.currency_id,
    )
    line_ids = fields.One2many('crm.lead.line', 'lead_id', string='Product Lines', copy=True)
    expected_revenue = fields.Monetary(
        string='Expected Revenue',
        currency_field='currency_id',
        compute='_compute_expected_revenue',
        store=True,
        readonly=False,
        tracking=True,
    )

    score = fields.Integer(
        string='Score',
        compute='_compute_score',
        store=True,
        help='0-100 lead score from customer type, revenue band and recency.',
    )

    quotation_ids = fields.One2many('crm.quotation', 'lead_id', string='Related Quotations')
    quotation_count = fields.Integer(
        string='Quotations', compute='_compute_quotation_count'
    )

    sale_order_ids = fields.One2many(
        'crm.sale.order',
        compute='_compute_sale_order_ids',
        string='Sales Orders',
    )
    sale_order_count = fields.Integer(compute='_compute_sale_order_ids')

    @api.depends('quotation_ids')
    def _compute_quotation_count(self):
        for lead in self:
            lead.quotation_count = len(lead.quotation_ids)

    @api.depends('quotation_ids.sale_order_ids')
    def _compute_sale_order_ids(self):
        for lead in self:
            orders = lead.quotation_ids.mapped('sale_order_ids')
            lead.sale_order_ids = orders
            lead.sale_order_count = len(orders)

    def action_view_sale_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sales Orders'),
            'res_model': 'crm.sale.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.sale_order_ids.ids)],
        }

    def action_create_quotation(self):
        self.ensure_one()
        Quotation = self.env['crm.quotation']
        line_vals = [
            (0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.quantity,
                'unit_price': l.unit_price,
                'sequence': l.sequence,
            })
            for l in self.line_ids
        ]
        quotation = Quotation.create({
            'name': self.name,
            'lead_id': self.id,
            'partner_id': self.partner_id.id,
            'contact_name': self.contact_name,
            'phone': self.phone,
            'email': self.email,
            'user_id': self.user_id.id,
            'currency_id': self.currency_id.id,
            'line_ids': line_vals,
        })
        self.message_post(
            body=_('Quotation %s created from lead.') % quotation.reference,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quotation'),
            'res_model': 'crm.quotation',
            'res_id': quotation.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_quotations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quotations'),
            'res_model': 'crm.quotation',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'default_lead_id': self.id},
        }

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        return stages.search([], order='sequence, id')

    @api.depends('line_ids.subtotal')
    def _compute_expected_revenue(self):
        for lead in self:
            if lead.line_ids:
                lead.expected_revenue = sum(lead.line_ids.mapped('subtotal'))

    @api.depends('customer_type', 'expected_revenue', 'date')
    def _compute_score(self):
        type_weight = {
            'builder': 25,
            'architect': 20,
            'hotel': 25,
            'office': 15,
            'residential': 10,
            'dealer': 30,
        }
        today = fields.Date.context_today(self)
        for lead in self:
            score = type_weight.get(lead.customer_type, 0)
            rev = lead.expected_revenue or 0
            if rev >= 100000:
                score += 40
            elif rev >= 50000:
                score += 30
            elif rev >= 20000:
                score += 20
            elif rev > 0:
                score += 10
            if lead.date:
                age_days = (today - lead.date).days
                if age_days <= 7:
                    score += 20
                elif age_days <= 30:
                    score += 10
            lead.score = min(score, 100)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            self.contact_name = self.partner_id.name
            self.phone = self.partner_id.phone
            self.email = self.partner_id.email

    @api.onchange('inquiry_id')
    def _onchange_inquiry_id(self):
        if not self.inquiry_id:
            return
        inq = self.inquiry_id
        if not self.name:
            self.name = inq.name
        self.partner_id = inq.partner_id
        self.contact_name = inq.partner_id.name or inq.name
        self.phone = inq.phone
        self.email = inq.email
        self.description = inq.description
        self.priority = inq.priority
        self.source = inq.source
        self.customer_type = inq.customer_type
        self.city = inq.city
        self.country_id = inq.country_id
        self.currency_id = inq.currency_id
        self.expected_revenue = inq.expected_revenue
        self.expected_closing_date = inq.expected_closing_date

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.lead') or 'New'
        return super().create(vals_list)

    def action_mark_won(self):
        won = self.env['crm.lead.stage'].search([('is_won', '=', True)], limit=1)
        if not won:
            raise UserError(_('No "Won" stage configured.'))
        self.write({'stage_id': won.id})

    def action_mark_lost(self):
        lost = self.env['crm.lead.stage'].search([('is_lost', '=', True)], limit=1)
        if not lost:
            raise UserError(_('No "Lost" stage configured.'))
        self.write({'stage_id': lost.id})


class LeadLine(models.Model):
    _name = 'crm.lead.line'
    _description = 'Lead Product Line'
    _order = 'sequence, id'

    lead_id = fields.Many2one('crm.lead', string='Lead', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('crm.product', string='Product', required=True)
    description = fields.Char(string='Description')
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    unit_price = fields.Monetary(string='Unit Price', currency_field='currency_id', required=True)
    subtotal = fields.Monetary(
        string='Subtotal',
        currency_field='currency_id',
        compute='_compute_subtotal',
        store=True,
    )
    currency_id = fields.Many2one(related='lead_id.currency_id', store=True)

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (line.quantity or 0) * (line.unit_price or 0)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.unit_price = self.product_id.list_price
            if not self.description:
                self.description = self.product_id.name
