"""
Stage 3.29 thumbnail pipeline: 1280x720 artifact, validation, upload only after a video id, upload on the
existing id (never the video), remote verification against what was sent, persisted state, readiness.
Temporary files and a fake YouTube service only.
"""
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageDraw

from backend.app.services import thumbnail_pipeline as tp


def _img(path, size=(1080, 1920), color=(200, 60, 40)):
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    for k in range(0, size[0], 60):
        d.rectangle((k, 0, k + 30, size[1]), fill=(20, 40, 160))
    im.save(path)
    return str(path)


def test_composed_artifact_is_1280x720_jpeg_under_2mb_and_valid(tmp_path):
    src = _img(tmp_path / "src.png", (1600, 1200))
    out = tp.compose_youtube_thumbnail(str(tmp_path / "yt.jpg"), headline="Why aging stars suffer a dip", source_image=src)
    v = tp.validate_thumbnail(out)
    assert v["ok"], v["problems"]
    assert (v["width"], v["height"], v["format"]) == (1280, 720, "JPEG") and v["bytes"] <= tp.MAX_BYTES


def test_headline_never_overflows_even_when_long():
    im = Image.new("RGB", (1280, 720))
    d = ImageDraw.Draw(im)
    font, lines, lh = tp._layout_headline(d, "Supercalifragilisticexpialidocious photosynthesis breakthrough revealed today", 690, 500)
    assert len(lines) <= 3 and all(d.textlength(l, font=font) <= 690 for l in lines) or len(lines) == 1


def test_validation_rejects_wrong_size_blank_and_missing(tmp_path):
    portrait = _img(tmp_path / "p.png")
    assert "dimensions 1080x1920" in " ".join(tp.validate_thumbnail(portrait)["problems"])
    blank = tmp_path / "b.jpg"
    Image.new("RGB", (1280, 720), (128, 128, 128)).save(blank)
    assert "nearly blank" in " ".join(tp.validate_thumbnail(str(blank))["problems"])
    assert tp.validate_thumbnail(str(tmp_path / "nope.jpg"))["problems"] == ["thumbnail file is missing"]


def test_artifact_for_a_portrait_uses_the_sidecar_and_is_cached(tmp_path):
    portrait = _img(tmp_path / "thumb_job.png")
    src = _img(tmp_path / "raw.png", (1500, 1000), (30, 160, 90))
    tp.write_sidecar(portrait, headline="ONE NUCLEAR BLAST", source_image=src)
    a1 = tp.youtube_artifact_for(portrait)
    assert a1.endswith(tp.YOUTUBE_SUFFIX) and tp.validate_thumbnail(a1)["ok"]
    assert tp.youtube_artifact_for(portrait) == a1


def _jpeg_bytes(path):
    b = io.BytesIO()
    Image.open(path).convert("RGB").save(b, "JPEG")
    return b.getvalue()


def test_remote_verification_matches_only_the_sent_image(tmp_path):
    art = tp.compose_youtube_thumbnail(str(tmp_path / "a.jpg"), headline="Lava flows studied", source_image=_img(tmp_path / "s.png", (1600, 900)))
    other = _img(tmp_path / "frame.png", (1280, 720), (10, 200, 200))
    assert tp.verify_remote("vid", art, fetch=lambda url: _jpeg_bytes(art))["status"] == "verified"
    # 4:3 letterboxed variant of the same image still verifies
    lb = Image.new("RGB", (480, 360)); lb.paste(Image.open(art).resize((480, 270)), (0, 45))
    buf = io.BytesIO(); lb.save(buf, "JPEG")
    assert tp.verify_remote("vid", art, fetch=lambda url: buf.getvalue() if "hq" in url else (_ for _ in ()).throw(OSError()))["status"] == "verified"
    assert tp.verify_remote("vid", art, fetch=lambda url: _jpeg_bytes(other))["status"] == "mismatch"
    assert tp.verify_remote("vid", art, fetch=lambda url: (_ for _ in ()).throw(OSError("404")))["status"] == "unavailable"


# ------------------------------------------------------------------ engine integration (fake service)
@pytest.fixture
def engine(tmp_path, monkeypatch):
    from backend.app.services.youtube_publishing_engine import PublishingDatabase, YouTubePublishingEngine
    db = PublishingDatabase(db_path=str(tmp_path / "pub.db"))
    eng = YouTubePublishingEngine(oauth_manager=MagicMock(), db=db)
    monkeypatch.setattr("backend.app.services.youtube_publishing_engine.MediaFileUpload", lambda path, mimetype=None: path)
    return eng


