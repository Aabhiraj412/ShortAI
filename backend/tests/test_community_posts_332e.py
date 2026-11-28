"""Stage 3.32E: YouTube Community post DRAFT automation.

Every test runs on a temporary SQLite database with production-shaped tables and a temporary media folder (research
dossiers, attribution manifests and real image files). No production database, no network, no YouTube client.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from backend.app.community import content as C
from backend.app.community import images as I
from backend.app.community import service as svc_mod
from backend.app.community import store as S
from backend.app.community import validation as V
from backend.app.community.service import CommunityService

OP = "abhi"
IST_10_30 = datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)          # 10:30 IST
JOBS = [
    ("cycle_1_20261119_0100", "Cassini spacecraft at Saturn", "Space and Astronomy", "PUBVID00001", "public"),
    ("cycle_2_20261119_0200", "The printing press revolution", "History", "PRIVID00002", "private"),
    ("cycle_3_20261119_0300", "How transistors shrank", "Technology", None, None),
    ("cycle_4_20261118_0400", "Why bread rises", "Everyday Science", None, None),
    ("cycle_5_20261118_0500", "Neural networks learn to see", "Artificial Intelligence", None, None),
    ("cycle_6_20261117_0600", "Octopus camouflage", "Nature and Wildlife", None, None),
]


# -- world ---------------------------------------------------------------------------------------------------------
# Encoding 900x700 random noise as PNG takes 2-5 s per world (18 images) and every test builds a fresh world. The
# images depend only on (seed, size), so each is encoded once per test session and its bytes are written into every
# world's own folder: same files, same hashes, nothing shared on disk (immutable bytes only).
_PNG_BYTES: dict = {}


def _png_bytes(seed: int, size) -> bytes:
    key = (seed, tuple(size))
    if key not in _PNG_BYTES:
        import io
        rnd = random.Random(seed)
        img = Image.frombytes("RGB", size, rnd.randbytes(size[0] * size[1] * 3))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        _PNG_BYTES[key] = buf.getvalue()
    return _PNG_BYTES[key]


def _png(path: Path, seed: int, size=(900, 700)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(seed, size))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_world(tmp_path: Path, n_jobs: int = len(JOBS)) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "studio.db"
    media = tmp_path / "media"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE persistent_jobs (id TEXT PRIMARY KEY, topic_title TEXT, status TEXT, completed_at TEXT);
        CREATE TABLE publishing_ledger (id INTEGER PRIMARY KEY, topic_title TEXT, video_path TEXT, youtube_video_id TEXT,
                                        upload_status TEXT, visibility TEXT);
        CREATE TABLE topic_candidate_ledger (candidate_id TEXT PRIMARY KEY, title TEXT, summary TEXT, category TEXT,
                                             score REAL, last_discovered_at TEXT, selection_status TEXT);
        CREATE TABLE content_ledger (id INTEGER PRIMARY KEY, category TEXT);
        CREATE TABLE creative_identity_registry (job_id TEXT, category TEXT);
    """)
    for i, (job, title, cat, vid, vis) in enumerate(JOBS[:n_jobs]):
        done = (IST_10_30 - timedelta(days=1, hours=i)).strftime("%Y-%m-%d %H:%M:%S.%f")
        conn.execute("INSERT INTO persistent_jobs VALUES (?,?,?,?)", (job, title, "COMPLETED", done))
        conn.execute("INSERT INTO content_ledger (category) VALUES (?)", (cat,))
        if vid:
            conn.execute("INSERT INTO publishing_ledger (topic_title, video_path, youtube_video_id, upload_status, visibility) "
                         "VALUES (?,?,?,?,?)", (title, f"D:\\x\\media\\productions\\{job}\\video\\v.mp4", vid,
                                                "uploaded_public", vis))
        base = media / "productions" / job
        (base / "research").mkdir(parents=True)
        dossier = {"title": title, "category": cat, "composite_score": 9 - i * 0.5,
                   "summary": f"{title} is a well documented subject.",
                   "factual_bullets": [f"Researchers measured {120 + i * 7} distinct samples during the {title.lower()} study.",
                                       f"The first detailed account of {title.lower()} was written in {1900 + i * 11}.",
                                       f"Experts describe {title.lower()} as a clear example of careful observation.",
                                       "This bullet was cut off in the dossier..."],
                   "winning_hook": f"{title} hides a surprising detail."}
        (base / "research" / f"research_{job}.json").write_text(json.dumps(dossier), encoding="utf-8")
        acq = base / "assets" / "acquired"
        sha_a = _png(acq / f"Openverse_a_{i}.png", seed=i * 10 + 1)
        sha_b = _png(acq / f"Openverse_b_{i}.png", seed=i * 10 + 2)
        _png(acq / f"unlisted_{i}.png", seed=i * 10 + 3)                               # no manifest entry
        manifest = {"items": [
            {"title": f"Photo A {i}", "creator": "Jane Doe", "source_repository": "Openverse", "license": "CC0",
             "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "verified": True,
             "asset_type": "image", "sha256": sha_a, "modification_permitted": True,
             "usage_restrictions": "None (Commercial Use Permitted)", "asset_id": f"ov_a_{i}"},
            {"title": f"Photo B &amp; friends {i}", "creator": "John Roe", "source_repository": "Wikimedia Commons",
             "license": "CC BY 4.0", "verified": True, "asset_type": "image", "sha256": sha_b,
             "modification_permitted": True, "usage_restrictions": "None", "asset_id": f"wm_b_{i}"},
        ]}
        (base / "metadata").mkdir(parents=True)
        (base / "metadata" / f"attribution_manifest_{job}.json").write_text(json.dumps(manifest), encoding="utf-8")
    conn.execute("INSERT INTO topic_candidate_ledger VALUES (?,?,?,?,?,?,?)",
                 ("cand_1", "Upcoming volcano study", "Volcanologists recorded 42 eruptions along the ridge last year. "
                  "The new survey maps every vent in detail.", "Space and Astronomy", 7.5,
                  (IST_10_30 - timedelta(days=1)).isoformat(), "ELIGIBLE"))
    conn.execute("INSERT INTO topic_candidate_ledger VALUES (?,?,?,?,?,?,?)",
                 ("cand_2", "Celebrity gossip roundup", "A famous actor bought a new house in the hills last week.",
                  "Celebrity", 9.9, (IST_10_30 - timedelta(days=1)).isoformat(), "ELIGIBLE"))
    conn.commit()
    conn.close()
    return {"db": db, "media": media, "tmp": tmp_path}


class World:
    def __init__(self, tmp_path: Path, n_jobs: int = len(JOBS)):
        self.__dict__.update(make_world(tmp_path, n_jobs))
        self.now = [IST_10_30]
        self.notes = []

    def svc(self, **kw) -> CommunityService:
        kw.setdefault("notifier", lambda s, m, sev: (self.notes.append((s, m, sev)), "test")[1])
        return CommunityService(str(self.db), data_dir=self.tmp / "community", media_dir=self.media,
                                clock=lambda: self.now[0], kill_file=self.tmp / "COMMUNITY_DRAFTS_DISABLED",
                                global_kill_file=self.tmp / "AUTONOMY_DISABLED", **kw)

    def production_hash(self) -> str:
        conn = sqlite3.connect(self.db)
        h = hashlib.sha256()
        for t in ("persistent_jobs", "publishing_ledger", "topic_candidate_ledger", "content_ledger",
                  "creative_identity_registry"):
            for row in conn.execute(f"SELECT * FROM {t} ORDER BY 1"):
                h.update(repr(row).encode())
        conn.close()
        for p in sorted(self.media.rglob("*")):
            if p.is_file():
                h.update(p.name.encode() + hashlib.sha256(p.read_bytes()).digest())
        return h.hexdigest()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def enable(world, svc, extra=None):
    """Switch automation on at 05:30 IST (before the 10:00 target), as an operator would the day before. Windows
    that began before automation was enabled are never generated retroactively."""
    saved = world.now[0]
    world.now[0] = saved.replace(hour=0, minute=0)
    try:
        return svc.update_config(OP, {"enabled": True, **(extra or {})})
    finally:
        world.now[0] = saved


