#!/bin/bash

# Proxmox Real Memory (proxmox-rmem) Installer
# Supports both local and one-liner remote installation

set -e

REPO_URL="https://raw.githubusercontent.com/IT-BAER/proxmox-rmem/main"
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/proxmox-rmem"
SERVICE_FILE="/etc/systemd/system/proxmox-rmem.service"
TEMP_DIR="/tmp/proxmox-rmem-install"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() { echo -e "${GREEN}[*]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[x]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     proxmox-rmem Installer               ║"
echo "║     Fix Proxmox Memory Reporting         ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Determine if running locally or remotely
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_INSTALL=false

if [ -f "$SCRIPT_DIR/proxmox-rmem.py" ] && [ -f "$SCRIPT_DIR/patch_pve.py" ]; then
    LOCAL_INSTALL=true
    print_status "Local installation detected"
else
    print_status "Remote installation - downloading files..."
    mkdir -p "$TEMP_DIR"
    cd "$TEMP_DIR"
    
    curl -fsSL "$REPO_URL/patch_pve.py" -o patch_pve.py || { print_error "Failed to download patch_pve.py"; exit 1; }
    curl -fsSL "$REPO_URL/proxmox-rmem.py" -o proxmox-rmem.py || { print_error "Failed to download proxmox-rmem.py"; exit 1; }
    curl -fsSL "$REPO_URL/config.example.json" -o config.example.json || { print_error "Failed to download config.example.json"; exit 1; }
    
    SCRIPT_DIR="$TEMP_DIR"
    print_status "Files downloaded successfully"
fi

cd "$SCRIPT_DIR"

# 1. Patch Proxmox
print_status "[1/5] Patching Proxmox QemuServer.pm..."
python3 patch_pve.py
if [ $? -ne 0 ]; then
    print_error "Error patching Proxmox. Aborting."
    exit 1
fi

# 2. Setup Config Directory
print_status "[2/5] Setting up configuration..."
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp config.example.json "$CONFIG_DIR/config.json"
    echo "  Created default config at $CONFIG_DIR/config.json"
else
    echo "  Config already exists, keeping current."
fi

# 3. Setup SSH Key
SSH_KEY="$CONFIG_DIR/id_rsa_monitor"
if [ ! -f "$SSH_KEY" ]; then
    print_status "[3/5] Generating SSH key for monitoring..."
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -q
    echo "  SSH Key created: $SSH_KEY"
else
    print_status "[3/5] Using existing SSH key"
fi

# 4. Install Script
print_status "[4/5] Installing service script..."
cp proxmox-rmem.py "$INSTALL_DIR/proxmox-rmem.py"
chmod +x "$INSTALL_DIR/proxmox-rmem.py"

# 5. Setup Systemd Service
print_status "[5/5] Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Proxmox Real Memory Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/proxmox-rmem.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable proxmox-rmem
systemctl start proxmox-rmem

# Cleanup temp files if remote install
if [ "$LOCAL_INSTALL" = false ]; then
    rm -rf "$TEMP_DIR"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Installation Complete!               ║"
echo "╚══════════════════════════════════════════╝"
echo ""
print_status "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.json to add your VMs"
echo "  2. Add the public key to your VMs:"
echo ""
cat "$SSH_KEY.pub"
echo ""
echo "  3. Check status: systemctl status proxmox-rmem"
echo ""

# Restart Proxmox services LAST to apply the patch
# Using --no-block prevents the script from waiting and avoids web console disconnection
print_status "Restarting Proxmox services to apply patch..."
print_warning "If using PVE web console, you may need to refresh the page."
systemctl restart --no-block pvestatd pvedaemon
# Delay pveproxy restart slightly to let the script output complete
( sleep 2 && systemctl restart pveproxy ) &

print_status "Done! Installation successful."
