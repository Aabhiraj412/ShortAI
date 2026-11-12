"""Centralized Windows-hidden subprocess helpers to eliminate flashing CMD/PowerShell windows."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict


def hidden_subprocess_kwargs(*, detached: bool = False) -> Dict[str, Any]:
    """
    Returns subprocess keyword arguments that prevent console windows from appearing on Windows
    while preserving stdout/stderr capture and structured logging.
    """
    if os.name != "nt":
        return {}

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    flags = create_no_window
    if detached:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "creationflags": flags,
        "startupinfo": startupinfo,
    }


def run_hidden(command, **kwargs) -> subprocess.CompletedProcess:
    """Runs a subprocess with Windows console-suppression flags applied unless overridden."""
    merged = hidden_subprocess_kwargs()
    merged.update(kwargs)
    return subprocess.run(command, **merged)


def popen_hidden(command, *, detached: bool = False, **kwargs) -> subprocess.Popen:
    """Spawns a subprocess with Windows console-suppression flags applied unless overridden."""
    merged = hidden_subprocess_kwargs(detached=detached)
    merged.update(kwargs)
    return subprocess.Popen(command, **merged)


_SUBPROCESS_PATCHED = False


def install_windows_hidden_subprocess_defaults() -> None:
    """
    Ensures all subprocess.Popen / subprocess.run calls inside headless Windows daemon processes
    default to CREATE_NO_WINDOW + SW_HIDE unless the caller explicitly requests a console.
    """
    global _SUBPROCESS_PATCHED
    if _SUBPROCESS_PATCHED or os.name != "nt":
        return
    _orig_popen = subprocess.Popen
    create_new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    class _HiddenPopen(_orig_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            flags = int(kwargs.get("creationflags", 0) or 0)
            if not (flags & create_new_console):
                kwargs["creationflags"] = flags | create_no_window
                if kwargs.get("startupinfo") is None:
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
                    si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                    kwargs["startupinfo"] = si
            super().__init__(*args, **kwargs)

    subprocess.Popen = _HiddenPopen  # type: ignore[misc]
    _SUBPROCESS_PATCHED = True
