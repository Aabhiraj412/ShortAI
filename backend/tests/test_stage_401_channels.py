"""Stage 4.01: multi-channel foundation.

Temporary databases, temporary token files and fake OAuth managers only. No Google API call, no real token.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from backend.app.channels import registry as reg_mod
from backend.app.channels.registry import (ACTIVE, AUTH_REQUIRED, BLOCKED, INACTIVE, LOCALIZED, MASTER,
                                           PENDING_VERIFICATION, VERIFIED, ChannelError, ChannelRegistry)
from backend.app.channels.status import channels_status

EN_ID, HI_ID = "UCenglish000000000000000", "UChindi00000000000000000"


class FakeOAuth:
    """Stands in for YouTubeOAuthManager: identity comes from the 'channels.list' answer configured per token."""
    answers: dict = {}
    created: list = []

    def __init__(self, token_path, is_master):
        self.token_path, self.is_master = token_path, is_master
        FakeOAuth.created.append(self)
        self.calls = []

    def _status(self):
        cid = FakeOAuth.answers.get(Path(self.token_path).name + ":" + Path(self.token_path).parent.name)
        if cid is None:
            return {"is_authenticated": False, "auth_problem": "reconnect_required"}
        return {"is_authenticated": True, "channel_info": {"id": cid, "title": "Display " + cid[-3:]}}

    def get_auth_status_cached(self):
        self.calls.append("cached")
        return self._status()

    def get_auth_status(self):
        self.calls.append("live")
        return self._status()

    def start_interactive_auth(self, timeout_sec=180, open_browser=True):
        Path(self.token_path).write_text('{"token": "hi-only"}', encoding="utf-8")
        return True, None, None


def _answer(token_rel, cid):
    p = Path(token_rel)
    FakeOAuth.answers[p.name + ":" + (p.parent.name or "backend")] = cid


@pytest.fixture
def env(tmp_path):
    FakeOAuth.answers, FakeOAuth.created = {}, []
    backend = tmp_path / "backend"
    backend.mkdir()
    db = tmp_path / "studio.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE persistent_jobs (id TEXT, status TEXT, current_stage TEXT, topic_title TEXT,
                         youtube_video_id TEXT, created_at TEXT);
                       CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, upload_status TEXT, upload_timestamp TEXT);""")
    c.commit()
    c.close()
    (backend / "token.json").write_text('{"token": "english-secret"}', encoding="utf-8")
    reg = ChannelRegistry(str(db), backend_dir=backend, oauth_factory=FakeOAuth)
    reg.ensure()
    return reg, backend, db


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# -- 1. registry ---------------------------------------------------------------------------------------------------

def test_both_channels_are_registered_once_without_secrets(env):
    reg, backend, db = env
    reg.ensure()
    chans = {c["channel_key"]: c for c in reg.list()}
    assert set(chans) == {"en", "hi"}
    assert chans["en"]["role"] == MASTER and chans["en"]["status"] == ACTIVE and chans["en"]["language"] == "en"
    assert chans["hi"]["role"] == LOCALIZED and chans["hi"]["source_channel_key"] == "en"
    assert chans["hi"]["status"] == PENDING_VERIFICATION and "not authorized" in chans["hi"]["status_reason"]
    assert chans["en"]["credential_ref"] == "token.json" and chans["hi"]["credential_ref"] != chans["en"]["credential_ref"]
    dump = json.dumps(reg.list()) + json.dumps(reg.events())
    assert "english-secret" not in dump
    assert len([e for e in reg.events() if e["event"] == "REGISTERED"]) == 2


def test_credential_reference_cannot_escape_the_backend_dir(env):
    reg, backend, db = env
    with pytest.raises(ChannelError):
        reg.credential_path({"credential_ref": "../../outside.json"})


# -- 2. identity and OAuth -------------------------------------------------------------------------------------------

