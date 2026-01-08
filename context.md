# Proxmox Real Memory (proxmox-rmem) Project Context

## Problem Statement
Proxmox VE (specifically versions 8 and 9) often reports incorrect, high memory usage for Linux and BSD Virtual Machines. This happens because the Proxmox summary and graphs include the Guest OS's **file cache** (and ZFS ARC) as "used" memory. This leads to "phantom" high memory usage (>90%) in the Proxmox UI, while the Guest OS actually has plenty of available RAM.

## Solution Overview
We developed a solution `proxmox-rmem` that overrides the Proxmox memory statistics with the *actual* active memory usage (Active + Wired for BSD, Total - Available for Linux) fetched directly from the Guest OS.

The solution consists of two parts:
1.  **Proxmox Patch:** A modification to `/usr/share/perl5/PVE/QemuServer.pm` on the host. This patch instructs Proxmox to check for a temporary override file (`/tmp/pve-vm-<VMID>-mem-override`) before calculating memory usage. If the file exists, its value (in bytes) is used instead of the QEMU-reported value.
2.  **Monitoring Service:** A Python-based systemd service (`proxmox-rmem`) that runs on the Proxmox host. It periodically fetches real memory stats from configured VMs and writes them to the override files.

## Project Structure (`D:\VSC\proxmox-rmem`)
*   **`install.sh`**: One-liner installer script that patches Proxmox, installs the service, and sets up config/keys.
*   **`patch_pve.py`**: Python script to safely apply the Perl patch to `QemuServer.pm`.
*   **`proxmox-rmem.py`**: The core service daemon. Supports fetching memory via:
    *   **SSH:** Connects to VM via SSH key (requires network reachability).
    *   **QGA (QEMU Guest Agent):** Executes commands via the hypervisor channel (no network required).
*   **`config.example.json`**: Template for `/etc/proxmox-rmem/config.json`.
*   **`README.md`**: Usage instructions.

## Implementation History

### Host 1: ROG-PVE01 (OPNsense - VM 101)
*   **VM OS:** OPNsense (FreeBSD).
*   **Challenge:** Proxmox reported >100% memory usage.
*   **Method:** SSH.
    *   Created a dedicated SSH key pair.
    *   Authorized the key on OPNsense.
    *   Service fetches `sysctl` stats (active + wired) via SSH.
*   **Result:** Memory usage corrected from ~4GB to ~3.3GB in Proxmox UI.

### Host 2: ROG-PVE02 (BM-HASS - VM 202)
*   **VM OS:** Home Assistant OS (Linux).
*   **Challenge:** VM is on a separate VLAN (40) inaccessible from the Host (VLAN 10/90). SSH failed due to routing/firewall issues.
*   **Method:** QEMU Guest Agent (QGA).
    *   Configured service with `"method": "qga"`.
    *   Service uses `qm guest exec 202 -- cat /proc/meminfo`.
*   **Result:** Memory usage corrected from ~5GB to ~2.3GB. Verified override file updates despite no network connectivity.

## Key Learnings
1.  **RRD Corruption:** Attempting to update RRD files directly (`rrdtool update`) concurrently with `pvestatd` causes corruption and empty graphs.
2.  **Safe Override:** The correct way to inject stats is to patch the *source* (`QemuServer.pm`) to read from a file, letting `pvestatd` handle the RRD writing naturally.
3.  **QGA vs SSH:** QGA is superior for "appliance" VMs or isolated networks as it doesn't require IP reachability or open ports, but SSH is useful for legacy/BSD systems where QGA might have limited exec capabilities (though OPNsense supports QGA too).

## Future Usage
To deploy on a new host:
1.  Copy the `proxmox-rmem` folder to the host.
2.  Run `./install.sh`.
3.  Edit `/etc/proxmox-rmem/config.json` with VM details.
4.  Restart service.
