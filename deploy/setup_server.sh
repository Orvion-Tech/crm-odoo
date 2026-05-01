#!/usr/bin/env bash
# Idempotent setup for /etc/odoo/odoo.conf, systemd unit, and nginx vhost.
# Run on the VPS as a sudoer:  sudo bash /opt/odoo/custom_addons/crm_custom/deploy/setup_server.sh crm.example.com
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

SERVER_NAME="${1:-}"
if [ -z "$SERVER_NAME" ]; then
    echo "Usage: sudo bash $0 <server_name>" >&2
    echo "Example: sudo bash $0 crm.orvionai.com" >&2
    exit 1
fi

REPO_DIR="/opt/odoo/custom_addons/crm_custom"
DEPLOY_DIR="$REPO_DIR/deploy"

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "ERROR: $DEPLOY_DIR not found" >&2
    exit 1
fi

# 1. Passwords
[ -f /root/odoo_db_pass ]    || { echo "ERROR: /root/odoo_db_pass missing"    >&2; exit 1; }
[ -f /root/odoo_admin_pass ] || { echo "ERROR: /root/odoo_admin_pass missing" >&2; exit 1; }
DBPASS=$(cat /root/odoo_db_pass)
ADMIN=$(cat /root/odoo_admin_pass)

# 2. /etc/odoo/odoo.conf
echo "[1/4] Writing /etc/odoo/odoo.conf"
mkdir -p /etc/odoo
sed -e "s|__ADMIN__|${ADMIN}|" -e "s|__DBPASS__|${DBPASS}|" \
    "$DEPLOY_DIR/odoo.conf.tpl" > /etc/odoo/odoo.conf
chown odoo:odoo /etc/odoo/odoo.conf
chmod 640       /etc/odoo/odoo.conf

# 3. /etc/systemd/system/odoo.service
echo "[2/4] Installing systemd unit"
install -m 644 "$DEPLOY_DIR/odoo.service" /etc/systemd/system/odoo.service
systemctl daemon-reload
systemctl enable odoo

# 4. /etc/nginx/sites-available/odoo
echo "[3/4] Writing nginx vhost (server_name = $SERVER_NAME)"
sed -e "s|__SERVER_NAME__|${SERVER_NAME}|" \
    "$DEPLOY_DIR/odoo.nginx" > /etc/nginx/sites-available/odoo
ln -sf /etc/nginx/sites-available/odoo /etc/nginx/sites-enabled/odoo
nginx -t

# 5. Logs dir
mkdir -p /var/log/odoo
chown odoo:odoo /var/log/odoo

echo
echo "[4/4] Verification"
ls -l /etc/odoo/odoo.conf /etc/systemd/system/odoo.service /etc/nginx/sites-available/odoo
echo
echo "odoo.conf key settings:"
grep -E '^(db_user|db_name|workers|addons_path|gevent_port|xmlrpc_port|proxy_mode|admin_passwd)' \
    /etc/odoo/odoo.conf | sed 's/admin_passwd = .*/admin_passwd = ********/'
echo
echo "Done. Next steps (run after reviewing above):"
echo "  sudo systemctl start odoo"
echo "  sudo systemctl status odoo"
echo "  sudo journalctl -u odoo -n 50 --no-pager"
echo "  sudo systemctl reload nginx"
