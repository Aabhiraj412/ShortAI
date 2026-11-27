"""Hindi publishing queue and the Hindi channel's own publishing schedule.

Separate from the English publishing ledger and the English 24-cycle schedule (neither is read for writing here).

* **Queue** (`hindi_publications`): one row per Hindi localization that PASSED Hindi QC (``UNIQUE(localization_id)``).
  English videos cannot enter: rows are created only from ``content_localizations`` rows whose target is the Hindi
  channel, and the video file must lie under ``media/localizations/<hi>/``.
* **Schedule** (``channel_registry.publishing_config_json`` of the Hindi channel, key ``schedule``): enabled flag
  (default OFF), daily times in Asia/Kolkata, daily limit, max attempts. Changing it is a dashboard action.
* **Uploads** use ONLY the Hindi channel's own token file (``ChannelRegistry.credential_path``), and the authenticated
  identity is checked with ``channels.list?mine=true`` against the verified Hindi channel id before every upload.
  Videos are uploaded private with ``publishAt`` = the scheduled slot (YouTube publishes at that time).
* **Gates** (all required, re-checked at every tick): Hindi channel ACTIVE and verified, ``publishing_authorized`` in
  its publishing config (operator authorization, never set by code), schedule enabled, localization QC_PASSED.
* **Duplicates**: one row per localization, one video id per row (UNIQUE), an atomic claim before upload, and an
  interrupted upload (no answer from YouTube) becomes NEEDS_CHECK: its retry first looks for the title on the Hindi
  channel's uploads and adopts that video instead of uploading again.
* **Thumbnails**: set only after YouTube reports processing SUCCEEDED; state is attempted / set / verified / rejected
  (``thumbnail_pipeline.set_and_verify``).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
TARGET = "hi"
DRAFT, SCHEDULED, UPLOADING, UPLOADED, FAILED, NEEDS_CHECK = (
    "DRAFT", "SCHEDULED", "UPLOADING", "UPLOADED", "FAILED", "NEEDS_CHECK")
EDITABLE = (DRAFT, SCHEDULED, FAILED)
DEFAULT_SCHEDULE = {"enabled": False, "timezone": "Asia/Kolkata", "times": ["19:30"], "daily_limit": 1,
                    "max_attempts": 3}
UPLOAD_LEAD = timedelta(hours=2)          # upload this long before the slot (YouTube publishes at publishAt)
MIN_LEAD = timedelta(minutes=20)
STALE_UPLOAD = timedelta(minutes=45)

SCHEMA = """
CREATE TABLE IF NOT EXISTS hindi_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    localization_id INTEGER NOT NULL UNIQUE,
    source_job_id TEXT NOT NULL,
    channel_key TEXT NOT NULL DEFAULT 'hi',
    video_path TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    category_id TEXT NOT NULL DEFAULT '27',
    thumbnail_path TEXT,
    state TEXT NOT NULL,
    scheduled_at_utc TEXT,
    youtube_video_id TEXT UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    thumbnail_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hindi_publication_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    publication_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
