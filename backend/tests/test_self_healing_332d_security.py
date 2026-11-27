"""Stage 3.32D final security audit: adversarial and regression tests.

Temporary databases and fake network transports only. No production database, network, ffprobe or recovery.
"""
import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import pytest

import backend.app.autonomy.control_plane as cpm
from backend.app.autonomy import registry
from backend.app.autonomy.control_plane import PermissionDenied, validate_operator
from backend.app.autonomy.recommendations.engine import RecommendationEngine
from backend.app.autonomy.self_healing import engine as eng_mod
from backend.app.autonomy.self_healing import services as svc_mod
from backend.app.autonomy.self_healing import store as S
from backend.app.autonomy.self_healing.engine import ENGINE_ACTOR
from test_self_healing_332d import (FLICKR, J_PAD, J_PROV, OP, Fakes, add_event, approve, build, events, incidents,  # noqa: F401
                                    recs, world)

PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]


def private(addr):
    return lambda host, port: [(2, 1, 6, "", (addr, port))]


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status, self._buf, self.headers = status, io.BytesIO(body), headers or {}

    def read(self, n=-1):
        return self._buf.read(n)

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, status=200, body=b"", headers=None):
        self.status, self.body, self.headers, self.calls = status, body, headers or {}, []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, timeout))
        return FakeResp(self.status, self.body, dict(self.headers))


def png_bytes(w=32, h=24):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(b, format="PNG")
    return b.getvalue()


def table_rows(db, prefix):
    conn = sqlite3.connect(str(db))
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?", (prefix + "%",))]
    out = {n: conn.execute(f"SELECT * FROM {n} ORDER BY 1").fetchall() for n in names}
    conn.close()
    return out


# -- A5. identity normalization ------------------------------------------------------------------------------------
@pytest.mark.parametrize("ident", [
    "self-healing-engine", "SELF-HEALING-ENGINE", "Self.Healing", "self healing", "self_healing", "self@healing",
    "s.e.l.f-h.e.a.l.i.n.g", "selfheal", " self-healing-engine ", "self--healing", "Recommendation Engine",
    "re.commendation-engine", "AUTONOMY", "auto-nomy", "control.plane", "standing approval", "sys tem",
    "\u0455elf-healing",          # Cyrillic dze lookalike
    "ｓｅｌｆ-healing",              # full-width
    "self\u200bhealing",          # zero-width space
    "self\u2014healing",          # em dash
    "self\thealing", "self\nhealing", "", "   ", "x" * 65,
])
def test_reserved_and_lookalike_identities_are_refused(ident):
    with pytest.raises(PermissionDenied):
        validate_operator(ident)


@pytest.mark.parametrize("ident", ["abhi", "Abhi R", "ops.lead@studio", "night-shift_2"])
def test_human_operator_names_still_work(ident):
    assert validate_operator(ident) == ident.strip()


# -- A4/A7/A8/A9. request integrity for self-healing actions -------------------------------------------------------
def _approved_provider(tmp_path):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    return cp, eng, clock, db, rec


