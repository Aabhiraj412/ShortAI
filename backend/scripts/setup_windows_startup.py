"""CrazzyDocs Windows Startup Setup & Status CLI.

Provides cross-platform / Python programmatic inspection and management of the
Windows Scheduled Tasks:
  - CrazzyDocs-Production-Scheduler
  - CrazzyDocs-Health-Dashboard
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PS_SCRIPT = PROJECT_ROOT / "backend" / "scripts" / "setup_windows_startup_tasks.ps1"


def run_ps_action(action: str) -> int:
    """Run setup_windows_startup_tasks.ps1 with specified action."""
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PS_SCRIPT),
        "-Action",
        action,
    ]
    res = subprocess.run(cmd)
    return res.returncode


def main():
    parser = argparse.ArgumentParser(description="Manage CrazzyDocs Windows Startup Tasks")
    parser.add_argument(
        "action",
        nargs="?",
        default="Status",
        choices=["Register", "Unregister", "Start", "Stop", "Status", "Enable-Scheduler", "Disable-Scheduler"],
        help="Action to execute (default: Status)",
    )
    args = parser.parse_args()
    return run_ps_action(args.action)


if __name__ == "__main__":
    sys.exit(main())
