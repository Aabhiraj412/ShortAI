"""Stage 3.32E Community draft service: generation, lifecycle, schedule, limits, kill switches, notifications.

Independent of video production: it reads production data read-only, writes only ``community_*`` tables and its own
folder (``backend/data/community``), and never touches cycles, recovery windows, jobs or publishing. It cannot
publish: no YouTube client is imported anywhere in ``backend.app.community`` and "approve" only records the
operator's decision on the draft.

Safety controls (all independent of the Stage 3.32B control plane, which may stay fully disabled):
* automation is OFF until an operator enables it; it can be paused and resumed;
* kill switches: the Community switch (dashboard), the file ``backend/data/COMMUNITY_DRAFTS_DISABLED`` and the
  global emergency file ``backend/data/AUTONOMY_DISABLED`` each block all generation and regeneration;
* one scheduled run per local date (UNIQUE idempotency key) and one active draft per date (UNIQUE partial index);
* bounded automatic retries (2 attempts, 10 minutes apart, inside the catch-up window), manual generations
  (3 per day), regenerations (10 per day) and Gemini calls (2 per day, off by default);
* a 120 s generation deadline and a 300 s run lease; interrupted runs are reconciled, never resumed blindly;
* bounded history and image retention.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app.community import content as C
from backend.app.community import images as I
from backend.app.community import schedule as SCH
from backend.app.community import store as S
from backend.app.community import validation as V
from backend.app.community.store import CommunityStore, Conflict, NotFound, StoreError

SCHEDULER_ACTOR = "system:community-scheduler"
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False, "paused": False, "generation_time": "10:00", "timezone": "Asia/Kolkata",
    "formats": list(C.FORMATS), "kill_switch": False, "text_mode": "template", "excluded_categories": [],
    "catch_up_hours": 12, "manual_daily_limit": 3, "max_auto_attempts": 2,
    "publishing_enabled": False,      # Stage 3.32F: only PublishingService.set_enabled changes it (fail closed)
}
ENABLED_SINCE_KEY = "_enabled_since"           # internal: when automation was last switched on (no retroactive runs)
GENERATION_TIMEOUT = 120.0
RUN_LEASE_SECONDS = 300
STALE_DRAFT_SECONDS = 600
MAX_AUTO_ATTEMPTS = 2                 # default; configurable 1-MAX_AUTO_ATTEMPTS_LIMIT as max_auto_attempts
MAX_AUTO_ATTEMPTS_LIMIT = 3
AUTO_RETRY_BACKOFF = timedelta(minutes=10)
REGENERATION_DAILY_LIMIT = 10
GEMINI_DAILY_LIMIT = 2
NOTIFICATION_DAILY_LIMIT = 10
MAX_EDIT_CHARS = 2000
DRAFT_ID = re.compile(r"^cdraft_[0-9a-f]{16}$")
IMAGE_ID = re.compile(r"^cimg_[0-9a-f]{16}$")
IMAGE_FILE = re.compile(r"^cimg_[0-9a-f]{16}\.jpg$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class CommunityError(StoreError):
    code = 400


class BadRequest(CommunityError):
    code = 400


class Forbidden(CommunityError):
    code = 403


class Blocked(CommunityError):
    code = 423


class LimitExceeded(CommunityError):
    code = 429


class NoSource(Exception):
    def __init__(self, explanation: Dict[str, Any]):
        super().__init__(explanation.get("reason") or "no eligible topic")
        self.explanation = explanation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tzdata_version() -> Optional[str]:
    try:
        import tzdata
        return str(getattr(tzdata, "IANA_VERSION", None))
    except Exception:
        return None


LOCAL_ACTOR = "local-dashboard"


def _validate_operator(operator: Any) -> str:
    """The identity recorded for an action. The dashboard has no user authentication: its only boundary is
    loopback + same-origin + the per-process CSRF token. When no operator label is supplied (the dashboard no longer
    asks for one), the action is attributed to the neutral identity ``local-dashboard``, meaning an unauthenticated
    request from the local dashboard, not a verified person. A label supplied through the API is validated and
    recorded as given (still unverified); reserved machine identities are refused."""
    if operator is None or (isinstance(operator, str) and not operator.strip()):
        return LOCAL_ACTOR
    from backend.app.autonomy.control_plane import ControlPlaneError, validate_operator
    try:
        return validate_operator(operator)
    except ControlPlaneError as exc:
        raise Forbidden(str(exc)) from exc


def _actor(op: str) -> str:
    return LOCAL_ACTOR if op == LOCAL_ACTOR else f"operator:{op}"


def _default_notifier(subject: str, message: str, severity: str) -> str:
    """Delivers to the existing 3.32B operator inbox only when its notifications level allows it (same gate as
    Stage 3.32D). Returns the delivery outcome; the dashboard record exists either way."""
    try:
        from backend.app.autonomy.control_plane import get_control_plane
        from backend.app.autonomy.registry import Level
        cp = get_control_plane()
        cfg, _err = cp._safe_config()
        if cp._effective(cfg, "notifications") < Level.RECOMMEND:
            return "dashboard"
        res = cp.submit("operator.notify", {"subject": subject[:120], "message": message[:2000], "severity": severity},
                        reason="Community draft notification", evidence={"kind": "community"},
                        proposed_by="system:community-drafts")
        return "inbox" if res.get("execution") and res["execution"].get("executed") else "inbox_queued"
    except Exception:
        return "dashboard"


class CommunityService:
    def __init__(self, db_path: Optional[str] = None, *, data_dir: Optional[Path] = None,
                 media_dir: Optional[Path] = None, clock: Optional[Callable[[], datetime]] = None,
                 gemini: Optional[Callable[[str], str]] = None, notifier: Optional[Callable[[str, str, str], str]] = None,
                 kill_file: Optional[Path] = None, global_kill_file: Optional[Path] = None,
                 source_reader: Optional[C.SourceReader] = None):
        from backend.app.core.paths import BACKEND_DIR, DATABASE_PATH, MEDIA_DIR
        self.db_path = str(db_path or DATABASE_PATH)
        base = BACKEND_DIR / "data"
        self.data_dir = Path(data_dir) if data_dir else base / "community"
        self.images_dir = self.data_dir / "images"
        self.media_dir = Path(media_dir) if media_dir else Path(MEDIA_DIR)
        self.kill_file = Path(kill_file) if kill_file else base / "COMMUNITY_DRAFTS_DISABLED"
        self.global_kill_file = Path(global_kill_file) if global_kill_file else base / "AUTONOMY_DISABLED"
        self.clock = clock or _utcnow
        self.gemini = gemini
        self.notifier = notifier or _default_notifier
        self.store = CommunityStore(self.db_path)
        self.reader = source_reader or C.SourceReader(self.db_path, self.media_dir)

    # -- configuration --------------------------------------------------------------------------------------
    def config(self) -> Dict[str, Any]:
        stored = {k: v for k, v in self.store.get_config().items() if k in DEFAULT_CONFIG}
        return {**DEFAULT_CONFIG, **stored}

    def kill_switches(self, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
        cfg = cfg or self.config()
        out = []
        if cfg.get("kill_switch"):
            out.append("community_dashboard_switch")
        if self.kill_file.exists():
            out.append("community_sentinel_file")
        if self.global_kill_file.exists():
            out.append("global_autonomy_sentinel_file")
        return out

    def _blocked(self, cfg: Dict[str, Any], automatic: bool) -> Optional[str]:
        ks = self.kill_switches(cfg)
        if ks:
            return "kill switch active: " + ", ".join(ks)
        if automatic and not cfg.get("enabled"):
            return "automatic generation is disabled"
        if automatic and cfg.get("paused"):
            return "automatic generation is paused"
        return None

    def update_config(self, operator: Optional[str], changes: Dict[str, Any]) -> Dict[str, Any]:
        op = _validate_operator(operator)
        clean: Dict[str, Any] = {}
        for k, v in (changes or {}).items():
            if v is None:
                continue
            if k in ("enabled", "paused", "kill_switch"):
                if not isinstance(v, bool):
                    raise BadRequest(f"{k} must be true or false")
                clean[k] = v
            elif k == "generation_time":
                if not isinstance(v, str) or not TIME_RE.match(v):
                    raise BadRequest("generation_time must be HH:MM (24-hour)")
                clean[k] = v
            elif k == "timezone":
                if not SCH.valid_timezone(v):
                    raise BadRequest("timezone must be an IANA timezone name such as America/New_York "
                                     "(fixed offsets such as Etc/GMT+5 or EST are not accepted)")
                clean[k] = v
            elif k == "formats":
                if not isinstance(v, list) or not v or any(f not in C.FORMATS for f in v) or len(set(v)) != len(v):
                    raise BadRequest(f"formats must be a non-empty list of: {', '.join(C.FORMATS)}")
                clean[k] = [f for f in C.FORMATS if f in v]
            elif k == "text_mode":
                if v not in ("template", "gemini"):
                    raise BadRequest("text_mode must be template or gemini")
                clean[k] = v
            elif k == "excluded_categories":
                if not isinstance(v, list) or len(v) > 20 or any(not isinstance(c, str) or not 0 < len(c) <= 60 for c in v):
                    raise BadRequest("excluded_categories must be up to 20 names of 1-60 characters")
                clean[k] = [c.strip() for c in v]
            elif k == "catch_up_hours":
                if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 23:
                    raise BadRequest("catch_up_hours must be 0-23")
                clean[k] = v
            elif k == "manual_daily_limit":
                if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 10:
                    raise BadRequest("manual_daily_limit must be 1-10")
                clean[k] = v
            elif k == "max_auto_attempts":
                if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_AUTO_ATTEMPTS_LIMIT:
                    raise BadRequest(f"max_auto_attempts must be 1-{MAX_AUTO_ATTEMPTS_LIMIT}")
                clean[k] = v
            else:
                raise BadRequest(f"unknown setting {k}")
        if not clean:
            raise BadRequest("no settings to change")
        before = self.config()
        changes = {k: {"from": before.get(k), "to": v} for k, v in clean.items() if before.get(k) != v}
        if not changes:
            return self.schedule_status()                    # nothing differs: no write, no duplicate audit entry
        now = self.clock()
        to_write = {k: clean[k] for k in changes}
        if changes.get("enabled", {}).get("to") is True:
            to_write[ENABLED_SINCE_KEY] = S.ts(now)          # windows that began earlier are not generated retroactively
        # Every value was validated above, so the write is all or nothing in one transaction.
        with self.store.tx() as conn:
            self.store.set_config(conn, to_write, _actor(op), now)
            self.store.event(conn, "CONFIG_CHANGED", _actor(op), now, detail={"changes": changes})
        return self.schedule_status()

    # -- schedule -------------------------------------------------------------------------------------------
    def _tz(self, cfg: Dict[str, Any]) -> ZoneInfo:
        try:
            return SCH.zone(cfg.get("timezone") or "Asia/Kolkata")
        except ValueError:
            return ZoneInfo("Asia/Kolkata")

    def _local_date(self, now: datetime, cfg: Dict[str, Any]) -> str:
        return now.astimezone(self._tz(cfg)).date().isoformat()

    def _enabled_since(self) -> Optional[datetime]:
        return S.parse_ts(self.store.get_config().get(ENABLED_SINCE_KEY))

    def _run_lookup(self, conn):
        def get(key: str) -> Optional[Dict[str, Any]]:
            if conn is None:
                return None
            run = self.store.get_run_by_key(conn, key)
            if run is not None:
                run["finished_at"] = S.parse_ts(run.get("finished_at_utc"))
            return run

        def active(post_date: str) -> bool:
            return conn is not None and self.store.active_draft_for_date(conn, post_date) is not None
        return get, active

    def _decisions(self, conn, now: datetime, cfg: Dict[str, Any]) -> List[SCH.Decision]:
        get, active = self._run_lookup(conn)
        # While automation is OFF, project "if enabled now": enabling never generates a window that already began.
        enabled_since = self._enabled_since() if cfg.get("enabled") else now
        return SCH.decide(now, cfg, get_run=get, has_active_draft=active, enabled_since=enabled_since,
                          retry_backoff=AUTO_RETRY_BACKOFF)

    def last_config_change(self) -> Optional[Dict[str, Any]]:
        if not self.store.tables_exist():
            return None
        with self.store.read() as conn:
            row = conn.execute("SELECT actor, created_at_utc, detail_json FROM community_events WHERE event='CONFIG_CHANGED' "
                               "ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        import json as _json
        return {"by": row["actor"], "at_utc": row["created_at_utc"],
                "changes": (_json.loads(row["detail_json"] or "{}").get("changes") or {})}

    def schedule_status(self) -> Dict[str, Any]:
        cfg = self.config()
        now = self.clock()
        tz = self._tz(cfg)
        today = now.astimezone(tz).date()
        configured = self.store.tables_exist()
        if configured:
            with self.store.read() as conn:
                decisions = self._decisions(conn, now, cfg)
        else:
            decisions = self._decisions(None, now, cfg)
        would_run_at, would_state = SCH.next_start(now, cfg, decisions)
        blocked = self._blocked(cfg, automatic=True)
        if blocked:
            nxt, state = None, "blocked"
        else:
            nxt, state = would_run_at, would_state
        runs = self.store.list_runs(limit=30)
        today_run = next((r for r in runs if r["idem_key"] == f"community:scheduled:{today.isoformat()}"), None)
        last_success = next((r for r in runs if r["status"] == S.RUN_SUCCEEDED), None)
        last_run = runs[0] if runs else None
        last_draft = self.store.get_draft(last_run["draft_id"]) if last_run and last_run.get("draft_id") else None
        state_text = {
            "blocked": f"blocked: {blocked}", "waiting": "waiting for today's target time",
            "due": "due now: the next 5-minute check generates the draft",
            "running": "a generation is running", "retry_pending": "a failed attempt will be retried",
            "completed": "today's scheduled draft is done; next run tomorrow",
            "missed": "today's window passed without a run; next run tomorrow",
            "not_enabled_yet": "today's target time passed before automation was enabled; first run tomorrow",
        }.get(state, state)
        return {"config": cfg, "kill_switches": self.kill_switches(cfg), "blocked_reason": blocked,
                "state": state, "state_text": state_text,
                "next_run_local": nxt.astimezone(tz).isoformat() if nxt else None,
                "next_run_utc": S.ts(nxt) if nxt else None,
                "next_run_if_enabled_local": would_run_at.astimezone(tz).isoformat() if would_run_at else None,
                "next_run_if_enabled_utc": S.ts(would_run_at) if would_run_at else None,
                "timezone": str(tz), "timezone_details": SCH.tz_details(str(tz), now),
                "dst_notes": SCH.dst_notes(cfg["generation_time"], tz, now)[:4],
                "today": today.isoformat(), "today_run": today_run, "last_run": last_run,
                "last_result": ({"run_status": last_run["status"], "trigger": last_run["trigger"],
                                 "at_utc": last_run.get("finished_at_utc") or last_run.get("started_at_utc"),
                                 "error": last_run.get("error"), "draft_id": last_run.get("draft_id"),
                                 "draft_status": (last_draft or {}).get("status"),
                                 "validation_status": (last_draft or {}).get("validation_status")}
                                if last_run else None),
                "last_successful_run": last_success, "last_config_change": self.last_config_change(),
                "enabled_since_utc": S.ts(self._enabled_since()) if self._enabled_since() else None,
                "windows": [{"date": d.day.isoformat(), "state": d.state, "start_utc": S.ts(d.start_utc),
                             "start_local": d.start_utc.astimezone(tz).isoformat(), "end_utc": S.ts(d.end_utc),
                             "dst_note": d.note} for d in decisions],
                "tick_interval_minutes": 5, "configured": configured,
                "youtube_publishing": {"available": False, "note": "Publishing time is a future capability "
                                       "(Stage 3.32F). Approved drafts are posted manually in YouTube Studio."}}

    def preview(self, timezone_name: str, generation_time: str, catch_up_hours: int = 12) -> Dict[str, Any]:
        """What a schedule WOULD do, without saving anything: next three target instants and DST notes."""
        if not SCH.valid_timezone(timezone_name):
            raise BadRequest("timezone must be an IANA timezone name such as America/New_York")
        if not isinstance(generation_time, str) or not TIME_RE.match(generation_time):
            raise BadRequest("generation_time must be HH:MM (24-hour)")
        if not isinstance(catch_up_hours, int) or not 0 <= catch_up_hours <= 23:
            raise BadRequest("catch_up_hours must be 0-23")
        now = self.clock()
        tz = SCH.zone(timezone_name)
        today = now.astimezone(tz).date()
        upcoming = []
        for i in range(0, 5):
            r, end = SCH.window(today + timedelta(days=i), generation_time, tz, catch_up_hours)
            if r.start_utc > now:
                upcoming.append({"date": r.day.isoformat(), "local": r.local(tz).isoformat(), "utc": S.ts(r.start_utc),
                                 "catch_up_until_local": end.astimezone(tz).isoformat(), "dst_note": r.note})
            if len(upcoming) == 3:
                break
        return {"timezone": SCH.tz_details(timezone_name, now), "generation_time": generation_time,
                "upcoming": upcoming, "dst_notes": SCH.dst_notes(generation_time, tz, now)[:4]}

    def timezones(self) -> Dict[str, Any]:
        return {"presets": SCH.presets(self.clock()), "zones": list(SCH.timezone_ids()),
                "tzdata_version": _tzdata_version()}

    # -- maintenance ------------------------------------------------------------------------------------------
    def recover_stale(self) -> Dict[str, int]:
        """Interrupted work is reconciled, never resumed: expired run leases become ABANDONED, and drafts stuck in
        an in-progress state become CANCELLED (when their run was abandoned) or the matching failed state."""
        out = {"runs": 0, "drafts": 0}
        if not self.store.tables_exist():
            return out
        now = self.clock()
        with self.store.tx() as conn:
            for run in self.store.expired_leases(conn, now):
                self.store.finish_run(conn, run["id"], S.RUN_ABANDONED, now, error="run lease expired (process stopped "
                                      "or timed out); the draft was cancelled and the run is not resumed")
                self.store.event(conn, "RUN_ABANDONED", SCHEDULER_ACTOR, now, run_id=run["id"])
                out["runs"] += 1
                for d in conn.execute("SELECT id FROM community_drafts WHERE run_id=? AND status IN (?,?,?)",
                                      (run["id"], *S.IN_PROGRESS)).fetchall():
                    self.store.transition(conn, d[0], S.CANCELLED, now, status_reason="generation run interrupted")
                    self.store.event(conn, "DRAFT_CANCELLED", SCHEDULER_ACTOR, now, draft_id=d[0], run_id=run["id"],
                                     detail={"reason": "run interrupted"})
                    out["drafts"] += 1
            cutoff = S.ts(now - timedelta(seconds=STALE_DRAFT_SECONDS))
            stuck = conn.execute("SELECT id, status FROM community_drafts WHERE status IN (?,?,?) AND updated_at_utc < ? "
                                 "LIMIT 50", (*S.IN_PROGRESS, cutoff)).fetchall()
            for did, status in stuck:
                target = {S.GENERATING: S.GENERATION_FAILED, S.IMAGE_PENDING: S.IMAGE_FAILED,
                          S.VALIDATING: S.VALIDATION_FAILED}[status]
                self.store.transition(conn, did, target, now, status_reason="interrupted; regenerate or archive")
                self.store.event(conn, "DRAFT_INTERRUPTED", SCHEDULER_ACTOR, now, draft_id=did, detail={"from": status})
                out["drafts"] += 1
        return out

    def prune(self) -> Dict[str, Any]:
        res = self.store.prune(self.clock())
        removed = 0
        for draft_id, filename in res.pop("image_files", []):
            removed += self._remove_image_file(draft_id, filename)
        for d in (self.images_dir.iterdir() if self.images_dir.is_dir() else []):
            if d.is_dir() and not d.is_symlink() and DRAFT_ID.match(d.name) and self.store.get_draft(d.name) is None:
                for f in d.iterdir():
                    if f.is_file() and not f.is_symlink() and (IMAGE_FILE.match(f.name) or f.name.endswith(".part")):
                        try:
                            f.unlink()
                            removed += 1
                        except OSError:
                            pass
                try:
                    d.rmdir()
                except OSError:
                    pass
        res["image_files_removed"] = removed
        return res

    def _remove_image_file(self, draft_id: str, filename: str) -> int:
        if not (DRAFT_ID.match(draft_id or "") and IMAGE_FILE.match(filename or "")):
            return 0
        p = self.images_dir / draft_id / filename
        try:
            if p.is_file() and not p.is_symlink() and I.inside(p, self.images_dir):
                p.unlink()
                return 1
        except OSError:
            pass
        return 0

    # -- scheduled generation ---------------------------------------------------------------------------------
    def tick(self) -> Dict[str, Any]:
        """Called every 5 minutes by the existing scheduler daemon. Cheap when nothing is due; never raises.
        Before an operator has configured Stage 3.32E it does not touch the database at all."""
        try:
            return self._tick()
        except Exception as exc:            # noqa: BLE001 - must never disturb the production scheduler
            return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def _tick(self) -> Dict[str, Any]:
        if not self.store.tables_exist():
            return {"status": "not_configured"}
        cfg = self.config()
        recovered = self.recover_stale()
        self._daily_maintenance(cfg)
        blocked = self._blocked(cfg, automatic=True)
        if blocked:
            return {"status": "blocked", "reason": blocked, "recovered": recovered}
        now = self.clock()
        tz = self._tz(cfg)
        missed: List[str] = []
        outcome: Dict[str, Any] = {}
        run = None
        # One transaction: the decision is re-made on fresh rows while holding the write lock, so two ticks, a
        # manual generation or a second process can never claim the same local date twice.
        with self.store.tx() as conn:
            for d in self._decisions(conn, now, cfg):
                post_date = d.day.isoformat()
                if d.state == SCH.MISSED:
                    r = self.store.insert_run(conn, idem_key=d.key, post_date=post_date, trigger="scheduled",
                                              requested_by=SCHEDULER_ACTOR, now=now, lease_seconds=RUN_LEASE_SECONDS)
                    self.store.finish_run(conn, r["id"], S.RUN_SKIPPED, now,
                                          error=f"missed: nothing ran between {d.start_utc.astimezone(tz).strftime('%H:%M %Z')}"
                                                f" and the end of the {cfg['catch_up_hours']} h catch-up window")
                    self.store.event(conn, "RUN_MISSED", SCHEDULER_ACTOR, now, run_id=r["id"], detail={"date": post_date})
                    missed.append(post_date)
                    outcome = {"status": "missed", "run_id": r["id"]}
                elif d.state == SCH.RETRY:
                    self.store.restart_run(conn, d.run["id"], d.run["status"], now, RUN_LEASE_SECONDS)
                    self.store.event(conn, "RUN_RETRY", SCHEDULER_ACTOR, now, run_id=d.run["id"],
                                     detail={"attempt": int(d.run["attempts"]) + 1, "date": post_date})
                    run = self.store.get_run(d.run["id"], conn)
                    break
                elif d.state == SCH.DUE:
                    if self.store.active_draft_for_date(conn, post_date) is not None or \
                            self.store.running_run_for_date(conn, post_date, now) is not None:
                        r = self.store.insert_run(conn, idem_key=d.key, post_date=post_date, trigger="scheduled",
                                                  requested_by=SCHEDULER_ACTOR, now=now, lease_seconds=RUN_LEASE_SECONDS)
                        self.store.finish_run(conn, r["id"], S.RUN_SKIPPED, now,
                                              error="a draft for this date already exists or is being generated")
                        self.store.event(conn, "RUN_SKIPPED", SCHEDULER_ACTOR, now, run_id=r["id"])
                        outcome = {"status": "skipped", "run_id": r["id"]}
                        continue
                    run = self.store.insert_run(conn, idem_key=d.key, post_date=post_date, trigger="scheduled",
                                                requested_by=SCHEDULER_ACTOR, now=now, lease_seconds=RUN_LEASE_SECONDS)
                    break
                elif d.state in (SCH.RUNNING, SCH.RETRY_PENDING, SCH.COMPLETED) and d.day == now.astimezone(tz).date():
                    outcome = outcome or {"status": "done", "run_status": (d.run or {}).get("status"),
                                          "run_id": (d.run or {}).get("id")}
                elif d.state == SCH.NOT_ENABLED_YET and d.day == now.astimezone(tz).date():
                    outcome = outcome or {"status": "not_due", "reason": "today's window began before automation was enabled"}
                elif d.state == SCH.WAITING and d.day == now.astimezone(tz).date():
                    outcome = outcome or {"status": "not_due", "next_run_local": d.start_utc.astimezone(tz).isoformat()}
        for post_date in missed:                          # outside the transaction; each missed date is recorded once
            self._notify("generation_missed", "Community draft missed",
                         f"No Community draft was generated for {post_date}: the scheduler was not running during "
                         "the generation window. Use Generate now to create one.", "medium", None)
        if run is None:
            return outcome or {"status": "not_due"}
        res = self._run_generation(run, SCHEDULER_ACTOR)
        return {**res, "status": "generated", "draft_status": res["status"]}

    def _daily_maintenance(self, cfg: Dict[str, Any]) -> None:
        today = self._local_date(self.clock(), cfg)
        if self.store.get_config().get("_maintenance_date") == today:
            return
        self.prune()
        with self.store.tx() as conn:
            self.store.set_config(conn, {"_maintenance_date": today}, SCHEDULER_ACTOR, self.clock())

    # -- manual generation -----------------------------------------------------------------------------------
    def generate_manual(self, operator: Optional[str] = None) -> Dict[str, Any]:
        op = _validate_operator(operator)
        cfg = self.config()
        blocked = self._blocked(cfg, automatic=False)
        if blocked:
            raise Blocked(blocked)
        self.store.ensure_schema()
        self.recover_stale()
        now = self.clock()
        tz = self._tz(cfg)
        local = now.astimezone(tz)
        post_date = local.date().isoformat()
        midnight = datetime(local.year, local.month, local.day, tzinfo=tz)
        with self.store.tx() as conn:
            if self.store.count_runs_since(conn, midnight, "manual") >= int(cfg["manual_daily_limit"]):
                raise LimitExceeded(f"manual generation limit reached ({cfg['manual_daily_limit']} per day)")
            if self.store.running_run_for_date(conn, post_date, now) is not None:
                raise Conflict("a generation for today is already running")
            if self.store.active_draft_for_date(conn, post_date) is not None:
                raise Conflict("today already has an active draft; regenerate, reject or archive it first")
            run = self.store.insert_run(conn, idem_key=f"community:manual:{post_date}:{S.new_id('m')}",
                                        post_date=post_date, trigger="manual", requested_by=_actor(op), now=now,
                                        lease_seconds=RUN_LEASE_SECONDS)
            self.store.event(conn, "MANUAL_GENERATION_REQUESTED", _actor(op), now, run_id=run["id"])
        return self._run_generation(run, _actor(op))

    # -- generation pipeline -----------------------------------------------------------------------------------
    def _recent(self, conn, now: datetime) -> List[Dict[str, Any]]:
        out = self.store.recent_drafts(conn, now - timedelta(days=V.DUPLICATE_DAYS))
        for d in out:
            d["created_at"] = S.parse_ts(d.get("created_at_utc"))
        return out

    def _check_deadline(self, deadline: float) -> None:
        import time
        if time.monotonic() > deadline:
            raise TimeoutError(f"generation exceeded {int(GENERATION_TIMEOUT)} s")

    def _run_generation(self, run: Dict[str, Any], actor: str) -> Dict[str, Any]:
        import time
        t0 = time.monotonic()
        deadline = t0 + GENERATION_TIMEOUT
        metrics: Dict[str, Any] = {}
        now = self.clock()
        try:
            with self.store.tx() as conn:
                draft = self.store.insert_draft(conn, run_id=run["id"], post_date=run["post_date"], now=now)
                self.store.event(conn, "GENERATION_STARTED", actor, now, draft_id=draft["id"], run_id=run["id"],
                                 detail={"trigger": run["trigger"]})
        except Conflict as exc:
            with self.store.tx() as conn:
                self.store.finish_run(conn, run["id"], S.RUN_SKIPPED, self.clock(), error=str(exc))
            return {"run_id": run["id"], "draft_id": None, "status": "skipped", "reason": str(exc)}
        did = draft["id"]
        try:
            self._text_stage(did, actor, deadline, metrics)
            self._check_deadline(deadline)
            self._image_stage(did, actor, None, deadline, metrics)
            self._check_deadline(deadline)
            final = self._validation_stage(did, actor, metrics)
            error = None if final["status"] == S.READY_FOR_REVIEW else "draft failed validation"
        except NoSource as exc:
            final, error = self._fail(did, actor, str(exc), {"selection": exc.explanation}), str(exc)
        except I.ImageError as exc:
            final, error = self._fail(did, actor, f"image: {exc}"), f"image: {exc}"
        except TimeoutError as exc:
            final, error = self._fail(did, actor, str(exc)), str(exc)
        except Exception as exc:                         # noqa: BLE001 - recorded, never raised into the scheduler
            from backend.app.autonomy.control_plane import safe_error
            final, error = self._fail(did, actor, safe_error(exc)), safe_error(exc)
        metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
        with self.store.tx() as conn:
            self.store.finish_run(conn, run["id"], S.RUN_SUCCEEDED if error is None else S.RUN_FAILED, self.clock(),
                                  draft_id=did, error=error, metrics=metrics)
        if error is None:
            vs = final.get("validation_status")
            self._notify("draft_ready", "Community draft ready for review",
                         f"The Community draft for {final['post_date']} ({C.FORMAT_LABELS.get(final.get('format'), '')}: "
                         f"{(final.get('topic_title') or '')[:80]}) is ready. Validation: {vs}.",
                         "info" if vs == S.VALID else "low", did)
        else:
            self._notify("generation_failed", "Community draft generation failed",
                         f"The Community draft for {run['post_date']} could not be completed: {error[:300]}", "medium", did)
        return {"run_id": run["id"], "draft_id": did, "status": final["status"], "error": error, "metrics": metrics,
                "draft": self.detail(did)}

    def _fail(self, did: str, actor: str, reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = self.clock()
        with self.store.tx() as conn:
            cur = self.store.get_draft(did, conn)
            target = {S.GENERATING: S.GENERATION_FAILED, S.IMAGE_PENDING: S.IMAGE_FAILED,
                      S.VALIDATING: S.VALIDATION_FAILED}.get(cur["status"])
            if target is None:
                return cur
            fields: Dict[str, Any] = {"status_reason": reason[:500]}
            if extra:
                fields["generation"] = {**(cur.get("generation") or {}), **extra}
            d = self.store.transition(conn, did, target, now, **fields)
            self.store.event(conn, target, actor, now, draft_id=did, detail={"reason": reason[:500]})
            return d

    def _gemini_allowed(self, cfg: Dict[str, Any]) -> bool:
        if cfg.get("text_mode") != "gemini":
            return False
        now = self.clock()
        tz = self._tz(cfg)
        local = now.astimezone(tz)
        since = S.ts(datetime(local.year, local.month, local.day, tzinfo=tz))
        with self.store.read() as conn:
            n = conn.execute("SELECT COUNT(*) FROM community_events WHERE event='GEMINI_CALL' AND created_at_utc >= ?",
                             (since,)).fetchone()[0]
        return n < GEMINI_DAILY_LIMIT

    def _text_stage(self, did: str, actor: str, deadline: float, metrics: Dict[str, Any], *,
                    keep_source: bool = False, attempt: int = 0) -> Dict[str, Any]:
        import time
        cfg = self.config()
        now = self.clock()
        t = time.monotonic()
        draft = self.store.get_draft(did)
        sources = self.reader.sources(now)
        metrics["sources_ms"] = int((time.monotonic() - t) * 1000)
        self._check_deadline(deadline)
        with self.store.read() as conn:
            recent = [d for d in self._recent(conn, now) if d["id"] != did]
        src, fmt, expl = None, None, {}
        if keep_source and draft.get("source_ref"):
            src = next((s for s in sources if s.ref == draft["source_ref"]), None)
            if src is not None:
                formats = [f for f in cfg["formats"] if C.feasible(f, src)]
                if draft.get("format") in formats and len(formats) > 1 and attempt % 2 == 1:
                    formats.remove(draft["format"])          # every other regeneration also changes the format
                fmt = (draft.get("format") if draft.get("format") in formats else (formats[0] if formats else None))
                expl = {"kept_source": True, "formats_available": formats}
        if src is None or fmt is None:
            src, fmt, expl = C.select(sources, recent, cfg["formats"], now,
                                      channel_categories=self.reader.channel_categories(),
                                      excluded_categories=cfg.get("excluded_categories") or [])
        if src is None:
            raise NoSource(expl)
        t = time.monotonic()
        public_ids = [src.video_id] if src.video_public and src.video_id else []
        gen = None
        gemini_called = False
        if self._gemini_allowed(cfg):
            gen = self.gemini or C.gemini_generate

            def counted(prompt: str, _g=gen) -> str:
                nonlocal gemini_called
                gemini_called = True
                return _g(prompt)
            gen = counted

        def check(text: str, comp: Dict[str, Any]) -> bool:
            checks = V.validate_text(text, corpus=src.corpus + "\n" + comp.get("extra_corpus", ""),
                                     allowed_numbers=comp.get("allowed_numbers", []), public_video_ids=public_ids,
                                     recent=recent, topic_key=src.topic_key, now=now, draft_id=did)
            return V.outcome(checks) in (S.VALID, S.VALID_WITH_WARNINGS)

        seed = f"{draft['post_date']}:{src.ref}:{fmt}:{attempt}"
        comp = C.write_text(src, fmt, recent, seed, mode="gemini" if gen else "template", generate=gen, check=check)
        if comp["text"] and C.fingerprint(comp["text"]) == draft.get("text_fingerprint"):
            for alt in range(1, 4):                           # regeneration must change the wording
                comp = C.write_text(src, fmt, recent, seed + f":alt{alt}", mode="template")
                if C.fingerprint(comp["text"]) != draft.get("text_fingerprint"):
                    break
        metrics["text_ms"] = int((time.monotonic() - t) * 1000)
        generation = {
            "format_label": C.FORMAT_LABELS[fmt], "selection": expl, "source": src.summary(),
            "corpus": (src.corpus + "\n" + comp.get("extra_corpus", ""))[:8000],
            "allowed_numbers": comp.get("allowed_numbers", []), "public_video_ids": public_ids,
            "text_mode": comp.get("text_mode"), "gemini": comp.get("gemini"), "answer": comp.get("answer"),
            "answer_value": comp.get("answer_value"), "fact_used": comp.get("fact_used"), "seed": seed,
            "credit_line": None, "generated_at_utc": S.ts(now),
        }
        now2 = self.clock()
        with self.store.tx() as conn:
            if gemini_called:
                self.store.event(conn, "GEMINI_CALL", actor, now2, draft_id=did,
                                 detail={"used": bool((comp.get("gemini") or {}).get("used"))})
            d = self.store.transition(conn, did, S.IMAGE_PENDING, now2, expect_status=(S.GENERATING,),
                                      text=comp["text"], text_fingerprint=C.fingerprint(comp["text"]), format=fmt,
                                      topic_key=src.topic_key, topic_title=src.title[:200], category=src.category[:80],
                                      source_kind=src.kind, source_ref=src.ref, source_video_id=public_ids[0] if public_ids else None,
                                      opening_key=comp["opening_key"], question_key=comp["question_key"],
                                      cta_key=comp["cta_key"], generation=generation, status_reason=None)
            self.store.event(conn, "TEXT_GENERATED", actor, now2, draft_id=did,
                             detail={"format": fmt, "topic": src.title[:120], "source": src.ref, "mode": comp.get("text_mode")})
        return d

    # -- images ------------------------------------------------------------------------------------------------
    def image_options(self, draft_id: str) -> Dict[str, Any]:
        d = self._draft(draft_id)
        used = set()
        if self.store.tables_exist():
            with self.store.read() as conn:
                used = {r["source_ref"] for r in self.store.recent_image_hashes(conn, self.clock() - timedelta(days=V.DUPLICATE_DAYS),
                                                                                  exclude_draft=draft_id)}
        opts = []
        if d.get("source_kind") == "production":
            for c in I.licensed_candidates(self.media_dir, d.get("source_ref") or ""):
                opts.append({"choice": c["choice"], "title": c["title"], "creator": c["creator"], "licence": c["licence"],
                             "repository": c["repository"], "used_by_another_draft": c["sha256"] in used,
                             "attribution_required": c["attribution_required"]})
        opts.append({"choice": "title_card", "title": "Typographic title card (generated locally)", "creator": "CrazzyDocs",
                     "licence": "Own work", "repository": "local", "used_by_another_draft": False,
                     "attribution_required": False})
        return {"draft_id": draft_id, "options": opts}

    def _image_stage(self, did: str, actor: str, choice: Optional[str], deadline: float, metrics: Dict[str, Any],
                     exclude_refs: Sequence[str] = ()) -> Dict[str, Any]:
        import time
        t = time.monotonic()
        draft = self.store.get_draft(did)
        now = self.clock()
        with self.store.read() as conn:
            used = {r["source_ref"] for r in self.store.recent_image_hashes(conn, now - timedelta(days=V.DUPLICATE_DAYS),
                                                                            exclude_draft=did)}
        candidates = I.licensed_candidates(self.media_dir, draft.get("source_ref") or "") \
            if draft.get("source_kind") == "production" else []
        if choice and choice != "title_card":
            candidates = [c for c in candidates if c["choice"] == choice]
            if not candidates:
                raise BadRequest("that image choice is not available for this draft")
        elif choice == "title_card":
            candidates = []
        else:
            candidates = [c for c in candidates if c["sha256"] not in used and c["sha256"] not in exclude_refs]
        image_id = S.new_id("cimg")
        out_dir = self.images_dir / did
        rendered, chosen, notes = None, None, []
        for c in candidates:
            self._check_deadline(deadline)
            try:
                rendered = I.render_asset_card(c["path"], out_dir, image_id)
                chosen = c
                break
            except I.ImageError as exc:
                notes.append(f"{c['original_filename']}: {exc}")
        if rendered is None:
            if "title_card" in exclude_refs and not choice:
                raise I.ImageError("no other licensed image is available for this topic")
            rendered = I.render_title_card(draft.get("topic_title") or "", draft.get("category") or "", out_dir, image_id)
        try:
            from PIL import __version__ as pil_version
        except Exception:
            pil_version = "unknown"
        if chosen:
            record = {"id": image_id, "draft_id": did, "method": "licensed_asset", "source_kind": "production_asset",
                      "source_ref": chosen["sha256"], "licence": chosen["licence"], "attribution": chosen["attribution"],
                      "provenance": {"method": "licensed_asset", "source": chosen["repository"] or "attribution manifest",
                                     "production_job": draft.get("source_ref"), "asset_id": chosen["asset_id"],
                                     "original_filename": chosen["original_filename"], "original_sha256": chosen["sha256"],
                                     "title": chosen["title"], "creator": chosen["creator"], "licence": chosen["licence"],
                                     "licence_url": chosen["licence_url"], "source_url": chosen["source_url"],
                                     "processing": f"cover-cropped to 1080x1080, no text added (Pillow {pil_version})",
                                     "skipped": notes[:5]}}
        else:
            record = {"id": image_id, "draft_id": did, "method": "title_card", "source_kind": "generated",
                      "source_ref": "title_card", "licence": "Own work", "attribution": "",
                      "provenance": {"method": "title_card", "source": "generated locally",
                                     "generator": f"Pillow {pil_version}",
                                     "text_on_image": [draft.get("category") or "", draft.get("topic_title") or "", "CrazzyDocs"],
                                     "reason": "no unused licensed image was available" if not choice else "operator choice",
                                     "skipped": notes[:5]}}
        record.update({k: rendered[k] for k in ("filename", "sha256", "dhash", "width", "height", "bytes", "mime")})
        metrics["image_ms"] = int((time.monotonic() - t) * 1000)
        gen = dict(draft.get("generation") or {})
        text = self._apply_credit(draft.get("text") or "", gen.get("credit_line"), record["attribution"] or None)
        gen["credit_line"] = record["attribution"] or None
        now2 = self.clock()
        with self.store.tx() as conn:
            self.store.insert_image(conn, record, now2)
            d = self.store.transition(conn, did, S.VALIDATING, now2, expect_status=(S.IMAGE_PENDING,), image_id=image_id,
                                      text=text, text_fingerprint=C.fingerprint(text), generation=gen)
            self.store.event(conn, "IMAGE_ATTACHED", actor, now2, draft_id=did,
                             detail={"image_id": image_id, "method": record["method"], "licence": record["licence"],
                                     "source": record["provenance"].get("source")})
        return d

    @staticmethod
    def _apply_credit(text: str, old: Optional[str], new: Optional[str]) -> str:
        t = text
        if old and old in t:
            t = t.replace("\n\n" + old, "").replace(old, "").rstrip()
        if new and new not in t:
            t = (t.rstrip() + "\n\n" + new).strip()
        return t

    def _image_path(self, rec: Optional[Dict[str, Any]]) -> Optional[Path]:
        if not rec or not DRAFT_ID.match(rec.get("draft_id") or "") or not IMAGE_FILE.match(rec.get("filename") or ""):
            return None
        return self.images_dir / rec["draft_id"] / rec["filename"]

    # -- validation -------------------------------------------------------------------------------------------
    def _validation_stage(self, did: str, actor: str, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import time
        t = time.monotonic()
        now = self.clock()
        draft = self.store.get_draft(did)
        gen = draft.get("generation") or {}
        img = self.store.get_image(draft["image_id"]) if draft.get("image_id") else None
        with self.store.read() as conn:
            recent = self._recent(conn, now)
            recent_imgs = self.store.recent_image_hashes(conn, now - timedelta(days=V.DUPLICATE_DAYS), exclude_draft=did)
        tc = V.validate_text(draft.get("text") or "", corpus=gen.get("corpus") or "",
                             allowed_numbers=gen.get("allowed_numbers") or [],
                             public_video_ids=gen.get("public_video_ids") or [], recent=recent,
                             topic_key=draft.get("topic_key") or "", now=now, draft_id=did,
                             opening_key=draft.get("opening_key"), question_key=draft.get("question_key"),
                             cta_key=draft.get("cta_key"), credit_line=gen.get("credit_line"),
                             topic_title=draft.get("topic_title") or "")
        ic = V.validate_image(img, self._image_path(img), images_root=self.images_dir, text=draft.get("text") or "",
                              recent_images=recent_imgs)
        summ = V.summary(tc, ic)
        summ["validated_at_utc"] = S.ts(now)
        target = S.VALIDATION_FAILED if summ["status"] == S.V_REJECTED else S.READY_FOR_REVIEW
        if metrics is not None:
            metrics["validation_ms"] = int((time.monotonic() - t) * 1000)
        with self.store.tx() as conn:
            if img:
                self.store.update_image_validation(conn, img["id"], {"status": V.outcome(ic), "checks": ic})
            d = self.store.transition(conn, did, target, now, expect_status=(S.VALIDATING,), validation_status=summ["status"],
                                      validation=summ, status_reason=None if target == S.READY_FOR_REVIEW else
                                      "; ".join(c["check"] + ": " + c["detail"] for c in summ["errors"])[:500])
            self.store.event(conn, "VALIDATED", actor, now, draft_id=did,
                             detail={"status": summ["status"], "errors": [c["check"] for c in summ["errors"]],
                                     "reviews": [c["check"] for c in summ["reviews"]],
                                     "warnings": [c["check"] for c in summ["warnings"]]})
        return d

    # -- operator actions -------------------------------------------------------------------------------------
    def _draft(self, draft_id: str) -> Dict[str, Any]:
        if not isinstance(draft_id, str) or not DRAFT_ID.match(draft_id):
            raise NotFound("draft not found")
        d = self.store.get_draft(draft_id)
        if d is None:
            raise NotFound("draft not found")
        return d

    def _assert_not_published(self, draft_id: str) -> None:
        """A draft that was published, is being published, or was reported posted can no longer change."""
        from backend.app.community import publishing as PUB
        if not PUB.tables_exist(self.db_path):
            return
        with self.store.read() as conn:
            why = PUB.lock_reason(conn, draft_id)
        if why:
            raise Conflict(why)

    def _regeneration_quota(self) -> None:
        cfg = self.config()
        tz = self._tz(cfg)
        local = self.clock().astimezone(tz)
        since = S.ts(datetime(local.year, local.month, local.day, tzinfo=tz))
        with self.store.read() as conn:
            n = conn.execute("SELECT COUNT(*) FROM community_events WHERE event IN ('TEXT_REGENERATION_REQUESTED', "
                             "'IMAGE_REGENERATION_REQUESTED') AND created_at_utc >= ?", (since,)).fetchone()[0]
        if n >= REGENERATION_DAILY_LIMIT:
            raise LimitExceeded(f"regeneration limit reached ({REGENERATION_DAILY_LIMIT} per day)")

    def edit(self, draft_id: str, operator: str, version: int, text: str) -> Dict[str, Any]:
        op = _validate_operator(operator)
        self._draft(draft_id)
        self._assert_not_published(draft_id)
        if not isinstance(text, str) or len(text) > MAX_EDIT_CHARS:
            raise BadRequest(f"text must be at most {MAX_EDIT_CHARS} characters")
        clean = V.sanitize_text(text)
        if not clean:
            raise BadRequest("text must not be empty")
        now = self.clock()
        with self.store.tx() as conn:
            cur = self.store.get_draft(draft_id, conn)
            if clean == (cur.get("text") or ""):
                raise BadRequest("the text is unchanged")
            self.store.transition(conn, draft_id, S.VALIDATING, now,
                                  expect_status=(S.READY_FOR_REVIEW, S.APPROVED, S.VALIDATION_FAILED),
                                  expect_version=version, text=clean, text_fingerprint=C.fingerprint(clean),
                                  decided_by=None, decided_at_utc=None, decision_reason=None, warnings_acknowledged=0)
            self.store.event(conn, "TEXT_EDITED", _actor(op), now, draft_id=draft_id,
                             detail={"before": cur.get("text"), "after": clean, "previous_status": cur["status"],
                                     "approval_withdrawn": cur["status"] == S.APPROVED})
        self._validation_stage(draft_id, _actor(op))
        return self.detail(draft_id)

    def regenerate_text(self, draft_id: str, operator: str, version: int) -> Dict[str, Any]:
        op = _validate_operator(operator)
        cur = self._draft(draft_id)
        self._assert_not_published(draft_id)
        blocked = self._blocked(self.config(), automatic=False)
        if blocked:
            raise Blocked(blocked)
        self._regeneration_quota()
        import time
        now = self.clock()
        with self.store.tx() as conn:
            self.store.transition(conn, draft_id, S.GENERATING, now,
                                  expect_status=(S.READY_FOR_REVIEW, S.APPROVED, S.VALIDATION_FAILED, S.GENERATION_FAILED),
                                  expect_version=version, decided_by=None, decided_at_utc=None, decision_reason=None,
                                  warnings_acknowledged=0)
            self.store.event(conn, "TEXT_REGENERATION_REQUESTED", _actor(op), now, draft_id=draft_id,
                             detail={"previous_text": cur.get("text"), "previous_status": cur["status"]})
            attempt = conn.execute("SELECT COUNT(*) FROM community_events WHERE draft_id=? AND "
                                   "event='TEXT_REGENERATION_REQUESTED'", (draft_id,)).fetchone()[0]
        deadline = time.monotonic() + GENERATION_TIMEOUT
        metrics: Dict[str, Any] = {}
        actor = _actor(op)
        try:
            d = self._text_stage(draft_id, actor, deadline, metrics, keep_source=bool(cur.get("source_ref")), attempt=attempt)
            img = self.store.get_image(d["image_id"]) if d.get("image_id") else None
            keep = img is not None and cur.get("source_ref") == d.get("source_ref")
            if keep:
                gen = dict(d.get("generation") or {})
                text = self._apply_credit(d["text"], None, img.get("attribution") or None)
                gen["credit_line"] = img.get("attribution") or None
                with self.store.tx() as conn:
                    self.store.transition(conn, draft_id, S.VALIDATING, self.clock(), expect_status=(S.IMAGE_PENDING,),
                                          text=text, text_fingerprint=C.fingerprint(text), generation=gen)
            else:
                self._image_stage(draft_id, actor, None, deadline, metrics)
            self._validation_stage(draft_id, actor, metrics)
        except NoSource as exc:
            self._fail(draft_id, actor, str(exc), {"selection": exc.explanation})
        except I.ImageError as exc:
            self._fail(draft_id, actor, f"image: {exc}")
        except (TimeoutError, StoreError) as exc:
            self._fail(draft_id, actor, str(exc))
        return self.detail(draft_id)

    def regenerate_image(self, draft_id: str, operator: str, version: int, choice: Optional[str] = None) -> Dict[str, Any]:
        op = _validate_operator(operator)
        cur = self._draft(draft_id)
        self._assert_not_published(draft_id)
        if choice is not None and not (choice == "title_card" or re.fullmatch(r"asset:[0-9a-f]{16}", str(choice))):
            raise BadRequest("choice must be title_card or one of the listed asset options")
        if not cur.get("source_ref") and not cur.get("topic_title"):
            raise Conflict("the draft has no topic yet; regenerate the text first")
        blocked = self._blocked(self.config(), automatic=False)
        if blocked:
            raise Blocked(blocked)
        self._regeneration_quota()
        import time
        now = self.clock()
        previous = [i.get("source_ref") for i in self.store.images_for_draft(draft_id)]
        if choice is None:
            opts = self.image_options(draft_id)["options"]
            fresh = [o for o in opts if o["choice"] != "title_card" and not o["used_by_another_draft"]
                     and not any((p or "").startswith(o["choice"][6:]) for p in previous)]
            if not fresh and "title_card" in previous:
                raise Conflict("no other image is available for this topic; edit the text or pick a listed option")
        with self.store.tx() as conn:
            self.store.transition(conn, draft_id, S.IMAGE_PENDING, now,
                                  expect_status=(S.READY_FOR_REVIEW, S.APPROVED, S.VALIDATION_FAILED, S.IMAGE_FAILED),
                                  expect_version=version, decided_by=None, decided_at_utc=None, decision_reason=None,
                                  warnings_acknowledged=0)
            self.store.event(conn, "IMAGE_REGENERATION_REQUESTED", _actor(op), now, draft_id=draft_id,
                             detail={"choice": choice, "previous_image": cur.get("image_id")})
        actor = _actor(op)
        try:
            self._image_stage(draft_id, actor, choice, time.monotonic() + GENERATION_TIMEOUT, {},
                              exclude_refs=[r for r in previous if r] if choice is None else ())
            self._validation_stage(draft_id, actor)
        except BadRequest:
            self._fail(draft_id, actor, "requested image choice is not available")
            raise
        except (I.ImageError, TimeoutError, StoreError) as exc:
            self._fail(draft_id, actor, f"image: {exc}")
        return self.detail(draft_id)

    def approve(self, draft_id: str, operator: str, version: int, acknowledge_warnings: bool = False,
                reason: str = "") -> Dict[str, Any]:
        op = _validate_operator(operator)
        d = self._draft(draft_id)
        if d["status"] != S.READY_FOR_REVIEW:
            raise S.InvalidTransition(f"only READY_FOR_REVIEW drafts can be approved (draft is {d['status']})")
        vs = d.get("validation_status")
        reason = (reason or "").strip()[:500]
        if vs == S.V_REJECTED or vs is None:
            raise Conflict("a draft that failed validation cannot be approved")
        if vs != S.VALID:
            if not acknowledge_warnings or len(reason) < 5:
                raise Conflict(f"validation is {vs}: approval needs acknowledge_warnings=true and a reason "
                               "(at least 5 characters) that is recorded in the audit history")
        img = self.store.get_image(d["image_id"]) if d.get("image_id") else None
        path = self._image_path(img)
        if img is None or path is None or not path.is_file() or I.sha256_file(path) != img["sha256"]:
            raise Conflict("the image is missing or changed since validation; regenerate the image")
        val = d.get("validation") or {}
        now = self.clock()
        with self.store.tx() as conn:
            out = self.store.transition(conn, draft_id, S.APPROVED, now, expect_status=(S.READY_FOR_REVIEW,),
                                        expect_version=version, decided_by=op, decided_at_utc=S.ts(now),
                                        decision_reason=reason or None, warnings_acknowledged=1 if vs != S.VALID else 0)
            self.store.event(conn, "DRAFT_APPROVED", _actor(op), now, draft_id=draft_id,
                             detail={"validation_status": vs, "reason": reason,
                                     "acknowledged": [c["check"] for c in (val.get("warnings") or []) + (val.get("reviews") or [])],
                                     "image_sha256": img["sha256"], "text_fingerprint": d.get("text_fingerprint"),
                                     "publishing": "none: Stage 3.32E approves drafts only and never publishes"})
        return self.detail(out["id"])

    def reject(self, draft_id: str, operator: str, version: int, reason: str) -> Dict[str, Any]:
        op = _validate_operator(operator)
        self._draft(draft_id)
        reason = (reason or "").strip()
        if not 3 <= len(reason) <= 500:
            raise BadRequest("a rejection reason of 3-500 characters is required")
        now = self.clock()
        with self.store.tx() as conn:
            self.store.transition(conn, draft_id, S.REJECTED, now, expect_status=(S.READY_FOR_REVIEW,),
                                  expect_version=version, decided_by=op, decided_at_utc=S.ts(now), decision_reason=reason)
            self.store.event(conn, "DRAFT_REJECTED", _actor(op), now, draft_id=draft_id, detail={"reason": reason})
        return self.detail(draft_id)

    def archive(self, draft_id: str, operator: str, version: int, reason: str = "") -> Dict[str, Any]:
        op = _validate_operator(operator)
        self._draft(draft_id)
        now = self.clock()
        with self.store.tx() as conn:
            self.store.transition(conn, draft_id, S.ARCHIVED, now,
                                  expect_status=(S.READY_FOR_REVIEW, S.APPROVED, S.REJECTED) + S.FAILED_STATES,
                                  expect_version=version)
            self.store.event(conn, "DRAFT_ARCHIVED", _actor(op), now, draft_id=draft_id,
                             detail={"reason": (reason or "").strip()[:500]})
        return self.detail(draft_id)

    # -- reads ----------------------------------------------------------------------------------------------------
    def _public_draft(self, d: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in d.items() if k not in ("generation",)}
        gen = d.get("generation") or {}
        out["generation"] = {k: v for k, v in gen.items() if k != "corpus"}
        out["format_label"] = C.FORMAT_LABELS.get(d.get("format") or "", d.get("format"))
        out["char_count"] = len(d.get("text") or "")
        out["image_url"] = f"/api/community/images/{d['image_id']}" if d.get("image_id") else None
        out["approvable"] = d["status"] == S.READY_FOR_REVIEW and d.get("validation_status") in (
            S.VALID, S.VALID_WITH_WARNINGS, S.NEEDS_OPERATOR_REVIEW)
        out["needs_acknowledgement"] = d.get("validation_status") in (S.VALID_WITH_WARNINGS, S.NEEDS_OPERATOR_REVIEW)
        return out

    def detail(self, draft_id: str) -> Dict[str, Any]:
        d = self._draft(draft_id)
        img = self.store.get_image(d["image_id"]) if d.get("image_id") else None
        return {"draft": self._public_draft(d), "image": img, "images": self.store.images_for_draft(draft_id),
                "events": self.store.events(draft_id, 100),
                "run": self.store.get_run(d["run_id"]) if d.get("run_id") else None,
                "corpus_excerpt": ((d.get("generation") or {}).get("corpus") or "")[:1500]}

    def list_drafts(self, **filters: Any) -> List[Dict[str, Any]]:
        return [self._public_draft(d) for d in self.store.list_drafts(**filters)]

    def overview(self) -> Dict[str, Any]:
        stats = self.store.stats()
        bs = stats["by_status"]
        sched = self.schedule_status()
        active = self.list_drafts(status="active", limit=20)
        failed_runs = stats["runs_by_status"].get(S.RUN_FAILED, 0) + stats["runs_by_status"].get(S.RUN_ABANDONED, 0)
        return {"schedule": sched, "stats": stats,
                "counts": {"ready_for_review": bs.get(S.READY_FOR_REVIEW, 0), "approved": bs.get(S.APPROVED, 0),
                           "rejected": bs.get(S.REJECTED, 0), "archived": bs.get(S.ARCHIVED, 0),
                           "failed_drafts": sum(bs.get(s, 0) for s in S.FAILED_STATES), "failed_runs": failed_runs,
                           "validation_warnings": sum(v for k, v in stats["by_validation"].items() if k != S.VALID)},
                "active_drafts": active, "notifications": self.store.notifications(10),
                "formats": [{"id": f, "label": C.FORMAT_LABELS[f]} for f in C.FORMATS],
                "publishing": {"available": False,
                               "note": "Stage 3.32E never publishes. Approved drafts are posted manually in YouTube "
                                       "Studio; API publishing is Stage 3.32F (blocked on posts API access)."}}

    def history(self, limit: int = 50) -> Dict[str, Any]:
        return {"runs": self.store.list_runs(limit=limit), "events": self.store.events(None, limit)}

    def image_file(self, image_id: str) -> Path:
        if not isinstance(image_id, str) or not IMAGE_ID.match(image_id):
            raise NotFound("image not found")
        rec = self.store.get_image(image_id)
        path = self._image_path(rec)
        if path is None or not path.is_file() or path.is_symlink() or not I.inside(path, self.images_dir):
            raise NotFound("image not found")
        return path

    # -- notifications ----------------------------------------------------------------------------------------
    def _notify(self, kind: str, subject: str, message: str, severity: str, draft_id: Optional[str]) -> None:
        try:
            now = self.clock()
            with self.store.read() as conn:
                n = conn.execute("SELECT COUNT(*) FROM community_notifications WHERE created_at_utc >= ?",
                                 (S.ts(now - timedelta(hours=24)),)).fetchone()[0]
            if n >= NOTIFICATION_DAILY_LIMIT:
                return
            delivery = self.notifier(subject, message, severity)
            with self.store.tx() as conn:
                self.store.add_notification(conn, kind, subject, message, severity, draft_id, delivery, now)
        except Exception:
            pass


_instance: Optional[CommunityService] = None
_instance_lock = threading.Lock()


def get_service() -> CommunityService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = CommunityService()
        return _instance


def scheduler_tick() -> Dict[str, Any]:
    """Entry point for the existing scheduler daemon (APScheduler job ``community_draft_tick``)."""
    return get_service().tick()
