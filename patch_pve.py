import sys
import re
import os
import shutil

TARGET_FILE = '/usr/share/perl5/PVE/QemuServer.pm'
BACKUP_FILE = '/usr/share/perl5/PVE/QemuServer.pm.bak'

PATCH_CODE = r'''
        # GEMINI PATCH: Override memory from external file
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

    if 'GEMINI PATCH' in content:
        print("Already patched.")
        sys.exit(0)

    # Create backup
    shutil.copy2(TARGET_FILE, BACKUP_FILE)
    print(f"Backup created at {BACKUP_FILE}")

    # Search for the insertion point
    # We look for the assignment of ballooninfo
    search_str = '$d->{ballooninfo} = $info;'
    idx = content.find(search_str)
    
    if idx == -1:
        # Fallback to regex if exact string match fails due to whitespace
        m = re.search(r'\$d->\{ballooninfo\}\s*=\s*\$info;', content)
        if not m:
            print("Error: Could not find target insertion point in QemuServer.pm")
            sys.exit(1)
        idx = m.start()
    
    # Find the start of the line (to maintain indentation flow, though we append our own block)
    # We insert BEFORE the found line.
    line_start = content.rfind('\n', 0, idx) + 1
    insert_pos = line_start

    new_content = content[:insert_pos] + PATCH_CODE + content[insert_pos:]

    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("Patch applied successfully.")
    print("Restarting pvestatd...")
    os.system("systemctl restart pvestatd pvedaemon pveproxy")

if __name__ == "__main__":
    main()
