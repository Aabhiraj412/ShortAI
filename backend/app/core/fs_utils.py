"""Stage 3.34: file operations that tolerate brief Windows sharing violations.

A freshly written file can be held for a moment by another process (antivirus scanning, the search indexer, a
just-exited FFmpeg/ffprobe whose handle closes asynchronously). ``os.replace`` / ``os.remove`` then fail with
WinError 32 ("being used by another process"), which the retry policy classifies as PERMANENT and which failed
whole productions. These helpers retry only that sharing violation (and WinError 5 access denied, which the same
scanners cause), a few times with a short backoff; every other error is raised immediately.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)
TRANSIENT_WINERRORS = (32, 5)          # ERROR_SHARING_VIOLATION, ERROR_ACCESS_DENIED
DEFAULT_ATTEMPTS = 6
DEFAULT_BASE_DELAY = 0.15              # 0.15, 0.3, 0.6, 1.2, 2.4 s -> about 4.7 s worst case


def is_transient_lock(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in TRANSIENT_WINERRORS


def _retry(op: Callable[[], None], what: str, attempts: int, base_delay: float,
           sleep: Callable[[float], None]) -> None:
    for i in range(attempts):
        try:
            op()
            return
        except PermissionError as exc:
            if not is_transient_lock(exc) or i == attempts - 1:
                raise
            logger.info("%s: file busy (%s); retrying in %.2fs", what, getattr(exc, "winerror", "?"), base_delay * 2 ** i)
            sleep(base_delay * 2 ** i)


def replace_with_retry(src: str, dst: str, *, attempts: int = DEFAULT_ATTEMPTS, base_delay: float = DEFAULT_BASE_DELAY,
                       sleep: Callable[[float], None] = time.sleep) -> None:
    _retry(lambda: os.replace(src, dst), f"replace {os.path.basename(src)}", attempts, base_delay, sleep)


def remove_with_retry(path: Optional[str], *, attempts: int = DEFAULT_ATTEMPTS, base_delay: float = DEFAULT_BASE_DELAY,
                      sleep: Callable[[float], None] = time.sleep, missing_ok: bool = True) -> bool:
    """Removes ``path``; True when it is gone. A file that stays locked is logged and left (never raises for a lock),
    so cleanup can never turn a handled failure into a crash."""
    if not path:
        return True

    def op() -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            if not missing_ok:
                raise
    try:
        _retry(op, f"remove {os.path.basename(path)}", attempts, base_delay, sleep)
        return True
    except PermissionError as exc:
        logger.warning("Could not remove %s (still locked: %s); leaving it for later cleanup", path, exc)
        return False
