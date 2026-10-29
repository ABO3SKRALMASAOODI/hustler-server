#!/bin/bash
# Valmera render box: install/refresh the executor service (idempotent).
# Run as root from a checkout: box/host/install.sh. Hardening is harden.sh.
# A second box passes its TLS name and slot plan (box/host/add_box.sh does):
#   BOX_HOSTNAME=1-2-3-4.sslip.io BOX_PLAN=box-h2.json box/host/install.sh
# Without them this installs the first box exactly as before.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
host=${BOX_HOSTNAME:-13-140-241-231.sslip.io}
plan=${BOX_PLAN:-box.json}
case "$plan" in box*.json) ;; *) echo "bad BOX_PLAN $plan" >&2; exit 2;; esac
test -f "$here/host/$plan" || { echo "no slot plan $plan" >&2; exit 2; }

install -d -m 0700 /etc/valmera
install -d -m 0755 /opt/valmera/box /var/lib/valmera /var/lib/valmera/slots \
  /var/log/valmera /var/log/caddy

# Tooling (boxctl update keeps it current from main afterwards).
cp -a "$here/." /opt/valmera/box/
chmod 0755 /opt/valmera/box/*.py

# Configuration: box.json and slot.env are not secret; executor.env is
# written only by provision.py (GitHub OIDC) and never by hand.
install -m 0644 "$here/host/$plan" /etc/valmera/box.json
if [ "$plan" != box.json ]; then
  printf '%s\n' "$plan" > /etc/valmera/plan   # boxctl keeps this plan current
fi
install -m 0600 "$here/host/slot.env" /etc/valmera/slot.env
test -s /etc/valmera/admin.token || \
  (umask 077; head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > /etc/valmera/admin.token)
test -e /etc/valmera/executor.env || (umask 077; : > /etc/valmera/executor.env)

# Slot containers reach admission on the bridge gateway for their own
# synchronous children (frames, captures); nothing else may.
docker network inspect valmera >/dev/null 2>&1 || docker network create \
  --driver bridge --subnet 172.30.0.0/24 --gateway 172.30.0.1 \
  -o com.docker.network.bridge.name=vbox0 valmera
ufw allow in on vbox0 from 172.30.0.0/24 to 172.30.0.1 port 8090 proto tcp \
  comment 'slot containers -> admission' >/dev/null

sed "s/13-140-241-231\.sslip\.io/$host/" "$here/host/Caddyfile" > /etc/caddy/Caddyfile.valmera
install -m 0644 /etc/caddy/Caddyfile.valmera /etc/caddy/Caddyfile
rm -f /etc/caddy/Caddyfile.valmera
for unit in valmera-admission.service valmera-provision.service \
    valmera-update.service valmera-update.timer valmera-clean.service \
    valmera-clean.timer; do
  install -m 0644 "$here/host/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now valmera-provision.service valmera-admission.service
systemctl enable --now valmera-update.timer valmera-clean.timer
systemctl reload caddy || systemctl restart caddy
echo "install: ok"