def gen(svc) -> dict:
    out = svc.generate_manual(OP)
    return out["draft"]["draft"]


def approve_any(svc, d):
    if d["validation_status"] == S.VALID:
        return svc.approve(d["id"], OP, d["version"])
    return svc.approve(d["id"], OP, d["version"], acknowledge_warnings=True, reason="checked by test")


# == content intelligence ===========================================================================================
def test_sources_read_dossiers_videos_and_candidates(world):
    reader = C.SourceReader(str(world.db), world.media)
    srcs = reader.sources(IST_10_30)
    by = {s.ref: s for s in srcs}
    assert len([s for s in srcs if s.kind == "production"]) == 6
    assert by[JOBS[0][0]].video_public and by[JOBS[0][0]].video_id == "PUBVID00001"
    assert not by[JOBS[1][0]].video_public                       # private video: never referenced
    assert all(not f.endswith("...") for s in srcs for f in s.facts)   # truncated dossier facts dropped
    assert "cand_1" in by and by["cand_1"].kind == "candidate"
    assert sorted(reader.channel_categories()) == sorted({j[2] for j in JOBS})


def test_channel_relevance_and_excluded_categories(world):
    reader = C.SourceReader(str(world.db), world.media)
    srcs = reader.sources(IST_10_30)
    _, _, expl = C.select(srcs, [], list(C.FORMATS), IST_10_30, channel_categories=reader.channel_categories())
    assert expl["rejected"]["off_channel"] == 1                  # the "Celebrity" candidate
    src, _, expl2 = C.select(srcs, [], list(C.FORMATS), IST_10_30, channel_categories=reader.channel_categories(),
                             excluded_categories=["Space and Astronomy"])
    assert src.category != "Space and Astronomy" and expl2["rejected"]["excluded_category"] >= 2


def test_topic_and_format_rotation_over_a_week(world):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    topics, formats = [], []
    for _ in range(7):
        res = svc.tick()
        d = svc.detail(res["draft_id"])["draft"]
        assert d["status"] == S.READY_FOR_REVIEW, d["validation"]
        topics.append(d["topic_key"])
        formats.append(d["format"])
        approve_any(svc, d)
        world.now[0] += timedelta(days=1)
    assert len(set(topics)) == 7                                  # no topic repeated within 60 days
    assert all(a != b for a, b in zip(formats, formats[1:]))      # never the same format two days running
    assert len(set(formats)) >= 5


def test_permitted_formats_only(world):
    svc = world.svc()
    svc.store.ensure_schema()
    svc.update_config(OP, {"formats": ["trivia", "fact"]})
    seen = set()
    for _ in range(3):
        d = gen(svc)
        seen.add(d["format"])
        svc.archive(d["id"], OP, d["version"])
    assert seen <= {"trivia", "fact"} and seen


def test_video_followup_links_only_public_videos(world):
    svc = world.svc()
    svc.store.ensure_schema()
    svc.update_config(OP, {"formats": ["video_followup"]})
    d = gen(svc)
    assert d["format"] == "video_followup" and "https://youtube.com/shorts/PUBVID00001" in d["text"]
    assert d["validation_status"] == S.VALID
    # editing in a link to the PRIVATE video (or any other site) is refused by validation
    for url in ("https://youtube.com/shorts/PRIVID00002", "https://example.com/x"):
        svc.recover_stale()
        cur = svc.detail(d["id"])["draft"]
        out = svc.edit(d["id"], OP, cur["version"], cur["text"].split("\nWatch")[0] + f" {url} What do you think?")
        assert out["draft"]["validation_status"] == S.V_REJECTED
        assert any(c["check"] == "urls" for c in out["draft"]["validation"]["errors"])
    # no other format is possible without a public video
    world2 = World(world.tmp / "w2")
    conn = sqlite3.connect(world2.db)
    conn.execute("UPDATE publishing_ledger SET visibility='private'")
    conn.commit()
    conn.close()
    src = [s for s in C.SourceReader(str(world2.db), world2.media).sources(IST_10_30) if s.kind == "production"]
    assert not any(C.feasible("video_followup", s) for s in src)


