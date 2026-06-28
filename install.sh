#!/bin/bash
# =============================================================================
# spud-router — appliance installer
# Tested: Armbian minimal (Le Potato / AML-S905X-CC), Ubuntu 22.04/24.04
#
# Run from the extracted release tarball:
#   tar xzf spud-router-v1.0.0.tar.gz
#   sudo bash install.sh
#
# Tarball contents: install.sh  main.py  spud-cli  update.py
#                   ssh-banner  motd     index.html  VERSION
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

if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl stop systemd-resolved && systemctl disable systemd-resolved
    rm -f /etc/resolv.conf
    echo "nameserver 1.1.1.1" > /etc/resolv.conf
    warn "Disabled systemd-resolved (dnsmasq handles DNS)"
fi

systemctl enable systemd-networkd
systemctl start systemd-networkd
systemctl enable netfilter-persistent 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
# hostapd is managed by spud-router Apply — don't start it yet
systemctl stop hostapd    2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
ok "Services configured"

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

# Look for main.py and index.html next to this installer script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/main.py" ]]; then
    cp "$SCRIPT_DIR/main.py" "$SPUD_DIR/main.py"
elif [[ -f "./main.py" ]]; then
    cp "./main.py" "$SPUD_DIR/main.py"
else
    die "main.py not found. Place main.py in the same directory as this installer and re-run."
fi
chmod 750 "$SPUD_DIR/main.py"
ok "Backend installed at $SPUD_DIR/main.py"

# Install updater
if [[ -f "$SCRIPT_DIR/update.py" ]]; then
    cp "$SCRIPT_DIR/update.py" "$SPUD_DIR/update.py"
    chmod 750 "$SPUD_DIR/update.py"
    ok "Updater installed at $SPUD_DIR/update.py"
elif [[ -f "./update.py" ]]; then
    cp ./update.py "$SPUD_DIR/update.py"
    chmod 750 "$SPUD_DIR/update.py"
    ok "Updater installed"
else
    warn "update.py not found — software update feature will not be available"
fi

# Write VERSION file (populated by release workflow; dev installs use "dev")
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
    cp "$SCRIPT_DIR/VERSION" "$SPUD_DIR/VERSION"
elif [[ -f "./VERSION" ]]; then
    cp ./VERSION "$SPUD_DIR/VERSION"
else
    echo "dev" > "$SPUD_DIR/VERSION"
