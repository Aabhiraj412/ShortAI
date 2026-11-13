"""
Shared HTTP for media providers: verified TLS, per-provider timeouts, bounded retries with
exponential backoff, response-size limits and provider health tracking.

TLS: certificates are always verified (CERT_REQUIRED + hostname check) against the Mozilla CA
bundle shipped by `certifi`. On Windows, Python's default context uses the local Windows root
store, which is populated lazily and was missing the GoDaddy G2 root, so every archive.org call
failed with "self-signed certificate in certificate chain" (diagnosed 2026-11-13: the leaf was a
genuine GoDaddy certificate for *.archive.org). Verification is never disabled.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Wikimedia's User-Agent policy asks for an identifiable client; same string the providers already send.
USER_AGENT = "CrazzyDocsStudio/1.0 (https://github.com/CrazzyDocs; research@crazzydocs.local)"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
PROVIDER_TIMEOUTS = {   # seconds; measured 2026-11-13: Openverse answers in 8-17 s when degraded
    "Wikimedia Commons": 12.0, "NASA Image Library": 12.0, "Openverse": 20.0, "Internet Archive": 15.0,
    "Pexels": 10.0, "Pixabay": 10.0,
}

_CTX: Optional[ssl.SSLContext] = None


def tls_context() -> ssl.SSLContext:
    global _CTX
    if _CTX is None:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:   # certifi missing: still a verifying context (OS store)
            ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        _CTX = ctx
    return _CTX


class ProviderError(RuntimeError):
    def __init__(self, provider: str, kind: str, detail: str = "", status: Optional[int] = None):
        super().__init__(f"{provider}: {kind}{f' HTTP {status}' if status else ''}{f' ({detail})' if detail else ''}")
        self.provider, self.kind, self.detail, self.status = provider, kind, detail, status

    @property
    def transient(self) -> bool:
        return self.kind in ("timeout", "connection") or (self.status in TRANSIENT_HTTP)


def classify(provider: str, exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        return ProviderError(provider, "http", status=exc.code)
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return ProviderError(provider, "tls", text[:160])
    if isinstance(reason, TimeoutError) or "timed out" in text.lower():
        return ProviderError(provider, "timeout", text[:120])
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return ProviderError(provider, "connection", text[:120])
    return ProviderError(provider, "error", f"{type(exc).__name__}: {text[:120]}")


class ProviderHealth:
    """Per-provider success/failure counts, latency and the last error (process-wide)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stats: Dict[str, Dict[str, Any]] = {}

    def _s(self, provider: str) -> Dict[str, Any]:
        return self.stats.setdefault(provider, {"ok": 0, "failed": 0, "consecutive_failures": 0,
                                                "last_error": None, "latency_ms": []})

    def success(self, provider: str, ms: float) -> None:
        with self._lock:
            s = self._s(provider)
            s["ok"] += 1; s["consecutive_failures"] = 0
            s["latency_ms"] = (s["latency_ms"] + [round(ms)])[-20:]

    def failure(self, provider: str, err: ProviderError) -> None:
        with self._lock:
            s = self._s(provider)
            s["failed"] += 1; s["consecutive_failures"] += 1
            s["last_error"] = {"kind": err.kind, "status": err.status, "detail": err.detail[:160], "at": time.time()}

    def tripped(self, provider: str, threshold: int = 3) -> bool:
        """A provider that failed `threshold` times in a row is skipped for the rest of the run."""
        with self._lock:
            return self._s(provider)["consecutive_failures"] >= threshold

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.stats))

    def persist(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"updated": time.time(), "providers": self.snapshot()}, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("provider health not persisted: %s", exc)


HEALTH = ProviderHealth()


def fetch(url: str, *, provider: str, timeout: Optional[float] = None, retries: int = 2, backoff: float = 1.5,
          max_bytes: int = DEFAULT_MAX_BYTES, headers: Optional[Dict[str, str]] = None,
          sleep: Callable[[float], None] = time.sleep) -> bytes:
    """GET with verified TLS. Retries only transient failures; raises ProviderError otherwise."""
    timeout = timeout or PROVIDER_TIMEOUTS.get(provider, 12.0)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    last: Optional[ProviderError] = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=tls_context()) as resp:
                hdrs = getattr(resp, "headers", None)
                declared = int((hdrs.get("Content-Length") if hdrs is not None else 0) or 0)
                if declared and declared > max_bytes:
                    raise ProviderError(provider, "too_large", f"{declared} bytes")
                body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ProviderError(provider, "too_large", f">{max_bytes} bytes")
            HEALTH.success(provider, (time.perf_counter() - t0) * 1000)
            return body
        except Exception as exc:   # classified, logged, maybe retried
            last = classify(provider, exc)
            HEALTH.failure(provider, last)
            logger.warning("media provider %s request failed (attempt %d/%d): %s", provider, attempt + 1,
                           retries + 1, last)
            if not last.transient or attempt == retries:
                break
            sleep(backoff * (2 ** attempt))
    assert last is not None
    raise last


def fetch_json(url: str, *, provider: str, **kw: Any) -> Any:
    body = fetch(url, provider=provider, max_bytes=kw.pop("max_bytes", 8 * 1024 * 1024), **kw)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise ProviderError(provider, "bad_response", f"invalid JSON: {exc}") from exc
