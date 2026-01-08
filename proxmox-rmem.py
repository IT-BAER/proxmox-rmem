import json
import time
import subprocess
import threading
import os
import sys

CONFIG_FILE = "/etc/proxmox-rmem/config.json"

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

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
        except Exception as e:
            log(f"Failed to write override for VM {vmid}: {e}")

def main():
    if not os.path.exists(CONFIG_FILE):
        log(f"Config file not found at {CONFIG_FILE}")
        sys.exit(1)

    log("Starting proxmox-rmem service (Multi-Method)...")
    
    while True:
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            
            threads = []
            for vm in config:
                if not vm.get('enabled', True):
                    continue
                t = threading.Thread(target=update_vm, args=(vm,))
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()
                
        except Exception as e:
            log(f"Main loop error: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    main()
