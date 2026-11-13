"""Shared provider HTTP: verified TLS always, bounded retries for transient failures only, size limits, health."""
import io
import ssl
import urllib.error
from unittest.mock import patch

import pytest

from backend.app.media import net


class _Resp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(code):
    return urllib.error.HTTPError("https://x.test", code, "err", {}, None)


def test_tls_context_always_verifies():
    ctx = net.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_transient_error_is_retried_with_backoff_then_succeeds():
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=[_http(503), _Resp(b'{"ok": 1}')]) as uo:
        body = net.fetch("https://x.test/a", provider="T1", retries=2, sleep=sleeps.append)
    assert body == b'{"ok": 1}' and uo.call_count == 2 and sleeps == [1.5]
    assert uo.call_args.kwargs["context"] is net.tls_context()


def test_non_transient_error_is_not_retried():
    with patch("urllib.request.urlopen", side_effect=[_http(404), _Resp(b"x")]) as uo:
        with pytest.raises(net.ProviderError) as e:
            net.fetch("https://x.test/b", provider="T2", retries=2, sleep=lambda s: None)
    assert uo.call_count == 1 and e.value.status == 404 and not e.value.transient


def test_tls_failure_is_classified_and_never_bypassed():
    err = urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED: self-signed certificate"))
    with patch("urllib.request.urlopen", side_effect=[err]) as uo:
        with pytest.raises(net.ProviderError) as e:
            net.fetch("https://x.test/c", provider="T3", retries=2, sleep=lambda s: None)
    assert e.value.kind == "tls" and uo.call_count == 1


def test_oversized_response_is_rejected():
    with patch("urllib.request.urlopen", return_value=_Resp(b"0" * 2048)):
        with pytest.raises(net.ProviderError) as e:
            net.fetch("https://x.test/d", provider="T4", retries=0, max_bytes=1024)
    assert e.value.kind == "too_large"


def test_health_trips_after_consecutive_failures():
    h = net.ProviderHealth()
    for _ in range(3):
        h.failure("P", net.ProviderError("P", "timeout"))
    assert h.tripped("P")
    h.success("P", 10)
    assert not h.tripped("P") and h.snapshot()["P"]["failed"] == 3