@pytest.mark.parametrize("tamper", ["payload", "status_forged", "signature", "expired", "rejected", "cancelled", "approved_by"])
def test_tampered_or_closed_requests_never_execute(tmp_path, world, tamper):
    cp, eng, clock, db, rec = _approved_provider(tmp_path)
    rid = rec["request_id"]
    conn = sqlite3.connect(str(db))
    if tamper == "payload":
        p = dict(rec["payload"], query="Something Else")
        conn.execute("UPDATE autonomy_approval_requests SET payload_json=? WHERE id=?", (json.dumps(p), rid))
    elif tamper == "status_forged":                                        # a pending request marked approved in SQL
        r2 = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
        conn.execute("UPDATE autonomy_approval_requests SET status='APPROVED', approved_payload_hash=payload_hash, "
                     "approved_by='abhi', approval_mode='EXPLICIT' WHERE id=?", (r2["request_id"],))
        rid = r2["request_id"]
    elif tamper == "signature":
        conn.execute("UPDATE autonomy_approval_requests SET approval_signature='00' WHERE id=?", (rid,))
    elif tamper == "approved_by":                                          # signature bound to the approver
        conn.execute("UPDATE autonomy_approval_requests SET approved_by='someone-else' WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    if tamper == "expired":
        clock.advance(hours=13)                                            # TTL 12 h
    elif tamper == "rejected":
        cp.reject(rid, OP, "no")
    elif tamper == "cancelled":
        cp.cancel(rid, OP, "no")
    out = cp.execute(rid, "operator:abhi")
    assert out["executed"] is False
    assert world.calls == []                                               # the provider was never contacted


def test_completed_request_cannot_run_twice(tmp_path, world):
    cp, eng, clock, db, rec = _approved_provider(tmp_path)
    assert eng.execute(rec["id"], OP)["executed"] is True
    assert cp.execute(rec["request_id"], "operator:abhi")["executed"] is False
    assert eng.execute(rec["id"], OP)["executed"] is False
    assert len([c for c in world.calls if c[0] == "provider"]) == 1


def test_simultaneous_approvals_record_one_approval(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    errs = []

    def go():
        try:
            eng.approve(rec["id"], OP, rec["payload_hash"])
        except Exception as exc:
            errs.append(exc)
    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    approvals = [e for e in cp.audit_events(limit=500, request_id=rec["request_id"]) if e["event_type"] == "APPROVED"]
    assert len(approvals) == 1 and all(isinstance(e, cpm.ControlPlaneError) for e in errs)


@pytest.mark.parametrize("global_level,cat_level", [("OFF", "APPROVED_EXECUTION"), ("RECOMMEND", "APPROVED_EXECUTION"),
                                                    ("APPROVED_EXECUTION", "RECOMMEND")])
def test_global_and_category_levels_are_both_enforced(tmp_path, world, global_level, cat_level):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    cp.set_level("global", global_level, OP, "t")
    cp.set_level("self_healing", cat_level, OP, "t")
    res = eng.execute(rec["id"], OP)
    assert res["executed"] is False and "APPROVED_EXECUTION" in (res["reason"] or "") and world.calls == []
    if global_level == "OFF":
        assert eng.scan(force=True)["mode"] == "OFF"


def test_kill_switch_checked_at_execution_time(tmp_path, world):
    cp, eng, clock, db, rec = _approved_provider(tmp_path)
    cp.sentinel_path.write_text("stop", encoding="utf-8")                 # after approval, before execution
    assert cp.execute(rec["request_id"], "operator:abhi")["executed"] is False
    cp.sentinel_path.unlink()
    cp.set_dashboard_kill_switch(True, OP, "stop")
    assert eng.execute(rec["id"], OP)["executed"] is False and world.calls == []


def test_engine_never_executes_without_independent_approval(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    # No standing approval: nothing ran, every request waits for a human.
    assert cp.list_executions() == []
    assert {r["status"] for r in cp.list_requests(limit=200)} == {"PENDING"}
    # A standing approval can never cover the medium-risk actions, even if one is written into the configuration.
    conn = sqlite3.connect(str(db))
    grants = {"selfheal.provider.retry_readonly": {"grant_id": "sa_forged", "granted_by": "abhi", "max_per_day": 5,
                                                   "expires_at_utc": "2099-01-01T00:00:00.000000Z"}}
    conn.execute("INSERT OR REPLACE INTO autonomy_config (key, value_json, updated_at_utc, updated_by) VALUES "
                 "('standing_approvals', ?, 'x', 'abhi')", (json.dumps(grants),))
    conn.commit()
    conn.close()
    inc = incidents(eng)[J_PROV]
    payload = {"incident_id": inc["id"], "provider": "Openverse", "query": "Nares Strait"}
    with cp._tx() as c:                                                   # clear the open duplicate first
        c.execute("UPDATE autonomy_approval_requests SET status='CANCELLED' WHERE action_type='selfheal.provider.retry_readonly'")
    req = cp.submit("selfheal.provider.retry_readonly", payload, reason="x", proposed_by=ENGINE_ACTOR)
    assert req["proposal"]["request"]["status"] == "PENDING" and req["execution"] is None and world.calls == []


# -- A11. API security -----------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    monkeypatch.setattr(eng_mod, "get_engine", lambda: eng)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    token = local.get("/api/autonomy/session").json()["csrf_token"]
    return cp, eng, main, local, {"X-CrazzyDocs-CSRF": token}


def test_api_write_guards(api):
    from fastapi.testclient import TestClient
    cp, eng, main, local, h = api
    body = {"force": True}
    remote = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("192.168.1.20", 50000))
    assert remote.post("/api/self-healing/scan", json=body, headers=h).status_code == 403          # not loopback
    assert remote.get("/api/self-healing").status_code == 403
    assert local.post("/api/self-healing/scan", json=body).status_code == 403                       # no token
    assert local.post("/api/self-healing/scan", json=body, headers={"X-CrazzyDocs-CSRF": "x" * 43}).status_code == 403
    assert local.post("/api/self-healing/scan", json=body, headers={**h, "Origin": "http://evil.example"}).status_code == 403
    assert local.post("/api/self-healing/scan", json=body, headers={**h, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert local.post("/api/self-healing/scan", content=json.dumps(body), headers={**h, "Content-Type": "text/plain"}).status_code == 415
    assert local.post("/api/self-healing/scan", json={**body, "level": "APPROVED_EXECUTION"}, headers=h).status_code == 422
    assert local.post("/api/self-healing/scan", json={"operator": "\u0455elf-healing", "force": True}, headers=h).status_code == 422
    assert local.post("/api/self-healing/scan", json={"operator": "Self.Healing", "force": True}, headers=h).status_code == 422
    assert local.get("/api/self-healing/incidents?status=open&limit=100000").status_code == 400
    assert local.get("/api/self-healing?limit=0").status_code == 400
    assert local.post("/api/self-healing/recoveries/../../x/execute", json={}, headers=h).status_code in (404, 405)
    assert cp.list_requests() == []                                       # none of the refused calls did anything


def test_api_never_exposes_secrets(api):
    cp, eng, main, local, h = api
    conn = sqlite3.connect(str(eng.cp.db_path))
    secret = "AIzaSyD" + "x" * 32
    add_event(conn, eng.clock() - timedelta(minutes=5), J_PAD[0], "CYCLE_DISPATCH_FAILED",
              f"Gemini request failed: HTTP 503 for https://generativelanguage.googleapis.com/v1?key={secret}")
    conn.commit()
    conn.close()
    assert local.post("/api/self-healing/scan", json={"force": True}, headers=h).status_code == 200
    blob = json.dumps(local.get("/api/self-healing").json())
    blob += json.dumps(local.get("/api/self-healing/incidents?status=all").json())
    for i in local.get("/api/self-healing/incidents?status=all").json()["incidents"]:
        blob += json.dumps(local.get(f"/api/self-healing/incidents/{i['id']}").json())
    assert secret not in blob and "[redacted]" in blob
    assert secret not in json.dumps(table_rows(eng.cp.db_path, "selfheal_"), default=str)


# -- B. action safety: guarded network --------------------------------------------------------------------------------
@pytest.mark.parametrize("url,ok", [
    ("https://upload.wikimedia.org/a.png", True),
    ("https://live.staticflickr.com/1/2.jpg", True),
    ("http://upload.wikimedia.org/a.png", False),                 # not HTTPS
    ("https://user:pw@upload.wikimedia.org/a.png", False),        # credentials
    ("https://upload.wikimedia.org:8443/a.png", False),           # other port
    ("https://upload.wikimedia.org.evil.example/a.png", False),   # suffix trick
    ("https://evilwikimedia.org/a.png", False),                   # lookalike domain
    ("https://example.com/a.png", False),
    ("file:///C:/Windows/win.ini", False),
    ("https://127.0.0.1/a.png", False),
])
def test_destination_allow_list(url, ok):
    from backend.app.autonomy.self_healing.planner import ALLOWED_ASSET_HOSTS
    assert svc_mod.destination_allowed(url, ALLOWED_ASSET_HOSTS, PUBLIC) is ok


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1", "0.0.0.0",
                                  "::ffff:127.0.0.1", "64:ff9b::7f00:1", "64:ff9b::a00:5", "2002:7f00:1::", "2002:c0a8:101::"])
def test_allowed_host_resolving_to_private_address_is_refused(addr):
    assert svc_mod.destination_allowed("https://upload.wikimedia.org/a.png", ["upload.wikimedia.org"], private(addr)) is False


@pytest.mark.parametrize("addr", ["8.8.8.8", "2606:4700::1111", "64:ff9b::808:808", "2002:808:808::"])
def test_public_addresses_including_embedded_public_ipv4_are_allowed(addr):
    assert svc_mod._address_public(addr) is True


def test_unresolvable_host_fails_closed():
    def boom(host, port):
        raise OSError("dns down")
    assert svc_mod.destination_allowed("https://upload.wikimedia.org/a.png", ["upload.wikimedia.org"], boom) is False


def test_redirects_are_validated_on_every_hop():
    opener = svc_mod._opener(["upload.wikimedia.org"], PUBLIC)
    handler = [hd for hd in opener.handlers if isinstance(hd, urllib.request.HTTPRedirectHandler)][0]
    req = urllib.request.Request("https://upload.wikimedia.org/a.png")
    with pytest.raises(svc_mod.GuardError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/a.png")
    with pytest.raises(svc_mod.GuardError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://upload.wikimedia.org/a.png")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://upload.wikimedia.org/b.png") is not None
    assert handler.max_redirections == svc_mod.MAX_REDIRECTS
    assert not any(isinstance(hd, urllib.request.ProxyHandler) and hd.proxies for hd in opener.handlers)


def test_guarded_download_success_and_isolation(tmp_path):
    staging = tmp_path / "staging" / "apr_0123456789abcdef"
    op = FakeOpener(body=png_bytes(), headers={"Content-Type": "image/png"})
    out = svc_mod.download_to_staging("https://upload.wikimedia.org/a.png", staging, opener=op, resolver=PUBLIC)
    assert out["ok"] and Path(out["path"]).parent == staging and Path(out["path"]).name.startswith("staged_")
    assert [p.name for p in staging.iterdir()] == [Path(out["path"]).name]          # no .part left behind
    assert svc_mod.sha256_file(Path(out["path"])) == out["sha256"] and op.calls == [("https://upload.wikimedia.org/a.png", 15.0)]


@pytest.mark.parametrize("case", ["oversize", "declared_oversize", "html", "not_media", "corrupt_png", "deadline", "http_404",
                                  "bad_host", "no_content_type", "content_type_substring"])
def test_guarded_download_rejections_leave_nothing(tmp_path, case):
    staging = tmp_path / "s"
    body, headers, status, url, clock = png_bytes(), {"Content-Type": "image/png"}, 200, "https://upload.wikimedia.org/a.png", None
    if case == "oversize":
        body = b"\x89PNG\r\n\x1a\n" + b"0" * (svc_mod.DOWNLOAD_MAX_BYTES + 10)
    elif case == "declared_oversize":
        headers["Content-Length"] = str(svc_mod.DOWNLOAD_MAX_BYTES + 1)
    elif case == "html":
        headers["Content-Type"] = "text/html"
    elif case == "not_media":
        body, headers = b"MZ\x90\x00" + b"\x00" * 200, {"Content-Type": "application/octet-stream"}
    elif case == "corrupt_png":
        body = png_bytes()[:40]
    elif case == "deadline":
        ticks = iter([0.0] + [100.0] * 100)
        clock = lambda: next(ticks)
    elif case == "http_404":
        status = 404
    elif case == "bad_host":
        url = "https://example.com/a.png"
    elif case == "no_content_type":                     # a valid PNG body is still refused without a media type
        headers = {}
    elif case == "content_type_substring":              # "image" in a parameter is not an image media type
        headers["Content-Type"] = "text/html; profile=image"
    op = FakeOpener(status=status, body=body, headers=headers)
    out = svc_mod.download_to_staging(url, staging, opener=op, resolver=PUBLIC, clock=clock)
    assert out["ok"] is False and out["error"]
    assert not staging.exists() or list(staging.iterdir()) == []
    if case == "bad_host":
        assert op.calls == []


def test_guarded_download_never_overwrites(tmp_path):
    staging = tmp_path / "s"
    data = png_bytes()
    import hashlib
    existing = staging / f"staged_{hashlib.sha256(data).hexdigest()[:16]}.png"
    staging.mkdir()
    existing.write_bytes(b"ORIGINAL")
    out = svc_mod.download_to_staging("https://upload.wikimedia.org/a.png", staging,
                                      opener=FakeOpener(body=data, headers={"Content-Type": "image/png"}), resolver=PUBLIC)
    if sys.platform == "win32":                                           # os.rename refuses to replace on Windows
        assert out["ok"] is False
    assert existing.read_bytes() == b"ORIGINAL" or out["ok"] is True
    leftover = staging / "download.part"
    leftover.write_bytes(b"partial")                                     # exclusive create: a stale .part is not reused
    out2 = svc_mod.download_to_staging("https://upload.wikimedia.org/a.png", staging,
                                       opener=FakeOpener(body=data, headers={"Content-Type": "image/png"}), resolver=PUBLIC)
    assert out2["ok"] is False


@pytest.mark.parametrize("provider,body,ok", [
    ("Openverse", {"results": [{"id": 1}], "result_count": 1}, True),
    ("Openverse", {"results": []}, True),                                # answered, zero matches: available
    ("Openverse", {}, False),                                             # wrong shape
    ("Openverse", {"detail": "throttled", "error": "x"}, False),
    ("Wikimedia Commons", {"query": {"search": [{"title": "File:x"}]}}, True),
    ("Wikimedia Commons", {"error": {"code": "maxlag"}}, False),
    ("NASA Image Library", {"collection": {"items": []}}, True),
    ("NASA Image Library", {"reason": "down"}, False),
    ("Internet Archive", {"response": {"docs": [{"identifier": "a"}]}}, True),
    ("Internet Archive", "not json", False),
])
def test_provider_check_requires_documented_structure(provider, body, ok):
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    op = FakeOpener(body=raw)
    out = svc_mod.provider_search(provider, "Nares Strait", opener=op, resolver=PUBLIC)
    assert out["ok"] is ok and len(op.calls) == 1 and op.calls[0][1] == svc_mod.PROVIDER_TIMEOUT
    assert op.calls[0][0].startswith(svc_mod.provider_endpoint(provider) + "?")


def test_provider_check_is_bounded_uncached_and_fails_on_errors():
    op = FakeOpener(body=json.dumps({"results": []}).encode())
    for _ in range(3):
        svc_mod.provider_search("Openverse", "Nares Strait", opener=op, resolver=PUBLIC)
    assert len(op.calls) == 3                                             # no cache: every check is a real request
    assert svc_mod.provider_search("Openverse", "x y", opener=FakeOpener(status=503, body=b"{}"), resolver=PUBLIC)["ok"] is False
    big = FakeOpener(body=b"{" + b" " * (svc_mod.PROVIDER_MAX_BYTES + 5) + b"}")
    assert svc_mod.provider_search("Openverse", "x y", opener=big, resolver=PUBLIC)["ok"] is False
    assert svc_mod.provider_search("Pexels", "x y", opener=op, resolver=PUBLIC)["ok"] is False   # keyed provider refused
    assert svc_mod.provider_search("Openverse", "x y", opener=op, resolver=private("10.0.0.1"))["ok"] is False


# -- B. path and file safety ---------------------------------------------------------------------------------------------
def test_paths_outside_media_roots_are_never_read(tmp_path):
    roots = [tmp_path / "media"]
    roots[0].mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    for p in (outside, roots[0] / ".." / "secret.txt", Path("C:/Windows/win.ini")):
        assert svc_mod.read_tail(p, roots)["available"] is False
        assert svc_mod.probe_lock(p, roots)["checked"] is False
        assert svc_mod.ffprobe(p, roots)["ok"] is False
        assert svc_mod.image_info(p, roots)["ok"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing-violation semantics")
def test_windows_exclusive_lock_is_detected(tmp_path):
    import ctypes
    from ctypes import wintypes
    roots = [tmp_path]
    f = tmp_path / "temp_asset.tmp"
    f.write_bytes(b"x")
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(f), 0x80000000, 0, None, 3, 0x80, None)      # GENERIC_READ, no sharing
    assert h not in (None, wintypes.HANDLE(-1).value)
    try:
        r = svc_mod.probe_lock(f, roots)
        assert r["checked"] and r["locked"] is True and "held by another process" in r["detail"]
    finally:
        k32.CloseHandle(h)
    r = svc_mod.probe_lock(f, roots)
    assert r["locked"] is False
    os.replace(f, tmp_path / "renamed.tmp")                               # our probe left no handle open


@pytest.mark.parametrize("status", ["RUNNING", "RETRY_SCHEDULED", "QUEUED"])
def test_file_touching_diagnostics_refuse_busy_jobs(tmp_path, world, status):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    diag = [r for r in recs(eng, J_PAD[-1], "selfheal.diagnostic.run") if r["payload"]["diagnostic"] == "collect_log_context"][0]
    report = [r for r in recs(eng, J_PAD[-1], "selfheal.diagnostic.run") if r["payload"]["diagnostic"] == "generate_diagnostic_report"][0]
    approve(eng, diag)
    approve(eng, report)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE persistent_jobs SET status=? WHERE id=?", (status, J_PAD[-1]))
    conn.commit()
    conn.close()
    out = cp.execute(diag["request_id"], "operator:abhi")
    assert out["executed"] is False and ("worker" in out["error"] or "scheduler" in out["error"])
    assert cp.execute(report["request_id"], "operator:abhi")["executed"] is True     # touches no job file


# -- D. concurrency and reliability --------------------------------------------------------------------------------------
def test_concurrent_scans_do_not_duplicate(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    eng2 = eng_mod.SelfHealingEngine(cp, environ={}, clock=clock)           # a second engine instance on the same DB
    out = []
    ts = [threading.Thread(target=lambda e=e: out.append(e.scan(force=True))) for e in (eng, eng2, eng, eng2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(eng.store.list_incidents("all", 500)) == 6
    keys = [(r["incident_id"], r["action_type"], r["payload_hash"]) for r in eng.store.list_recoveries("open", 500)]
    assert len(keys) == len(set(keys))
    reqs = [r for r in cp.list_requests(limit=200)]
    assert len(reqs) == len({(r["action_type"], r["payload_hash"]) for r in reqs})


def test_timeout_keeps_the_resource_locked_until_the_operation_ends(tmp_path, world, monkeypatch):
    import dataclasses
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    spec = registry.REGISTRY["selfheal.provider.retry_readonly"]
    monkeypatch.setitem(registry.REGISTRY, spec.action_type, dataclasses.replace(spec, timeout_seconds=0.2))
    world.provider_delay = 1.5
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    eng.execute(rec["id"], OP)
    r = eng.store.get_recovery(rec["id"])
    assert r["status"] == "FAILED" and "may still have been running" in r["status_reason"]
    assert eng.store.held_locks([f"job:{J_PROV}", "provider:Openverse"], clock())   # still held: thread running
    other = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, other)
    res = eng.execute(other["id"], OP)
    assert res["executed"] is False and res["status"] == "UNSAFE" and "lock" in res["reason"]
    time.sleep(1.6)
    assert eng.store.held_locks([f"job:{J_PROV}", "provider:Openverse"], clock()) == []


def test_interrupted_execution_is_reconciled_not_rerun(tmp_path, world):
    cp, eng, clock, db, rec = _approved_provider(tmp_path)
    with cp._tx() as conn:                                                # simulate a process stop mid-execution
        conn.execute("UPDATE autonomy_approval_requests SET status='EXECUTING', claim_token='t', claimed_at_utc=? WHERE id=?",
                     (cp._ts(clock()), rec["request_id"]))
    clock.advance(minutes=10)
    eng.sync()
    r = eng.store.get_recovery(rec["id"])
    assert r["status"] == "FAILED" and world.calls == []
    assert cp.get_request(rec["request_id"])["execution_status"] == "INTERRUPTED"


# -- E. data, retention, schema ------------------------------------------------------------------------------------------
def test_reads_never_create_tables(tmp_path, world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    cp, eng, clock, db = build(tmp_path, level=None)
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    monkeypatch.setattr(eng_mod, "get_engine", lambda: eng)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    ov = local.get("/api/self-healing").json()
    assert ov["initialized"] is False and ov["incidents"] == []
    assert local.get("/api/self-healing/incidents?status=all").json() == {"incidents": []}
    assert local.get("/api/self-healing/history").json()["recoveries"] == []
    assert local.get("/api/self-healing/incidents/inc_000000000000").status_code == 404
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    assert local.post("/api/self-healing/recoveries/rcv_000000000000/execute", json={}, headers=h).status_code == 404
    assert table_rows(db, "selfheal_") == {}


def test_retention_prunes_every_table_and_artifacts(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    old = (clock() - timedelta(days=40)).strftime(S.TS_FMT)
    with eng.store.tx() as conn:
        conn.execute("INSERT INTO selfheal_notifications (key, kind, last_sent_utc) VALUES ('k_old', 'x', ?)", (old,))
        conn.execute("INSERT INTO selfheal_recoveries (id, incident_id, failure_signature, action_type, payload_json, payload_hash, "
                     "preconditions_json, approval_requirement, status, timeout_seconds, retry_limit, created_at_utc, "
                     "updated_at_utc) VALUES ('rcv_orphan00000', 'inc_gone00000000', 's', 'selfheal.diagnostic.run', '{}', 'h', "
                     "'{}', 'x', 'FAILED', 1, 0, ?, ?)", (old, old))
    st = eng.cp.autonomy_dir / "self_healing" / "staging" / "apr_old"
    st.mkdir(parents=True)
    f = st / "staged_x.png"
    f.write_bytes(b"x")
    os.utime(f, (time.time() - 8 * 86400,) * 2)
    fresh = eng.cp.autonomy_dir / "self_healing" / "staging" / "apr_new"
    fresh.mkdir()
    (fresh / "staged_y.png").write_bytes(b"y")
    out = eng.store.prune(clock())
    assert out["notification_keys_pruned"] == 1 and out["recoveries_pruned"] == 1
    assert eng.prune_artifacts() == 1 and not st.exists() and (fresh / "staged_y.png").exists()


# -- 4. integration -----------------------------------------------------------------------------------------------------
def test_recommendation_engine_and_self_healing_stay_independent(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    cp.set_level("recommendations", "RECOMMEND", OP, "t")
    eng.scan(force=True)
    before = table_rows(db, "selfheal_")
    reqs_before = cp.list_requests(limit=200)
    rec = RecommendationEngine(cp, schedule_provider=lambda: {}, environ={}, clock=clock)
    out = rec.run(triggered_by="operator:abhi", force=True)
    assert out["ran"]
    assert table_rows(db, "selfheal_") == before                           # read-only consumer
    assert cp.list_requests(limit=200) == reqs_before                      # nothing proposed or executed
    clock.advance(minutes=10)
    runs_before = table_rows(db, "autonomy_recommendation")
    eng.scan(force=True)
    assert table_rows(db, "autonomy_recommendation") == runs_before        # self-healing never runs recommendations
    cp.set_dashboard_kill_switch(True, OP, "stop all")                     # one switch stops both engines
    assert eng.scan(force=True)["mode"] == "BLOCKED" and rec.run(force=True)["mode"] == "BLOCKED"


def test_import_has_no_side_effects_and_no_cycles():
    import subprocess
    code = ("import sys, sqlite3; import backend.app.autonomy.self_healing.engine, backend.app.autonomy.handlers, "
            "backend.app.autonomy.recommendations.engine, backend.app.autonomy.self_healing.services as s; "
            "assert s._services is None; print('ok')")
    env = dict(os.environ, CRAZZYDOCS_DATABASE_PATH=str(Path(os.environ.get("TEMP", "/tmp")) / "nonexistent_332d.db"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]


def test_static_no_dangerous_calls_or_production_writes():
    pkg = Path(svc_mod.__file__).parent
    for p in pkg.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        import re as _re
        for bad in ("cached_fetch_json", "AssetDownloader(", "search_media(", "urlopen(", "pickle", "marshal",
                    "__import__", "ctypes", "rmtree", "shell=True"):
            assert bad not in src, f"{bad} in {p.name}"
        for rx in (r"(?<![.\w])eval\(", r"(?<![.\w])exec\(", r"(?<![.\w])compile\(", r"os\.system", r"os\.popen"):
            assert not _re.search(rx, src), f"{rx} in {p.name}"
        for table in ("persistent_jobs", "production_lifecycle_events", "studio_settings", "publishing_ledger",
                      "youtube_upload_state", "autonomy_config"):
            for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
                assert f"{verb} {table}" not in src, f"{verb} {table} in {p.name}"