def _svc(processing="succeeded", thumbs=None):
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{
        "processingDetails": {"processingStatus": processing.upper()}, "snippet": {"thumbnails": thumbs or {}}}]}
    svc.thumbnails.return_value.set.return_value.execute.return_value = {"items": [{"default": {}, "shortsAspectRatio": {}}]}
    return svc


def test_upload_sends_the_shorts_safe_artifact_after_processing_and_persists_state(engine, tmp_path):
    portrait = _img(tmp_path / "thumb_j.png")
    tp.write_sidecar(portrait, headline="COMBINING BATTERY ADDITIVES", source_image=None)
    waiting = _svc(processing="processing")
    res = engine._upload_thumbnail(waiting, 7, "VID123", portrait)
    assert res["status"] == "awaiting_processing" and res["error"] is None
    waiting.thumbnails.return_value.set.assert_not_called()       # never before YouTube finished processing
    assert engine.thumbnail_store().get(7)["upload_status"] == "awaiting_processing"
    svc = _svc()
    res = engine._upload_thumbnail(svc, 7, "VID123", portrait)
    assert res["uploaded"] and res["artifact"].endswith(tp.SHORTS_SUFFIX)
    kwargs = svc.thumbnails.return_value.set.call_args.kwargs
    assert kwargs["videoId"] == "VID123" and kwargs["media_body"].endswith(tp.SHORTS_SUFFIX)
    svc.videos.return_value.insert.assert_not_called()           # the video is never uploaded again
    st = engine.thumbnail_store().get(7)
    assert st["upload_status"] == "uploaded" and st["verify_status"] == "pending"
    assert tp.ready_label(st).startswith("uploaded, verification")


def test_invalid_thumbnail_is_not_uploaded_and_marks_not_ready(engine, tmp_path):
    svc = MagicMock()
    res = engine._upload_thumbnail(svc, 8, "VID", str(tmp_path / "missing.png"))
    assert not res["uploaded"]
    svc.thumbnails.assert_not_called()
    st = engine.thumbnail_store().get(8)
    assert st["upload_status"] == "invalid" and tp.ready_label(st).startswith("NOT READY")


def test_failed_upload_is_recorded_with_its_error(engine, tmp_path):
    from googleapiclient.errors import HttpError
    portrait = _img(tmp_path / "t.png")
    svc = _svc()
    resp = MagicMock(); resp.status = 403
    svc.thumbnails.return_value.set.return_value.execute.side_effect = HttpError(resp, b"forbidden")
    res = engine._upload_thumbnail(svc, 9, "VID", portrait)
    assert res["status"] == "eligibility_restricted"
    assert engine.thumbnail_store().get(9)["upload_status"] == "restricted"


def test_verification_sweep_verifies_or_marks_not_ready_after_retries(engine, tmp_path, monkeypatch):
    portrait = _img(tmp_path / "t.png")
    engine._upload_thumbnail(_svc(), 10, "VIDOK", portrait)
    engine._upload_thumbnail(_svc(), 11, "VIDBAD", portrait)
    art = engine.thumbnail_store().get(10)["artifact_path"]
    wrong = _img(tmp_path / "w.png", (1280, 720), (240, 240, 20))
    # YouTube's metadata lists the 9:16 variant; it shows the portrait design for the good video
    engine.oauth.get_authenticated_service.return_value = _svc(thumbs={"shortsAspectRatio": {"url": "sar/VIDOK"}})
    fetch = lambda url: (_jpeg_bytes(portrait) if "sar/" in url else _jpeg_bytes(art) if "VIDOK" in url  # noqa: E731
                         else _jpeg_bytes(wrong))
    errors = {}
    monkeypatch.setattr(engine.db, "update_queue_metadata_and_schedule", lambda lid, **kw: errors.__setitem__(lid, kw))
    monkeypatch.setattr(engine.db, "get_publication", lambda lid: {"error_details": None})
    for _ in range(6):
        engine.verify_uploaded_thumbnails(min_age_sec=0, fetch=fetch)
    assert engine.thumbnail_store().get(10)["verify_status"] == "verified"
    assert tp.ready_label(engine.thumbnail_store().get(10)) == "ready"
    bad = engine.thumbnail_store().get(11)
    assert bad["verify_status"] == "failed" and "verification failed" in errors[11]["error_details"]
