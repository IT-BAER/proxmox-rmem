# proxmox-rmem

**Fix inflated memory usage in Proxmox VE 9 for Linux and BSD VMs.**

## The Problem

After upgrading to Proxmox VE 9, VM memory usage may appear higher than expected — sometimes even over 100%. This happens because:

- **Proxmox VE 9 changed memory accounting** to include VM overhead on the host side
- If the VM doesn't report detailed memory via the ballooning device, Proxmox shows the **host's view** instead of guest-reported usage

**Affected systems:**
- VMs with ballooning device disabled
- FreeBSD-based systems (pfSense, OPNsense) — do not report memory details
- Windows VMs without BalloonService running
- Any guest that doesn't communicate memory stats back to Proxmox

> See: [Proxmox VE 9.0 Upgrade Notes](https://pve.proxmox.com/wiki/Upgrade_from_8_to_9#VM_Memory_Consumption_Shown_is_Higher)

> **Note:** Proxmox's host-side memory view is technically correct for hypervisor capacity planning. This tool provides guest-reported memory usage, which is more useful for application monitoring and avoids misleading high-usage alerts.

## The Solution

**proxmox-rmem** fetches *actual* memory usage directly from guest VMs and overrides Proxmox's display:

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