def test_template_generation_needs_no_network_or_paid_api(world, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(C, "gemini_generate", lambda p: (_ for _ in ()).throw(AssertionError("Gemini called")))
    d = gen(world.svc())
    assert d["status"] == S.READY_FOR_REVIEW and d["generation"]["text_mode"] == "template"


def test_gemini_is_optional_validated_and_bounded(world):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "Here is a claim with an invented 98765 figure. What do you think?"   # unsupported number
        return "Something worth knowing about this topic: careful observation matters. What would you ask next?"
    svc = world.svc(gemini=fake)
    svc.store.ensure_schema()
    svc.update_config(OP, {"text_mode": "gemini", "formats": ["fact", "curiosity", "trivia", "educational_question"]})
    d1 = gen(svc)
    assert d1["generation"]["text_mode"] == "template" and "98765" not in d1["text"]      # rejected -> template kept
    svc.archive(d1["id"], OP, d1["version"])
    d2 = gen(svc)
    assert d2["generation"]["text_mode"] == "gemini" and "careful observation" in d2["text"]
    svc.archive(d2["id"], OP, d2["version"])
    d3 = gen(svc)
    assert len(calls) == 2 and d3["generation"]["text_mode"] == "template"              # 2 calls per day maximum
    # without the setting Gemini is never called
    calls.clear()
    svc.update_config(OP, {"text_mode": "template", "manual_daily_limit": 10})
    svc.archive(d3["id"], OP, d3["version"])
    gen(svc)
    assert calls == []


def test_clean_fact_and_mcq_parts():
    assert C._clean_fact("The study was cut off in order...") is None
    assert C._clean_fact("short") is None
    assert C._clean_fact("See https://example.com for the full research data set details.") is None
    p = C._mcq_parts("The museum holds 240 rare maps from the old archive.")
    assert p["options"] == ["120", "240", "480"] and p["answer"] == "B" and "___" in p["blanked"]
    y = C._mcq_parts("The bridge opened to traffic in 1932 after six years of work.")
    assert "1932" in y["options"] and all(int(o) <= datetime.now().year for o in y["options"])


def test_multiple_choice_options_are_allowed_numbers(world):
    svc = world.svc()
    svc.store.ensure_schema()
    svc.update_config(OP, {"formats": ["multiple_choice"]})
    d = gen(svc)
    assert d["format"] == "multiple_choice" and "A) " in d["text"] and d["generation"]["answer"] in "ABC"
    assert d["validation_status"] == S.VALID          # distractors are explicit options, not claims


# == validation =====================================================================================================
CORPUS = "The museum holds 240 rare maps. It opened in 1932 in the old harbour district."
GOOD = "From our research desk:\n\nThe museum holds 240 rare maps in the old harbour district.\n\nHad you heard about this before?"


@pytest.mark.parametrize("text,check,severity", [
    ("", "not_empty", "error"),
    ("x" * 520 + "?", "length", "error"),
    (GOOD + " Subscribe for more!!", "spam_wording", "error"),
    (GOOD + " Read more at https://example.com/a", "urls", "error"),
    (GOOD.replace("240", "250"), "numbers_supported", "review"),
    (GOOD + " It holds nine thousand maps.", "spelled_numbers_supported", "review"),
    (GOOD + " This shocking fact is mind-blowing.", "no_sensational_claims", "warning"),
    (GOOD + " What the fuck?", "inappropriate_content", "error"),
    ("From our research desk:\n\nThe museum holds 240 rare maps in the old harbour district.", "engagement_element", "warning"),
    (GOOD + " The museum museum museum is big.", "internal_repetition", "warning"),
    (GOOD + " It matters for religious history.", "sensitive_subject", "review"),
    (GOOD.upper(), "excessive_capitals", "warning"),
])
def test_text_validation_checks(text, check, severity):
    checks = V.validate_text(text, corpus=CORPUS, now=IST_10_30)
    failed = {c["check"]: c for c in checks if not c["ok"]}
    assert check in failed and failed[check]["severity"] == severity, failed


def test_clean_text_is_valid_and_duplicates_are_rejected():
    assert V.outcome(V.validate_text(GOOD, corpus=CORPUS, now=IST_10_30)) == S.VALID
    prev = {"id": "cdraft_0000000000000001", "text": GOOD, "text_fingerprint": C.fingerprint(GOOD),
            "created_at": IST_10_30 - timedelta(days=3), "topic_key": "maps"}
    dup = V.validate_text(GOOD, corpus=CORPUS, now=IST_10_30, recent=[prev])
    assert {c["check"] for c in dup if not c["ok"]} >= {"duplicate_draft", "near_duplicate"} and V.outcome(dup) == S.V_REJECTED
    near = GOOD.replace("old harbour", "historic harbour")                     # one word changed: still a copy
    assert V.outcome(V.validate_text(near, corpus=CORPUS, now=IST_10_30, recent=[{**prev, "text_fingerprint": "x"}])) == S.V_REJECTED
    similar = GOOD.replace("Had you heard about this before?", "Did this surprise you?")   # new question: similar only
    sim = V.validate_text(similar, corpus=CORPUS, now=IST_10_30, recent=[{**prev, "text_fingerprint": "x"}])
    assert {c["check"] for c in sim if not c["ok"]} == {"similar_wording", "opening_varied"}
    assert V.outcome(sim) == S.VALID_WITH_WARNINGS
    old = {**prev, "created_at": IST_10_30 - timedelta(days=400)}
    assert V.outcome(V.validate_text(GOOD, corpus=CORPUS, now=IST_10_30, recent=[old])) == S.VALID
    same_topic = V.validate_text("Totally different words about harbour maps today. Did you visit?", corpus=CORPUS,
                                 now=IST_10_30, recent=[{**prev, "text": "unrelated", "text_fingerprint": "y"}], topic_key="maps")
    assert V.outcome(same_topic) == S.NEEDS_OPERATOR_REVIEW


def test_warnings_never_count_as_valid():
    w = [{"check": "a", "ok": False, "severity": "warning", "detail": ""}]
    r = [{"check": "b", "ok": False, "severity": "review", "detail": ""}]
    e = [{"check": "c", "ok": False, "severity": "error", "detail": ""}]
    assert V.outcome(w) == S.VALID_WITH_WARNINGS and V.outcome(w + r) == S.NEEDS_OPERATOR_REVIEW
    assert V.outcome(w + r + e) == S.V_REJECTED and V.outcome([]) == S.VALID


def _image_record(tmp_path, **over):
    root = tmp_path / "imgs"
    path = root / "cdraft_0000000000000001" / "cimg_0000000000000001.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    size = over.pop("size", (1080, 1080))
    rnd = random.Random(over.pop("seed", 1))
    img = Image.frombytes("L", size, rnd.randbytes(size[0] * size[1])).convert("RGB")
    img.save(path, over.pop("fmt", "JPEG"), quality=90)
    rec = {"id": "cimg_0000000000000001", "draft_id": "cdraft_0000000000000001", "sha256": I.sha256_file(path),
           "method": "licensed_asset", "licence": "CC0", "attribution": "",
           "provenance": {"method": "licensed_asset", "source": "Openverse"}}
    rec.update(over)
    return rec, path, root


@pytest.mark.parametrize("case,check,severity", [
    ("missing", "image_exists", "error"),
    ("aspect", "image_aspect_ratio", "error"),
    ("corrupt", "image_decodes", "error"),
    ("oversized", "image_file_size", "error"),
    ("duplicate", "image_not_duplicate", "error"),
    ("tampered", "image_integrity", "error"),
    ("no_provenance", "image_provenance", "review"),
    ("cc_by_no_credit", "image_attribution", "error"),
    ("bad_licence", "image_licence", "error"),
    ("format", "image_format", "error"),
    ("small", "image_dimensions", "error"),
])
def test_image_validation_checks(tmp_path, case, check, severity, monkeypatch):
    kw = {"size": (1200, 900)} if case == "aspect" else {"size": (400, 400)} if case == "small" else \
        {"fmt": "WEBP"} if case == "format" else {}
    rec, path, root = _image_record(tmp_path, **kw)
    recent = []
    if case == "missing":
        path.unlink()
    elif case == "corrupt":
        path.write_bytes(path.read_bytes()[:500])
        rec["sha256"] = I.sha256_file(path)
    elif case == "oversized":
        monkeypatch.setattr(V, "MAX_IMAGE_BYTES", 1000)
    elif case == "duplicate":
        recent = [{"draft_id": "cdraft_0000000000000009", "sha256": rec["sha256"], "dhash": None}]
    elif case == "tampered":
        rec["sha256"] = "0" * 64
    elif case == "no_provenance":
        rec["provenance"] = {}
    elif case == "cc_by_no_credit":
        rec.update(licence="CC BY", attribution="Image: X by Y (CC BY)")
    elif case == "bad_licence":
        rec["licence"] = "CC BY-NC-SA 4.0"
    checks = V.validate_image(rec, path, images_root=root, text="post text", recent_images=recent)
    failed = {c["check"]: c for c in checks if not c["ok"]}
    assert check in failed and failed[check]["severity"] == severity, failed


def test_image_not_blank_wording_matches_outcome(tmp_path):
    assert V.MIN_IMAGE_STDDEV == 8                                     # threshold unchanged
    rec, path, root = _image_record(tmp_path)                          # random noise: high detail
    chk = next(c for c in V.validate_image(rec, path, images_root=root, text="t") if c["check"] == "image_not_blank")
    assert chk["ok"] is True and chk["severity"] == "warning"
    assert "low" not in chk["detail"].lower() and "blank" not in chk["detail"].lower() and "(minimum 8)" in chk["detail"]
    flat = tmp_path / "flat" / "cdraft_0000000000000002" / "cimg_0000000000000002.jpg"
    flat.parent.mkdir(parents=True)
    Image.new("RGB", (1080, 1080), (40, 40, 40)).save(flat, "JPEG", quality=90)
    rec2 = {**rec, "sha256": I.sha256_file(flat)}
    checks = V.validate_image(rec2, flat, images_root=tmp_path / "flat", text="t")
    bad = next(c for c in checks if c["check"] == "image_not_blank")
    assert bad["ok"] is False and bad["severity"] == "warning"
    assert "almost blank" in bad["detail"] and "below the minimum 8" in bad["detail"]
    assert V.outcome(checks) == S.VALID_WITH_WARNINGS                    # still a warning, nothing stricter
    # the boundary is unchanged: exactly the threshold passes
    for sd, ok in ((8.0, True), (7.99, False)):
        import unittest.mock as um
        with um.patch.object(I, "image_stats", return_value={"format": "JPEG", "width": 1080, "height": 1080,
                                                               "stddev": sd, "dhash": "0" * 16}):
            c = next(x for x in V.validate_image(rec, path, images_root=root, text="t") if x["check"] == "image_not_blank")
        assert c["ok"] is ok, sd


def test_valid_image_and_credit_present(tmp_path):
    rec, path, root = _image_record(tmp_path, licence="CC BY", attribution="Image: X by Y (CC BY)")
    checks = V.validate_image(rec, path, images_root=root, text="Post.\n\nImage: X by Y (CC BY)")
    assert V.outcome(checks) == S.VALID, [c for c in checks if not c["ok"]]
    assert V.validate_image(None, None, images_root=root, text="")[0]["severity"] == "review"


def test_licence_normalisation():
    ok = {"CC0": "CC0", "cc0": "CC0", "pdm": "Public Domain", "CC BY 4.0": "CC BY", "CC-BY-2.0": "CC BY", "by": "CC BY"}
    for raw, want in ok.items():
        assert I.normalise_licence(raw) == want, raw
    for raw in ("CC BY-SA 4.0", "CC BY-NC 4.0", "CC BY-ND 2.0", "All rights reserved", "", None, "by-nc-sa"):
        assert I.normalise_licence(raw) is None, raw


def test_licensed_candidates_need_verified_manifest_match(world):
    job = JOBS[0][0]
    cands = I.licensed_candidates(world.media, job)
    assert {c["licence"] for c in cands} == {"CC0", "CC BY"} and len(cands) == 2            # unlisted file ignored
    cc_by = next(c for c in cands if c["licence"] == "CC BY")
    assert cc_by["attribution"] == "Image: Photo B & friends 0 by John Roe (CC BY, via Wikimedia Commons)"
    mpath = world.media / "productions" / job / "metadata" / f"attribution_manifest_{job}.json"
    m = json.loads(mpath.read_text())
    m["items"][0]["verified"] = False
    m["items"][1]["usage_restrictions"] = "Non-commercial use only"
    mpath.write_text(json.dumps(m))
    assert I.licensed_candidates(world.media, job) == []
    assert I.licensed_candidates(world.media, "../../etc") == []


def test_image_card_is_square_and_never_overwrites(world, tmp_path):
    job = JOBS[0][0]
    src = I.licensed_candidates(world.media, job)[0]["path"]
    out = I.render_asset_card(src, tmp_path / "o", "cimg_0000000000000001")
    with Image.open(out["path"]) as im:
        assert im.size == (1080, 1080) and im.format == "JPEG"
    with pytest.raises(FileExistsError):
        I.render_asset_card(src, tmp_path / "o", "cimg_0000000000000001")
    tc = I.render_title_card("A long title about the history of very large telescopes", "Space", tmp_path / "o", "cimg_0000000000000002")
    assert tc["width"] == tc["height"] == 1080 and not list((tmp_path / "o").glob("*.part"))
    tiny = tmp_path / "tiny.png"
    Image.new("RGB", (200, 200)).save(tiny)
    with pytest.raises(I.ImageError):
        I.render_asset_card(tiny, tmp_path / "o", "cimg_0000000000000003")


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_locked_source_image_falls_back_without_crashing(world):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    handles = []
    for c in I.licensed_candidates(world.media, JOBS[0][0]):
        h = k32.CreateFileW(str(c["path"]), 0x80000000, 0, None, 3, 0x80, None)     # GENERIC_READ, no sharing
        assert h not in (None, wintypes.HANDLE(-1).value)
        handles.append(h)
    try:
        svc = world.svc()
        svc.store.ensure_schema()
        svc.update_config(OP, {"formats": ["video_followup"]})                    # forces the locked job's topic
        d = gen(svc)
        img = svc.detail(d["id"])["image"]
        # locked files cannot even be hashed, so they never match the manifest: the draft falls back to a title card
        assert d["status"] == S.READY_FOR_REVIEW and img["method"] == "title_card"
        assert I.licensed_candidates(world.media, JOBS[0][0]) == []
    finally:
        for h in handles:
            k32.CloseHandle(h)


# == lifecycle =====================================================================================================
def test_lifecycle_transitions_and_audit(world):
    svc = world.svc()
    d = gen(svc)
    assert d["status"] == S.READY_FOR_REVIEW and d["active"] == 1
    a = svc.approve(d["id"], OP, d["version"])["draft"] if d["validation_status"] == S.VALID else \
        approve_any(svc, d)["draft"]
    assert a["status"] == S.APPROVED and a["decided_by"] == OP
    with pytest.raises(S.Conflict):
        svc.approve(a["id"], OP, a["version"])                    # already approved
    with pytest.raises(S.Conflict):
        svc.reject(a["id"], OP, a["version"], "too late")        # APPROVED -> REJECTED is not a transition
    e = svc.edit(a["id"], OP, a["version"], a["text"] + " What do you think?")["draft"]
    assert e["status"] == S.READY_FOR_REVIEW and e["decided_by"] is None                      # approval withdrawn
    r = svc.reject(e["id"], OP, e["version"], "not our style")["draft"]
    assert r["status"] == S.REJECTED and r["active"] == 0
    with pytest.raises(S.Conflict):
        svc.edit(r["id"], OP, r["version"], "new text that is long enough to count?")
    x = svc.archive(r["id"], OP, r["version"])["draft"]
    assert x["status"] == S.ARCHIVED
    with pytest.raises(S.Conflict):
        svc.archive(x["id"], OP, x["version"])
    events = [ev["event"] for ev in svc.detail(d["id"])["events"]][::-1]
    assert events[:2] == ["GENERATION_STARTED", "TEXT_GENERATED"]
    for e_name in ("IMAGE_ATTACHED", "VALIDATED", "DRAFT_APPROVED", "TEXT_EDITED", "DRAFT_REJECTED", "DRAFT_ARCHIVED"):
        assert e_name in events
    edited = next(ev for ev in svc.detail(d["id"])["events"] if ev["event"] == "TEXT_EDITED")
    assert edited["detail"]["before"] == a["text"] and edited["detail"]["approval_withdrawn"] is True
    conn = sqlite3.connect(world.db)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE community_events SET actor='x'")
    conn.close()


def test_transition_table_is_closed():
    for frm, tos in S.TRANSITIONS.items():
        assert all(t in S.STATUSES for t in tos)
    assert S.TRANSITIONS[S.ARCHIVED] == () and S.TRANSITIONS[S.CANCELLED] == ()
    assert S.APPROVED not in S.TRANSITIONS[S.VALIDATING] and S.APPROVED not in S.TRANSITIONS[S.REJECTED]


def test_stale_and_duplicate_requests_are_refused(world):
    svc = world.svc()
    d = gen(svc)
    first = approve_any(svc, d)["draft"]
    with pytest.raises(S.Conflict):
        approve_any(svc, d)                                   # same request again (stale version)
    with pytest.raises(S.Conflict):
        svc.edit(d["id"], OP, d["version"], "stale edit that should be refused, right?")
    assert svc.detail(d["id"])["draft"]["version"] == first["version"]


def test_approval_of_warnings_needs_acknowledgement(world):
    svc = world.svc()
    d = gen(svc)
    e = svc.edit(d["id"], OP, d["version"], d["text"] + "\nIt is a shocking result.")["draft"]
    assert e["validation_status"] == S.VALID_WITH_WARNINGS and e["needs_acknowledgement"]
    with pytest.raises(S.Conflict):
        svc.approve(e["id"], OP, e["version"])
    with pytest.raises(S.Conflict):
        svc.approve(e["id"], OP, e["version"], acknowledge_warnings=True, reason="ok")      # reason too short
    a = svc.approve(e["id"], OP, e["version"], acknowledge_warnings=True, reason="wording reviewed")
    assert a["draft"]["warnings_acknowledged"] == 1
    ev = next(x for x in a["events"] if x["event"] == "DRAFT_APPROVED")
    assert "no_sensational_claims" in ev["detail"]["acknowledged"] and "never publishes" in ev["detail"]["publishing"]


def test_rejected_validation_can_never_be_approved(world):
    svc = world.svc()
    d = gen(svc)
    e = svc.edit(d["id"], OP, d["version"], "Click here and subscribe!! https://spam.example")["draft"]
    assert e["status"] == S.VALIDATION_FAILED and e["validation_status"] == S.V_REJECTED
    with pytest.raises(S.Conflict):
        svc.approve(e["id"], OP, e["version"], acknowledge_warnings=True, reason="please let me")


def test_regenerate_text_and_image(world):
    svc = world.svc()
    d = gen(svc)
    t = svc.regenerate_text(d["id"], OP, d["version"])["draft"]
    assert t["text"] != d["text"] and t["status"] == S.READY_FOR_REVIEW and t["source_ref"] == d["source_ref"]
    img0 = svc.detail(t["id"])["image"]
    i = svc.regenerate_image(t["id"], OP, t["version"])
    assert i["draft"]["status"] == S.READY_FOR_REVIEW and i["image"]["id"] != img0["id"]
    assert i["image"]["source_ref"] != img0["source_ref"]            # a different source image
    opts = svc.image_options(t["id"])["options"]
    assert opts[-1]["choice"] == "title_card"
    c = svc.regenerate_image(t["id"], OP, i["draft"]["version"], choice="title_card")
    assert c["image"]["method"] == "title_card" and c["draft"]["status"] == S.READY_FOR_REVIEW
    assert "Image:" not in c["draft"]["text"]                         # an old CC BY credit is removed with its image
    with pytest.raises(svc_mod.BadRequest):
        svc.regenerate_image(t["id"], OP, c["draft"]["version"], choice="../../secrets")
    with pytest.raises(svc_mod.BadRequest):
        svc.regenerate_image(t["id"], OP, c["draft"]["version"], choice="asset:0000000000000000")
    assert len(svc.store.images_for_draft(t["id"])) == 3


def test_cc_by_image_adds_credit_line(world):
    svc = world.svc()
    d = gen(svc)
    opts = [o for o in svc.image_options(d["id"])["options"] if o["licence"] == "CC BY"]
    if not opts:
        pytest.skip("source topic has no CC BY asset")
    out = svc.regenerate_image(d["id"], OP, d["version"], choice=opts[0]["choice"])
    assert "Image: Photo B & friends" in out["draft"]["text"] and out["draft"]["validation_status"] == S.VALID


def test_approve_refuses_tampered_image(world):
    svc = world.svc()
    d = gen(svc)
    img = svc.detail(d["id"])["image"]
    p = svc._image_path(img)
    p.write_bytes(p.read_bytes() + b"tamper")
    with pytest.raises(S.Conflict):
        approve_any(svc, d)


def test_archive_frees_the_date(world):
    svc = world.svc()
    d = gen(svc)
    with pytest.raises(S.Conflict):
        gen(svc)                                                  # one active draft per date
    svc.archive(d["id"], OP, d["version"])
    d2 = gen(svc)
    assert d2["post_date"] == d["post_date"] and d2["id"] != d["id"]


# == scheduling =====================================================================================================
def test_ist_schedule_and_next_run(world):
    svc = world.svc()
    svc.store.ensure_schema()
    world.now[0] = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)                       # 09:30 IST
    enable(world, svc)
    st = svc.schedule_status()
    assert st["next_run_utc"] == "2026-11-20T04:30:00.000000Z" and st["next_run_local"].startswith("2026-11-20T10:00:00+05:30")
    assert svc.tick()["status"] == "not_due"
    world.now[0] = datetime(2026, 11, 20, 4, 32, tzinfo=timezone.utc)                      # 10:02 IST
    assert svc.tick()["status"] == "generated"
    st = svc.schedule_status()
    assert st["next_run_local"].startswith("2026-11-21T10:00:00+05:30")
    # 23:45 UTC on the 20th is already the 21st in India: the IST date is used, not the UTC date
    world.now[0] = datetime(2026, 11, 20, 23, 45, tzinfo=timezone.utc)
    assert svc._local_date(world.now[0], svc.config()) == "2026-11-21"
    svc.update_config(OP, {"timezone": "UTC", "generation_time": "23:30"})
    assert svc.tick()["status"] == "done"                  # the 20th (UTC) already had its draft: one per date
    world.now[0] = datetime(2026, 11, 21, 23, 45, tzinfo=timezone.utc)
    assert svc.tick()["status"] == "generated"             # the 21st in UTC: a new date key
    with pytest.raises(svc_mod.BadRequest):
        svc.update_config(OP, {"timezone": "Mars/Base"})
    with pytest.raises(svc_mod.BadRequest):
        svc.update_config(OP, {"generation_time": "25:00"})


