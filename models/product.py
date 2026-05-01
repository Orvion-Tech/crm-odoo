from odoo import api, fields, models


class ProductCategory(models.Model):
    _name = 'crm.product.category'
    _description = 'Smart Lock Product Category'
    _order = 'sequence, name'

    name = fields.Char(string='Category Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    description = fields.Text(string='Description')
    product_count = fields.Integer(
        string='Products',
        compute='_compute_product_count',
    )

    @api.depends()
    def _compute_product_count(self):
        Product = self.env['crm.product']
        for cat in self:
            cat.product_count = Product.search_count([('category_id', '=', cat.id)])

    def action_view_products(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'crm.product',
            'view_mode': 'kanban,list,form',
            'domain': [('category_id', '=', self.id)],
            'context': {'default_category_id': self.id},
        }


class Product(models.Model):
    _name = 'crm.product'
    _description = 'Smart Lock Product'
    _order = 'sequence, name'

    reference = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    name = fields.Char(string='Product Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    category_id = fields.Many2one(
        'crm.product.category',
        string='Category',
        required=True,
        ondelete='restrict',
    )
    sku = fields.Char(string='SKU', copy=False)
    description = fields.Html(string='Description')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.INR', raise_if_not_found=False) or self.env.company.currency_id,
    )
    list_price = fields.Monetary(
        string='List Price',
        currency_field='currency_id',
        required=True,
    )

    active = fields.Boolean(string='Active', default=True)
    image = fields.Image(string='Image', max_width=1024, max_height=1024)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.product') or 'New'
        return super().create(vals_list)
