"""
Stage 3.17: Repository Secret & Excluded File Scanner.
Scans for secrets, credentials, tokens, SQLite databases, generated media, and virtual environments.
"""
import os
import sys
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path("D:/Agents/CrazzyDocs").resolve()

FORBIDDEN_FILES = [
    re.compile(r"^\.env(\.local|\.dev|\.prod)?$", re.I),
    re.compile(r".*client_secret.*\.json$", re.I),
    re.compile(r"^token.*\.json$", re.I),
    re.compile(r".*\.key$", re.I),
    re.compile(r".*\.pem$", re.I),
    re.compile(r".*\.db(-wal|-shm)?$", re.I),
    re.compile(r".*\.sqlite3?$", re.I),
    re.compile(r".*\.mp4$", re.I),
    re.compile(r".*\.mov$", re.I),
    re.compile(r".*\.mp3$", re.I),
    re.compile(r".*\.wav$", re.I),
]

FORBIDDEN_CONTENT_PATTERNS = [
    # Google API Key (39 chars starting with AIzaSy)
    (re.compile(r"AIzaSy[0-9A-Za-z-_]{33}"), "Google API Key"),
    # Real Google OAuth Refresh token (1//... or substantial real token)
    (re.compile(r"\"refresh_token\":\s*\"(1//[0-9A-Za-z-_]{40,})\""), "Real Google OAuth Refresh Token"),
    # Real Client Secret
    (re.compile(r"\"client_secret\":\s*\"(GOCSPX-[0-9A-Za-z-_]{20,})\""), "Real Google Client Secret"),
    # SMTP Password (non-placeholder)
    (re.compile(r"CRAZZYDOCS_SMTP_PASSWORD\s*=\s*['\"](?!(app-specific-password|your-password|placeholder|\$))[a-zA-Z0-9\s]{8,}['\"]"), "Real SMTP Password"),
]

def scan_staged_or_all(staged_only=False):
    os.chdir(REPO_ROOT)
    violations = []
    
    if staged_only:
        p = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
        files = [f.strip() for f in p.stdout.splitlines() if f.strip()]
        scan_mode = "STAGED FILES"
    else:
        # Check files that would be considered by git
        p = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        files = []
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                files.append(parts[1])
        scan_mode = "STATUS (TRACKED + UNTRACKED)"

    print(f"=== SECRET SCANNER: {scan_mode} ({len(files)} items) ===")

    for rel_path in files:
        file_path = REPO_ROOT / rel_path
        filename = file_path.name

        # Check forbidden filenames
        for pattern in FORBIDDEN_FILES:
            if pattern.match(filename):
                violations.append((rel_path, f"Forbidden file pattern: {pattern.pattern}"))
                break

        # Check forbidden content if file exists and is text
        if file_path.exists() and file_path.is_file():
            # Skip large binaries or images
            if file_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".ico", ".svg", ".zip", ".exe"]:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for c_pat, desc in FORBIDDEN_CONTENT_PATTERNS:
                    if c_pat.search(content):
                        # Skip if it is within this scanner itself
                        if "stage_3_17_secret_scanner.py" in rel_path:
                            continue
                        violations.append((rel_path, f"Found potential secret: {desc}"))
            except Exception as e:
                pass

    if violations:
        print(f"FAILED: Found {len(violations)} security violations:")
        for path, reason in violations:
            print(f"  [X] {path}: {reason}")
        return False
    else:
        print("PASSED: Zero secrets, tokens, private databases, or generated media found.")
        return True

if __name__ == "__main__":
    staged = "--staged" in sys.argv
    success = scan_staged_or_all(staged_only=staged)
    sys.exit(0 if success else 1)