fi
ok "Version: $(cat $SPUD_DIR/VERSION)"

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
ok "Backend installed at $SPUD_DIR/main.py"

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
{"vlans":[{"vlan_id":10,"name":"LAN","interface":"${MGMT_IF}","ip_address":"192.168.10.1","prefix_len":24,"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h","isolate":false}],"router":{"wan_interface":"${WAN_VLAN}","wan_mode":"dhcp","wan_dns":"1.1.1.1","hostname":"spud-router","mgmt_enabled":true,"mgmt_interface":"${MGMT_IF}","mgmt_ip":"192.168.1.1","mgmt_prefix":24,"mgmt_dhcp_start":"192.168.1.100","mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h"},"static_routes":[],"dns_entries":[],"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},"fw_inbound":[],"fw_intervlan":[]}
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
User=root
WorkingDirectory=$SPUD_DIR
ExecStart=$SPUD_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port $SPUD_PORT --log-level warning
Restart=always
RestartSec=5
PrivateTmp=true
StandardOutput=journal
StandardError=journal
SyslogIdentifier=spud-router

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable spud-router
systemctl start spud-router
ok "spud-router service started"

# ── Bootstrap netplan ─────────────────────────────────────────────────────────
# Must happen BEFORE dnsmasq so the management interface has an IP
if [[ ! -f /etc/netplan/50-spud-router.yaml ]]; then
    # Remove Armbian's default netplan configs that conflict with our setup
    rm -f /etc/netplan/10-dhcp-all-interfaces.yaml /etc/netplan/20-eth-fixed-mac.yaml 2>/dev/null || true

    # Detect trunk/mgmt (first interface) and WAN (second interface, if present)
    ALL_IFS=($(ip -br link | grep -v "^lo" | awk '{print $1}' | grep -v "\."))
    TRUNK_IF="${ALL_IFS[0]:-eth0}"
    WAN_IF="${ALL_IFS[1]:-}"
    if [[ -n "$WAN_IF" ]]; then
        info "Two+ interfaces detected — WAN DHCP on $WAN_IF, mgmt on $TRUNK_IF"
        cat > /etc/netplan/50-spud-router.yaml << EOF
# spud-router bootstrap — configure remaining settings via web UI then Apply
# Management interface: http://192.168.1.1:8080  (plug a laptop into $TRUNK_IF)
network:
  version: 2
  renderer: networkd
  ethernets:
    ${WAN_IF}:
      dhcp4: true
    ${TRUNK_IF}:
      addresses: [192.168.1.1/24]
      dhcp4: false
EOF
    else
        info "Single interface detected — mgmt-only bootstrap on $TRUNK_IF (configure WAN in web UI)"
        cat > /etc/netplan/50-spud-router.yaml << EOF
# spud-router bootstrap — single interface (router-on-a-stick)
# Configure WAN as a VLAN subinterface via the web UI
network:
  version: 2
  renderer: networkd
  ethernets:
    ${TRUNK_IF}:
      addresses: [192.168.1.1/24]
      dhcp4: false
EOF
    fi
    chmod 600 /etc/netplan/50-spud-router.yaml
    netplan apply 2>/dev/null || warn "netplan apply failed — check /etc/netplan/"
    ok "Bootstrap netplan written"
fi

# ── Bootstrap dnsmasq for mgmt interface ──────────────────────────────────────
# Provides DHCP on the management interface immediately after install,
# before the user clicks Apply in the web UI for the first time.
info "Writing bootstrap dnsmasq config for management interface..."
MGMT_IF_BOOT=$(ip -br link | grep -v "^lo" | awk '{print $1}' | grep -v "\." | head -1)
MGMT_IF_BOOT="${MGMT_IF_BOOT:-eth0}"
mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/spud-router.conf << EOF
# spud-router bootstrap dnsmasq — management interface DHCP
# Will be overwritten when you click Apply in the web UI
domain-needed
bogus-priv
local=/spud-router.lan/
domain=spud-router.lan

# Management interface (untagged)
interface=${MGMT_IF_BOOT}
dhcp-range=${MGMT_IF_BOOT},192.168.1.100,192.168.1.150,12h
dhcp-option=${MGMT_IF_BOOT},3,192.168.1.1
dhcp-option=${MGMT_IF_BOOT},6,192.168.1.1
EOF
systemctl enable dnsmasq
systemctl restart dnsmasq 2>/dev/null || warn "dnsmasq start failed — check 'journalctl -u dnsmasq'"
ok "Bootstrap dnsmasq started (DHCP on ${MGMT_IF_BOOT}: 192.168.1.100-150)"

# ── 9. SSH hardening + banner ─────────────────────────────────────────────────
info "Hardening SSH..."

# Copy banner file (shown before password prompt)
if [[ -f "$SCRIPT_DIR/ssh-banner" ]]; then
    cp "$SCRIPT_DIR/ssh-banner" /etc/ssh/spud-router-banner
elif [[ -f "./ssh-banner" ]]; then
    cp ./ssh-banner /etc/ssh/spud-router-banner
fi

cat > /etc/ssh/sshd_config.d/99-spud-router.conf << 'SSHEOF'
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
Banner /etc/ssh/spud-router-banner
SSHEOF
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
ok "SSH hardened (root login disabled, banner set)"

# ── 10. spud user + CLI ───────────────────────────────────────────────────────
info "Creating spud user and installing CLI..."

# Install spud-cli
if [[ -f "$SCRIPT_DIR/spud-cli" ]]; then
    cp "$SCRIPT_DIR/spud-cli" /usr/local/bin/spud-cli
elif [[ -f "./spud-cli" ]]; then
    cp ./spud-cli /usr/local/bin/spud-cli
else
    warn "spud-cli not found — CLI will not be installed"
fi

if [[ -f /usr/local/bin/spud-cli ]]; then
    chmod 755 /usr/local/bin/spud-cli

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

    # Allow the spud user and the install user to SSH in
    INSTALL_USER="${SUDO_USER:-root}"
    cat >> /etc/ssh/sshd_config.d/99-spud-router.conf << SSHEOF
AllowUsers spud ${INSTALL_USER}
SSHEOF
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

    # Give spud user read access to cli-token dir
    chown root:spud /etc/spud-router 2>/dev/null || true
    chmod 750 /etc/spud-router 2>/dev/null || true

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

# ── 14. Optional Tailscale ────────────────────────────────────────────────────
echo ""
read -rp "  Install Tailscale? [y/N]: " INSTALL_TS
if [[ "${INSTALL_TS,,}" == "y" ]]; then
    info "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "Tailscale installed — enable and configure in the web UI, then run 'tailscale up' once to authenticate"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
LAN_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "<device-ip>")

echo ""
echo -e "${GRN}  ══════════════════════════════════════════${NC}"
echo -e "${GRN}  🥔  spud-router is running!${NC}"
echo -e "${GRN}  ══════════════════════════════════════════${NC}"
echo ""
echo -e "  ${YLW}── Plug a laptop into ${MGMT_IF_BOOT} (untagged) ──${NC}"
echo -e "  Your IP  →  192.168.1.100–192.168.1.150 (DHCP)"
echo ""
echo -e "  ${YLW}── Web UI ──${NC}"
echo -e "  ${BLU}http://192.168.1.1:8080${NC}"
echo -e "  Login: ${YLW}${ADMIN_USER}${NC} / (password set above)"
echo ""
echo -e "  ${YLW}── Shell CLI (SSH) ──${NC}"
echo -e "  ${BLU}ssh spud@192.168.1.1${NC}"
echo -e "  Login: ${YLW}spud${NC} / (password set above)"
echo -e "  Launches the interactive spud-cli TUI automatically"
echo ""
echo "  Logs:  journalctl -u spud-router -f"
echo "  Install log: $INSTALL_LOG"
echo ""
