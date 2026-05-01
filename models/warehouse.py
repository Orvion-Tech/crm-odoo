from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmWarehouse(models.Model):
    _name = 'crm.warehouse'
    _description = 'CRM Warehouse'
    _order = 'sequence, code, name'
    _rec_name = 'display_name'

    code = fields.Char(string='Code', required=True, copy=False, size=8)
    name = fields.Char(string='Name', required=True, tracking=True)
    display_name = fields.Char(compute='_compute_display_name', store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    manager_id = fields.Many2one('res.users', string='Warehouse Manager')

    street = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one('res.country.state', string='State')
    zip = fields.Char()
    country_id = fields.Many2one('res.country', string='Country')
    phone = fields.Char()
    note = fields.Text()

    is_default = fields.Boolean(
        string='Default',
        help='The default warehouse is used by Sales Orders that do not specify one.',
    )

    inventory_ids = fields.One2many(
        'crm.inventory', 'warehouse_id', string='Stock Lines',
    )
    move_ids = fields.One2many(
        'crm.stock.move', 'warehouse_id', string='Movements',
    )
    product_count = fields.Integer(
        string='Products Stocked', compute='_compute_product_count',
    )

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Warehouse code must be unique.'),
    ]

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for w in self:
            if w.code and w.name:
                w.display_name = '[%s] %s' % (w.code, w.name)
            else:
                w.display_name = w.name or w.code or ''

    @api.depends('inventory_ids')
    def _compute_product_count(self):
        for w in self:
            w.product_count = len(w.inventory_ids.filtered(lambda i: i.quantity > 0))

    @api.model
    def default_warehouse(self):
        wh = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        if not wh:
            wh = self.search([('active', '=', True)], limit=1)
        return wh

    @api.constrains('is_default')
    def _check_single_default(self):
        for w in self:
            if w.is_default:
                others = self.search([
                    ('is_default', '=', True),
                    ('id', '!=', w.id),
                ])
                if others:
                    others.write({'is_default': False})

    def action_view_inventory(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Stock — %s') % self.display_name,
            'res_model': 'crm.inventory',
            'view_mode': 'list,form',
            'domain': [('warehouse_id', '=', self.id)],
            'context': {'default_warehouse_id': self.id},
        }

    def action_view_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Movements — %s') % self.display_name,
            'res_model': 'crm.stock.move',
            'view_mode': 'list,form',
            'domain': [('warehouse_id', '=', self.id)],
            'context': {'default_warehouse_id': self.id},
        }
