from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _crm_custom_hide_other_apps(self):
        """Deactivate every top-level app menu except CRM Custom and Settings.

        Called from data/menu_hide.xml on every module install/upgrade so the
        app drawer shows only this module. The Settings app is always kept
        visible so admins can still reach Technical settings, System
        Parameters, Users, etc. To bring other apps back, enable developer
        mode → Settings → Technical → Menus, or remove this hook.
        """
        crm_root = self.env.ref('crm_custom.menu_crm_root', raise_if_not_found=False)
        if not crm_root:
            return
        keep_ids = [crm_root.id]
        settings_root = self.env.ref('base.menu_administration', raise_if_not_found=False)
        if settings_root:
            keep_ids.append(settings_root.id)
            # re-activate Settings in case a previous run hid it
            if not settings_root.active:
                settings_root.write({'active': True})
        others = self.search([
            ('parent_id', '=', False),
            ('id', 'not in', keep_ids),
            ('active', '=', True),
        ])
        if others:
            others.write({'active': False})
