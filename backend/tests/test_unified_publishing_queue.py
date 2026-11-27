"""Unified YouTube publishing queue (2026-11-28): one view of English and Hindi videos, channel isolation kept.
Fakes only: no YouTube request, no production database."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.services import unified_publishing_queue as uq

HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"


def en(i, at=None, status="Ready to Schedule", vid=None, **kw):
    return dict(id=i, title=f"English {i}", topic_title=f"Topic {i}", production_cycle_id=f"cycle_{i}_20261128_0100",
                ui_status=status, scheduled_utc_publish_at=at, youtube_video_id=vid, thumbnail_path="t.png",
                thumbnail_ready="ready", **kw)


def hi(i, at=None, state="SCHEDULED", qc=True, vid=None):
    return dict(id=i, source_job_id=f"cycle_{i}_20261127_2300", title=f"Hindi {i} | Hinglish", state=state,
                scheduled_at_utc=at, youtube_video_id=vid, localization_status="QC_PASSED" if qc else "QC_REJECTED",
                qc_passed=qc, thumbnail={}, thumbnail_asset=True, last_error=None)


def test_both_channels_appear_together_sorted_by_scheduled_time():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)
    rows = uq.merge([en(1, "2026-11-28T09:30:00Z", "Scheduled"), en(2), en(3, "2026-11-27T05:00:00Z", "Published"),
                     en(4, "2026-11-28T04:00:00Z", "Published")],
                    [hi(5, "2026-11-28T08:00:00Z"), hi(6, "2026-11-28T10:00:00+00:00")], now=now)
    # upcoming soonest first, then past most recent first, then unscheduled
    assert [r["key"] for r in rows] == ["hi:5", "en:1", "hi:6", "en:4", "en:3", "en:2"]
    assert rows[0]["channel_label"] == "Hindi" and rows[1]["channel_label"] == "English"
    assert rows[0]["scheduled_ist"] == "2026-11-28 13:30 IST"
    for r in rows:
        assert set(r) >= {"title", "channel", "qc_status", "thumbnail", "scheduled_at_utc", "youtube_status", "actions"}


def test_channel_filters():
    e, h = [en(1), en(2)], [hi(5)]
    assert {r["channel"] for r in uq.merge(e, h, "en")} == {"en"} and len(uq.merge(e, h, "en")) == 2
    assert [r["key"] for r in uq.merge(e, h, "hi")] == ["hi:5"]
    assert len(uq.merge(e, h, "all")) == 3


def test_no_duplicate_rows():
    rows = uq.merge([en(1), en(1)], [hi(1), hi(1)])
    assert sorted(r["key"] for r in rows) == ["en:1", "hi:1"]                 # same id on two channels stays two rows


def test_english_actions_follow_the_english_state_machine():
    a = {s: uq.english_row(en(1, "2026-11-29T01:00:00Z", s, vid="abc"))["actions"] for s in
         ("Ready to Schedule", "Scheduled", "Published", "Failed")}
    assert a["Scheduled"] == ["edit", "thumbnail", "schedule", "unschedule", "publish_now", "view"]
    assert a["Published"] == ["view"]
    assert "retry" in a["Failed"] and "unschedule" not in a["Ready to Schedule"]
    assert uq.english_row(en(1))["thumbnail"]["editable"] is True


def test_hindi_without_qc_pass_has_no_action():
    r = uq.hindi_row(hi(9, state="DRAFT", qc=False))
    assert r["actions"] == [] and r["qc_passed"] is False and "not passed" in r["qc_status"]
    ok = uq.hindi_row(hi(9, state="DRAFT"))
    assert ok["actions"] == ["edit", "schedule", "publish_now"] and ok["thumbnail"]["editable"] is False
    assert uq.hindi_row(hi(9, state="UPLOADED", vid="v1"))["actions"] == ["view"]
    assert "retry" in uq.hindi_row(hi(9, state="NEEDS_CHECK"))["actions"]


# -- route ---------------------------------------------------------------------------------------------------------
def test_route_is_read_only_and_merges_both_queues(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    from backend.app.localization import publishing as hp

    calls = []

    async def fake_en(limit=100, _=None):
        calls.append("en.list")
        return {"items": [en(1, "2026-11-29T03:30:00Z", "Scheduled")], "youtube_oauth": {"channel_connected": True}}

    class FakePub:
        def queue(self):
            calls.append("hi.queue")
            return {"items": [hi(2, "2026-11-29T01:00:00Z")], "gates": {"can_publish": False, "reasons": ["not authorized"]}}

        def __getattr__(self, name):                    # any write attempt would show up here
            calls.append(f"hi.{name}")
            raise AssertionError(name)
    monkeypatch.setattr(main, "api_list_publishing_queue", fake_en)
    monkeypatch.setattr(hp, "get_publisher", lambda: FakePub())
    c = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    d = c.get("/api/publishing/unified").json()
    assert [r["key"] for r in d["items"]] == ["hi:2", "en:1"] and calls == ["en.list", "hi.queue"]
    assert d["hindi"]["gates"]["can_publish"] is False and "1" in d["english"]["raw"] and "2" in d["hindi"]["raw"]
    assert [r["channel"] for r in c.get("/api/publishing/unified?channel=hi").json()["items"]] == ["hi"]
    assert c.get("/api/publishing/unified?channel=fr").status_code == 400
    remote = TestClient(main.app, base_url="http://example.com", client=("10.1.2.3", 5000))
    assert remote.get("/api/publishing/unified").status_code in (400, 403)


def test_english_still_listed_when_the_hindi_side_fails(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    from backend.app.localization import publishing as hp

    async def fake_en(limit=100, _=None):
        return {"items": [en(1)]}

    def boom():
        raise RuntimeError("hindi db locked")
    monkeypatch.setattr(main, "api_list_publishing_queue", fake_en)
    monkeypatch.setattr(hp, "get_publisher", boom)
    d = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)).get("/api/publishing/unified").json()
    assert [r["key"] for r in d["items"]] == ["en:1"] and d["hindi"]["error"] == "RuntimeError"


# -- dashboard: one queue, each row's actions use only its own channel's endpoints --------------------------------
def _html():
    return HTML.read_text(encoding="utf-8")


def test_queue_tab_has_one_card_and_admin_moved_to_channels():
    h = _html()
    q = h[h.index('<section id="tab-queue"'):h.index('<section id="tab-channels"')]
    c = h[h.index('<section id="tab-channels"'):h.index('<section id="tab-ops"')]
    assert q.count('class="card"') == 1 and "YouTube Publishing Queue" in q
    for head in ("<th>Video</th>", "<th>Channel</th>", "<th>QC Status</th>", "<th>Thumbnail</th>",
                 "<th>Scheduled Time</th>", "<th>YouTube Status</th>", "<th>Actions</th>"):
        assert head in q
    assert all(f'data-ch="{x}"' in q for x in ("all", "en", "hi"))
    for card in ("chCard", "locCard", "hpCard", "thCard", "hpEvents"):           # still available, just moved
        assert f'id="{card}"' in c and f'id="{card}"' not in q


def _fn(h, name):
    i = h.index(f"function {name}(")
    i = h.rfind("\n", 0, i) + 1
    depth, j = 0, h.index("{", i)
    for k in range(j, len(h)):
        depth += {"{": 1, "}": -1}.get(h[k], 0)
        if depth == 0:
            return h[i:k + 1]


def test_row_buttons_call_only_their_own_channel(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    h = _html()
    code = "\n".join(_fn(h, n) for n in ("escHtml", "uqActionButtons", "uqFilterRows"))
    rows = [uq.english_row(en(1, "2026-11-29T01:00:00Z", "Scheduled", vid="e1")),
            uq.english_row(en(2, status="Failed")),
            uq.hindi_row(hi(3, "2026-11-29T02:00:00Z", vid=None)), uq.hindi_row(hi(4, state="FAILED")),
            uq.hindi_row(hi(5, state="DRAFT", qc=False))]
    script = code + f"\nconst rows = {json.dumps(rows)};\nconsole.log(JSON.stringify(rows.map(r => uqActionButtons(r))));" \
                    "\nconsole.log(JSON.stringify(uqFilterRows(rows, 'hi').map(r => r.key)));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    btns, hi_keys = [json.loads(l) for l in out.stdout.strip().splitlines()]
    en_fns, hi_fns = {"openPubEditModal", "cancelQueueItemSchedule", "publishQueueItemNow"}, \
        {"hpEdit", "hpAct", "openHiSched", "hiPublishSoon"}
    called = [set(re.findall(r'onclick="(\w+)\(', b)) for b in btns]
    assert called[0] == en_fns and called[1] == {"openPubEditModal", "publishQueueItemNow"}
    assert called[2] == hi_fns and called[3] <= hi_fns and "hpAct" in called[3]
    assert all(not (c & hi_fns) for c in called[:2]) and all(not (c & en_fns) for c in called[2:])
    assert "onclick" not in btns[4]                                              # no QC pass, no action
    assert hi_keys == ["hi:3", "hi:4", "hi:5"]
    # the functions behind the buttons call only their own channel's API
    for fn in en_fns:
        body = _fn(h, fn)
        assert "/api/hindi-publishing" not in body
    for fn in hi_fns:
        body = _fn(h, fn)
        assert "/api/publishing/queue" not in body
    for fn in ("hpAct", "hiPublishSoon", "hiSchedAt", "hiSchedPlanned"):
        assert "/api/hindi-publishing/" in _fn(h, fn)
    assert "/api/publishing/queue/${ledgerId}/publish-now" in _fn(h, "publishQueueItemNow")


def test_hindi_publish_now_respects_the_hindi_minimum_lead():
    body = _fn(_html(), "hiPublishSoon")
    m = re.search(r"Date\.now\(\) \+ (\d+) \* 60000", body)
    from backend.app.localization.publishing import MIN_LEAD
    assert m and int(m.group(1)) * 60 > MIN_LEAD.total_seconds()
