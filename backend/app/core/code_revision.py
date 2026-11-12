"""
Source-code revision fingerprint used to detect processes running stale code.

The revision is a SHA-256 over every backend/app/**/*.py file plus the daemon and
dashboard entry scripts (path + bytes, sorted), so it changes whenever code that a
long-running process imported changes on disk, committed or not.

LOADED_CODE_REVISION is computed once at import, i.e. when the process loaded its
code. current_source_revision() re-hashes the files on disk (cached briefly).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from backend.app.core.paths import APP_DIR, BACKEND_DIR

_ENTRY_SCRIPTS = ("scripts/run_scheduler_daemon.py", "scripts/run_dashboard.py", "scripts/startup_manager.py")
_CACHE_SECONDS = 15.0
_cache: tuple[float, str] | None = None


def _source_files() -> list[Path]:
    files = sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    files += [BACKEND_DIR / s for s in _ENTRY_SCRIPTS if (BACKEND_DIR / s).is_file()]
    return files


def compute_source_revision() -> str:
    h = hashlib.sha256()
    for path in _source_files():
        try:
            data = path.read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            continue
        h.update(path.relative_to(BACKEND_DIR).as_posix().encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()[:16]


def current_source_revision(max_age_seconds: float = _CACHE_SECONDS) -> str:
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > max_age_seconds:
        _cache = (now, compute_source_revision())
    return _cache[1]


LOADED_CODE_REVISION = compute_source_revision()
