from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmInventory(models.Model):
    """Stock-on-hand row per (product, warehouse).

    The `quantity` field is computed by summing all confirmed stock moves
    for the (product, warehouse) pair — IN moves add, OUT moves subtract.
    """
    _name = 'crm.inventory'
    _description = 'CRM Stock On Hand'
    _order = 'warehouse_id, product_id'
    _rec_name = 'display_name'

    product_id = fields.Many2one(
        'crm.product', string='Product', required=True, ondelete='cascade',
    )
    warehouse_id = fields.Many2one(
        'crm.warehouse', string='Warehouse', required=True, ondelete='cascade',
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    quantity = fields.Float(
        string='On Hand',
        compute='_compute_quantity',
        store=True,
        help='Quantity available — sum of confirmed IN moves minus confirmed OUT moves.',
    )
    reorder_min = fields.Float(
        string='Reorder Threshold', default=0.0,
        help='When On Hand drops below this, the product is flagged as low stock.',
    )
    is_low_stock = fields.Boolean(
        compute='_compute_quantity', store=True,
    )

    move_ids = fields.One2many(
        'crm.stock.move', compute='_compute_move_ids',
        string='Movements',
    )
    move_count = fields.Integer(compute='_compute_move_ids')

    _sql_constraints = [
        ('product_warehouse_unique',
         'unique(product_id, warehouse_id)',
         'Only one stock row per product per warehouse.'),
    ]

    @api.depends('product_id', 'warehouse_id', 'product_id.name',
                 'warehouse_id.code', 'warehouse_id.name')
    def _compute_display_name(self):
        for inv in self:
            inv.display_name = '%s @ %s' % (
                inv.product_id.name or '',
                inv.warehouse_id.display_name or '',
            )

    @api.depends('product_id', 'warehouse_id')
    def _compute_move_ids(self):
        Move = self.env['crm.stock.move']
        for inv in self:
            moves = Move.search([
                ('product_id', '=', inv.product_id.id),
                ('warehouse_id', '=', inv.warehouse_id.id),
            ])
            inv.move_ids = moves
            inv.move_count = len(moves)

    @api.depends('move_ids.state', 'move_ids.signed_quantity', 'reorder_min')
    def _compute_quantity(self):
        for inv in self:
            done = inv.move_ids.filtered(lambda m: m.state == 'done')
            qty = sum(done.mapped('signed_quantity'))
            inv.quantity = qty
            inv.is_low_stock = (
                inv.reorder_min > 0 and qty <= inv.reorder_min
            )

    @api.model
    def _get_or_create(self, product, warehouse):
        if not product or not warehouse:
            raise UserError(_('Both product and warehouse are required.'))
        inv = self.search([
            ('product_id', '=', product.id),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not inv:
            inv = self.create({
                'product_id': product.id,
                'warehouse_id': warehouse.id,
            })
        return inv

    @api.model
    def get_quantity(self, product, warehouse):
        """Return current on-hand for a (product, warehouse). 0 if no row exists."""
        if not product or not warehouse:
            return 0.0
        inv = self.search([
            ('product_id', '=', product.id),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        return inv.quantity if inv else 0.0

    def action_view_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Movements — %s') % self.display_name,
            'res_model': 'crm.stock.move',
            'view_mode': 'list,form',
            'domain': [
                ('product_id', '=', self.product_id.id),
                ('warehouse_id', '=', self.warehouse_id.id),
            ],
            'context': {
                'default_product_id': self.product_id.id,
                'default_warehouse_id': self.warehouse_id.id,
            },
        }

    def action_adjust_stock(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Adjust Stock'),
            'res_model': 'crm.stock.adjust.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_id': self.product_id.id,
                'default_warehouse_id': self.warehouse_id.id,
                'default_current_qty': self.quantity,
            },
        }


class CrmStockMove(models.Model):
    """A single stock movement: IN (receipt), OUT (delivery), or ADJUST."""
    _name = 'crm.stock.move'
    _description = 'CRM Stock Move'
    _order = 'date desc, id desc'
    _rec_name = 'reference'

    reference = fields.Char(
        string='Reference', required=True, copy=False, readonly=True, default='New',
    )
    move_type = fields.Selection(
        [('in', 'Receipt (IN)'),
         ('out', 'Delivery (OUT)'),
         ('adjust', 'Adjustment')],
        string='Type', required=True, default='in', tracking=True,
    )
    state = fields.Selection(
        [('draft', 'Draft'),
         ('done', 'Done'),
         ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True,
    )
    date = fields.Datetime(
        string='Date', default=fields.Datetime.now, required=True, tracking=True,
    )
    product_id = fields.Many2one('crm.product', required=True, ondelete='restrict')
    warehouse_id = fields.Many2one('crm.warehouse', required=True, ondelete='restrict')
    quantity = fields.Float(string='Quantity', required=True, default=1.0)
    signed_quantity = fields.Float(
        string='Signed Quantity',
        compute='_compute_signed_quantity', store=True,
        help='Positive for IN, negative for OUT, signed delta for ADJUST.',
    )

    sale_order_id = fields.Many2one(
        'crm.sale.order', string='Source Order', ondelete='set null',
    )
    note = fields.Text(string='Note')
    user_id = fields.Many2one(
        'res.users', string='Done By', default=lambda self: self.env.user,
    )

    @api.depends('move_type', 'quantity')
    def _compute_signed_quantity(self):
        for m in self:
            qty = m.quantity or 0.0
            if m.move_type == 'in':
                m.signed_quantity = qty
            elif m.move_type == 'out':
                m.signed_quantity = -qty
            else:  # adjust — quantity may already be signed
                m.signed_quantity = qty

    @api.constrains('move_type', 'quantity')
    def _check_quantity(self):
        for m in self:
            if m.move_type in ('in', 'out') and m.quantity <= 0:
                raise UserError(_('Receipt and Delivery quantities must be greater than zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == 'New':
                vals['reference'] = self.env['ir.sequence'].next_by_code('crm.stock.move') or 'New'
        moves = super().create(vals_list)
        # Make sure the inventory row exists so on-hand recomputes pick it up.
        Inv = self.env['crm.inventory']
        for m in moves:
            Inv._get_or_create(m.product_id, m.warehouse_id)
        return moves

    def action_done(self):
        for m in self:
            if m.state != 'draft':
                raise UserError(_('Only draft moves can be confirmed.'))
            if m.move_type == 'out':
                on_hand = self.env['crm.inventory'].get_quantity(m.product_id, m.warehouse_id)
                if on_hand < m.quantity:
                    raise UserError(_(
                        'Insufficient stock for %(product)s at %(wh)s.\n'
                        'On hand: %(have)g — Trying to ship: %(need)g'
                    ) % {
                        'product': m.product_id.name,
                        'wh': m.warehouse_id.display_name,
                        'have': on_hand,
                        'need': m.quantity,
                    })
        self.write({'state': 'done'})
        # Trigger recompute on the matching inventory rows
        Inv = self.env['crm.inventory']
        for m in self:
            inv = Inv._get_or_create(m.product_id, m.warehouse_id)
            inv._compute_quantity()

    def action_cancel(self):
        for m in self:
            if m.state == 'done':
                raise UserError(_(
                    'A confirmed move cannot be cancelled. Create a reverse move instead.'
                ))
            m.state = 'cancelled'

    def action_reset_draft(self):
        for m in self:
            if m.state != 'cancelled':
                raise UserError(_('Only cancelled moves can be reset to draft.'))
            m.state = 'draft'

    @api.ondelete(at_uninstall=False)
    def _block_delete_done(self):
        for m in self:
            if m.state == 'done':
                raise UserError(_(
                    'Confirmed stock moves cannot be deleted — cancel-and-reverse instead.'
                ))


class CrmStockAdjustWizard(models.TransientModel):
    """Adjust on-hand to a target quantity, generating a signed ADJUST move."""
    _name = 'crm.stock.adjust.wizard'
    _description = 'Stock Adjustment Wizard'

    product_id = fields.Many2one('crm.product', required=True)
    warehouse_id = fields.Many2one('crm.warehouse', required=True)
    current_qty = fields.Float(string='Current On Hand', readonly=True)
    target_qty = fields.Float(string='Target On Hand', required=True)
    reason = fields.Char(string='Reason', required=True)

    def action_apply(self):
        self.ensure_one()
        delta = (self.target_qty or 0.0) - (self.current_qty or 0.0)
        if not delta:
            raise UserError(_('Target equals current quantity — nothing to adjust.'))
        move = self.env['crm.stock.move'].create({
            'move_type': 'adjust',
            'product_id': self.product_id.id,
            'warehouse_id': self.warehouse_id.id,
            'quantity': delta,
            'note': self.reason,
        })
        move.action_done()
        return {'type': 'ir.actions.act_window_close'}
