import json
import time
import subprocess
import threading
import os
import sys
import glob

CONFIG_FILE = "/etc/proxmox-rmem/config.json"
LOG_INTERVAL = 30  # Log successful updates every 30 cycles (~1 minute)

# Track last known state for change detection
_vm_status = {}  # vmid -> {'success': bool, 'mem': int}
_cycle_count = 0

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def log_vm_status(vmid, success, mem_bytes=None, method=None, os_type=None):
    """Log only on status changes or periodically."""
    global _vm_status, _cycle_count
    
    prev = _vm_status.get(vmid, {})
    status_changed = prev.get('success') != success
    periodic_log = (_cycle_count % LOG_INTERVAL == 0)
    
    if success:
        _vm_status[vmid] = {'success': True, 'mem': mem_bytes}
        if status_changed:
            log(f"VM {vmid}: Now receiving memory updates ({mem_bytes / 1024 / 1024:.1f} MB)")
        elif periodic_log:
            log(f"VM {vmid}: {mem_bytes / 1024 / 1024:.1f} MB")
    else:
        _vm_status[vmid] = {'success': False, 'mem': None}
        if status_changed:
            log(f"VM {vmid}: Failed to fetch memory (method={method}, type={os_type})")

def fetch_memory_ssh_bsd(ip, port, key_path):
    cmd = [
        'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=3',
        '-p', str(port), '-i', key_path,
        f'root@{ip}',
        "sysctl -n vm.stats.vm.v_active_count vm.stats.vm.v_wire_count vm.stats.vm.v_page_size"
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().split()
        if len(output) != 3:
            return None
        active = int(output[0])
        wired = int(output[1])
        page_size = int(output[2])
        return (active + wired) * page_size
    except:
        return None

def fetch_memory_ssh_linux(ip, port, key_path):
    cmd = [
        'ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=3',
        '-p', str(port), '-i', key_path,
        f'root@{ip}',
        "cat /proc/meminfo"
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
        return parse_linux_meminfo(output)
    except:
        return None

def fetch_memory_qga_linux(vmid):
    cmd = ['qm', 'guest', 'exec', str(vmid), '--', 'cat', '/proc/meminfo']
    try:
        output_json = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        result = json.loads(output_json)
        if result.get('exited') != 1 or result.get('exitcode') != 0:
            return None
        return parse_linux_meminfo(result.get('out-data', ''))
    except:
        return None

def fetch_memory_qga_bsd(vmid):
    cmd = ['qm', 'guest', 'exec', str(vmid), '--', 'sysctl', '-n', 'vm.stats.vm.v_active_count', 'vm.stats.vm.v_wire_count', 'vm.stats.vm.v_page_size']
    try:
        output_json = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        result = json.loads(output_json)
        if result.get('exited') != 1 or result.get('exitcode') != 0:
            return None
        output = result.get('out-data', '').split()
        if len(output) != 3:
            return None
        active = int(output[0])
        wired = int(output[1])
        page_size = int(output[2])
        return (active + wired) * page_size
    except:
        return None

def fetch_memory_qga_windows(vmid):
    # Use wmic to get memory stats (works on Windows 7+)
    cmd = ['qm', 'guest', 'exec', str(vmid), '--', 'wmic', 'OS', 'get', 'TotalVisibleMemorySize,FreePhysicalMemory', '/value']
    try:
        output_json = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        result = json.loads(output_json)
        if result.get('exited') != 1 or result.get('exitcode') != 0:
            return None
        return parse_windows_wmic(result.get('out-data', ''))
    except:
        return None

def parse_windows_wmic(content):
    """Parse wmic OS memory output. Values are in KB."""
    mem_total = 0
    mem_free = 0
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('TotalVisibleMemorySize='):
            try:
                mem_total = int(line.split('=')[1]) * 1024  # KB to bytes
            except:
                pass
        elif line.startswith('FreePhysicalMemory='):
            try:
                mem_free = int(line.split('=')[1]) * 1024  # KB to bytes
            except:
                pass
    
    if mem_total > 0 and mem_free >= 0:
        return mem_total - mem_free
    return None

def parse_linux_meminfo(content):
    mem_total = 0
    mem_available = 0
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            mem_available = int(line.split()[1]) * 1024
    
    if mem_total > 0 and mem_available > 0:
        return mem_total - mem_available
    return None

def update_vm(vm_config):
    vmid = vm_config.get('vmid')
    method = vm_config.get('method', 'ssh').lower()
    os_type = vm_config.get('type', 'linux').lower()
    
    mem_bytes = None
    
    if method == 'qga':
        if os_type in ['bsd', 'opnsense', 'freebsd']:
            mem_bytes = fetch_memory_qga_bsd(vmid)
        elif os_type in ['windows', 'win']:
            mem_bytes = fetch_memory_qga_windows(vmid)
        else:
            mem_bytes = fetch_memory_qga_linux(vmid)
    else:
        ip = vm_config.get('ip')
        port = vm_config.get('port', 22)
        key_path = vm_config.get('ssh_key', '/etc/proxmox-rmem/id_rsa_monitor')
        if os_type in ['bsd', 'opnsense', 'freebsd']:
            mem_bytes = fetch_memory_ssh_bsd(ip, port, key_path)
        else:
            mem_bytes = fetch_memory_ssh_linux(ip, port, key_path)
    
    if mem_bytes is not None:
        override_file = f"/tmp/pve-vm-{vmid}-mem-override"
        try:
            with open(override_file, 'w') as f:
                f.write(str(mem_bytes))
            log_vm_status(vmid, True, mem_bytes, method, os_type)
        except Exception as e:
            log(f"VM {vmid}: Failed to write override file: {e}")
            log_vm_status(vmid, False, method=method, os_type=os_type)
    else:
        log_vm_status(vmid, False, method=method, os_type=os_type)

def cleanup_stale_overrides(active_vmids):
    """Remove override files for VMs no longer in config."""
    for filepath in glob.glob("/tmp/pve-vm-*-mem-override"):
        try:
            # Extract VMID from filename: /tmp/pve-vm-101-mem-override
            parts = os.path.basename(filepath).split("-")
            vmid = int(parts[2])
            if vmid not in active_vmids:
                os.remove(filepath)
                log(f"Cleaned up stale override for VM {vmid}")
                # Also remove from status tracking
                if vmid in _vm_status:
                    del _vm_status[vmid]
        except (ValueError, IndexError, OSError):
            pass

def main():
    global _cycle_count
    
    if not os.path.exists(CONFIG_FILE):
        log(f"Config file not found at {CONFIG_FILE}")
        sys.exit(1)

    log("Starting proxmox-rmem service (Multi-Method)...")
    log(f"Config file: {CONFIG_FILE}")
    log("Config is reloaded every cycle - no restart needed after editing config.json")
    
    while True:
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            
            # Get active VMIDs for cleanup
            active_vmids = set()
            threads = []
            
            for vm in config:
                if not vm.get('enabled', True):
                    continue
                vmid = vm.get('vmid')
                if vmid:
                    active_vmids.add(vmid)
                t = threading.Thread(target=update_vm, args=(vm,))
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()
            
            # Cleanup stale override files every 30 cycles (~1 minute)
            _cycle_count += 1
            if _cycle_count >= LOG_INTERVAL:
                cleanup_stale_overrides(active_vmids)
                _cycle_count = 0
                
        except json.JSONDecodeError as e:
            log(f"Config parse error: {e}")
        except Exception as e:
            log(f"Main loop error: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    main()