def test_daily_idempotency_and_restart(world):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    assert svc.tick()["status"] == "generated"
    for _ in range(3):
        assert svc.tick()["status"] == "done"
    restarted = world.svc()                                         # a new process: state comes from the database
    assert restarted.tick()["status"] == "done"
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT COUNT(*) FROM community_drafts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM community_runs WHERE trigger='scheduled'").fetchone()[0] == 1
    conn.close()


def test_manual_and_scheduled_overlap(world):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    gen(svc)                                                        # manual first
    assert svc.tick()["status"] == "skipped"                        # scheduled run sees the draft and skips
    assert svc.tick()["status"] == "done"
    world.now[0] += timedelta(days=1)
    assert svc.tick()["status"] == "generated"                      # scheduled first
    with pytest.raises(S.Conflict):
        gen(svc)


def test_concurrent_generation_makes_one_draft(world):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc, {"manual_daily_limit": 10})
    results, barrier = [], threading.Barrier(6)

    def worker(kind):
        s = world.svc()
        barrier.wait()
        try:
            results.append(("tick", s.tick()["status"]) if kind == "tick" else ("manual", gen(s)["id"]))
        except S.StoreError as exc:
            results.append((kind, type(exc).__name__))
    ts = [threading.Thread(target=worker, args=("tick" if i % 2 else "manual",)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    conn = sqlite3.connect(world.db)
    active = conn.execute("SELECT COUNT(*) FROM community_drafts WHERE active=1").fetchone()[0]
    total_sched = conn.execute("SELECT COUNT(*) FROM community_runs WHERE trigger='scheduled'").fetchone()[0]
    conn.close()
    assert active == 1 and total_sched == 1, results
    assert len(results) == 6


def test_pause_resume_disable(world):
    svc = world.svc()
    svc.store.ensure_schema()
    assert svc.tick()["status"] == "blocked"                       # disabled by default
    enable(world, svc, {"paused": True})
    assert svc.tick() == {"status": "blocked", "reason": "automatic generation is paused", "recovered": {"runs": 0, "drafts": 0}}
    assert svc.schedule_status()["next_run_local"] is None
    svc.update_config(OP, {"paused": False})
    assert svc.tick()["status"] == "generated"
    d = svc.list_drafts(status="active")[0]
    assert d["status"] == S.READY_FOR_REVIEW
    gen_ok = svc.update_config(OP, {"enabled": False})
    assert gen_ok["blocked_reason"] == "automatic generation is disabled"


def test_failure_and_bounded_retry(world, monkeypatch):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    real = svc.reader.sources
    monkeypatch.setattr(svc.reader, "sources", lambda now: [])           # nothing to post about
    r1 = svc.tick()
    assert r1["status"] == "generated" and r1["error"] and r1["draft"]["draft"]["status"] == S.GENERATION_FAILED
    assert svc.tick()["status"] == "done"                               # backoff: no immediate retry
    world.now[0] += timedelta(minutes=11)
    monkeypatch.setattr(svc.reader, "sources", real)
    r2 = svc.tick()
    assert r2["status"] == "generated" and r2["error"] is None            # second attempt succeeds
    run = svc.store.get_run(r2["run_id"])
    assert run["attempts"] == 2 and run["status"] == S.RUN_SUCCEEDED
    assert any("failed" in s.lower() for s, _, _ in world.notes) and any("ready" in s.lower() for s, _, _ in world.notes)


def test_retries_stop_after_limit(world, monkeypatch):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    monkeypatch.setattr(svc.reader, "sources", lambda now: [])
    svc.tick()
    world.now[0] += timedelta(minutes=11)
    svc.tick()
    world.now[0] += timedelta(minutes=11)
    assert svc.tick()["status"] == "done"
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT attempts, status FROM community_runs").fetchone() == (2, "FAILED")
    conn.close()


def test_missed_window_is_recorded_and_notified(world):
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc, {"catch_up_hours": 2})
    world.now[0] = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)                        # 13:30 IST
    assert svc.tick()["status"] == "missed"
    assert svc.tick()["status"] == "done"
    assert any("missed" in s.lower() for s, _, _ in world.notes)


