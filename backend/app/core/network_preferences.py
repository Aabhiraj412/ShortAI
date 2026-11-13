"""
Prefer IPv4 when connecting to Google API hosts.

On this machine's network the adapter has a global IPv6 address and gateway, but IPv6
traffic to Google is dropped, so connect attempts hang until Windows gives up (~21 s).
The YouTube client sends API calls through httplib2, which re-raises the first connect
timeout instead of trying the next address (socket.timeout is TimeoutError on
Python 3.10+), so it never reaches the working IPv4 addresses. Every YouTube API call
(channel check, upload, thumbnail, status sync) then failed.

install_google_ipv4_preference() wraps socket.getaddrinfo so that, for *.googleapis.com
hosts only, IPv4 results come first. IPv6 results stay in the list as a fallback, and
every other host keeps the system's ordering. Idempotent; disable with
CRAZZYDOCS_PREFER_IPV4_FOR_GOOGLE=0.
"""
from __future__ import annotations

import os
import socket
import threading

GOOGLE_API_SUFFIXES = (".googleapis.com",)
_lock = threading.Lock()
_original_getaddrinfo = None


def _is_google_api_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return False
    h = host.lower().rstrip(".")
    return any(h.endswith(s) or h == s.lstrip(".") for s in GOOGLE_API_SUFFIXES)


def _ipv4_first(results):
    return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)   # stable


def install_google_ipv4_preference() -> bool:
    """Returns True when the preference is active."""
    global _original_getaddrinfo
    if os.getenv("CRAZZYDOCS_PREFER_IPV4_FOR_GOOGLE", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    with _lock:
        if _original_getaddrinfo is not None:
            return True
        original = socket.getaddrinfo

        def getaddrinfo(host, *args, **kwargs):
            results = original(host, *args, **kwargs)
            return _ipv4_first(results) if _is_google_api_host(host) else results

        getaddrinfo.__wrapped__ = original
        _original_getaddrinfo = original
        socket.getaddrinfo = getaddrinfo
        return True


def uninstall_google_ipv4_preference() -> None:
    """For tests."""
    global _original_getaddrinfo
    with _lock:
        if _original_getaddrinfo is not None:
            socket.getaddrinfo = _original_getaddrinfo
            _original_getaddrinfo = None
