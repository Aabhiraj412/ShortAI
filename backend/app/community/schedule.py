"""Community draft schedule: IANA timezones, DST-correct daily windows and the scheduling decision.

The daily target time is a LOCAL wall-clock time in an IANA timezone (for example ``America/New_York``). Every
instant is computed with the timezone database (``zoneinfo``; tzdata), never with a fixed UTC offset.

Daylight-saving rules (RFC 5545 semantics, the same as calendar software):
* a target time that does not exist on a spring-forward day (02:30 in New York on the DST start date) is shifted
  forward by the length of the gap (03:30 EDT that day);
* a target time that occurs twice on a fall-back day (01:30 in New York on the DST end date) uses the FIRST
  occurrence; the second occurrence never starts another run, because runs are keyed by the local calendar date.

The catch-up window is real elapsed time: ``end = start (UTC) + catch_up_hours``, so a DST change inside the window
neither stretches nor shortens it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

PRESET_TIMEZONES = (
    ("America/New_York", "US Eastern"), ("America/Chicago", "US Central"), ("America/Denver", "US Mountain"),
    ("America/Los_Angeles", "US Pacific"), ("America/Phoenix", "Arizona (no DST)"),
    ("America/Anchorage", "Alaska"), ("Pacific/Honolulu", "Hawaii (no DST)"), ("Asia/Kolkata", "India"),
)
# Fixed-offset and legacy rule names are not accepted: a schedule must follow a real place's timezone rules.
_FIXED_OR_LEGACY = re.compile(r"^(Etc/.*|GMT.*|UCT|Universal|Zulu|Greenwich|EST|MST|HST|EST5EDT|CST6CDT|MST7MDT|"
                              r"PST8PDT|Factory|localtime|posixrules)$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@lru_cache(maxsize=1)
def timezone_ids() -> Tuple[str, ...]:
    """Every accepted IANA identifier (tzdata), sorted; fixed-offset and legacy names excluded, UTC kept."""
    return tuple(sorted({z for z in available_timezones() if not _FIXED_OR_LEGACY.match(z)} | {"UTC"}))


def valid_timezone(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= 64 and name in timezone_ids()


def zone(name: str) -> ZoneInfo:
    if not valid_timezone(name):
        raise ValueError(f"unknown or unsupported timezone {name!r}: use an IANA name such as America/New_York")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {name!r}") from exc


def _fmt_offset(td: Optional[timedelta]) -> str:
    total = int((td or timedelta(0)).total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _next_transition(tz: ZoneInfo, now: datetime, horizon_days: int = 400) -> Optional[datetime]:
    """First instant after ``now`` where the UTC offset changes (daily scan, then minute bisection)."""
    prev = now.astimezone(tz).utcoffset()
    t = now
    for _ in range(horizon_days):
        nxt = t + timedelta(days=1)
        if nxt.astimezone(tz).utcoffset() != prev:
            lo, hi = t, nxt
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if mid.astimezone(tz).utcoffset() == prev:
                    lo = mid
                else:
                    hi = mid
            return hi.replace(second=0, microsecond=0)
        t = nxt
    return None


def tz_details(name: str, now: datetime) -> Dict[str, Any]:
    tz = zone(name)
    local = now.astimezone(tz)
    jan = datetime(local.year, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime(local.year, 7, 15, 12, tzinfo=tz).utcoffset()
    nt = _next_transition(tz, now) if jan != jul else None
    out = {"id": name, "abbreviation": local.tzname(), "utc_offset": _fmt_offset(local.utcoffset()),
           "dst_active": bool(local.dst()), "observes_dst": jan != jul, "local_now": local.isoformat(),
           "next_transition_utc": None, "next_transition_local": None, "offset_after_transition": None}
    if nt is not None:
        after = nt.astimezone(tz)
        out.update(next_transition_utc=nt.strftime("%Y-%m-%dT%H:%M:%SZ"), next_transition_local=after.isoformat(),
                   offset_after_transition=_fmt_offset(after.utcoffset()))
    return out


def presets(now: datetime) -> List[Dict[str, Any]]:
    return [{**tz_details(z, now), "label": label} for z, label in PRESET_TIMEZONES]


@dataclass
class Resolved:
    day: date
    start_utc: datetime
    note: Optional[str] = None          # "nonexistent" | "ambiguous" | None

    def local(self, tz: ZoneInfo) -> datetime:
        return self.start_utc.astimezone(tz)


def resolve(day: date, hhmm: str, tz: ZoneInfo) -> Resolved:
    """The UTC instant of local ``hhmm`` on ``day`` in ``tz``, with the DST rules described in the module docstring."""
    m = TIME_RE.match(hhmm or "")
    if not m:
        raise ValueError("generation_time must be HH:MM (24-hour)")
    naive = datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)))
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    start = first.astimezone(timezone.utc)
    if first.utcoffset() == second.utcoffset():
        return Resolved(day, start)
    roundtrip = start.astimezone(tz).replace(tzinfo=None)
    return Resolved(day, start, "nonexistent" if roundtrip != naive else "ambiguous")


def window(day: date, hhmm: str, tz: ZoneInfo, catch_up_hours: int) -> Tuple[Resolved, datetime]:
    r = resolve(day, hhmm, tz)
    return r, r.start_utc + timedelta(hours=int(catch_up_hours))


def dst_notes(hhmm: str, tz: ZoneInfo, now: datetime, days: int = 400) -> List[Dict[str, Any]]:
    """Upcoming dates where the target time falls in a DST gap or overlap, and what happens on those dates."""
    out = []
    start = now.astimezone(tz).date()
    jan = datetime(start.year, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime(start.year, 7, 15, 12, tzinfo=tz).utcoffset()
    if jan == jul:
        return out
    for i in range(days):
        d = start + timedelta(days=i)
        r = resolve(d, hhmm, tz)
        if r.note:
            loc = r.local(tz)
            out.append({"date": d.isoformat(), "case": r.note, "runs_at_local": loc.isoformat(),
                        "explanation": (f"{hhmm} does not exist on {d.isoformat()} (clocks spring forward); the draft "
                                        f"is generated at {loc.strftime('%H:%M %Z')} instead")
                                       if r.note == "nonexistent" else
                                       (f"{hhmm} occurs twice on {d.isoformat()} (clocks fall back); the draft is "
                                        f"generated once, at the first {hhmm} ({loc.strftime('%Z')})")})
    return out


# -- the scheduling decision ----------------------------------------------------------------------------------------
WAITING, DUE, RUNNING, RETRY, RETRY_PENDING, COMPLETED, MISSED, NOT_ENABLED_YET = (
    "waiting", "due", "running", "retry", "retry_pending", "completed", "missed", "not_enabled_yet")


@dataclass
class Decision:
    state: str
    day: date
    start_utc: datetime
    end_utc: datetime
    note: Optional[str] = None
    run: Optional[Dict[str, Any]] = None
    retry_at_utc: Optional[datetime] = None
    record_missed: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"community:scheduled:{self.day.isoformat()}"


def decide(now: datetime, cfg: Dict[str, Any], *, get_run, has_active_draft, enabled_since: Optional[datetime],
           retry_backoff: timedelta) -> List[Decision]:
    """Pure scheduling decision for yesterday's and today's local dates (yesterday matters when a late target time's
    catch-up window crosses local midnight). Returns one Decision per date, oldest first.

    * a window that started before automation was enabled is not generated retroactively (``not_enabled_yet``);
    * a scheduled run exists once per local date (its key); a FAILED or ABANDONED run is retried while
      ``attempts < max_auto_attempts``, inside the window, after the backoff, and only if the date has no active draft;
    * a window that ended without any run is recorded once as missed.
    """
    tz = zone(cfg["timezone"])
    hhmm = cfg["generation_time"]
    catch_up = int(cfg["catch_up_hours"])
    max_attempts = int(cfg["max_auto_attempts"])
    today = now.astimezone(tz).date()
    out: List[Decision] = []
    for day in (today - timedelta(days=1), today):
        r, end = window(day, hhmm, tz, catch_up)
        key = f"community:scheduled:{day.isoformat()}"
        run = get_run(key)
        base = dict(day=day, start_utc=r.start_utc, end_utc=end, note=r.note, run=run)
        if now < r.start_utc:
            out.append(Decision(WAITING, **base))
        elif run is not None:
            if run["status"] == "RUNNING":
                out.append(Decision(RUNNING, **base))
            elif run["status"] in ("FAILED", "ABANDONED") and int(run["attempts"]) < max_attempts and now <= end \
                    and not has_active_draft(day.isoformat()):
                finished = run.get("finished_at")
                retry_at = (finished or now) + retry_backoff
                out.append(Decision(RETRY if retry_at <= now else RETRY_PENDING, retry_at_utc=retry_at, **base))
            else:
                out.append(Decision(COMPLETED, **base))
        elif enabled_since is not None and r.start_utc < enabled_since:
            out.append(Decision(NOT_ENABLED_YET, **base))
        elif now > end:
            out.append(Decision(MISSED, record_missed=True, **base))
        else:
            out.append(Decision(DUE, **base))
    return out


def next_start(now: datetime, cfg: Dict[str, Any], decisions: List[Decision]) -> Tuple[Optional[datetime], str]:
    """The next generation instant and the overall state shown to the operator."""
    tz = zone(cfg["timezone"])
    actionable = [d for d in decisions if d.state in (DUE, RETRY, RUNNING, RETRY_PENDING)]
    if actionable:
        d = actionable[0]
        when = d.retry_at_utc if d.state == RETRY_PENDING else (now if d.state in (DUE, RETRY) else d.start_utc)
        return when, d.state
    today = decisions[-1]
    if today.state == WAITING:
        return today.start_utc, WAITING
    tomorrow = resolve(today.day + timedelta(days=1), cfg["generation_time"], tz)
    return tomorrow.start_utc, today.state