def test_interrupted_run_is_reconciled_not_resumed(world):
    svc = world.svc()
    svc.store.ensure_schema()
    now = world.now[0]
    with svc.store.tx() as conn:
        run = svc.store.insert_run(conn, idem_key="community:scheduled:2026-11-20", post_date="2026-11-20",
                                   trigger="scheduled", requested_by="system:test", now=now, lease_seconds=60)
        d = svc.store.insert_draft(conn, run_id=run["id"], post_date="2026-11-20", now=now)
    world.now[0] += timedelta(minutes=5)
    assert svc.recover_stale() == {"runs": 1, "drafts": 1}
    assert svc.store.get_run(run["id"])["status"] == S.RUN_ABANDONED
    assert svc.store.get_draft(d["id"])["status"] == S.CANCELLED
    # a stuck regeneration (no run) is moved to its failed state after 10 minutes
    d2 = gen(svc)
    with svc.store.tx() as conn:
        svc.store.transition(conn, d2["id"], S.GENERATING, world.now[0])
    world.now[0] += timedelta(minutes=11)
    svc.recover_stale()
    assert svc.store.get_draft(d2["id"])["status"] == S.GENERATION_FAILED


def test_generation_timeout(world, monkeypatch):
    svc = world.svc()
    monkeypatch.setattr(svc_mod, "GENERATION_TIMEOUT", 0.05)
    slow = svc.reader.sources

    def slow_sources(now):
        time.sleep(0.2)
        return slow(now)
    monkeypatch.setattr(svc.reader, "sources", slow_sources)
    out = svc.generate_manual(OP)
    assert out["status"] == S.GENERATION_FAILED and "exceeded" in out["error"]


