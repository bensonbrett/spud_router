#!/bin/bash
# =============================================================================
# spud-router — appliance installer
# Tested: Armbian minimal (Le Potato / AML-S905X-CC), Ubuntu 22.04/24.04
#
# Run from the extracted release tarball:
#   tar xzf spud-router-v1.0.0.tar.gz
#   sudo bash install.sh
#
# Tarball contents: install.sh  backend/  spud-cli  ssh-banner  motd  update.py
#                   run-update.sh  index.html  VERSION
# =============================================================================
set -euo pipefail

# Read version from VERSION file (written by release workflow)
SPUD_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
SPUD_DIR="/opt/spud-router"
SPUD_CONF="/etc/spud-router"
SPUD_PORT="8080"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info() { echo -e "${BLU}[spud]${NC} $*"; }
ok()   { echo -e "${GRN}[✓]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗] $*${NC}"; exit 1; }

# /usr/local/bin/spud-cli becomes the 'spud' user's login shell. A
# truncated/invalid copy silently promoted there bricks SSH as that user
# with a cryptic "exec format error" and no hint at the cause (observed on
# real hardware — a healthy disk, just a bad write). "Valid" = non-empty and
# starts with a shebang.
_valid_spudcli() {  # $1 = path
    [[ -s "$1" ]] && [[ "$(head -c2 "$1" 2>/dev/null)" == "#!" ]]
}

[[ $EUID -ne 0 ]] && die "Must run as root (sudo bash $0)"

# ── Logging ───────────────────────────────────────────────────────────────────
INSTALL_LOG="/var/log/spud-router-install.log"
exec > >(tee -a "$INSTALL_LOG") 2>&1
info "Logging to $INSTALL_LOG"

echo ""
echo "  🥔  spud-router appliance installer v${SPUD_VERSION}"
echo "  ─────────────────────────────────────────────────"
echo ""

# ── 1. Platform check ─────────────────────────────────────────────────────────
ARCH=$(uname -m)
info "Platform: $ARCH / $(. /etc/os-release && echo "$PRETTY_NAME")"
[[ "$ARCH" != "aarch64" && "$ARCH" != "x86_64" ]] && warn "Untested architecture: $ARCH"

# ── 2. Packages ───────────────────────────────────────────────────────────────
info "Installing packages..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-pip python3-venv \
    dnsmasq iptables iptables-persistent \
    vlan netplan.io \
    hostapd \
    iw wireless-tools \
    curl jq \
    openssl \
    fail2ban openssh-server \
    unattended-upgrades
ok "Packages installed"

# Load 802.1q VLAN module
modprobe 8021q
echo "8021q" > /etc/modules-load.d/8021q.conf
ok "802.1q module loaded"

# ── 3. Disable conflicting services ──────────────────────────────────────────
info "Configuring services..."

if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    systemctl stop NetworkManager && systemctl disable NetworkManager
    warn "Disabled NetworkManager (networkd will manage interfaces)"
fi

# Run systemd-resolved with its stub listener OFF: dnsmasq owns port 53 and is
# the LAN resolver, while resolved harvests DHCP-provided upstream DNS into
# /run/systemd/resolve/resolv.conf, which dnsmasq reads in "auto" mode.
# NOTE: /etc/resolv.conf is NOT pointed at 127.0.0.1 here — dnsmasq isn't
# actually running yet (it's stopped below and not restarted with a real
# config until the bootstrap step near the end of this script). Rewriting
# resolv.conf this early leaves nothing listening on port 53 and breaks DNS
# for the rest of the install, including the pip install a few steps down.
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/spud-router.conf << 'RESOLVEDEOF'
[Resolve]
DNSStubListener=no
RESOLVEDEOF
systemctl enable systemd-resolved
systemctl restart systemd-resolved
# Point resolv.conf at resolved's non-stub file (real upstream nameservers)
# for now, so DNS keeps working for the rest of this script (apt, pip, curl).
# This gets switched to 127.0.0.1 once dnsmasq is actually up, further down.
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
ok "systemd-resolved configured (stub listener off — dnsmasq owns DNS)"

systemctl enable systemd-networkd
systemctl start systemd-networkd
systemctl enable netfilter-persistent 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
# hostapd is managed by spud-router Apply — don't start it yet
systemctl stop hostapd    2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
ok "Services configured"

