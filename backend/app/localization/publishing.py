"""Hindi publishing queue and the Hindi channel's own publishing schedule.

Separate from the English publishing ledger and the English 24-cycle schedule (neither is read for writing here).

* **Queue** (`hindi_publications`): one row per Hindi localization that PASSED Hindi QC (``UNIQUE(localization_id)``).
  English videos cannot enter: rows are created only from ``content_localizations`` rows whose target is the Hindi
  channel, and the video file must lie under ``media/localizations/<hi>/``.
* **Schedule** (``channel_registry.publishing_config_json`` of the Hindi channel, key ``schedule``): a scheduling
  ``strategy`` plus its settings, the enabled flag (default OFF), daily limit and max attempts.
  - ``MANUAL`` (current): ``cycles`` maps each English production cycle id to ``{"same_as_english": bool,
    "time": "HH:MM"}``. "Same as English" (the default for every cycle, also for cycles added later) follows the
    English cycle's Target Publish Time as it is in the active schedule. The English schedule itself is never
    written: Hindi times are stored only here.
  - Future strategies (e.g. an analytical one) register a planner in ``PLANNERS`` that returns an individual
    timestamp per video. The queue stores one ``scheduled_at_utc`` per video, never a fixed slot.
  Each channel has its own strategy; English keeps its existing per-cycle target times.
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
DEFAULT_SCHEDULE = {"strategy": "MANUAL", "enabled": False, "timezone": "Asia/Kolkata", "daily_limit": 24,
                    "max_attempts": 3, "cycles": {}}
STRATEGIES = ("MANUAL",)                  # reserved for later: "ANALYTICAL" (per-video timestamps)
LEGACY_KEYS = ("times",)                  # the former single daily-time list; ignored
CYCLE_RE = re.compile(r"cycle_\d{1,3}")
JOB_RE = re.compile(r"^(cycle_\d{1,3})_(\d{8})_(\d{4})$")
PUBLISH_MIN_LEAD = timedelta(minutes=15)  # YouTube needs publishAt in the future; a missed time is re-planned
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


def _hhmm(t: Any) -> Optional[str]:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(t or ""))
    return m.group(0) if m else None


def validate_cycles(cycles: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(cycles, dict) or len(cycles) > 96:
        raise PublishingError("cycles must map cycle ids to Hindi time settings", 400)
    out = {}
    for cid, v in cycles.items():
        if not CYCLE_RE.fullmatch(str(cid)) or not isinstance(v, dict) or set(v) - {"same_as_english", "time"}:
            raise PublishingError(f"invalid Hindi time setting for {cid!r}", 400)
        same = v.get("same_as_english", True) is not False
        t = v.get("time")
        if not same and not _hhmm(t):
            raise PublishingError(f"{cid}: choose a Hindi time (HH:MM) or use Same as English", 400)
        out[str(cid)] = {"same_as_english": same, "time": None if same else _hhmm(t)}
    return out


def validate_schedule(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULT_SCHEDULE)
    cfg = {k: v for k, v in cfg.items() if k not in LEGACY_KEYS}
    unknown = set(cfg) - set(DEFAULT_SCHEDULE)
    if unknown:
        raise PublishingError(f"unknown schedule field(s): {', '.join(sorted(unknown))}", 400)
    out.update(cfg)
    if out["timezone"] != "Asia/Kolkata":
        raise PublishingError("the Hindi schedule uses Asia/Kolkata", 400)
    if out["strategy"] not in STRATEGIES:
        raise PublishingError(f"scheduling strategy {out['strategy']!r} is not available (available: MANUAL)", 400)
    out["cycles"] = validate_cycles(out["cycles"])
    if not isinstance(out["daily_limit"], int) or not 1 <= out["daily_limit"] <= 24:
        raise PublishingError("daily_limit must be 1 to 24", 400)
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
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 english_cycles: Optional[Callable[[], List[Dict[str, Any]]]] = None):
        if registry is None:
            from backend.app.channels.registry import get_registry
            registry = get_registry()
        if media_dir is None:
            from backend.app.core.paths import MEDIA_DIR
            media_dir = Path(MEDIA_DIR)
        self.db_path, self.reg, self.media_dir, self.clock = db_path, registry, Path(media_dir), clock
        self._service_factory = service_factory
        self._quota = quota
        self._english_cycles = english_cycles
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

    def set_cycle_times(self, cycles: Dict[str, Any], *, actor: str = "dashboard") -> Dict[str, Any]:
        """Saves the Hindi time of each production cycle (merged per cycle). The English schedule is not touched."""
        cur = self.schedule()
        merged = dict(cur["cycles"])
        merged.update(validate_cycles(cycles))
        return self.set_schedule({k: v for k, v in dict(cur, cycles=merged).items()}, actor=actor)

    def english_cycles(self) -> List[Dict[str, Any]]:
        """The ACTIVE English production schedule's cycles (read only)."""
        if self._english_cycles:
            return self._english_cycles()
        from backend.app.services import schedule_service
        return [{k: c.get(k) for k in ("cycle_id", "name", "start_time", "target_publish_time", "enabled")}
                for c in (schedule_service.schedule_view().get("cycles") or [])]

    def cycle_times(self) -> List[Dict[str, Any]]:
        """Per English cycle: its target time, the Hindi setting and the effective Hindi time (for the UI)."""
        cfg = self.schedule()["cycles"]
        out = []
        for c in self.english_cycles():
            setting = cfg.get(c["cycle_id"]) or {"same_as_english": True, "time": None}
            eff = c.get("target_publish_time") if setting["same_as_english"] else setting["time"]
            out.append({"cycle_id": c["cycle_id"], "name": c.get("name"), "start_time": c.get("start_time"),
                        "english_target_publish_time": c.get("target_publish_time"),
                        "same_as_english": setting["same_as_english"], "hindi_time": setting["time"],
                        "effective_hindi_time": _hhmm(eff)})
        return out

    # -- planning (strategy) ---------------------------------------------------------------------------------------
    def _plan_manual(self, source_job_id: str, not_before: datetime) -> Dict[str, Any]:
        """MANUAL: the cycle's Hindi time (or the English target time, "Same as English"). The first occurrence at or
        after the English production start that is also at least PUBLISH lead time after ``not_before`` (a late
        localization or a missed time moves to the next day's occurrence; never "now")."""
        m = JOB_RE.match(source_job_id or "")
        if not m:
            return {"at": None, "reason": f"{source_job_id}: not a production cycle job"}
        cid, ymd, hm = m.groups()
        row = next((r for r in self.cycle_times() if r["cycle_id"] == cid), None)
        if row is None:
            return {"at": None, "reason": f"{cid} is not in the active production schedule"}
        t = row["effective_hindi_time"]
        if not t:
            return {"at": None, "reason": f"{cid}: no Hindi time (English has no target time; choose one)"}
        start = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]), int(hm[:2]), int(hm[2:]), tzinfo=IST)
        h, mi = map(int, t.split(":"))
        occ = start.replace(hour=h, minute=mi)
        if occ < start:                                       # e.g. 23:00 production, 00:00 target: next day
            occ += timedelta(days=1)
        while occ < not_before:
            occ += timedelta(days=1)
        return {"at": occ, "reason": f"{cid} Hindi time {t} IST" + (" (same as English)" if row["same_as_english"] else "")}

    def plan(self, row: Dict[str, Any], *, exclude: Optional[int] = None) -> Dict[str, Any]:
        """Strategy dispatch -> one individual timestamp for this video, within the daily limit."""
        cfg = self.schedule()
        planner = PLANNERS[cfg["strategy"]]
        res = planner(self, row["source_job_id"], self.clock() + MIN_LEAD)
        if res["at"] is None:
            return res
        taken = self._taken(exclude=exclude)
        at = res["at"]
        for _ in range(60):
            day = at.astimezone(IST).date()
            if sum(1 for t in taken if t and t.astimezone(IST).date() == day) < cfg["daily_limit"]:
                return dict(res, at=at)
            at += timedelta(days=1)
        return {"at": None, "reason": "daily limit reached for the next 60 days"}

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
                    new_id = cur.lastrowid
                else:
                    new_id = None
            if new_id:
                try:
                    self.schedule_item(new_id)                    # its cycle's Hindi time (strategy plan)
                except PublishingError as exc:
                    self._update(new_id, "NOT_PLANNED", str(exc), last_error=str(exc)[:300])
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
        try:
            cycles = self.cycle_times()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hindi cycle times unavailable: %s", type(exc).__name__)
            cycles = []
        return {"items": items, "events": events, "schedule": self.schedule(), "gates": self.gates(),
                "cycle_times": cycles,
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

    def _taken(self, exclude: Optional[int] = None) -> List[datetime]:
        with self._connect() as c:
            rows = c.execute("SELECT id, scheduled_at_utc FROM hindi_publications WHERE scheduled_at_utc IS NOT NULL "
                             "AND state IN ('SCHEDULED','UPLOADING','UPLOADED','NEEDS_CHECK')").fetchall()
        return [_parse(r[1]) for r in rows if r[0] != exclude]

    def schedule_item(self, pid: int, at_utc: Optional[str] = None) -> Dict[str, Any]:
        """Schedules one video: at an individual timestamp (``at_utc``, at least 20 min ahead) or at the time the
        channel's strategy plans for it (MANUAL: its cycle's Hindi time). One time per video; daily limit kept."""
        row = self.get(pid)
        if row["state"] not in (DRAFT, FAILED, SCHEDULED):
            raise PublishingError(f"cannot schedule in state {row['state']}")
        problems = validate_metadata(row["title"], row["description"], row["tags"])
        if problems:
            raise PublishingError("fix the metadata first: " + "; ".join(problems), 400)
        loc = next((l for l in self.reg.localizations(TARGET, limit=200) if l["id"] == row["localization_id"]), None)
        if not loc or loc["status"] != "QC_PASSED":
            raise PublishingError("only a localization that passed Hindi QC can be scheduled")
        if at_utc:
            want = _parse(at_utc)
            if want is None or want < self.clock() + MIN_LEAD:
                raise PublishingError("the time must be at least 20 minutes from now", 400)
            day = want.astimezone(IST).date()
            if sum(1 for t in self._taken(exclude=pid) if t and t.astimezone(IST).date() == day) >= \
                    self.schedule()["daily_limit"]:
                raise PublishingError(f"the daily limit ({self.schedule()['daily_limit']}) is reached for that day")
            at, why = want, "individual time"
        else:
            p = self.plan(row, exclude=pid)
            if p["at"] is None:
                raise PublishingError(p["reason"])
            at, why = p["at"], p["reason"]
        if not self._update(pid, "SCHEDULED", {"at": _ts(at), "why": why}, expect=(DRAFT, FAILED, SCHEDULED),
                            state=SCHEDULED, scheduled_at_utc=_ts(at), last_error=None):
            raise PublishingError("the item changed; refresh and try again")
        return self.get(pid)

    def replan_missed(self) -> int:
        """A SCHEDULED video whose time is too close or past (not uploaded: gates closed, late localization) moves to
        the next valid occurrence of its planned time. Never published immediately."""
        now = self.clock()
        with self._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM hindi_publications WHERE state='SCHEDULED' AND "
                                               "scheduled_at_utc < ?", (_ts(now + PUBLISH_MIN_LEAD),))]
        n = 0
        for r in rows:
            p = self.plan(r, exclude=r["id"])
            if p["at"] is None:                                   # individual time without a plan: next day
                at = _parse(r["scheduled_at_utc"])
                while at < now + MIN_LEAD:
                    at += timedelta(days=1)
                p = {"at": at, "reason": "same time on the next day"}
            if self._update(r["id"], "REPLANNED", {"from": r["scheduled_at_utc"], "to": _ts(p["at"]),
                                                   "why": p["reason"]}, expect=(SCHEDULED,),
                            scheduled_at_utc=_ts(p["at"])):
                n += 1
        return n

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
        if not row["scheduled_at_utc"] or _parse(row["scheduled_at_utc"]) < self.clock() + PUBLISH_MIN_LEAD:
            return {"uploaded": False, "reason": "the planned time has passed: it is re-planned, never published now"}
        if not self._update(pid, "UPLOAD_STARTED", None, expect=(SCHEDULED,), state=UPLOADING):
            return {"uploaded": False, "reason": "not claimable (already uploading or changed)"}
        try:
            from googleapiclient.http import MediaFileUpload
            svc = self._service()
            self._check_identity(svc)
            publish_at = _parse(row["scheduled_at_utc"])
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
        self.replan_missed()
        gates = self.gates()
        if not gates["can_publish"]:
            return {"ran": False, "reasons": gates["reasons"]}
        now = self.clock()
        with self._connect() as c:
            due = c.execute("SELECT id FROM hindi_publications WHERE state='SCHEDULED' AND scheduled_at_utc <= ? "
                            "ORDER BY scheduled_at_utc, id LIMIT 1", (_ts(now + UPLOAD_LEAD),)).fetchone()
        res = self.upload(due[0]) if due else {"uploaded": False, "reason": "nothing due"}
        return {"ran": True, "upload": res, "thumbnails": self._thumbnails()}


PLANNERS: Dict[str, Callable[..., Dict[str, Any]]] = {"MANUAL": HindiPublisher._plan_manual}


def get_publisher() -> HindiPublisher:
    from backend.app.database.db import DB_PATH as _DB
    return HindiPublisher(str(_DB))