def test_daily_limits(world):
    svc = world.svc()
    svc.store.ensure_schema()
    svc.update_config(OP, {"manual_daily_limit": 2})
    for _ in range(2):
        d = gen(svc)
        svc.archive(d["id"], OP, d["version"])
    with pytest.raises(svc_mod.LimitExceeded):
        gen(svc)
    world.now[0] += timedelta(days=1)
    d = gen(svc)
    for _ in range(svc_mod.REGENERATION_DAILY_LIMIT):
        d = svc.regenerate_text(d["id"], OP, d["version"])["draft"]
    with pytest.raises(svc_mod.LimitExceeded):
        svc.regenerate_text(d["id"], OP, d["version"])


def test_tick_and_reads_before_configuration_create_nothing(world):
    svc = world.svc()
    assert svc.tick() == {"status": "not_configured"}
    ov = svc.overview()
    assert ov["schedule"]["configured"] is False and ov["active_drafts"] == []
    assert svc.list_drafts() == [] and svc.history()["runs"] == []
    conn = sqlite3.connect(world.db)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'community_%'").fetchone()[0] == 0
    conn.close()


# == kill switches, security and isolation ==========================================================================
@pytest.mark.parametrize("switch", ["dashboard", "community_file", "global_file"])
def test_kill_switches_block_all_generation(world, switch):
    svc = world.svc()
    d = gen(svc)
    enable(world, svc)
    world.now[0] += timedelta(days=1)
    if switch == "dashboard":
        svc.update_config(OP, {"kill_switch": True})
    else:
        (world.tmp / ("COMMUNITY_DRAFTS_DISABLED" if switch == "community_file" else "AUTONOMY_DISABLED")).write_text("x")
    assert svc.tick()["status"] == "blocked"
    with pytest.raises(svc_mod.Blocked):
        gen(svc)
    with pytest.raises(svc_mod.Blocked):
        svc.regenerate_text(d["id"], OP, d["version"])
    with pytest.raises(svc_mod.Blocked):
        svc.regenerate_image(d["id"], OP, d["version"])
    assert svc.schedule_status()["kill_switches"]
    approve_any(svc, d)                                             # decisions on existing drafts stay possible


def test_operator_identity_is_validated(world):
    svc = world.svc()
    for bad in ("system:community-scheduler", "autonomy", "Self Healing", "x" * 80, "evil\nname"):
        with pytest.raises(svc_mod.Forbidden):
            svc.generate_manual(bad)
    d = svc.generate_manual("   ")["draft"]["draft"]          # blank or absent: the neutral identity, never a person
    assert {e["actor"] for e in svc.detail(d["id"])["events"]} == {"local-dashboard"}


