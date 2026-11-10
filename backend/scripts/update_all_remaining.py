"""
Updates all operational scripts in backend/scripts/, tests in backend/tests/, and PowerShell scripts in scripts/
from D:\\AI Arts\\AI to D:\\Agents\\CrazzyDocs.
"""

import os

DEST_ROOT = r"D:\Agents\CrazzyDocs"
OLD_ROOT = r"D:\AI Arts\AI"

dirs_to_process = [
    os.path.join(DEST_ROOT, "backend", "scripts"),
    os.path.join(DEST_ROOT, "backend", "tests"),
    os.path.join(DEST_ROOT, "scripts"),
    os.path.join(DEST_ROOT, "assets", "audio"),
]

updated_count = 0
for d in dirs_to_process:
    if not os.path.isdir(d):
        continue
    for root, _, files in os.walk(d):
        for f in files:
            if f.endswith((".py", ".ps1", ".bat")):
                # Avoid modifying the script itself in confusing ways
                if f in ["apply_path_migration.py", "fix_remaining_paths.py", "update_all_remaining.py"]:
                    continue
                fp = os.path.join(root, f)
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                new_content = content.replace(OLD_ROOT, DEST_ROOT).replace("D:/AI Arts/AI", "D:/Agents/CrazzyDocs")
                if new_content != content:
                    with open(fp, "w", encoding="utf-8") as fh:
                        fh.write(new_content)
                    print(f"Updated: {os.path.relpath(fp, DEST_ROOT)}")
                    updated_count += 1

print(f"Total files updated: {updated_count}")