"""


class PublishingError(ValueError):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def _ts(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def validate_schedule(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULT_SCHEDULE)
    unknown = set(cfg) - set(DEFAULT_SCHEDULE)
    if unknown:
        raise PublishingError(f"unknown schedule field(s): {', '.join(sorted(unknown))}", 400)
    out.update(cfg)
    if out["timezone"] != "Asia/Kolkata":
        raise PublishingError("the Hindi schedule uses Asia/Kolkata", 400)
    times = out["times"]
    if not isinstance(times, list) or not 1 <= len(times) <= 6:
        raise PublishingError("give 1 to 6 daily times", 400)
    norm = []
    for t in times:
        m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(t))
        if not m:
            raise PublishingError(f"invalid time {t!r} (HH:MM, 24 h)", 400)
        norm.append(m.group(0))
    if len(set(norm)) != len(norm):
        raise PublishingError("duplicate daily times", 400)
    out["times"] = sorted(norm)
    if not isinstance(out["daily_limit"], int) or not 1 <= out["daily_limit"] <= 6:
        raise PublishingError("daily_limit must be 1 to 6", 400)
    if not isinstance(out["max_attempts"], int) or not 1 <= out["max_attempts"] <= 5:
        raise PublishingError("max_attempts must be 1 to 5", 400)
    out["enabled"] = out["enabled"] is True
    return out


def validate_metadata(title: str, description: str, tags: List[str]) -> List[str]:
    problems = []
    if not (title or "").strip() or len(title) > 100:
        problems.append("title must be 1 to 100 characters")
    if len(description or "") > 5000:
        problems.append("description must be at most 5000 characters")
    if any(c in (title or "") + (description or "") for c in "<>"):
        problems.append("YouTube does not allow < or > in the title or description")
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        problems.append("tags must be a list of strings")
    elif sum(len(t) + 2 for t in tags) > 480:
        problems.append("tags are too long (YouTube allows about 500 characters)")
    return problems


class HindiPublisher:
    def __init__(self, db_path: str, *, registry=None, media_dir: Optional[Path] = None,
                 service_factory: Optional[Callable[[], Any]] = None, quota=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if registry is None:
            from backend.app.channels.registry import get_registry
            registry = get_registry()
        if media_dir is None:
            from backend.app.core.paths import MEDIA_DIR
            media_dir = Path(MEDIA_DIR)
        self.db_path, self.reg, self.media_dir, self.clock = db_path, registry, Path(media_dir), clock
        self._service_factory = service_factory
        self._quota = quota
        with self._connect() as c:
            c.executescript(SCHEMA)

    # -- plumbing ------------------------------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    @property
    def quota(self):
        if self._quota is None:
            from backend.app.services.youtube_quota import QuotaLedger
            self._quota = QuotaLedger(self.db_path)
        return self._quota

    def _service(self):
        """The Hindi channel's own authorization (never the English token)."""
        if self._service_factory:
            return self._service_factory()
        ch = self.reg.get(TARGET)
        mgr = self.reg.oauth_factory(str(self.reg.credential_path(ch)), False)
        return mgr.get_authenticated_service()

    def _event(self, c, pid: int, event: str, detail: Any = None) -> None:
        c.execute("INSERT INTO hindi_publication_events (ts_utc, publication_id, event, detail) VALUES (?,?,?,?)",
                  (_ts(self.clock()), pid, event, json.dumps(detail, default=str)[:2000] if detail is not None else None))

    def _update(self, pid: int, event: str, detail: Any = None, expect: Optional[tuple] = None, **values) -> bool:
        values["updated_at_utc"] = _ts(self.clock())
        sets = ", ".join(f"{k}=?" for k in values)
        sql, args = f"UPDATE hindi_publications SET {sets} WHERE id=?", list(values.values()) + [pid]
        if expect:
            sql += f" AND state IN ({','.join('?' * len(expect))})"
            args += list(expect)
        with self._connect() as c:
            n = c.execute(sql, args).rowcount
            if n:
                self._event(c, pid, event, detail)
        return bool(n)

    def get(self, pid: int) -> Dict[str, Any]:
        with self._connect() as c:
            r = c.execute("SELECT * FROM hindi_publications WHERE id=?", (pid,)).fetchone()
        if not r:
            raise PublishingError("publication not found", 404)
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
        d["thumbnail"] = json.loads(d.pop("thumbnail_json") or "{}")
        return d

    # -- configuration -------------------------------------------------------------------------------------------
    def schedule(self) -> Dict[str, Any]:
        cfg = (self.reg.get(TARGET).get("publishing_config") or {}).get("schedule") or {}
        try:
            return validate_schedule(cfg)
        except PublishingError:
            return dict(DEFAULT_SCHEDULE)

    def set_schedule(self, cfg: Dict[str, Any], *, actor: str = "dashboard") -> Dict[str, Any]:
        new = validate_schedule(cfg)
        ch = self.reg.get(TARGET)
        pub = dict(ch.get("publishing_config") or {})
        old = pub.get("schedule")
        pub["schedule"] = new                       # publishing_authorized is never touched here
        conn = self.reg._connect(True)
        try:
            conn.execute("UPDATE channel_registry SET publishing_config_json=? WHERE channel_key=?",
                         (json.dumps(pub), TARGET))
            self.reg._event(conn, TARGET, "PUBLISHING_SCHEDULE_CHANGED", actor, ch["status"], ch["status"],
                            {"from": old, "to": new})
            conn.commit()
        finally:
            conn.close()
        return new

    def gates(self) -> Dict[str, Any]:
        reasons = []
        ch = self.reg.get(TARGET)
        if ch["status"] != "ACTIVE" or not ch.get("youtube_channel_id"):
            reasons.append(f"Hindi channel not verified and active ({ch['status']})")
        if not (ch.get("publishing_config") or {}).get("publishing_authorized"):
            reasons.append("Hindi publishing is not authorized (operator authorization required)")
        if not self.schedule()["enabled"]:
            reasons.append("the Hindi publishing schedule is disabled")
        return {"can_publish": not reasons, "reasons": reasons}

    # -- queue ---------------------------------------------------------------------------------------------------
    def _english_metadata(self, source_job_id: str) -> Dict[str, Any]:
        try:
            with sqlite3.connect(Path(self.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10) as c:
                r = c.execute("SELECT l.metadata_json, l.thumbnail_path FROM publishing_ledger l "
                              "JOIN persistent_jobs j ON j.publishing_ledger_id = l.id WHERE j.id=?",
                              (source_job_id,)).fetchone()
        except sqlite3.Error:
            r = None
        meta = json.loads(r[0] or "{}") if r else {}
        thumb = r[1] if r else None
        if not thumb:
            p = self.media_dir / "productions" / source_job_id / "thumbnails" / f"thumb_{source_job_id}.png"
            thumb = str(p) if p.is_file() else None
        return {"meta": meta, "thumbnail": thumb}

    def sync(self) -> int:
        """Adds every QC_PASSED Hindi localization not yet in the queue (as DRAFT, metadata to review)."""
        added = 0
        root = (self.media_dir / "localizations" / TARGET).resolve()
        for loc in self.reg.localizations(TARGET, limit=200):
            if loc["status"] != "QC_PASSED" or loc["target_channel_key"] != TARGET:
                continue
            arts = json.loads(loc.get("artifacts_json") or "{}")
            mp4 = Path(str(arts.get("final_mp4") or ""))
            try:
                inside = mp4.is_file() and root in mp4.resolve().parents
            except OSError:
                inside = False
            if not inside:
                continue                                 # never an English (or foreign) file
            src = self._english_metadata(loc["source_job_id"])
            m = src["meta"]
            base = (m.get("title") or loc.get("source_topic_title") or loc["source_job_id"]).strip()
            title = (base[:88] + " | Hinglish") if len(base) <= 88 else base[:87] + "… | Hinglish"
            desc = ("Hinglish version. " + (m.get("description") or "")).strip()[:5000]
            tags = [t for t in (m.get("tags") or []) if isinstance(t, str)][:15] + ["hinglish", "hindi"]
            now = _ts(self.clock())
            with self._connect() as c:
                cur = c.execute(
                    "INSERT OR IGNORE INTO hindi_publications (localization_id, source_job_id, video_path, title, "
                    "description, tags_json, category_id, thumbnail_path, state, created_at_utc, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (loc["id"], loc["source_job_id"], str(mp4), title.replace("<", "").replace(">", ""),
                     desc.replace("<", "").replace(">", ""), json.dumps(tags, ensure_ascii=False),
                     str(m.get("category_id") or "27"), src["thumbnail"], DRAFT, now, now))
                if cur.rowcount:
                    added += 1
                    self._event(c, cur.lastrowid, "QUEUED", {"localization_id": loc["id"]})
        return added

    def queue(self) -> Dict[str, Any]:
        self.sync()
        with self._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM hindi_publications ORDER BY "
                                                "COALESCE(scheduled_at_utc, '9999'), id")]
            events = [dict(r) for r in c.execute("SELECT * FROM hindi_publication_events ORDER BY id DESC LIMIT 30")]
        locs = {l["id"]: l for l in self.reg.localizations(TARGET, limit=200)}
        items = []
        for r in rows:
            loc = locs.get(r["localization_id"]) or {}
            qc = json.loads(loc.get("qc_json") or "null") or {}
            items.append({"id": r["id"], "source_job_id": r["source_job_id"], "title": r["title"],
                          "description": r["description"], "tags": json.loads(r["tags_json"] or "[]"),
                          "state": r["state"], "scheduled_at_utc": r["scheduled_at_utc"],
                          "youtube_video_id": r["youtube_video_id"], "attempts": r["attempts"],
                          "last_error": r["last_error"], "localization_status": loc.get("status"),
                          "qc_passed": bool(qc.get("passed")), "thumbnail": json.loads(r["thumbnail_json"] or "{}"),
                          "thumbnail_asset": bool(r["thumbnail_path"] and Path(r["thumbnail_path"]).is_file())})
        return {"items": items, "events": events, "schedule": self.schedule(), "gates": self.gates(),
                "note": "Hindi videos only (QC passed). Uploads use the Hindi channel's own authorization; nothing "
                        "is published unless the channel is active, Hindi publishing is authorized and the "
                        "schedule is enabled."}

    def update_metadata(self, pid: int, *, title: str, description: str, tags: List[str]) -> Dict[str, Any]:
        problems = validate_metadata(title, description, tags)
        if problems:
            raise PublishingError("; ".join(problems), 400)
        if not self._update(pid, "METADATA_EDITED", None, expect=EDITABLE, title=title.strip(),
                            description=description, tags_json=json.dumps(tags, ensure_ascii=False)):
            raise PublishingError(f"metadata can be edited only in {', '.join(EDITABLE)} (state {self.get(pid)['state']})")
        return self.get(pid)

    def _slots(self, start: datetime, days: int = 30) -> List[datetime]:
        cfg = self.schedule()
        out = []
        d0 = start.astimezone(IST).date()
        for i in range(days):
            for t in cfg["times"]:
                h, m = map(int, t.split(":"))
                out.append(datetime(d0.year, d0.month, d0.day, h, m, tzinfo=IST) + timedelta(days=i))
        return [s for s in out if s >= start]

    def _taken(self, exclude: Optional[int] = None) -> List[datetime]:
        with self._connect() as c:
            rows = c.execute("SELECT id, scheduled_at_utc FROM hindi_publications WHERE scheduled_at_utc IS NOT NULL "
                             "AND state IN ('SCHEDULED','UPLOADING','UPLOADED','NEEDS_CHECK')").fetchall()
        return [_parse(r[1]) for r in rows if r[0] != exclude]

    def schedule_item(self, pid: int, at_utc: Optional[str] = None) -> Dict[str, Any]:
        """Assigns a slot: the given one (must be a configured daily time) or the next free one. Enforces the daily
        limit (per IST day), one video per slot and the minimum lead time."""
        row = self.get(pid)
        if row["state"] not in (DRAFT, FAILED, SCHEDULED):
            raise PublishingError(f"cannot schedule in state {row['state']}")
        problems = validate_metadata(row["title"], row["description"], row["tags"])
        if problems:
            raise PublishingError("fix the metadata first: " + "; ".join(problems), 400)
        loc = next((l for l in self.reg.localizations(TARGET, limit=200) if l["id"] == row["localization_id"]), None)
        if not loc or loc["status"] != "QC_PASSED":
            raise PublishingError("only a localization that passed Hindi QC can be scheduled")
        cfg = self.schedule()
        earliest = self.clock() + MIN_LEAD
        taken = self._taken(exclude=pid)
        per_day: Dict[Any, int] = {}
        for t in taken:
            per_day[t.astimezone(IST).date()] = per_day.get(t.astimezone(IST).date(), 0) + 1
        slots = self._slots(earliest)
        if at_utc:
            want = _parse(at_utc)
            if want is None or want < earliest:
                raise PublishingError("the time must be at least 20 minutes from now", 400)
            if not any(abs((s - want).total_seconds()) < 60 for s in slots):
                raise PublishingError(f"not a configured daily time ({', '.join(cfg['times'])} IST)", 400)
            slots = [want]
        for s in slots:
            if any(t and abs((t - s).total_seconds()) < 60 for t in taken):
                if at_utc:
                    raise PublishingError("another Hindi video already has this slot")
                continue
            if per_day.get(s.astimezone(IST).date(), 0) >= cfg["daily_limit"]:
                if at_utc:
                    raise PublishingError(f"the daily limit ({cfg['daily_limit']}) is reached for that day")
                continue
            if not self._update(pid, "SCHEDULED", {"at": _ts(s)}, expect=(DRAFT, FAILED, SCHEDULED),
                                state=SCHEDULED, scheduled_at_utc=_ts(s), last_error=None):
                raise PublishingError("the item changed; refresh and try again")
            return self.get(pid)
        raise PublishingError("no free slot in the next 30 days")

    def unschedule(self, pid: int) -> Dict[str, Any]:
        if not self._update(pid, "UNSCHEDULED", None, expect=(SCHEDULED, FAILED), state=DRAFT, scheduled_at_utc=None):
            raise PublishingError(f"only a scheduled or failed item can be unscheduled (state {self.get(pid)['state']})")
        return self.get(pid)

    # -- upload --------------------------------------------------------------------------------------------------
    def _check_identity(self, svc) -> None:
        ch = self.reg.get(TARGET)
        res = self.quota.execute(svc.channels().list(part="id", mine=True), "channels.list",
                                 purpose="hindi_identity_check")
        ids = [i.get("id") for i in res.get("items") or []]
        if ids != [ch.get("youtube_channel_id")]:
            raise PublishingError("the Hindi authorization does not belong to the verified Hindi channel; nothing "
                                  "was uploaded", 403)

    def _find_remote(self, svc, title: str) -> Optional[str]:
        res = self.quota.execute(svc.channels().list(part="contentDetails", mine=True), "channels.list",
                                 purpose="hindi_duplicate_check")
        items = res.get("items") or []
        if not items:
            return None
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = self.quota.execute(svc.playlistItems().list(part="snippet", playlistId=uploads, maxResults=50),
                                "playlistItems.list", purpose="hindi_duplicate_check")
        for it in pl.get("items") or []:
            sn = it.get("snippet") or {}
            if sn.get("title") == title:
                return (sn.get("resourceId") or {}).get("videoId")
        return None

    def recover(self) -> int:
        """An upload that never finished (process stopped) is NEEDS_CHECK: never re-uploaded blindly."""
        cutoff = _ts(self.clock() - STALE_UPLOAD)
        with self._connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM hindi_publications WHERE state='UPLOADING' "
                                           "AND updated_at_utc < ?", (cutoff,))]
        for pid in ids:
            self._update(pid, "UPLOAD_INTERRUPTED", None, expect=(UPLOADING,), state=NEEDS_CHECK,
                         last_error="upload interrupted: the Hindi channel is checked before any retry")
        return len(ids)

    def retry(self, pid: int) -> Dict[str, Any]:
        row = self.get(pid)
        if row["state"] == NEEDS_CHECK:
            vid = self._find_remote(self._service(), row["title"])
            if vid:
                self._update(pid, "ADOPTED_REMOTE_UPLOAD", {"video_id": vid}, expect=(NEEDS_CHECK,), state=UPLOADED,
                             youtube_video_id=vid, last_error=None)
                return self.get(pid)
            self._update(pid, "RETRY", None, expect=(NEEDS_CHECK,), state=FAILED)
            row = self.get(pid)
        if row["state"] != FAILED:
            raise PublishingError(f"only a failed upload can be retried (state {row['state']})")
        self._update(pid, "RETRY_RESET", None, expect=(FAILED,), attempts=0)
        return self.schedule_item(pid, row["scheduled_at_utc"] if row["scheduled_at_utc"] and
                                  _parse(row["scheduled_at_utc"]) > self.clock() + MIN_LEAD else None)

    def upload(self, pid: int) -> Dict[str, Any]:
        gates = self.gates()
        if not gates["can_publish"]:
            raise PublishingError("; ".join(gates["reasons"]), 403)
        row = self.get(pid)
        cfg = self.schedule()
        gate = self.quota.check("uploads", 1)
        if not gate["allowed"]:
            return {"uploaded": False, "reason": gate["reason"]}
        if not self._update(pid, "UPLOAD_STARTED", None, expect=(SCHEDULED,), state=UPLOADING):
            return {"uploaded": False, "reason": "not claimable (already uploading or changed)"}
        try:
            from googleapiclient.http import MediaFileUpload
            svc = self._service()
            self._check_identity(svc)
            publish_at = max(_parse(row["scheduled_at_utc"]), self.clock() + timedelta(minutes=15))
            body = {"snippet": {"title": row["title"], "description": row["description"], "tags": row["tags"],
                                "categoryId": row["category_id"], "defaultLanguage": "hi",
                                "defaultAudioLanguage": "hi"},
                    "status": {"privacyStatus": "private", "publishAt": _ts(publish_at),
                               "selfDeclaredMadeForKids": False}}
            req = svc.videos().insert(part="snippet,status", body=body,
                                      media_body=MediaFileUpload(row["video_path"], mimetype="video/mp4",
                                                                 resumable=True))
            res = self.quota.execute(req, "videos.insert", purpose="hindi_upload", job_id=row["source_job_id"])
        except PublishingError as exc:
            self._update(pid, "UPLOAD_REFUSED", str(exc), state=FAILED, last_error=str(exc)[:500],
                         attempts=int(row["attempts"]) + 1)
            raise
        except Exception as exc:  # noqa: BLE001
            from backend.app.services.youtube_quota import classify_http_error
            answered = classify_http_error(exc).get("http_status") is not None
            attempts = int(row["attempts"]) + 1
            state = FAILED if answered else NEEDS_CHECK            # no answer: maybe uploaded -> check first
            msg = f"{type(exc).__name__}: {str(exc)[:300]}"
            self._update(pid, "UPLOAD_FAILED", msg, state=state, last_error=msg, attempts=attempts)
            if answered and attempts < cfg["max_attempts"]:
                self._update(pid, "AUTO_RETRY_SCHEDULED", None, expect=(FAILED,), state=SCHEDULED)
            return {"uploaded": False, "reason": msg}
        vid = res.get("id")
        self._update(pid, "UPLOADED", {"video_id": vid, "publish_at": _ts(publish_at)}, state=UPLOADED,
                     youtube_video_id=vid, last_error=None, thumbnail_json=json.dumps({"state": "pending"}))
        return {"uploaded": True, "video_id": vid}

    def _thumbnails(self) -> List[Dict[str, Any]]:
        from backend.app.services import thumbnail_pipeline as tpl
        out = []
        with self._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM hindi_publications WHERE state='UPLOADED' AND "
                                               "youtube_video_id IS NOT NULL")]
        for r in rows:
            st = json.loads(r["thumbnail_json"] or "{}")
            if st.get("state") in ("verified", "rejected", "no_asset") or int(st.get("checks") or 0) >= 12:
                continue
            if not r["thumbnail_path"]:
                self._update(r["id"], "THUMBNAIL_NO_ASSET", None, thumbnail_json=json.dumps({"state": "no_asset"}))
                continue
            res = tpl.set_and_verify(self._service(), self.quota, r["youtube_video_id"], r["thumbnail_path"],
                                     kind="SHORT", previous=st, purpose="hindi_thumbnail")
            self._update(r["id"], "THUMBNAIL_" + res["state"].upper(), res.get("error"),
                         thumbnail_json=json.dumps(res, default=str))
            out.append({"id": r["id"], **res})
        return out

    def tick(self) -> Dict[str, Any]:
        """Scheduler entry point (coordinator tick, every few minutes). Uploads at most one due video per tick."""
        self.recover()
        self.sync()
        gates = self.gates()
        if not gates["can_publish"]:
            return {"ran": False, "reasons": gates["reasons"]}
        now = self.clock()
        with self._connect() as c:
            due = c.execute("SELECT id FROM hindi_publications WHERE state='SCHEDULED' AND scheduled_at_utc <= ? "
                            "ORDER BY scheduled_at_utc, id LIMIT 1", (_ts(now + UPLOAD_LEAD),)).fetchone()
        res = self.upload(due[0]) if due else {"uploaded": False, "reason": "nothing due"}
        return {"ran": True, "upload": res, "thumbnails": self._thumbnails()}


def get_publisher() -> HindiPublisher:
    from backend.app.database.db import DB_PATH as _DB
    return HindiPublisher(str(_DB))
