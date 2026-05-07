/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";

const DASHBOARD_MODEL = "crm.dashboard";
const BODY_CLASS = "o_crm_dashboard_active";

patch(FormController.prototype, {
    setup() {
        super.setup();
        onMounted(() => {
            if (this.props.resModel === DASHBOARD_MODEL) {
                document.body.classList.add(BODY_CLASS);
            }
        });
        onWillUnmount(() => {
            if (this.props.resModel === DASHBOARD_MODEL) {
                document.body.classList.remove(BODY_CLASS);
            }
        });
    },
});
