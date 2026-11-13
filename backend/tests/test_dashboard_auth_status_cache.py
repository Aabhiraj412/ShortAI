"""
The dashboard polls YouTube connection status from every open tab. Each live check
refreshes the token, builds the API client and calls channels.list (seconds), and it
used to run on the event loop, so the whole dashboard answered ~20 s late and the
startup monitor kept declaring it unhealthy. These tests pin the fix: status reads
are shared through a short cache and run off the event loop.
"""
import asyncio
import threading
import time
from unittest.mock import patch

import httpx

from backend.app.main import app
from backend.app.services import youtube_publishing_engine as ype
from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager


def _mgr(tmp_path, name="token.json"):
    m = YouTubeOAuthManager.__new__(YouTubeOAuthManager)
    m.token_file = str(tmp_path / name)
    m.client_secrets_file = str(tmp_path / "secrets.json")
    return m


def test_status_is_shared_within_the_cache_window_and_refreshed_after(tmp_path):
    m = _mgr(tmp_path)
    calls = []
    with patch.object(YouTubeOAuthManager, "get_auth_status", lambda self: calls.append(1) or {"status": "connected"}):
        assert m.get_auth_status_cached(max_age=60)["status"] == "connected"
        m.get_auth_status_cached(max_age=60)
        assert len(calls) == 1
        m.get_auth_status_cached(max_age=0)          # expired
        assert len(calls) == 2
        _mgr(tmp_path, "other.json").get_auth_status_cached(max_age=60)   # other token file
        assert len(calls) == 3


def test_saving_or_disconnecting_credentials_invalidates_the_cache(tmp_path):
    m = _mgr(tmp_path)
    calls = []
    with patch.object(YouTubeOAuthManager, "get_auth_status", lambda self: calls.append(1) or {"status": "connected"}):
        m.get_auth_status_cached()
        ype.invalidate_auth_status_cache()
        m.get_auth_status_cached()
        assert len(calls) == 2
        with patch.object(ype, "write_protected_file", lambda *a, **k: None):
            class _Creds:
                def to_json(self):
                    return "{}"
            m.save_credentials(_Creds())
        m.get_auth_status_cached()
        assert len(calls) == 3
        m.disconnect_channel()
        m.get_auth_status_cached()
        assert len(calls) == 4


def test_concurrent_pollers_trigger_a_single_live_check(tmp_path):
    m = _mgr(tmp_path)
    calls = []

    def slow(self):
        calls.append(1)
        time.sleep(0.3)
        return {"status": "connected"}

    with patch.object(YouTubeOAuthManager, "get_auth_status", slow):
        threads = [threading.Thread(target=m.get_auth_status_cached) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    assert len(calls) == 1


def test_slow_youtube_status_does_not_block_other_dashboard_requests():
    def slow(self):
        time.sleep(1.5)
        return {"status": "connected", "channel_connected": True}

    async def run():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            started = time.monotonic()
            yt = asyncio.create_task(c.get("/api/stage-3-22/youtube/status"))
            await asyncio.sleep(0.1)
            fast = await c.get("/api/dashboard/health")
            fast_elapsed = time.monotonic() - started
            yt_resp = await yt
            return fast, fast_elapsed, yt_resp

    with patch.object(YouTubeOAuthManager, "get_auth_status", slow):
        fast, fast_elapsed, yt_resp = asyncio.run(run())
    assert yt_resp.status_code == 200 and yt_resp.json()["status"] == "connected"
    assert fast.status_code == 200
    assert fast_elapsed < 1.0, f"health waited {fast_elapsed:.2f}s behind the YouTube status call"
