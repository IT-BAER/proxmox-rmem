import sys
import re
import os
import shutil

TARGET_FILE = '/usr/share/perl5/PVE/QemuServer.pm'
BACKUP_FILE = '/usr/share/perl5/PVE/QemuServer.pm.bak'

# Patch code to be inserted AFTER '$d->{mem} = $d->{memhost};'
# This location is in the main loop that runs for every VM, not inside a callback
PATCH_CODE = r'''
        # proxmox-rmem: Override memory from external file if available
        if (-f "/tmp/pve-vm-$vmid-mem-override") {
            if (open(my $fh, '<', "/tmp/pve-vm-$vmid-mem-override")) {
                my $override_mem = <$fh>;
                chomp $override_mem;
                if ($override_mem && $override_mem =~ /^\d+$/) {
                    $d->{mem} = $override_mem;
                }
                close($fh);
            }
        }
'''

def main():
    if not os.path.exists(TARGET_FILE):
        print(f"Error: {TARGET_FILE} not found.")
        sys.exit(1)

    with open(TARGET_FILE, 'r') as f:
        content = f.read()

    if 'proxmox-rmem' in content:
        print("Already patched.")
        sys.exit(0)

    # Create backup
    shutil.copy2(TARGET_FILE, BACKUP_FILE)
    print(f"Backup created at {BACKUP_FILE}")

    # Search for the insertion point - AFTER the default memhost assignment
    # This is in the main vmstatus loop that runs for EVERY VM
    # The line: $d->{mem} = $d->{memhost}; # default to cgroup PSS sum...
    search_pattern = r'\$d->\{mem\}\s*=\s*\$d->\{memhost\};[^\n]*'
    m = re.search(search_pattern, content)
    
    if not m:
        # Fallback: try finding the ballooninfo assignment (old method)
        print("Warning: Could not find preferred insertion point, trying fallback...")
        search_str = '$d->{ballooninfo} = $info;'
        idx = content.find(search_str)
        if idx == -1:
            m2 = re.search(r'\$d->\{ballooninfo\}\s*=\s*\$info;', content)
            if not m2:
                print("Error: Could not find any valid insertion point in QemuServer.pm")
                sys.exit(1)
            idx = m2.start()
        line_start = content.rfind('\n', 0, idx) + 1
        insert_pos = line_start
    else:
        # Insert AFTER the memhost assignment line
        insert_pos = m.end()
    
    new_content = content[:insert_pos] + PATCH_CODE + content[insert_pos:]

    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("Patch applied successfully.")
    print("Location: after '$d->{mem} = $d->{memhost}' in main vmstatus loop")
    # Note: Service restart is handled by install.sh to avoid disconnecting web console

if __name__ == "__main__":
    main()
