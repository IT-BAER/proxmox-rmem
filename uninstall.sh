#!/bin/bash

# Proxmox Real Memory (proxmox-rmem) Uninstaller
# Completely removes proxmox-rmem and restores original Proxmox state.

set -e

INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/proxmox-rmem"
SERVICE_FILE="/etc/systemd/system/proxmox-rmem.service"
TARGET_PM="/usr/share/perl5/PVE/QemuServer.pm"
BACKUP_PM="/usr/share/perl5/PVE/QemuServer.pm.bak"

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
echo "║     proxmox-rmem Uninstaller             ║"
echo "║     Restore Original Proxmox State       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Stop and disable the service
print_status "[1/6] Stopping and disabling service..."
if systemctl is-active --quiet proxmox-rmem 2>/dev/null; then
    systemctl stop proxmox-rmem
    echo "  Service stopped."
fi
if systemctl is-enabled --quiet proxmox-rmem 2>/dev/null; then
    systemctl disable proxmox-rmem
    echo "  Service disabled."
fi

# 2. Remove systemd service file
print_status "[2/6] Removing systemd service file..."
if [ -f "$SERVICE_FILE" ]; then
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    echo "  Service file removed."
else
    echo "  Service file not found, skipping."
fi

# 3. Restore original QemuServer.pm from backup
print_status "[3/6] Restoring original QemuServer.pm..."
if [ -f "$BACKUP_PM" ]; then
    cp -f "$BACKUP_PM" "$TARGET_PM"
    rm -f "$BACKUP_PM"
    echo "  Original QemuServer.pm restored and backup removed."
else
    print_warning "Backup file not found at $BACKUP_PM"
    echo "  Cannot restore automatically. To restore manually:"
    echo "  apt reinstall pve-qemu-kvm"
fi

# 4. Remove installed script
print_status "[4/6] Removing installed script..."
if [ -f "$INSTALL_DIR/proxmox-rmem.py" ]; then
    rm -f "$INSTALL_DIR/proxmox-rmem.py"
    echo "  Script removed."
else
    echo "  Script not found, skipping."
fi

# 5. Remove all override files
print_status "[5/6] Cleaning up memory override files..."
OVERRIDE_COUNT=$(ls /tmp/pve-vm-*-mem-override 2>/dev/null | wc -l || echo "0")
if [ "$OVERRIDE_COUNT" -gt 0 ]; then
    rm -f /tmp/pve-vm-*-mem-override
    echo "  Removed $OVERRIDE_COUNT override file(s)."
else
    echo "  No override files found."
fi

# 6. Remove config directory (including SSH keys)
print_status "[6/6] Removing configuration directory..."
if [ -d "$CONFIG_DIR" ]; then
    rm -rf "$CONFIG_DIR"
    echo "  Config directory removed: $CONFIG_DIR"
else
    echo "  Config directory not found, skipping."
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Uninstallation Complete!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "proxmox-rmem has been completely removed."
echo "Proxmox will now use default memory reporting."
echo ""

# Restart Proxmox services LAST to apply changes
# Using --no-block prevents the script from waiting and avoids web console disconnection
print_status "Restarting Proxmox services..."
print_warning "If using PVE web console, you may need to refresh the page."
systemctl restart --no-block pvestatd pvedaemon
# Delay pveproxy restart slightly to let the script output complete
( sleep 2 && systemctl restart pveproxy ) &

print_status "Done!"