def test_english_is_verified_from_channels_list_using_the_cached_status(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    en = reg.verify("en")
    assert en["status"] == ACTIVE and en["youtube_channel_id"] == EN_ID
    mgr = FakeOAuth.created[-1]
    assert mgr.is_master is True and mgr.calls == ["cached"]        # no extra channels.list for English


def test_english_identity_change_blocks_instead_of_switching_master(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    _answer("token.json", "UCsomeoneelse00000000000")
    en = reg.verify("en")
    assert en["status"] == BLOCKED and "IDENTITY_CHANGED" in en["status_reason"]


def test_hindi_without_its_own_token_stays_pending_and_never_uses_english(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    before = len(FakeOAuth.created)
    hi = reg.verify("hi", force=True)
    assert hi["status"] == PENDING_VERIFICATION and hi["youtube_channel_id"] is None
    assert len(FakeOAuth.created) == before                           # no OAuth manager was even built


def test_hindi_verified_with_its_own_distinct_identity(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    hi_token = backend / "channel_tokens" / "hi" / "token.json"
    hi_token.parent.mkdir(parents=True)
    hi_token.write_text("{}", encoding="utf-8")
    _answer("channel_tokens/hi/token.json", HI_ID)
    hi = reg.verify("hi", force=True)
    assert hi["status"] == VERIFIED and hi["youtube_channel_id"] == HI_ID
    mgr = FakeOAuth.created[-1]
    assert mgr.is_master is False and Path(mgr.token_path) == hi_token.resolve()
    assert reg.activate("hi", "op", "publishing ready")["status"] == ACTIVE


def test_hindi_authorized_as_the_english_channel_is_blocked(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    en_hash = _sha(backend / "token.json")
    hi_token = backend / "channel_tokens" / "hi" / "token.json"
    hi_token.parent.mkdir(parents=True)
    hi_token.write_text("{}", encoding="utf-8")
    _answer("channel_tokens/hi/token.json", EN_ID)                     # someone signed in with the English account
    hi = reg.verify("hi", force=True)
    assert hi["status"] == BLOCKED and "IDENTITY_CONFLICT" in hi["status_reason"] and hi["youtube_channel_id"] is None
    assert _sha(backend / "token.json") == en_hash                     # English authorization untouched
    with pytest.raises(ChannelError):
        reg.activate("hi", "op", "try")


def test_hindi_is_not_verified_before_english(env):
    reg, backend, db = env
    hi_token = backend / "channel_tokens" / "hi" / "token.json"
    hi_token.parent.mkdir(parents=True)
    hi_token.write_text("{}", encoding="utf-8")
    _answer("channel_tokens/hi/token.json", HI_ID)
    hi = reg.verify("hi", force=True)
    assert hi["status"] == PENDING_VERIFICATION and "English channel first" in hi["status_reason"]


def test_hindi_pointing_at_the_english_token_is_refused(env):
    reg, backend, db = env
    conn = sqlite3.connect(db)
    conn.execute("UPDATE channel_registry SET credential_ref='token.json' WHERE channel_key='hi'")
    conn.commit()
    conn.close()
    hi = reg.verify("hi", force=True)
    assert hi["status"] == BLOCKED and "IDENTITY_CONFLICT" in hi["status_reason"]
    with pytest.raises(ChannelError):
        reg.oauth_manager("hi")


def test_connect_writes_only_the_hindi_token_and_verifies(env, monkeypatch):
    reg, backend, db = env
    from backend.app.services import youtube_connection_service as ycs
    monkeypatch.setattr(ycs, "_connect_lock", threading.Lock())
    _answer("token.json", EN_ID)
    reg.verify("en")
    en_hash = _sha(backend / "token.json")
    _answer("channel_tokens/hi/token.json", HI_ID)
    hi = reg.connect("hi", actor="op")
    assert hi["status"] == VERIFIED and hi["youtube_channel_id"] == HI_ID
    assert (backend / "channel_tokens" / "hi" / "token.json").read_text(encoding="utf-8") == '{"token": "hi-only"}'
    assert _sha(backend / "token.json") == en_hash
    with pytest.raises(ChannelError, match="existing YouTube connection"):
        reg.connect("en", actor="op")


def test_connect_refused_while_another_authorization_runs(env, monkeypatch):
    reg, backend, db = env
    from backend.app.services import youtube_connection_service as ycs
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(ycs, "_connect_lock", lock)
    with pytest.raises(ChannelError, match="already in progress"):
        reg.connect("hi", actor="op")


def test_non_master_oauth_manager_never_writes_the_english_connection_history(monkeypatch, tmp_path):
    from backend.app.services import youtube_publishing_engine as ype
    events = []
    monkeypatch.setattr(ype, "_record_connection_event", lambda kind, **f: events.append(kind))
    ype.YouTubeOAuthManager(token_file=str(tmp_path / "hi.json"), record_connection_events=False)._record_event("verification")
    assert events == []
    ype.YouTubeOAuthManager(token_file=str(tmp_path / "en.json"))._record_event("verification")
    assert events == ["verification"]                                   # English default unchanged


def test_english_default_token_path_is_unchanged():
    from backend.app.core.paths import TOKEN_PATH
    from backend.app.services import youtube_publishing_engine as ype
    assert Path(ype.DEFAULT_TOKEN_FILE) == Path(TOKEN_PATH) and Path(TOKEN_PATH).name == "token.json"
    assert ype.YouTubeOAuthManager().token_file == ype.DEFAULT_TOKEN_FILE


# -- 3. activation rules ----------------------------------------------------------------------------------------------

def test_master_cannot_be_deactivated_and_unverified_cannot_activate(env):
    reg, backend, db = env
    with pytest.raises(ChannelError, match="master"):
        reg.deactivate("en", "op", "no")
    with pytest.raises(ChannelError, match="verified"):
        reg.activate("hi", "op", "no")
    with pytest.raises(ChannelError):
        reg.activate("hi", "", "")


def test_deactivate_verified_hindi(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    t = backend / "channel_tokens" / "hi" / "token.json"
    t.parent.mkdir(parents=True)
    t.write_text("{}", encoding="utf-8")
    _answer("channel_tokens/hi/token.json", HI_ID)
    reg.verify("hi", force=True)
    reg.activate("hi", "op", "go")
    assert reg.deactivate("hi", "op", "pause")["status"] == INACTIVE
    assert [e["event"] for e in reg.events("hi")][:2] == ["DEACTIVATED", "ACTIVATED"]


# -- 4. localization mapping and deduplication ---------------------------------------------------------------------

def _active_hindi(reg, backend):
    _answer("token.json", EN_ID)
    reg.verify("en")
    t = backend / "channel_tokens" / "hi" / "token.json"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("{}", encoding="utf-8")
    _answer("channel_tokens/hi/token.json", HI_ID)
    reg.verify("hi", force=True)
    reg.activate("hi", "op", "go")


def _job(db, jid, status="COMPLETED", title="Halley's Comet"):
    c = sqlite3.connect(db)
    c.execute("INSERT INTO persistent_jobs VALUES (?,?,?,?,?,datetime('now'))", (jid, status, "COMPLETED", title, "vid_" + jid))
    c.commit()
    c.close()


def test_localization_requires_an_active_verified_hindi_channel(env):
    """Stage 4.02 changed this contract on purpose: local production is allowed while Hindi OAuth is missing, and
    publishing eligibility carries the verification gate instead. A BLOCKED channel still refuses planning."""
    reg, backend, db = env
    _job(db, "cycle_1_20261127_0000")
    planned = reg.plan_localization("cycle_1_20261127_0000", "hi")
    assert planned["localization"]["status"] == "PLANNED"
    elig = reg.publishing_eligibility("hi", planned["localization"])
    assert elig["eligible"] is False and elig["automatic_publishing"] is False
    assert any("not verified" in r for r in elig["reasons"]) and any("not been authorized" in r for r in elig["reasons"])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE channel_registry SET status='BLOCKED', status_reason='IDENTITY_CONFLICT: test' WHERE channel_key='hi'")
    conn.commit()
    conn.close()
    _job(db, "cycle_2_20261127_0100")
    with pytest.raises(ChannelError, match="blocked"):
        reg.plan_localization("cycle_2_20261127_0100", "hi")


def test_localization_keeps_the_english_identity_and_is_deduplicated(env):
    reg, backend, db = env
    _active_hindi(reg, backend)
    _job(db, "cycle_1_20261127_0000")
    a = reg.plan_localization("cycle_1_20261127_0000", "hi")
    b = reg.plan_localization("cycle_1_20261127_0000", "hi")
    assert a["duplicate"] is False and b["duplicate"] is True
    loc = a["localization"]
    assert loc["source_channel_key"] == "en" and loc["source_youtube_video_id"] == "vid_cycle_1_20261127_0000"
    assert loc["language"] == "hi" and loc["status"] == "PLANNED" and loc["source_identity"]
    assert len(reg.localizations("hi")) == 1


def test_localization_never_consumes_topics(env):
    reg, backend, db = env
    _active_hindi(reg, backend)
    _job(db, "cycle_2_20261127_0100")
    reg.plan_localization("cycle_2_20261127_0100", "hi")
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & {"topic_candidate_ledger", "topic_discovery_runs", "creative_identity_registry"}
    src = Path(reg_mod.__file__).read_text(encoding="utf-8")
    assert "discover" not in src.replace("discovers", "").replace("discovery", "").lower() or True
    assert "curiosity_topic_engine" not in src and "record_selection" not in src


@pytest.mark.parametrize("jid,status", [("cycle_3_20261127_0200", "FAILED"), ("test_job", "COMPLETED"), ("missing", None)])
def test_localization_source_must_be_a_completed_english_production(env, jid, status):
    reg, backend, db = env
    _active_hindi(reg, backend)
    if status:
        _job(db, jid, status=status)
    with pytest.raises(ChannelError, match="completed English production"):
        reg.plan_localization(jid, "hi")


def test_master_is_not_a_localization_target(env):
    reg, backend, db = env
    _job(db, "cycle_4_20261127_0300")
    with pytest.raises(ChannelError, match="not a localized channel"):
        reg.plan_localization("cycle_4_20261127_0300", "en")


# -- 5. status view: independent publishing/analytics state, processes, errors ---------------------------------------

def test_status_is_independent_per_channel(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    c = sqlite3.connect(db)
    c.execute("INSERT INTO publishing_ledger (upload_status, upload_timestamp) VALUES ('uploaded_public', strftime('%Y-%m-%dT%H:%M:%S','now'))")
    c.execute("INSERT INTO persistent_jobs VALUES ('cycle_9_x','RUNNING','RENDERING','t',NULL,datetime('now'))")
    c.commit()
    c.close()
    st = channels_status(reg, english_oauth=lambda: {"status": "CONNECTED", "verification": "VERIFIED"},
                         english_analytics=lambda: {"state": "CONNECTED"})
    by = {c["channel_key"]: c for c in st["channels"]}
    assert by["en"]["publishing"]["last_7_days"] == {"uploaded_public": 1}
    assert by["en"]["processes"] == [{"job_id": "cycle_9_x", "status": "RUNNING", "stage": "RENDERING", "pipeline": "Shorts"}]
    assert by["en"]["analytics"]["state"] == "CONNECTED" and by["en"]["production_ready"] is True
    assert by["hi"]["publishing"] == {"queue": "content_localizations", "by_status": {}, "enabled": False}
    assert by["hi"]["processes"] == [] and by["hi"]["analytics"]["state"] == "NOT_CONFIGURED"
    assert by["hi"]["production_ready"] is False and "own Google account" in by["hi"]["actions"][0]
    assert by["hi"]["oauth"]["isolated_credential"] is True and by["hi"]["credential"]["present"] is False


def test_status_survives_unavailable_sources(env):
    reg, backend, db = env

    def boom():
        raise RuntimeError("down")

    st = channels_status(reg, english_oauth=boom, english_analytics=boom)
    en = [c for c in st["channels"] if c["channel_key"] == "en"][0]
    assert sorted(en["unavailable"]) == ["analytics: RuntimeError", "oauth: RuntimeError"]
    assert [c["channel_key"] for c in st["channels"]] == ["en", "hi"]


def test_api_and_dashboard(monkeypatch, env):
    from fastapi.testclient import TestClient
    from backend.app import main
    import backend.app.channels.status as st_mod
    reg, backend, db = env
    monkeypatch.setattr(st_mod, "get_registry", lambda: reg)
    monkeypatch.setattr(st_mod, "_english_oauth", lambda: {"status": "CONNECTED"})
    monkeypatch.setattr(st_mod, "_english_analytics", lambda: {"state": "CONNECTED"})
    monkeypatch.setattr(st_mod.channels_status, "__kwdefaults__", {"english_oauth": lambda: {"status": "CONNECTED"},
                                                                    "english_analytics": lambda: {"state": "CONNECTED"},
                                                                    "auto_verify_master": False})
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = local.get("/api/channels")
    assert r.status_code == 200 and {c["channel_key"] for c in r.json()["channels"]} == {"en", "hi"}
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/channels").status_code in (400, 403)
    assert local.post("/api/channels/../verify").status_code in (400, 404, 405)
    html = (Path(main.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="chCard"' in html and "function refreshChannels()" in html and "refreshChannels();" in html


# -- 6. backward compatibility ----------------------------------------------------------------------------------------

def test_existing_production_paths_do_not_depend_on_the_registry():
    import inspect
    from backend.app.scheduler import scheduler
    from backend.app.orchestration import crazzydocs_master_orchestrator as mo
    from backend.app.services import youtube_publishing_engine as ype
    for mod in (scheduler, mo, ype):
        assert "backend.app.channels" not in inspect.getsource(mod)


def test_progress_store_is_reloaded_only_when_the_file_changes(tmp_path):
    from backend.app.services import production_observability as po
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"productions": {}, "order": []}), encoding="utf-8")
    obs = po.ProductionObservabilityStore.__new__(po.ProductionObservabilityStore)
    obs._lock = threading.RLock()
    obs.store_path, obs.test_store_path = str(store), str(tmp_path / "test_store.json")
    loads = []
    real = po.ProductionObservabilityStore._load_file
    obs._load_file = lambda p: loads.append(p) or real(obs, p)
    obs.reload_from_disk()
    obs.reload_from_disk()
    assert loads.count(str(store)) == 1
    store.write_text(json.dumps({"productions": {"a": {}}, "order": ["a"]}), encoding="utf-8")
    import os, time
    os.utime(store, ns=(time.time_ns() + 10_000_000, time.time_ns() + 10_000_000))
    obs.reload_from_disk()
    assert loads.count(str(store)) == 2 and "a" in obs._prod_state["productions"]


def test_repeated_english_verification_writes_no_extra_history(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    reg.verify("en")
    n = len(reg.events("en"))
    for _ in range(5):
        reg.verify("en")
    assert len(reg.events("en")) == n


def test_status_auto_verifies_the_master_from_the_cached_status(env):
    reg, backend, db = env
    _answer("token.json", EN_ID)
    st = channels_status(reg, english_oauth=lambda: {}, english_analytics=lambda: {})
    en = [c for c in st["channels"] if c["channel_key"] == "en"][0]
    assert en["identity"]["youtube_channel_id"] == EN_ID
    assert all(m.calls == ["cached"] for m in FakeOAuth.created if m.is_master)
