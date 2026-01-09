# proxmox-rmem

**Fix inflated memory usage in Proxmox VE 9 for Linux, BSD, and Windows VMs.**

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

> **Note:** Proxmox VE 9 now exposes both `mem` (Memory Usage) and `memhost` (Host memory usage) separately. Since the host view is always preserved in `memhost`, overriding `mem` with guest-reported values doesn't lose any information — you get both views available in the UI and API.

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

### Update

Re-run the install script to check for and apply updates:

```bash
# One-liner update
bash -c "$(curl -fsSL https://raw.githubusercontent.com/IT-BAER/proxmox-rmem/main/install.sh)"

# Force reinstall (even if up to date)
FORCE_INSTALL=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/IT-BAER/proxmox-rmem/main/install.sh)"

# Or from cloned repo (pull first)
git pull && ./install.sh
```

The script will:
- ✅ Check for new commits on GitHub
- ✅ Skip if already up to date
- ✅ Preserve your config and SSH keys
- ✅ Update only the service files

## Configuration

Edit `/etc/proxmox-rmem/config.json`:

### Auto-Discovery Mode (Recommended)

The easiest setup — automatically monitors **all running VMs with QGA enabled**:

```json
{
  "auto": true
}
```

This will:
- ✅ Detect all running VMs with QEMU Guest Agent enabled
- ✅ Automatically determine the OS type (Linux, BSD, Windows)
- ✅ Apply the correct memory fetching method
- ✅ Re-scan for new VMs every ~2 minutes

### Auto-Discovery with Overrides

Combine auto-discovery with explicit configuration for special cases:

```json
{
  "auto": true,
  "vms": [
    {
      "vmid": 101,
      "type": "bsd",
      "ip": "10.10.10.1",
      "method": "ssh"
    },
    {
      "vmid": 999,
      "enabled": false
    }
  ]
}
```

Explicit VM configs **override** auto-discovered settings. Use this to:
- Use SSH instead of QGA for specific VMs
- Disable monitoring for certain VMs
- Force a specific OS type if auto-detection fails

### Manual Mode (Original)

For full control, list each VM explicitly:

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
  },
  {
    "vmid": 303,
    "type": "windows",
    "method": "qga"
  }
]
```

### Options

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `auto` | ❌ | `false` | Enable auto-discovery of VMs with QGA |
| `max_concurrent` | ❌ | `5` | Max parallel VM queries (higher = faster, more memory) |
| `vms` | ❌ | `[]` | List of explicit VM configurations (used with `auto`) |
| `vmid` | ✅* | - | VM ID in Proxmox (* not needed for auto mode) |
| `type` | ❌ | auto-detected | `linux`, `bsd`, or `windows` |
| `method` | ❌ | `qga` (auto) / `ssh` (manual) | `ssh` or `qga` |
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

### Windows Setup

For Windows VMs with QEMU Guest Agent:
1. Download and install [virtio-win drivers](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso)
2. Install the **QEMU Guest Agent** from the virtio-win package (located in `guest-agent` folder)
3. Ensure the `QEMU Guest Agent` service is running in Windows Services
4. Enable "QEMU Guest Agent" in Proxmox VM Options
5. Add the VM to config with `"type": "windows"` and `"method": "qga"`

```json
{
  "vmid": 303,
  "type": "windows",
  "method": "qga"
}
```

> **Note:** Windows VMs only support the QGA method (not SSH) for memory monitoring.

## Commands

```bash
# Check service status
systemctl status proxmox-rmem

# View logs (live)
journalctl -u proxmox-rmem -f

# View recent logs
journalctl -u proxmox-rmem --since "10 minutes ago"
```

> **💡 Hot Reload:** Config changes are applied automatically within 2 seconds — no service restart required!

## Troubleshooting

**VM shows "Failed to fetch memory":**
- For SSH: Verify IP is reachable, SSH key is authorized, and port is correct
- For QGA: Ensure QEMU Guest Agent is installed and running in the VM
- For Windows: Verify the QEMU Guest Agent service is running in Windows Services

**Memory not updating in Proxmox UI:**
- Check that the patch was applied: `grep "proxmox-rmem" /usr/share/perl5/PVE/QemuServer.pm`
- Restart Proxmox services: `systemctl restart pvestatd pvedaemon pveproxy`

**Check override files:**
```bash
# List all memory override files
ls -la /tmp/pve-vm-*-mem-override

# View a specific VM's override value (in bytes)
cat /tmp/pve-vm-101-mem-override
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
| Windows | QGA | `TotalVisibleMemorySize - FreePhysicalMemory` |

## 💜 Support Development

If this project helps you, consider supporting this and future work, which heavily relies on coffee:

<div align="center">
<a href="https://www.buymeacoffee.com/itbaer" target="_blank"><img src="https://github.com/user-attachments/assets/64107f03-ba5b-473e-b8ad-f3696fe06002" alt="Buy Me A Coffee" style="height: 60px; max-width: 217px;"></a>
<br>
<a href="https://www.paypal.com/donate/?hosted_button_id=5XXRC7THMTRRS" target="_blank">Donate via PayPal</a>
</div>

<br>

## License

MIT
