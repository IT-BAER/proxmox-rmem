import json
import time
import subprocess
import threading
import os
import sys
import glob
import socket
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

CONFIG_FILE = "/etc/proxmox-rmem/config.json"
LOG_INTERVAL = 30  # Log successful updates every 30 cycles (~1 minute)
AUTO_DISCOVER_INTERVAL = 60  # Re-discover VMs every 60 cycles (~2 minutes)
MAX_CONCURRENT_VMS = 5  # Increased since direct QMP uses much less memory
QMP_TIMEOUT = 10  # Timeout for QMP socket operations

# Track last known state for change detection
_vm_status = {}  # vmid -> {'success': bool, 'mem': int}
_cycle_count = 0
_discovered_vms = {}  # vmid -> {'type': str, 'last_check': int}
_local_node = None  # Cached local node name

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_local_node():
    """Get the local Proxmox node name."""
    global _local_node
    if _local_node:
        return _local_node
    try:
        _local_node = socket.gethostname()
        return _local_node
    except:
        return "localhost"

# ============================================================================
# Direct QMP Socket Communication (bypasses Perl, low memory footprint)
# ============================================================================

class QMPConnection:
    """Direct connection to QEMU's QMP socket for guest agent commands."""
    
    def __init__(self, vmid):
        self.vmid = vmid
        self.socket_path = f"/run/qemu-server/{vmid}.qga"
        self.sock = None
    
    def connect(self):
        """Connect to the QGA socket."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(QMP_TIMEOUT)
        self.sock.connect(self.socket_path)
    
    def close(self):
        """Close the socket connection."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
    
    def send_command(self, execute, arguments=None):
        """Send a QGA command and return the response."""
        cmd = {"execute": execute}
        if arguments:
            cmd["arguments"] = arguments
        
        # Send command
        msg = json.dumps(cmd) + "\n"
        self.sock.sendall(msg.encode())
        
        # Read response (may come in chunks)
        response = b""
        while True:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                # Check if we have a complete JSON response
                try:
                    return json.loads(response.decode())
                except json.JSONDecodeError:
                    continue  # Need more data
            except socket.timeout:
                break
        
        if response:
            return json.loads(response.decode())
        return None
    
    def guest_exec(self, path, args=None, capture_output=True):
        """Execute a command in the guest and return the output."""
        arguments = {
            "path": path,
            "capture-output": capture_output
        }
        if args:
            arguments["arg"] = args
        
        # Start the command
        result = self.send_command("guest-exec", arguments)
        if not result or "return" not in result:
            return None
        
        pid = result["return"].get("pid")
        if not pid:
            return None
        
        # Poll for completion
        for _ in range(30):  # Max 30 attempts
            time.sleep(0.1)
            status = self.send_command("guest-exec-status", {"pid": pid})
            if status and "return" in status:
                ret = status["return"]
                if ret.get("exited"):
                    if ret.get("exitcode", 1) == 0:
                        out_data = ret.get("out-data", "")
                        if out_data:
                            # Output is base64 encoded
                            try:
                                return base64.b64decode(out_data).decode('utf-8', errors='replace')
                            except:
                                return out_data
                        return ""
                    return None
        return None
    
    def get_osinfo(self):
        """Get OS information from the guest agent."""
        result = self.send_command("guest-get-osinfo")
        if result and "return" in result:
            return result["return"]
        return None


def qga_exec(vmid, path, args=None):
    """Execute a command via QGA using direct socket (low memory)."""
    try:
        qmp = QMPConnection(vmid)
        qmp.connect()
        try:
            return qmp.guest_exec(path, args)
        finally:
            qmp.close()
    except Exception:
        return None


def qga_get_osinfo(vmid):
    """Get OS info via QGA using direct socket."""
    try:
        qmp = QMPConnection(vmid)
        qmp.connect()
        try:
            return qmp.get_osinfo()
        finally:
            qmp.close()
    except Exception:
        return None

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
    """Fetch memory from Linux VM using direct QGA socket."""
    output = qga_exec(vmid, "cat", ["/proc/meminfo"])
    if output:
        return parse_linux_meminfo(output)
    return None

