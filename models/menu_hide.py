from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _crm_custom_hide_other_apps(self):
        """Deactivate every top-level app menu except CRM Custom.

        Called from data/menu_hide.xml on every module install/upgrade so the
        app drawer shows only this module. To bring other apps back, enable
        developer mode → Settings → Technical → Menus, or remove this hook.
        """
        crm_root = self.env.ref('crm_custom.menu_crm_root', raise_if_not_found=False)
        if not crm_root:
            return
        others = self.search([
            ('parent_id', '=', False),
            ('id', '!=', crm_root.id),
            ('active', '=', True),
        ])
        if others:
            others.write({'active': False})
