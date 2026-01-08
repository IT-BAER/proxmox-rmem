# proxmox-rmem

**Fix phantom memory usage in Proxmox VE for Linux and BSD VMs.**

Proxmox 9 reports inflated memory usage because it counts OS file cache as "used." This tool overrides that with *actual* active memory fetched directly from guest VMs.

## How It Works

1. **Patches Proxmox** — Modifies `QemuServer.pm` to read memory overrides from `/tmp/pve-vm-<VMID>-mem-override`
2. **Monitors VMs** — Background service fetches real memory via SSH or QEMU Guest Agent
3. **Updates Stats** — Writes correct values that Proxmox displays in the UI and graphs

## 🚀 Quick Install

> **One-liner installation** (run on Proxmox host):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/IT-BAER/proxmox-rmem/main/install.sh)"
```

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/IT-BAER/proxmox-rmem.git
cd proxmox-rmem

# Run installer
chmod +x install.sh
./install.sh
```

## Configuration

Edit `/etc/proxmox-rmem/config.json`:

```json
[
  {
    "vmid": 101,
    "type": "bsd",
    "ip": "10.10.10.1"
  },
  {
    "vmid": 202,
    "type": "linux",
    "method": "qga"
  }
]
```

### Options

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `vmid` | ✅ | - | VM ID in Proxmox |
| `type` | ❌ | `linux` | `linux` or `bsd` |
| `method` | ❌ | `ssh` | `ssh` or `qga` |
| `ip` | ⚠️ | - | Required for SSH method |
| `port` | ❌ | `22` | SSH port |
| `ssh_key` | ❌ | `/etc/proxmox-rmem/id_rsa_monitor` | SSH private key path |
| `enabled` | ❌ | `true` | Enable/disable this VM |

### SSH Setup

For SSH method, add the generated public key to each VM:

```bash
# On Proxmox host, view the public key:
cat /etc/proxmox-rmem/id_rsa_monitor.pub

# Add to VM's authorized_keys
```

### QGA Setup

For QEMU Guest Agent method (ideal for isolated VMs):
1. Install `qemu-guest-agent` in the VM
2. Enable "QEMU Guest Agent" in Proxmox VM Options
3. No network/SSH needed — works via hypervisor channel

## Commands

```bash
# Check service status
systemctl status proxmox-rmem

# View logs
journalctl -u proxmox-rmem -f

# Restart after config changes
systemctl restart proxmox-rmem
```

## Uninstall

Completely removes all components and restores original Proxmox behavior:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/IT-BAER/proxmox-rmem/main/uninstall.sh)"
```

Or if installed locally:
```bash
chmod +x uninstall.sh
./uninstall.sh
```

## Supported Systems

| Guest OS | Method | Memory Calculation |
|----------|--------|-------------------|
| Linux | SSH, QGA | `MemTotal - MemAvailable` |
| FreeBSD / OPNsense | SSH, QGA | `Active + Wired` pages × page size |

## License

MIT