def fetch_memory_qga_bsd(vmid):
    """Fetch memory from BSD VM using direct QGA socket."""
    output = qga_exec(vmid, "sysctl", ["-n", "vm.stats.vm.v_active_count", "vm.stats.vm.v_wire_count", "vm.stats.vm.v_page_size"])
    if output:
        parts = output.split()
        if len(parts) == 3:
            try:
                active = int(parts[0])
                wired = int(parts[1])
                page_size = int(parts[2])
                return (active + wired) * page_size
            except ValueError:
                pass
    return None

def fetch_memory_qga_windows(vmid):
    """Fetch memory from Windows VM using direct QGA socket."""
    # Use wmic to get memory stats
    output = qga_exec(vmid, "wmic", ["OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/value"])
    if output:
        return parse_windows_wmic(output)
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

def detect_os_via_qga(vmid):
    """
    Detect the OS type of a VM using QGA (direct socket).
    Returns: 'linux', 'windows', 'bsd', or None if detection fails.
    """
    # First, try to get OS info via direct QGA socket
    result = qga_get_osinfo(vmid)
    if result:
        os_name = result.get('name', '').lower()
        kernel = result.get('kernel-release', '').lower()
        os_id = result.get('id', '').lower()
        
        # Check for Windows
        if 'windows' in os_name or 'microsoft' in os_name:
            return 'windows'
        
        # Check for BSD variants
        if any(bsd in os_id for bsd in ['freebsd', 'openbsd', 'netbsd', 'opnsense', 'pfsense']):
            return 'bsd'
        if 'freebsd' in kernel:
            return 'bsd'
        
        # Default to Linux for other Unix-like systems
        if os_name or os_id:
            return 'linux'
    
    # Fallback: Try to detect via command execution
    # Check for Windows by running cmd.exe
    output = qga_exec(vmid, "cmd.exe", ["/c", "ver"])
    if output and 'Windows' in output:
        return 'windows'
    
    # Check for Linux/BSD by uname
    output = qga_exec(vmid, "uname", ["-s"])
    if output:
        os_name = output.strip().lower()
        if 'bsd' in os_name:
            return 'bsd'
        elif 'linux' in os_name:
            return 'linux'
    
    return None

def get_running_vms_with_qga():
    """
    Get list of running VMs on this node that have QGA enabled.
    Returns: list of vmid integers
    """
    node = get_local_node()
    vm_list = []
    
    try:
        # Get all VMs on this node
        cmd = ['pvesh', 'get', f'/nodes/{node}/qemu', '--output-format', 'json']
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10)
        vms = json.loads(output)
        
        for vm in vms:
            vmid = vm.get('vmid')
            status = vm.get('status', '')
            
            # Only process running VMs
            if status != 'running' or not vmid:
                continue
            
            # Check if QGA is enabled in VM config
            try:
                cfg_cmd = ['pvesh', 'get', f'/nodes/{node}/qemu/{vmid}/config', '--output-format', 'json']
                cfg_output = subprocess.check_output(cfg_cmd, stderr=subprocess.DEVNULL, timeout=5)
                config = json.loads(cfg_output)
                
                # Check for agent setting (agent: 1 or agent: enabled=1,...)
                agent_setting = str(config.get('agent', '0'))
                if agent_setting.startswith('1') or 'enabled=1' in agent_setting:
                    vm_list.append(vmid)
            except:
                continue
    except Exception as e:
        log(f"Auto-discover: Failed to get VM list: {e}")
    
    return vm_list

