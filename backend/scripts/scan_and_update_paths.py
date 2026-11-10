"""
Inspects and updates active runtime code and test fixtures in D:\\Agents\\CrazzyDocs
to use centralized path resolution (backend.app.core.paths) instead of hardcoded D:\\AI Arts\\AI.
"""

import os
import re

DEST_ROOT = r"D:\Agents\CrazzyDocs"
OLD_PATH_VARIANTS = [
    r"D:\Agents\CrazzyDocs",
    r"D:\\AI Arts\\AI",
    r"D:/Agents/CrazzyDocs",
]

def scan():
    skip_dirs = {".git", ".venv", "python-env", "cache", "__pycache__", "archive", "backups"}
    matches = {}
    for root, dirs, files in os.walk(DEST_ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith((".py", ".bat", ".ps1")):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, DEST_ROOT)
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if any(v in line for v in OLD_PATH_VARIANTS):
                            matches.setdefault(rel, []).append((i, line.strip()))
    return matches

if __name__ == "__main__":
    found = scan()
    print(f"Total matching files: {len(found)}")
    for rel, lines in sorted(found.items()):
        print(f"{rel}: {len(lines)} occurrences")
        for i, l in lines[:2]:
            print(f"   L{i}: {l[:100]}")