# ── 3b. Service user ──────────────────────────────────────────────────────────
info "Creating spud-router service user..."
if ! id -u spud-router &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d /nonexistent -c "spud-router web UI" spud-router
    ok "Created system user 'spud-router'"
else
    ok "System user 'spud-router' already exists"
fi

# ── 4. Python venv ────────────────────────────────────────────────────────────
info "Creating Python environment..."
mkdir -p "$SPUD_DIR"
python3 -m venv "$SPUD_DIR/venv"
"$SPUD_DIR/venv/bin/pip" install --quiet --upgrade pip
"$SPUD_DIR/venv/bin/pip" install --quiet fastapi "uvicorn[standard]"
ok "Python venv ready ($SPUD_DIR/venv)"


# ── 5. Install backend & UI ───────────────────────────────────────────────────
info "Installing backend..."
mkdir -p "$SPUD_CONF"

# Look for backend directory next to this installer script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -d "$SCRIPT_DIR/backend" ]]; then
    cp -r "$SCRIPT_DIR/backend" "$SPUD_DIR/backend"
elif [[ -d "./backend" ]]; then
    cp -r "./backend" "$SPUD_DIR/backend"
else
    die "backend/ directory not found. Place backend/ in the same directory as this installer and re-run."
fi
chmod -R 755 "$SPUD_DIR/backend"
ok "Backend installed at $SPUD_DIR/backend/"

# Write VERSION file (populated by release workflow; dev installs use "dev")
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
    cp "$SCRIPT_DIR/VERSION" "$SPUD_DIR/VERSION"
elif [[ -f "./VERSION" ]]; then
    cp ./VERSION "$SPUD_DIR/VERSION"
else
    echo "dev" > "$SPUD_DIR/VERSION"
fi
ok "Version: $(cat $SPUD_DIR/VERSION)"

# Install the standalone updater at the install root (SSH + web UI both expect
# it at $SPUD_DIR/update.py, not nested under backend/)
if [[ -f "$SCRIPT_DIR/update.py" ]]; then
    cp "$SCRIPT_DIR/update.py" "$SPUD_DIR/update.py"
elif [[ -f "./update.py" ]]; then
    cp ./update.py "$SPUD_DIR/update.py"
else
    cp "$SPUD_DIR/backend/update.py" "$SPUD_DIR/update.py"
fi
chmod 755 "$SPUD_DIR/update.py"
ok "Updater installed at $SPUD_DIR/update.py"

# Install the root-owned update/reboot wrapper. Must stay root:root 0755 —
# NOT writable by spud-router, since it's the one thing sudoers lets the
# service run as root (see the sudoers block below).
if [[ -f "$SCRIPT_DIR/run-update.sh" ]]; then
    cp "$SCRIPT_DIR/run-update.sh" "$SPUD_DIR/run-update.sh"
elif [[ -f "./run-update.sh" ]]; then
    cp ./run-update.sh "$SPUD_DIR/run-update.sh"
else
    cp "$SPUD_DIR/backend/run-update.sh" "$SPUD_DIR/run-update.sh"
fi
chown root:root "$SPUD_DIR/run-update.sh"
chmod 755 "$SPUD_DIR/run-update.sh"
ok "Update/reboot wrapper installed at $SPUD_DIR/run-update.sh (root:root, 0755)"

mkdir -p "$SPUD_DIR/static"
if [[ -f "$SCRIPT_DIR/index.html" ]]; then
    cp "$SCRIPT_DIR/index.html" "$SPUD_DIR/static/index.html"
elif [[ -f "./index.html" ]]; then
    cp "./index.html" "$SPUD_DIR/static/index.html"
else
    die "index.html not found. Run from the extracted release tarball."
fi

# Copy Vite-built assets (JS/CSS chunks) if present
if [[ -d "$SCRIPT_DIR/assets" ]]; then
    cp -r "$SCRIPT_DIR/assets" "$SPUD_DIR/static/"
elif [[ -d "./assets" ]]; then
    cp -r "./assets" "$SPUD_DIR/static/"
fi

ok "UI installed at $SPUD_DIR/static/"