def discover_vms():
    """
    Discover all running VMs with QGA and detect their OS type.
    Returns: list of vm_config dicts ready for update_vm()
    """
    global _discovered_vms
    
    vms_with_qga = get_running_vms_with_qga()
    discovered = []
    
    for vmid in vms_with_qga:
        # Check if we already know this VM's type (cache for performance)
        cached = _discovered_vms.get(vmid)
        if cached and (_cycle_count - cached.get('last_check', 0)) < AUTO_DISCOVER_INTERVAL:
            os_type = cached['type']
        else:
            # Detect OS type
            os_type = detect_os_via_qga(vmid)
            if os_type:
                _discovered_vms[vmid] = {'type': os_type, 'last_check': _cycle_count}
                log(f"Auto-discover: VM {vmid} detected as '{os_type}'")
            else:
                log(f"Auto-discover: VM {vmid} - could not detect OS (QGA not responding?)")
                continue
        
        discovered.append({
            'vmid': vmid,
            'type': os_type,
            'method': 'qga',
            '_auto_discovered': True
        })
    
    return discovered

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
    
    auto_mode = False
    last_discover_cycle = -AUTO_DISCOVER_INTERVAL  # Force discovery on first run
    
    while True:
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            
            # Check if auto-discovery mode is enabled
            # Config can be: {"auto": true} or [{"vmid": "*"}] or [{"auto": true}] or mixed
            explicit_vms = []
            auto_mode = False
            
            if isinstance(config, dict):
                auto_mode = config.get('auto', False)
                explicit_vms = config.get('vms', [])
            elif isinstance(config, list):
                for vm in config:
                    if vm.get('auto') == True or vm.get('vmid') == '*' or vm.get('vmid') == 'auto':
                        auto_mode = True
                    elif vm.get('vmid'):
                        explicit_vms.append(vm)
            
            # Build the list of VMs to process
            all_vms = []
            explicit_vmids = set()
            
            # Add explicit VMs first (they take priority)
            for vm in explicit_vms:
                if vm.get('enabled', True) and vm.get('vmid'):
                    all_vms.append(vm)
                    explicit_vmids.add(vm.get('vmid'))
            
            # Auto-discover additional VMs if enabled
            if auto_mode:
                if (_cycle_count - last_discover_cycle) >= AUTO_DISCOVER_INTERVAL or _cycle_count == 0:
                    discovered = discover_vms()
                    # Only add VMs that aren't explicitly configured
                    for vm in discovered:
                        if vm['vmid'] not in explicit_vmids:
                            all_vms.append(vm)
                    last_discover_cycle = _cycle_count
                    if discovered:
                        log(f"Auto-discover: Found {len(discovered)} VMs with QGA, {len([v for v in discovered if v['vmid'] not in explicit_vmids])} new")
                else:
                    # Use cached discovery data
                    for vmid, cached in _discovered_vms.items():
                        if vmid not in explicit_vmids:
                            all_vms.append({
                                'vmid': vmid,
                                'type': cached['type'],
                                'method': 'qga',
                                '_auto_discovered': True
                            })
            
            # Get active VMIDs for cleanup
            active_vmids = set()
            
            for vm in all_vms:
                vmid = vm.get('vmid')
                if vmid:
                    active_vmids.add(vmid)
            
            # Use thread pool to limit concurrent QGA/SSH calls (reduces memory spikes)
            if all_vms:
                with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_VMS) as executor:
                    futures = [executor.submit(update_vm, vm) for vm in all_vms]
                    # Wait for all to complete
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            log(f"Worker error: {e}")
            
            # Cleanup stale override files every 30 cycles (~1 minute)
            _cycle_count += 1
            if _cycle_count >= LOG_INTERVAL:
                cleanup_stale_overrides(active_vmids)
                # Don't reset cycle count, we need it for discovery interval
                if _cycle_count >= LOG_INTERVAL * 100:  # Prevent overflow
                    _cycle_count = LOG_INTERVAL
                    last_discover_cycle = 0
                
        except json.JSONDecodeError as e:
            log(f"Config parse error: {e}")
        except Exception as e:
            log(f"Main loop error: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    main()
