"""Scene-level photo selection: relevance from captions (not filenames), licence gate, integrity, cache, fallback."""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset
from backend.app.rendering.pop_info import scene_photos as sp


def _jpeg(w=1200, h=900, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    arr = (rng.random((h // 8, w // 8, 3)) * 255).astype("uint8")
    img = Image.fromarray(arr).resize((w, h), Image.NEAREST)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _asset(i, title, desc="", tags=(), status=LicenseStatus.APPROVED, lic=LicenseType.CC_BY, provider="Wikimedia Commons"):
    return MediaAsset(asset_id=f"a{i}", title=title, creator="Someone", source_repository=provider,
                      source_url=f"https://commons.example/{i}", download_url=f"https://upload.example/{i}.jpg",
                      license_type=lic, attribution_text="credit", description=desc, tags=list(tags),
                      verification_status=status)


NEED = sp.PhotoNeed(3, "scene_04", "The prize is backed by a foundation co-founded by Yuri Milner, an entrepreneur.",
                    ["Yuri Milner", "Breakthrough Prize"], ["Yuri Milner"], "names")


def test_entities_skip_months_and_sentence_starts():
    ents = sp.entities("Launched in July 2012, it honors physics. Funding came from Yuri Milner and the Jet Propulsion Laboratory.")
    assert "Yuri Milner" in ents and "Jet Propulsion Laboratory" in ents
    assert "July" not in ents and "Launched" not in ents and "Funding" not in ents


def test_anchor_is_the_name_the_script_uses():
    a = sp.topic_anchor("Breakthrough Prize in Fundamental Physics",
                        "The Breakthrough Prize pays more. The Breakthrough Prize is big.",
                        ["Breakthrough Prize in Fundamental Physics", "The Breakthrough Prize", "Fundamental Physics"])
    assert a == "Breakthrough Prize"


def test_caption_evidence_required_not_filename():
    filename_only = _asset(1, "Yuri Milner", desc="", tags=())
    r = sp.text_relevance(filename_only, "Yuri Milner", NEED, "Breakthrough Prize")
    assert not r["evidence_beyond_filename"]
    good = _asset(2, "Yuri Milner 1", desc="Yuri Milner, entrepreneur, at a conference")
    assert sp.text_relevance(good, "Yuri Milner", NEED, "Breakthrough Prize")["evidence_beyond_filename"]


def test_photo_of_a_different_named_person_is_rejected():
    need = sp.PhotoNeed(2, "s", "Slava Turyshev from the Jet Propulsion Laboratory explains it.",
                        ["Jet Propulsion Laboratory"], ["Slava Turyshev", "Jet Propulsion Laboratory"], "names")
    other = _asset(3, "Beth Jens at the Jet Propulsion Laboratory", desc="Beth Jens at the Jet Propulsion Laboratory")
    r = sp.text_relevance(other, "Jet Propulsion Laboratory", need, "space junk")
    assert r["unrelated_people"] == ["Beth Jens"] and not r["evidence_beyond_filename"]


def test_topic_anchor_alone_does_not_make_a_photo_relevant_to_a_scene():
    need = sp.PhotoNeed(5, "s", "Finalists still got three hundred thousand dollars and automatic nominations.",
                        ["Breakthrough Prize"], ["Physics Frontiers Prize"], "names")
    lab = _asset(4, "ACES clock", desc="Atomic clock for fundamental physics research, Breakthrough Prize physics")
    assert not sp.text_relevance(lab, "Breakthrough Prize", need, "Breakthrough Prize")["scene_specific"]


class _Cache(sp.AssetCache):
    pass


def test_download_integrity_and_cache(tmp_path, monkeypatch):
    calls = []
    payload = {"https://upload.example/5.jpg": _jpeg(), "https://upload.example/6.jpg": _jpeg(300, 200),
               "https://upload.example/7.jpg": b"<html>not an image</html>"}

    def fake_fetch(url, **kw):
        calls.append(url); return payload[url]
    monkeypatch.setattr("backend.app.media.net.fetch", fake_fetch)
    cache = sp.AssetCache(tmp_path)
    rec, why = sp.download_checked(_asset(5, "x"), cache)
    assert rec and rec["size"] == [1200, 900] and rec["original_format"] == "image/jpeg" and not rec["cache_hit"]
    assert 0.3 <= rec["metrics"]["focus"]["x"] <= 0.7
    rec2, why2 = sp.download_checked(_asset(5, "x"), cache)
    assert rec2["cache_hit"] and calls.count("https://upload.example/5.jpg") == 1
    Path(rec["path"]).write_bytes(b"tampered")             # a changed file is never served from cache
    rec3, _ = sp.download_checked(_asset(5, "x"), cache)
    assert not rec3["cache_hit"] and calls.count("https://upload.example/5.jpg") == 2
    assert sp.download_checked(_asset(6, "x"), cache)[0] is None          # too small
    none, why = sp.download_checked(_asset(7, "x"), cache)
    assert none is None and "magic" in why


class _Provider:
    def __init__(self, name, results=None, fail=False):
        self.provider_name, self.results, self.fail = name, results or [], fail

    def _sync_fetch(self, query, limit):
        if self.fail:
            raise RuntimeError("provider down")
        return list(self.results)


def _plan():
    narr = ["A title.", "The prize is backed by a foundation co-founded by Yuri Milner, an entrepreneur.",
            "It pays more.", "Something else entirely here.", "The end."]
    return {"scenes": [{"scene_id": f"scene_{i + 1:02d}", "narration": t, "template": "callout"} for i, t in enumerate(narr)]}


def test_select_picks_licensed_relevant_photo_despite_a_failed_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.media.net.fetch", lambda url, **kw: _jpeg(seed=hash(url) % 100))
    good = _asset(10, "Yuri Milner 1", desc="Portrait of Yuri Milner, entrepreneur")
    nc = _asset(11, "Yuri Milner 2", desc="Yuri Milner portrait", status=LicenseStatus.REJECTED, lic=LicenseType.CC_BY_NC)
    chosen, report = sp.select_scene_photos(
        plan=_plan(), topic_title="Breakthrough Prize", key_entities=["Breakthrough Prize"], job_id="t",
        cache_root=tmp_path, providers=[_Provider("Broken", fail=True), _Provider("Wikimedia Commons", [nc, good])])
    assert list(chosen) == [1]
    ph = chosen[1]
    assert ph["asset_id"] == "a10" and ph["license"] == "CC BY" and ph["attribution_required"]
    for k in ("provider", "source_url", "creator", "license", "downloaded_at", "original_sha256", "format",
              "resolution", "search_query", "topic", "scene_id"):
        assert ph.get(k), k
    scene = report["scenes"][0]
    assert scene["rejected"].get("licence not approved") == 1
    assert any(s["provider"] == "Broken" and "error" in s["note"] for s in report["searches"])


def test_no_suitable_photo_keeps_vector_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.media.net.fetch", lambda url, **kw: _jpeg())
    unrelated = _asset(12, "Sunset", desc="A sunset over the sea")
    chosen, report = sp.select_scene_photos(
        plan=_plan(), topic_title="Breakthrough Prize", key_entities=[], job_id="t", cache_root=tmp_path,
        providers=[_Provider("Wikimedia Commons", [unrelated])])
    assert chosen == {}
    assert report["scenes"] and all(s["chosen"] is None and "vector" in s["fallback"] for s in report["scenes"])