# ── 5b. TLS certificate ───────────────────────────────────────────────────────
info "Generating self-signed TLS certificate..."
mkdir -p "$SPUD_CONF/tls"
chmod 700 "$SPUD_CONF/tls"
openssl req -x509 -newkey rsa:2048 \
    -keyout "$SPUD_CONF/tls/server.key" \
    -out    "$SPUD_CONF/tls/server.crt" \
    -days 3650 -nodes \
    -subj "/CN=spud-router" \
    -addext "subjectAltName=IP:192.168.1.1,DNS:spud-router,DNS:localhost" \
    2>/dev/null
chmod 600 "$SPUD_CONF/tls/server.key"
chmod 644 "$SPUD_CONF/tls/server.crt"
ok "TLS cert generated at $SPUD_CONF/tls/ (valid 10 years; replace with a real cert if desired)"

# ── 6. Credentials prompt ─────────────────────────────────────────────────────
echo ""
echo -e "${YLW}  ── Set admin credentials ──${NC}"
read -rp "  Admin username [admin]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"

while true; do
    read -rsp "  Admin password (min 12 chars): " ADMIN_PASS; echo ""
    [[ ${#ADMIN_PASS} -ge 12 ]] && break
    warn "Password must be at least 12 characters."
done
read -rsp "  Confirm password: " ADMIN_PASS2; echo ""
[[ "$ADMIN_PASS" != "$ADMIN_PASS2" ]] && die "Passwords don't match"

PASS_HASH=$(echo -n "$ADMIN_PASS" | sha256sum | awk '{print $1}')
cat > "$SPUD_CONF/auth.json" << EOF
{"username":"$ADMIN_USER","password_sha256":"$PASS_HASH"}
EOF
chmod 600 "$SPUD_CONF/auth.json"
ok "Credentials saved"

# ── 7. Default state — router-on-a-stick out of the box ─────────────────────────
if [[ ! -f "$SPUD_CONF/state.json" ]]; then
    MGMT_IF=$(ip -br link | grep -v "^lo" | awk '{print $1}' | grep -v "\." | head -1)
    MGMT_IF="${MGMT_IF:-eth0}"
    # Router-on-a-stick: WAN and LAN are VLANs on the trunk port
    WAN_VLAN="${MGMT_IF}.2"
    cat > "$SPUD_CONF/state.json" << EOF
{"vlans":[{"vlan_id":2,"name":"WAN","interface":"${MGMT_IF}","ip_address":"","prefix_len":0,"dhcp_enabled":false,"dhcp_start":"","dhcp_end":"","dhcp_lease":"12h","isolate":false},{"vlan_id":10,"name":"LAN","interface":"${MGMT_IF}","ip_address":"192.168.10.1","prefix_len":24,"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h","isolate":false}],"router":{"wan_interface":"${WAN_VLAN}","wan_mode":"dhcp","wan_dns_mode":"auto","wan_dns":"1.1.1.1","wan_dns_alt":"8.8.8.8","hostname":"spud-router","mgmt_enabled":true,"mgmt_interface":"${MGMT_IF}","mgmt_ip":"192.168.1.1","mgmt_prefix":24,"mgmt_dhcp_start":"192.168.1.100","mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h"},"static_routes":[],"dns_entries":[],"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},"fw_inbound":[],"fw_intervlan":[]}
EOF
    ok "Default state written — mgmt: ${MGMT_IF} (192.168.1.1), WAN: ${WAN_VLAN} (DHCP), LAN: VLAN 10 (192.168.10.1)"
fi

# ── 8. Systemd service ────────────────────────────────────────────────────────
info "Installing systemd service..."
cat > /etc/systemd/system/spud-router.service << EOF
[Unit]
Description=spud-router web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=spud-router
WorkingDirectory=$SPUD_DIR
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$SPUD_DIR/venv/bin/uvicorn backend.main:app \
    --host 0.0.0.0 --port $SPUD_PORT --log-level warning \
    --ssl-keyfile $SPUD_CONF/tls/server.key \
    --ssl-certfile $SPUD_CONF/tls/server.crt
Restart=always
RestartSec=5
PrivateTmp=true
NoNewPrivileges=false
StandardOutput=journal
StandardError=journal
SyslogIdentifier=spud-router

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable spud-router

# ── 8b. Privilege delegation (sudoers) ────────────────────────────────────────
info "Installing sudoers rules for spud-router..."
cat > /etc/sudoers.d/spud-router << 'SUDOEOF'
# spud-router: scoped privilege for network apply operations
# No wildcard commands; argument wildcards on tailscale only (dynamic route CIDRs).
Defaults:spud-router !requiretty

# File writes to root-owned config directories
spud-router ALL=(root) NOPASSWD: /usr/bin/tee /etc/netplan/50-spud-router.yaml
spud-router ALL=(root) NOPASSWD: /usr/bin/tee /etc/dnsmasq.d/spud-router.conf
spud-router ALL=(root) NOPASSWD: /usr/bin/tee /etc/hostapd/hostapd.conf

# Network apply commands — explicit subcommands only
spud-router ALL=(root) NOPASSWD: /usr/sbin/netplan apply
spud-router ALL=(root) NOPASSWD: /usr/bin/systemctl restart dnsmasq
spud-router ALL=(root) NOPASSWD: /usr/bin/systemctl enable --now hostapd
spud-router ALL=(root) NOPASSWD: /usr/bin/systemctl restart hostapd
spud-router ALL=(root) NOPASSWD: /usr/bin/systemctl stop hostapd
spud-router ALL=(root) NOPASSWD: /usr/bin/systemctl disable hostapd

# iptables apply script (written by service to /etc/spud-router/, run as root)
spud-router ALL=(root) NOPASSWD: /bin/bash /etc/spud-router/iptables.sh

# Tailscale — argument wildcard required for dynamic --advertise-routes CIDRs.
# Bare "up" (enabled with no route flags) needs its own rule: sudo's "up *"
# pattern requires at least one trailing argument and won't match "up" alone.
spud-router ALL=(root) NOPASSWD: /usr/bin/tailscale up
spud-router ALL=(root) NOPASSWD: /usr/bin/tailscale up *
spud-router ALL=(root) NOPASSWD: /usr/bin/tailscale down

# spud-router: update/reboot wrapper (managed by update.py)
spud-router ALL=(root) NOPASSWD: /opt/spud-router/run-update.sh apply
spud-router ALL=(root) NOPASSWD: /opt/spud-router/run-update.sh reboot
SUDOEOF
chmod 440 /etc/sudoers.d/spud-router
ok "sudoers installed (/etc/sudoers.d/spud-router)"

# ── 8c. Config directory ownership ────────────────────────────────────────────
info "Setting config directory ownership..."
# /etc/spud-router owned by spud-router so the service can write state/auth/iptables
chown -R spud-router:spud-router "$SPUD_CONF"
chmod 750 "$SPUD_CONF"
# TLS private key readable only by service user
chmod 700 "$SPUD_CONF/tls"
chmod 600 "$SPUD_CONF/tls/server.key"
chmod 644 "$SPUD_CONF/tls/server.crt"
# Sensitive credential files
[[ -f "$SPUD_CONF/auth.json"  ]] && chmod 600 "$SPUD_CONF/auth.json"
[[ -f "$SPUD_CONF/state.json" ]] && chmod 600 "$SPUD_CONF/state.json"
ok "Config directory ownership set (spud-router:spud-router, 750)"

# ── 8c-2. CLI service token ────────────────────────────────────────────────────
# The spud-cli TUI (the 'spud' user's login shell) authenticates to the local
# API with this long-lived token — backend/auth.py accepts a request whose token
# matches this file. Without it, 'spud' has no way to authenticate: it can't use
# the admin web login (different persona) and can't even persist a token because
# it only has group r-x on 750 $SPUD_CONF (no write). Make it group-readable so
# 'spud' (a member of the spud-router group) can read it; it never writes.
info "Issuing CLI service token..."
if [[ ! -s "$SPUD_CONF/cli-token" ]]; then
    openssl rand -hex 32 > "$SPUD_CONF/cli-token"
fi
chown spud-router:spud-router "$SPUD_CONF/cli-token"
chmod 640 "$SPUD_CONF/cli-token"
ok "CLI service token issued ($SPUD_CONF/cli-token, spud-router:spud-router 640)"

# ── 8d. Update status directory (tmpfs) ───────────────────────────────────────
# The detached updater (running as root) writes progress here, world-readable,
# so the spud-router service can poll it. tmpfiles.d recreates it on every
# boot since /run is tmpfs and doesn't survive a reboot.
info "Setting up update status directory..."
mkdir -p /run/spud-router
chmod 755 /run/spud-router
cat > /etc/tmpfiles.d/spud-router.conf << 'TMPFILESEOF'
d /run/spud-router 0755 root root -
TMPFILESEOF
ok "Update status directory ready (/run/spud-router)"

systemctl start spud-router
ok "spud-router service started (User=spud-router)"

# ── Generate and apply bootstrap configs from state.json ──────────────────────
# Use the actual generators to produce configs that match state.json exactly
# This ensures the user doesn't need to click Apply after reboot
info "Generating bootstrap configs from state.json..."

# Generate configs using Python generators
$SPUD_DIR/venv/bin/python3 << 'PYEOF'
import sys
sys.path.insert(0, "/opt/spud-router")
from backend.generators import netplan, dnsmasq, iptables
from backend.state import load_state

state = load_state()

# Generate and write netplan config
netplan_config = netplan.generate(state)
with open("/etc/netplan/50-spud-router.yaml", "w") as f:
    f.write(netplan_config)

# Generate and write dnsmasq config
dnsmasq_config = dnsmasq.generate(state)
with open("/etc/dnsmasq.d/spud-router.conf", "w") as f:
    f.write(dnsmasq_config)

# Generate and write iptables script
iptables_script = iptables.generate(state)
with open("/etc/spud-router/iptables.sh", "w") as f:
    f.write(iptables_script)

import os, subprocess
os.chmod("/etc/spud-router/iptables.sh", 0o750)
# Transfer ownership to service user so Apply can overwrite it later
subprocess.run(["chown", "spud-router:spud-router", "/etc/spud-router/iptables.sh"], check=False)
PYEOF

# Remove Armbian's default netplan configs that conflict with our setup
rm -f /etc/netplan/10-dhcp-all-interfaces.yaml /etc/netplan/20-eth-fixed-mac.yaml 2>/dev/null || true

# Set permissions
chmod 600 /etc/netplan/50-spud-router.yaml

# Apply dnsmasq config immediately (doesn't break SSH)
systemctl enable dnsmasq
if systemctl restart dnsmasq 2>/dev/null && systemctl is-active --quiet dnsmasq; then
    # The router itself now resolves through dnsmasq.
    rm -f /etc/resolv.conf
    echo "nameserver 127.0.0.1" > /etc/resolv.conf
    ok "Bootstrap dnsmasq started"
else
    warn "dnsmasq start failed — check 'journalctl -u dnsmasq'; leaving resolv.conf on upstream DNS"
fi

# Apply iptables rules immediately (doesn't break SSH)
mkdir -p /etc/iptables
iptables-restore < /etc/iptables/rules.v4 2>/dev/null || /etc/spud-router/iptables.sh 2>/dev/null || warn "iptables apply failed"
systemctl enable netfilter-persistent 2>/dev/null || true
ok "Bootstrap iptables applied"

ok "Bootstrap configs generated and applied (netplan applies on reboot)"

# ── 9. SSH hardening + banner ─────────────────────────────────────────────────
info "Hardening SSH..."

# Copy banner file (shown before password prompt)
if [[ -f "$SCRIPT_DIR/ssh-banner" ]]; then
    cp "$SCRIPT_DIR/ssh-banner" /etc/ssh/spud-router-banner
elif [[ -f "./ssh-banner" ]]; then
    cp ./ssh-banner /etc/ssh/spud-router-banner
fi

# Resolve which non-root account may SSH in with a *real* shell, in addition
# to 'spud' (whose shell is the restricted TUI, not bash). Installing as
# root directly (no unprivileged sudo user, so SUDO_USER is unset) is a
# lockout trap: AllowUsers would otherwise end up "spud root" with
# PermitRootLogin no — leaving no admin shell at all on a device that may be
# an hour away. Never let that combination happen silently.
ADMIN_SSH_USER="${SUDO_USER:-}"
[[ "$ADMIN_SSH_USER" == "root" ]] && ADMIN_SSH_USER=""
ALLOW_ROOT_SSH=false

if [[ -z "$ADMIN_SSH_USER" ]]; then
    echo ""
    warn "Running as root directly (not via sudo) — 'spud' (a restricted TUI shell, not"
    warn "bash) would otherwise be the only account allowed to SSH in."
    read -rp "  Existing (or soon-to-exist) non-root username to permit for SSH [blank = skip]: " ADMIN_SSH_USER
    [[ "$ADMIN_SSH_USER" == "root" ]] && ADMIN_SSH_USER=""

    if [[ -n "$ADMIN_SSH_USER" ]] && ! id -u "$ADMIN_SSH_USER" &>/dev/null; then
        read -rp "  User '$ADMIN_SSH_USER' doesn't exist yet — create it now? [y/N] " CREATE_ADMIN
        if [[ "$CREATE_ADMIN" =~ ^[Yy]$ ]]; then
            useradd -m -s /bin/bash "$ADMIN_SSH_USER"
            read -rsp "  Password for '$ADMIN_SSH_USER': " ADMIN_SSH_PASS; echo ""
            echo "${ADMIN_SSH_USER}:${ADMIN_SSH_PASS}" | chpasswd
            ok "Created user '$ADMIN_SSH_USER'"
        else
            warn "Not creating '$ADMIN_SSH_USER' now — make sure it exists before you rely on it for SSH."
        fi
    fi

    if [[ -z "$ADMIN_SSH_USER" ]]; then
        ALLOW_ROOT_SSH=true
        warn "No admin user provided — leaving root SSH login enabled so this install does not"
        warn "lock you out. Lock this down once you have a real admin account:"
        warn "  useradd -m -s /bin/bash <you> && passwd <you>"
        warn "  Add <you> to AllowUsers in /etc/ssh/sshd_config.d/99-spud-router.conf,"
        warn "  then set 'PermitRootLogin no' there and run: systemctl reload ssh"
    fi
fi

# Build AllowUsers: 'spud' (the CLI account, created below) plus any
# resolved admin account. Only disable root login once a real, non-root
# admin account is actually permitted — never disable root while it would
# be the only human account left standing.
SSH_ALLOW_USERS="spud"
[[ -n "$ADMIN_SSH_USER" ]] && SSH_ALLOW_USERS="spud ${ADMIN_SSH_USER}"
# AllowUsers is a whitelist: when we're intentionally leaving root SSH enabled
# (no admin account was provided), root MUST also be listed here, or it's
# denied regardless of PermitRootLogin — recreating the exact lockout this
# fallback exists to prevent (only 'spud'/the TUI would be able to log in).
$ALLOW_ROOT_SSH && SSH_ALLOW_USERS="${SSH_ALLOW_USERS} root"

SSHD_ROOT_LINE="PermitRootLogin no"
$ALLOW_ROOT_SSH && SSHD_ROOT_LINE="PermitRootLogin prohibit-password"

[[ -f /etc/ssh/sshd_config.d/99-spud-router.conf ]] && \
    cp /etc/ssh/sshd_config.d/99-spud-router.conf /etc/ssh/sshd_config.d/99-spud-router.conf.bak

cat > /etc/ssh/sshd_config.d/99-spud-router.conf << EOF
${SSHD_ROOT_LINE}
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
Banner /etc/ssh/spud-router-banner
AllowUsers ${SSH_ALLOW_USERS}
EOF

# Never reload a broken sshd config — validate first, and revert on failure.
if sshd -t 2>/tmp/spud-sshd-check.err; then
    rm -f /etc/ssh/sshd_config.d/99-spud-router.conf.bak /tmp/spud-sshd-check.err
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    if $ALLOW_ROOT_SSH; then
        ok "SSH hardened (root login left enabled — see warning above; AllowUsers: ${SSH_ALLOW_USERS})"
    else
        ok "SSH hardened (root login disabled; AllowUsers: ${SSH_ALLOW_USERS})"
    fi
else
    warn "Generated sshd config failed validation (sshd -t) — reverting so SSH isn't locked out:"
    sed 's/^/    /' /tmp/spud-sshd-check.err 2>/dev/null || true
    if [[ -f /etc/ssh/sshd_config.d/99-spud-router.conf.bak ]]; then
        mv /etc/ssh/sshd_config.d/99-spud-router.conf.bak /etc/ssh/sshd_config.d/99-spud-router.conf
    else
        rm -f /etc/ssh/sshd_config.d/99-spud-router.conf
    fi
    rm -f /tmp/spud-sshd-check.err
fi

# ── 10. spud user + CLI ───────────────────────────────────────────────────────
info "Creating spud user and installing CLI..."

# Locate spud-cli and validate it before AND after copying — /usr/local/bin/
# spud-cli is the 'spud' user's ONLY shell. 'spud' must only ever get a
# working TUI: there is no acceptable fallback (e.g. bash) for that account,
# so any corruption here is a failed install, not something to silently
# paper over.
SPUD_CLI_SRC=""
if [[ -f "$SCRIPT_DIR/spud-cli" ]]; then
    SPUD_CLI_SRC="$SCRIPT_DIR/spud-cli"
elif [[ -f "./spud-cli" ]]; then
    SPUD_CLI_SRC="./spud-cli"
fi

if [[ -n "$SPUD_CLI_SRC" ]]; then
    _valid_spudcli "$SPUD_CLI_SRC" || \
        die "spud-cli source ($SPUD_CLI_SRC) is empty or not a valid script — refusing to install a broken login shell. Re-extract the release tarball and re-run."

    cp "$SPUD_CLI_SRC" /usr/local/bin/spud-cli
    chmod 755 /usr/local/bin/spud-cli
    if ! _valid_spudcli /usr/local/bin/spud-cli; then
        warn "spud-cli copy came out empty/invalid — retrying once..."
        cp "$SPUD_CLI_SRC" /usr/local/bin/spud-cli
        chmod 755 /usr/local/bin/spud-cli
    fi
    _valid_spudcli /usr/local/bin/spud-cli || \
        die "spud-cli at /usr/local/bin/spud-cli is still empty/invalid after a retry. The 'spud' user must only ever get a working TUI — check disk health and re-run install.sh."
elif [[ ! -f /usr/local/bin/spud-cli ]]; then
    warn "spud-cli not found — CLI will not be installed"
elif ! _valid_spudcli /usr/local/bin/spud-cli; then
    die "Existing /usr/local/bin/spud-cli is empty/invalid and no replacement was provided alongside this run. Re-run install.sh with spud-cli present, or fix the file manually before continuing."
fi
# Not shipped alongside this run (e.g. install.sh re-run on its own) but a
# valid copy is already installed from before falls through here untouched.

if [[ -f /usr/local/bin/spud-cli ]]; then
    # Reaching here means spud-cli is confirmed valid (or install.sh already
    # died above) — 'spud' always gets the real TUI, never a fallback shell.

    # Create the 'spud' system user if it doesn't exist
    if ! id -u spud &>/dev/null; then
        useradd -r -m -d /home/spud -s /usr/local/bin/spud-cli \
            -c "spud-router CLI user" spud
        ok "Created user 'spud' with spud-cli as shell"
    else
        # Already exists — just update the shell
        usermod -s /usr/local/bin/spud-cli spud
        ok "Updated 'spud' user shell to spud-cli"
    fi

    # Set the spud user's password
    echo ""
    echo -e "${YLW}  ── CLI (SSH) credentials for user 'spud' ──${NC}"
    echo "  (Use these to SSH in and get the interactive CLI)"
    while true; do
        read -rsp "  Password for 'spud' user (min 8 chars): " SPUD_PASS; echo ""
        [[ ${#SPUD_PASS} -ge 8 ]] && break
        warn "Password must be at least 8 characters."
    done
    echo "spud:${SPUD_PASS}" | chpasswd
    ok "Password set for 'spud' user"

    # Allow spud-cli as a valid shell
    if ! grep -q "/usr/local/bin/spud-cli" /etc/shells; then
        echo "/usr/local/bin/spud-cli" >> /etc/shells
    fi

    # Add spud to the spud-router group so it can read the cli-token
    usermod -aG spud-router spud

    ok "CLI installed — ssh spud@<device-ip>"
fi

# ── 11. fail2ban ──────────────────────────────────────────────────────────────
info "Configuring fail2ban..."
cat > /etc/fail2ban/jail.d/spud-router.conf << 'F2BEOF'
[sshd]
enabled  = true
port     = ssh
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 5
bantime  = 3600
F2BEOF
systemctl enable fail2ban && systemctl restart fail2ban 2>/dev/null || warn "fail2ban start failed — check 'journalctl -u fail2ban'"
ok "fail2ban enabled"

# ── 12. MOTD ──────────────────────────────────────────────────────────────────
info "Installing MOTD..."

# Disable default Armbian/Ubuntu MOTDs that clutter the screen
chmod -x /etc/update-motd.d/* 2>/dev/null || true

# Install our dynamic MOTD
if [[ -f "$SCRIPT_DIR/motd" ]]; then
    cp "$SCRIPT_DIR/motd" /etc/update-motd.d/99-spud-router
elif [[ -f "./motd" ]]; then
    cp ./motd /etc/update-motd.d/99-spud-router
fi

if [[ -f /etc/update-motd.d/99-spud-router ]]; then
    chmod +x /etc/update-motd.d/99-spud-router
    ok "MOTD installed"
fi

# Also disable the default /etc/motd static file
echo "" > /etc/motd

# ── 13. Persist IP forwarding across reboots ───────────────────────────────────
info "Persisting IP forwarding..."
cat > /etc/sysctl.d/99-spud-router.conf << 'EOF'
# spud-router — enable IP forwarding for routing between VLANs and WAN
net.ipv4.ip_forward = 1
EOF
sysctl --system > /dev/null 2>&1 || true
ok "IP forwarding persisted (/etc/sysctl.d/99-spud-router.conf)"

# ── 14. Tailscale ─────────────────────────────────────────────────────────────
info "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh
ok "Tailscale installed — enable and configure in the web UI, then run 'tailscale up' once to authenticate"

# ── Done ──────────────────────────────────────────────────────────────────────
# Get management interface from state.json
MGMT_IF=$($SPUD_DIR/venv/bin/python3 -c "import json; print(json.load(open('/etc/spud-router/state.json'))['router']['mgmt_interface'])")
LAN_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "<device-ip>")

echo ""
echo -e "${GRN}  ══════════════════════════════════════════${NC}"
echo -e "${GRN}  🥔  spud-router installed!${NC}"
echo -e "${GRN}  ══════════════════════════════════════════${NC}"
echo ""
echo -e "  ${YLW}── Reboot to apply network changes ──${NC}"
echo -e "  ${BLU}sudo reboot${NC}"
echo ""
echo -e "  ${YLW}── After reboot ──${NC}"
echo -e "  Plug a laptop into ${MGMT_IF} (untagged) for management"
echo -e "  Your IP  →  192.168.1.100–192.168.1.150 (DHCP)"
echo ""
echo -e "  ${YLW}── VLANs configured ──${NC}"
echo -e "  WAN: ${MGMT_IF}.2 (VLAN 2, DHCP from ISP)"
echo -e "  LAN: ${MGMT_IF}.10 (VLAN 10, 192.168.10.1/24, DHCP 100-200)"
echo -e "  Mgmt: ${MGMT_IF} (untagged, 192.168.1.1/24)"
echo ""
echo -e "  ${YLW}── Web UI (HTTPS) ──${NC}"
echo -e "  ${BLU}https://192.168.1.1:8080${NC}"
echo -e "  Login: ${YLW}${ADMIN_USER}${NC} / (password set above)"
echo -e "  ${YLW}Note: accept the self-signed cert warning on first visit.${NC}"
echo -e "        Replace $SPUD_CONF/tls/ with a real cert to remove the warning."
echo ""
echo -e "  ${YLW}── Shell CLI (SSH) ──${NC}"
echo -e "  ${BLU}ssh spud@192.168.1.1${NC}"
echo -e "  Login: ${YLW}spud${NC} / (password set above)"
echo -e "  Launches the interactive spud-cli TUI automatically"
echo -e "  ${YLW}Note: SSH is only permitted on the management interface and over Tailscale${NC}"
echo -e "  ${YLW}by default (not on LAN VLANs). To allow it from a LAN VLAN, add an inbound${NC}"
echo -e "  ${YLW}tcp/22 rule for that VLAN in the web UI's Firewall tab.${NC}"
echo ""
echo "  Logs:  journalctl -u spud-router -f"
echo "  Install log: $INSTALL_LOG"
echo ""