def _client(world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    svc = world.svc()
    monkeypatch.setattr(svc_mod, "_instance", svc)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    return main, svc, local, h


def test_api_guards(world, monkeypatch):
    from fastapi.testclient import TestClient
    main, svc, local, h = _client(world, monkeypatch)
    remote = TestClient(main.app)
    assert remote.get("/api/community").status_code == 403                                   # not loopback
    assert remote.post("/api/community/generate", json={}, headers=h).status_code == 403
    assert local.post("/api/community/generate", json={}).status_code == 403   # no CSRF token
    assert local.post("/api/community/generate", content=json.dumps({}),
                      headers={**h, "Content-Type": "text/plain"}).status_code == 415
    assert local.post("/api/community/generate", json={"publish": True}, headers=h).status_code == 422
    assert local.post("/api/community/generate", json={},
                      headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert local.post("/api/community/generate", json={},
                      headers={**h, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert local.post("/api/community/generate", json={"operator": "system"}, headers=h).status_code == 422   # no identity field
    assert local.post("/api/community/schedule", json={"command": "rm"}, headers=h).status_code == 422
    assert local.post("/api/community/schedule", json={"generation_time": "9am"}, headers=h).status_code == 400
    for q in ("?limit=0", "?limit=999", "?status=DROP", "?format=x", "?date_from=yesterday", "?q=" + "x" * 101):
        assert local.get("/api/community/drafts" + q).status_code == 400, q
    assert local.get("/api/community/history?limit=9999").status_code == 400


def test_api_path_traversal_and_file_access(world, monkeypatch):
    main, svc, local, h = _client(world, monkeypatch)
    r = local.post("/api/community/generate", json={}, headers=h)
    assert r.status_code == 200
    d = r.json()["draft"]["draft"]
    for bad in ("..%2F..%2Fstudio.db", "cdraft_xyz", "cdraft_0000000000000000", "C:%5CWindows"):
        assert local.get(f"/api/community/drafts/{bad}").status_code == 404, bad
    for bad in ("..%2F..%2Fbackend%2Fstudio.db", "cimg_0000000000000000", "cimg_..", "token.json"):
        assert local.get(f"/api/community/images/{bad}").status_code == 404, bad
    img = local.get(d["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"
    # a tampered image record pointing outside the folder is never served
    conn = sqlite3.connect(world.db)
    conn.execute("UPDATE community_images SET filename='../../studio.db', draft_id='../..' WHERE id=?", (d["image_id"],))
    conn.commit()
    conn.close()
    assert local.get(d["image_url"]).status_code == 404
    assert "corpus" not in json.dumps(r.json()["draft"]["draft"]["generation"])


def test_api_end_to_end_flow(world, monkeypatch):
    main, svc, local, h = _client(world, monkeypatch)
    assert local.get("/api/community").json()["schedule"]["configured"] is False
    s = local.post("/api/community/schedule", json={"enabled": True, "generation_time": "10:00",
                                                     "timezone": "Asia/Kolkata", "formats": ["fact", "trivia"]}, headers=h)
    assert s.status_code == 200 and s.json()["config"]["enabled"] is True
    d = local.post("/api/community/generate", json={}, headers=h).json()["draft"]["draft"]
    assert local.post("/api/community/generate", json={}, headers=h).status_code == 409
    v = local.get(f"/api/community/drafts/{d['id']}/validation").json()
    assert v["validation_status"] == d["validation_status"]
    e = local.post(f"/api/community/drafts/{d['id']}/edit", json={"version": d["version"],
                                                                     "text": d["text"] + " Tell us below."}, headers=h)
    assert e.status_code == 200
    d2 = e.json()["draft"]
    assert local.post(f"/api/community/drafts/{d['id']}/approve", json={"version": d["version"]},
                      headers=h).status_code == 409                                         # stale version
    body = {"version": d2["version"]}
    if d2["validation_status"] != "VALID":
        body.update(acknowledge_warnings=True, reason="reviewed by operator")
    a = local.post(f"/api/community/drafts/{d['id']}/approve", json=body, headers=h)
    assert a.status_code == 200 and a.json()["draft"]["status"] == "APPROVED"
    opts = local.get(f"/api/community/drafts/{d['id']}/image-options").json()["options"]
    assert opts and opts[-1]["choice"] == "title_card"
    a2 = a.json()["draft"]
    ri = local.post(f"/api/community/drafts/{d['id']}/regenerate-image",
                    json={"version": a2["version"], "choice": "title_card"}, headers=h)
    assert ri.status_code == 200 and ri.json()["draft"]["status"] == "READY_FOR_REVIEW"     # approval withdrawn
    rv = ri.json()["draft"]
    rj = local.post(f"/api/community/drafts/{d['id']}/reject", json={"version": rv["version"], "reason": "x"},
                    headers=h)
    assert rj.status_code == 422                                                           # reason too short
    rj = local.post(f"/api/community/drafts/{d['id']}/reject",
                    json={"version": rv["version"], "reason": "off topic"}, headers=h)
    assert rj.status_code == 200
    ar = local.post(f"/api/community/drafts/{d['id']}/archive",
                    json={"version": rj.json()["draft"]["version"]}, headers=h)
    assert ar.status_code == 200
    hist = local.get("/api/community/drafts?status=ARCHIVED&q=" + d["topic_title"][:10]).json()["drafts"]
    assert [x["id"] for x in hist] == [d["id"]]
    assert local.get("/api/community/history").json()["runs"]
    k = local.post("/api/community/schedule", json={"kill_switch": True}, headers=h)
    assert k.status_code == 200
    assert local.post("/api/community/generate", json={}, headers=h).status_code == 423


def test_secrets_are_redacted_from_errors(world, monkeypatch):
    svc = world.svc()
    monkeypatch.setattr(svc.reader, "sources", lambda now: (_ for _ in ()).throw(RuntimeError("failed key=AIzaSyA1234567890abcdefghijklmnop")))
    out = svc.generate_manual(OP)
    assert out["status"] == S.GENERATION_FAILED and "AIzaSy" not in json.dumps(out, default=str)
    conn = sqlite3.connect(world.db)
    dump = " ".join(str(r) for t in ("community_runs", "community_drafts", "community_events", "community_notifications")
                    for r in conn.execute(f"SELECT * FROM {t}"))
    conn.close()
    assert "AIzaSy" not in dump


def test_publishing_is_confined_to_the_gated_publishing_modules():
    """Stage 3.32F boundary: network and OAuth code live ONLY in youtube_posts.py (documented endpoints only), which
    only publishing.py imports. Draft generation, validation, images, schedule and store never reach YouTube."""
    import ast
    pkg = Path(svc_mod.__file__).parent
    network_or_oauth = ("youtube_service", "youtube_publishing_engine", "youtube_connection_service", "googleapiclient",
                        "google.oauth2", "urlopen", "import requests", "token.json", "client_secrets", "secure_storage")
    always_forbidden = ("upload_short", "videos().insert", "publishing_ledger SET", "INSERT INTO publishing_ledger",
                        "UPDATE persistent_jobs", "INSERT INTO persistent_jobs", "run_cycle_generation", "reload_scheduler",
                        "subprocess", "os.system", "eval(", "exec(", "shell=True", "topic_history.json",
                        "creative_policy_config")
    for p in pkg.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for bad in always_forbidden:
            assert bad not in src, f"{bad} in {p.name}"
        for line in src.splitlines():
            if "DELETE FROM" in line:
                assert "community_" in line, f"DELETE outside community tables in {p.name}"
        if p.name != "youtube_posts.py":
            for bad in network_or_oauth:
                assert bad not in src, f"{bad} in {p.name}"
        imports = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                imports |= {a.name for a in node.names}
        if p.name not in ("publishing.py", "youtube_posts.py"):
            assert "backend.app.community.youtube_posts" not in imports, p.name
        if p.name not in ("service.py", "publishing.py"):
            assert "backend.app.community.publishing" not in imports, p.name
    posts_src = (pkg / "youtube_posts.py").read_text(encoding="utf-8")
    import re as _re
    urls = set(_re.findall(r'f?"\{(API|UPLOAD)\}(/[a-z/]+)', posts_src))
    assert {u[1] for u in urls} <= {"/channels", "/posts", "/posts/image"}, urls
    svc_src = (pkg / "service.py").read_text(encoding="utf-8")
    assert "publish(" not in svc_src.replace("_assert_not_published(", "")      # generation never publishes
    from backend.app.autonomy import registry
    assert registry.REGISTRY["youtube.community.publish"].prohibited is True     # autonomy can never publish
    main_src = (Path(svc_mod.__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    routes = [ln for ln in main_src.splitlines() if ln.startswith("@app.") and "/api/community" in ln]
    assert len(routes) == 29             # 3.32F completion: + authorize-publication, + publications/{id}/cancel
    assert [r for r in routes if "publish" in r and "/publishing" not in r] == ['@app.post("/api/community/drafts/{draft_id}/publish")']
    assert [r for r in routes if "authorize" in r] == ['@app.post("/api/community/drafts/{draft_id}/authorize-publication")']
    assert not any("upload" in r or "delete" in r.lower() for r in routes)


def test_scheduler_job_is_isolated_from_production(monkeypatch):
    from backend.app.scheduler import scheduler as sched_mod
    sched_mod.configure_scheduler_jobs()
    jobs = {j.id: j for j in sched_mod.scheduler.get_jobs()}
    tick = jobs["community_draft_tick"]
    assert tick.func is sched_mod.run_community_draft_tick and tick.max_instances == 1 and tick.coalesce
    assert "2-59/5" in str(tick.trigger)
    profile = sched_mod.get_active_profile()
    assert len([j for j in jobs if j.startswith("gen_")]) == len(profile.cycles)
    assert not any(j.startswith("pub_") for j in jobs)
    called = []
    monkeypatch.setattr(svc_mod, "scheduler_tick", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert sched_mod.run_community_draft_tick()["status"] == "error"                       # never raises
    monkeypatch.setattr(svc_mod, "scheduler_tick", lambda: called.append(1) or {"status": "not_configured"})
    assert sched_mod.run_community_draft_tick() == {"status": "not_configured"} and called


def test_production_data_untouched_by_full_flow(world, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    before = world.production_hash()
    svc = world.svc()
    svc.store.ensure_schema()
    enable(world, svc)
    for _ in range(3):
        r = svc.tick()
        d = svc.detail(r["draft_id"])["draft"]
        d = svc.regenerate_text(d["id"], OP, d["version"])["draft"]
        d = svc.regenerate_image(d["id"], OP, d["version"])["draft"]
        approve_any(svc, d)
        world.now[0] += timedelta(days=1)
    assert world.production_hash() == before
    conn = sqlite3.connect(world.db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert tables - {"persistent_jobs", "publishing_ledger", "topic_candidate_ledger", "content_ledger",
                     "creative_identity_registry", "sqlite_sequence"} == set(S.TABLES)
    files = [p for p in (world.tmp / "community").rglob("*") if p.is_file()]
    assert files and all(p.suffix == ".jpg" and p.parent.parent.name == "images" for p in files)


def test_notifications_default_to_dashboard_when_inbox_is_off(world, monkeypatch):
    from backend.app.autonomy import control_plane as cpm

    class FakeCP:
        def _safe_config(self):
            return {"global_level": "OFF", "category_levels": {}}, None

        @staticmethod
        def _effective(cfg, cat):
            from backend.app.autonomy.registry import Level
            return Level.OFF

        def submit(self, *a, **kw):
            raise AssertionError("must not submit while notifications are OFF")
    monkeypatch.setattr(cpm, "get_control_plane", lambda: FakeCP())
    assert svc_mod._default_notifier("s", "m", "info") == "dashboard"
    svc = CommunityService(str(world.db), data_dir=world.tmp / "community", media_dir=world.media,
                           clock=lambda: world.now[0], kill_file=world.tmp / "k1", global_kill_file=world.tmp / "k2")
    gen(svc)
    notes = svc.store.notifications()
    assert notes and notes[0]["delivery"] == "dashboard" and notes[0]["kind"] == "draft_ready"
    for _ in range(svc_mod.NOTIFICATION_DAILY_LIMIT + 3):
        svc._notify("x", "s", "m", "info", None)
    assert len(svc.store.notifications(200)) == svc_mod.NOTIFICATION_DAILY_LIMIT


def test_retention_prunes_old_drafts_and_files(world):
    svc = world.svc()
    d = gen(svc)
    svc.archive(d["id"], OP, d["version"])
    keep = gen(svc)
    img_dir = svc.images_dir / d["id"]
    assert img_dir.is_dir()
    (svc.images_dir / "cdraft_00000000000000ff").mkdir()                       # orphan folder
    (svc.images_dir / "cdraft_00000000000000ff" / "cimg_00000000000000ff.jpg").write_bytes(b"x")
    (svc.images_dir / "notes.txt").write_text("not ours to delete")
    world.now[0] += timedelta(days=S.RETENTION_DAYS_DRAFTS + 1)
    res = svc.prune()
    assert res["drafts"] == 1 and res["image_files_removed"] >= 2
    assert not img_dir.exists() and svc.store.get_draft(keep["id"]) is not None
    assert (svc.images_dir / keep["id"]).is_dir() and (svc.images_dir / "notes.txt").exists()


def test_dashboard_community_tab():
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('data-tab="community"', 'id="tab-community"', 'id="cmDrafts"', 'id="cmHistory"', 'id="cmRuns"',
                   'id="cmNotes"', "function refreshCommunity", "function cmApprove", "function cmSaveSchedule",
                   "/api/community/generate", "/regenerate-image", "/regenerate-text", "/approve", "/reject", "/archive",
                   "/edit", 'if (tabId === "community")', "Nothing is ever published"):
        assert needle in html, needle
    # Stage 3.32F: the API Publish button exists only behind the server's readiness flag (all gates passed)
    assert html.count(">Publish now via API (public, immediate)</button>") == 1
    assert "(st.api_publishing_ready ? `<button class=\"btn sm primary\"" in html
    assert 'id="cmOperator"' not in html                       # no operator name is asked for any more
    for needle in ('id="cmPubCard"', "function cmCapCheck", "function cmHandoff", "/handoff", "/manual-posted",
                   "operator-reported", "Schedule"):
        assert needle in html, needle
    for tab in ('data-tab="ops"', 'data-tab="recs"', 'data-tab="autonomy"', 'data-tab="schedule"', 'data-tab="heal"'):
        assert tab in html
    script = html[html.rfind("<script>") + 8: html.rfind("</script>")]
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0 and "ok" in res.stdout, res.stderr[:500]


def test_performance_budget(world):
    """Warm generation stays well inside the 120 s deadline on the target laptop (measured, not mocked)."""
    import tracemalloc
    svc = world.svc()
    d = gen(svc)                                   # warm-up (imports)
    svc.archive(d["id"], OP, d["version"])
    tracemalloc.start()
    t = time.perf_counter()
    out = svc.generate_manual(OP)
    wall = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    m = out["metrics"]
    print(f"\n[3.32E perf] wall={wall:.3f}s metrics={m} peak_python_alloc={peak / 1e6:.1f}MB")
    assert out["status"] == S.READY_FOR_REVIEW and wall < 10 and peak < 200e6
    t = time.perf_counter()
    for _ in range(20):
        svc.overview()
    assert (time.perf_counter() - t) / 20 < 0.5
