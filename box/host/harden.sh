#!/bin/bash
# Valmera render box: host hardening (idempotent). Run as root on Ubuntu 24.04.
# Key-only SSH, fail2ban, ufw (22 + 443 only), unattended security upgrades,
# UTC + time sync, swap, journald limits, Docker with bounded logs.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# ── SSH: keys only, root by key only ─────────────────────────────────────
# 00- sorts before cloud-init's 50- file; sshd keeps the FIRST value it reads.
cat > /etc/ssh/sshd_config.d/00-valmera.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 60
ClientAliveCountMax 5
CONF
sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config.d/50-cloud-init.conf 2>/dev/null || true
mkdir -p /etc/cloud/cloud.cfg.d
printf 'ssh_pwauth: false\n' > /etc/cloud/cloud.cfg.d/99-valmera-ssh.cfg
sshd -t
systemctl reload ssh

# ── Packages ─────────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y -q fail2ban ufw unattended-upgrades apt-listchanges \
  docker.io docker-buildx caddy git jq curl sqlite3 python3 \
  chrony logrotate ca-certificates

# ── Time: UTC + chrony (replaces systemd-timesyncd) ─────────────────────
timedatectl set-timezone UTC
systemctl enable --now chrony

# ── fail2ban: sshd jail on the systemd journal ──────────────────────────
cat > /etc/fail2ban/jail.d/valmera.local <<'CONF'
[DEFAULT]
backend = systemd
bantime = 1h
findtime = 10m
maxretry = 5
bantime.increment = true
bantime.maxtime = 1w

[sshd]
enabled = true
port = 22
CONF
systemctl enable fail2ban
systemctl restart fail2ban

# ── Firewall: deny inbound except SSH and HTTPS ─────────────────────────
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow 22/tcp comment 'ssh (key only)'
ufw allow 443/tcp comment 'caddy https -> admission'
ufw allow 443/udp comment 'caddy http3'
ufw --force enable

# ── Unattended SECURITY upgrades; reboots are drained by valmera-maint ──
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
cat > /etc/apt/apt.conf.d/52valmera-unattended <<'CONF'
Unattended-Upgrade::Allowed-Origins {
        "${distro_id}:${distro_codename}-security";
        "${distro_id}ESMApps:${distro_codename}-apps-security";
        "${distro_id}ESM:${distro_codename}-infra-security";
};
// Docker restarts would kill in-flight renders; the drained maintenance
// window (valmera-maint.timer) restarts it with the box.
Unattended-Upgrade::Package-Blacklist { "docker.io"; "containerd"; "runc"; };
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF
systemctl enable --now unattended-upgrades

# ── Swap: 8 GiB host safety net (containers get none: memory-swap=memory)
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 8G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
cat > /etc/sysctl.d/90-valmera.conf <<'CONF'
vm.swappiness = 10
vm.overcommit_memory = 0
fs.inotify.max_user_watches = 524288
net.core.somaxconn = 1024
CONF
sysctl --system >/dev/null

# ── journald: bounded ───────────────────────────────────────────────────
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/90-valmera.conf <<'CONF'
[Journal]
Storage=persistent
SystemMaxUse=2G
SystemKeepFree=10G
SystemMaxFileSize=128M
MaxRetentionSec=30day
RateLimitIntervalSec=30s
RateLimitBurst=20000
CONF
systemctl restart systemd-journald

# ── Docker: bounded json logs, live-restore so a daemon restart keeps
#    containers (and their in-flight calls) running ──────────────────────
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'CONF'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "50m", "max-file": "5"},
  "live-restore": true,
  "default-ulimits": {"nofile": {"Name": "nofile", "Soft": 65536, "Hard": 65536}}
}
CONF
systemctl enable docker
systemctl restart docker

# ── Valmera directories ─────────────────────────────────────────────────
install -d -m 0700 /etc/valmera
install -d -m 0755 /opt/valmera /var/lib/valmera /var/lib/valmera/slots /var/log/valmera
echo "harden: ok"
