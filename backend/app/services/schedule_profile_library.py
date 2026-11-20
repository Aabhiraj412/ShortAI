"""Named Production Schedule Profiles: save the active production schedule under any name and restore it later.

This is a *library* next to the existing schedule system, not a second schedule manager:

* The active schedule stays where it always was: studio_settings['control_center.schedule.active'], written only
  by the existing activation path (schedule_service.activate -> control_center_service.activate_schedule_draft).
* Profiles live in their own table, ``production_schedule_profiles``. Saving, renaming, duplicating and deleting
  never touch the active schedule, the draft, the runtime profile or the YouTube publication schedule.
* Loading a profile validates the stored configuration (schema, checksum, schedule validator), refuses while a
  production job is active or when it would remove / shorten the recovery window that is currently open, and then
  calls the existing ``schedule_service.activate``. The scheduler daemon picks the new revision up on its normal
  poll; nothing is restarted and no cycle is created.
* Which profile is active is metadata only: studio_settings['control_center.schedule.named_profile'].
* Every change is recorded with the existing control-center change history (``record_change``).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from backend.app.database.db import JobModel, ProductionScheduleProfileModel
from backend.app.services import control_center_service as cc
from backend.app.services import schedule_service

IST_TZ = ZoneInfo("Asia/Kolkata")
SCHEMA_VERSION = 1
ACTIVE_MARKER_KEY = "control_center.schedule.named_profile"
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_PROFILES = 200
CYCLE_KEYS = ("cycle_id", "name", "start_time", "recovery_duration_minutes", "target_publish_time", "enabled",
              "active_weekdays")
# A production job in one of these states blocks activation: its timing, worker and recovery window must not move.
BLOCKING_JOB_STATUSES = ("RUNNING", "RETRYING", "PAUSED", "QUEUED", "RETRY_SCHEDULED", "PENDING", "INTERRUPTED")

_utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class ProfileError(ValueError):
    code = 400


class ProfileNotFound(ProfileError):
    code = 404


class ProfileConflict(ProfileError):
    code = 409


class ActivationUnsafe(ProfileError):
    code = 409

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("Activation refused: " + " ".join(reasons))


# -- configuration, checksum, comparison --------------------------------------------------------------------

def _norm_cycle(c: dict[str, Any]) -> dict[str, Any]:
    clean = schedule_service._clean_cycle(c)
    return {
        "cycle_id": str(clean.get("cycle_id") or ""),
        "name": str(clean.get("name") or ""),
        "start_time": str(clean.get("start_time") or ""),
        "recovery_duration_minutes": int(clean["recovery_duration_minutes"]),
        "target_publish_time": clean.get("target_publish_time") or None,
        "enabled": bool(clean["enabled"]),
        "active_weekdays": sorted(int(d) for d in clean["active_weekdays"]),
    }


def schedule_config(spec: dict[str, Any]) -> dict[str, Any]:
    """The complete schedule configuration that a profile stores (no version, revision or activation metadata)."""
    if not isinstance(spec, dict) or not isinstance(spec.get("cycles"), list):
        raise ProfileError("A schedule configuration needs a cycle list.")
    if any(not isinstance(c, dict) for c in spec["cycles"]):
        raise ProfileError("Each schedule cycle must be an object.")
    cycles = sorted((_norm_cycle(c) for c in spec["cycles"]), key=lambda c: (c["start_time"], c["cycle_id"]))
    return {
        "profile_name": str(spec.get("profile_name") or "CUSTOM").upper(),
        "timezone_name": str(spec.get("timezone_name") or "Asia/Kolkata"),
        "cycles": cycles,
    }


def checksum(config: dict[str, Any]) -> str:
    canonical = schedule_config(config)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def summarize(config: dict[str, Any]) -> dict[str, Any]:
    cycles = config.get("cycles") or []
    enabled = [c for c in cycles if c.get("enabled", True)]
    durations: dict[int, int] = {}
    for c in enabled:
        d = int(c.get("recovery_duration_minutes") or 240)
        durations[d] = durations.get(d, 0) + 1
    if not durations:
        recovery = "no enabled cycles"
    elif len(durations) == 1:
        recovery = f"{next(iter(durations))} min (all cycles)"
    else:
        recovery = ", ".join(f"{d} min ×{n}" for d, n in sorted(durations.items()))
    return {"cycles_total": len(cycles), "cycles_enabled": len(enabled), "timezone": config.get("timezone_name"),
            "recovery_window": recovery,
            "recovery_minutes_min": min(durations) if durations else None,
            "recovery_minutes_max": max(durations) if durations else None,
            "start_times": [c["start_time"] for c in enabled]}


def compare(active: Optional[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    """Differences between the active schedule and a profile, cycle by cycle (matched by cycle_id)."""
    tgt = schedule_config(target)
    if active is None:
        return {"identical": False, "has_active_schedule": False, "timezone": None, "profile_type": None,
                "added": tgt["cycles"], "removed": [], "changed": [], "unchanged": 0,
                "recovery_shortened": [], "summary": "There is no active schedule; every cycle is new."}
    act = schedule_config(active)
    a = {c["cycle_id"]: c for c in act["cycles"]}
    b = {c["cycle_id"]: c for c in tgt["cycles"]}
    added = [b[k] for k in b if k not in a]
    removed = [a[k] for k in a if k not in b]
    changed, unchanged, shortened = [], 0, []
    for k in (k for k in b if k in a):
        fields = {f: {"from": a[k][f], "to": b[k][f]} for f in CYCLE_KEYS[1:] if a[k][f] != b[k][f]}
        if fields:
            item = {"cycle_id": k, "name": b[k]["name"], "fields": fields}
            if b[k]["recovery_duration_minutes"] < a[k]["recovery_duration_minutes"]:
                item["recovery_shortened"] = True
                shortened.append(k)
            changed.append(item)
        else:
            unchanged += 1
    tz = None if act["timezone_name"] == tgt["timezone_name"] else {"from": act["timezone_name"], "to": tgt["timezone_name"]}
    ptype = None if act["profile_name"] == tgt["profile_name"] else {"from": act["profile_name"], "to": tgt["profile_name"]}
    identical = not (added or removed or changed or tz or ptype)
    parts = []
    if tz:
        parts.append(f"timezone {tz['from']} → {tz['to']}")
    for label, items in (("added", added), ("removed", removed), ("changed", changed)):
        if items:
            parts.append(f"{len(items)} cycle(s) {label}")
    if shortened:
        parts.append(f"recovery shortened for {', '.join(shortened)}")
    return {"identical": identical, "has_active_schedule": True, "timezone": tz, "profile_type": ptype,
            "added": added, "removed": removed, "changed": changed, "unchanged": unchanged,
            "recovery_shortened": shortened,
            "summary": "Identical to the active schedule." if identical else "; ".join(parts) + "."}


# -- storage helpers ------------------------------------------------------------------------------------------

def _ts(dt: Optional[datetime] = None) -> str:
    return (dt or _utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_name(name: Any) -> tuple[str, str]:
    if not isinstance(name, str):
        raise ProfileError("A profile name is required.")
    clean = re.sub(r"\s+", " ", name).strip()
    if not clean:
        raise ProfileError("A profile name is required.")
    if len(clean) > MAX_NAME:
        raise ProfileError(f"Profile names are limited to {MAX_NAME} characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        raise ProfileError("Profile names cannot contain control characters.")
    if not re.search(r"[0-9A-Za-z]", clean, flags=re.UNICODE) and not any(ch.isalnum() for ch in clean):
        raise ProfileError("A profile name needs at least one letter or digit.")
    return clean, clean.casefold()


def _clean_description(description: Any) -> Optional[str]:
    if description is None:
        return None
    if not isinstance(description, str):
        raise ProfileError("The description must be text.")
    description = description.strip()
    if len(description) > MAX_DESCRIPTION:
        raise ProfileError(f"Descriptions are limited to {MAX_DESCRIPTION} characters.")
    return description or None


def _active_spec() -> Optional[dict[str, Any]]:
    return schedule_service.active_spec()


def _marker() -> Optional[dict[str, Any]]:
    raw = cc._read_setting(ACTIVE_MARKER_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def _set_marker(value: Optional[dict[str, Any]]) -> None:
    cc._write_settings({ACTIVE_MARKER_KEY: json.dumps(value) if value is not None else None})


def _load_config(row: ProductionScheduleProfileModel) -> dict[str, Any]:
    """Parse and verify a stored profile before it is used: schema version, structure and checksum."""
    if int(row.schema_version or 0) != SCHEMA_VERSION:
        raise ProfileError(f"Profile '{row.name}' uses unsupported schema version {row.schema_version}.")
    try:
        config = json.loads(row.config_json)
    except ValueError as exc:
        raise ProfileError(f"Profile '{row.name}' has unreadable stored data.") from exc
    config = schedule_config(config)
    if checksum(config) != row.checksum:
        raise ProfileError(f"Profile '{row.name}' failed its checksum: the stored configuration was changed outside the profile manager.")
    return config


def _public(row: ProductionScheduleProfileModel, active_checksum: Optional[str], marker: Optional[dict[str, Any]],
            include_config: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": row.id, "name": row.name, "description": row.description,
                           "checksum": row.checksum, "schema_version": row.schema_version,
                           "created_at_utc": row.created_at_utc, "updated_at_utc": row.updated_at_utc}
    try:
        config = _load_config(row)
        out.update(summarize(config), valid_data=True, data_error=None)
    except ProfileError as exc:
        config = None
        out.update(valid_data=False, data_error=str(exc), cycles_total=None, cycles_enabled=None, timezone=None,
                   recovery_window=None)
    out["is_active"] = bool(config is not None and active_checksum and row.checksum == active_checksum)
    out["is_loaded_profile"] = bool(marker and marker.get("profile_id") == row.id)
    out["status"] = ("ACTIVE" if out["is_active"] else
                     "ACTIVE_MODIFIED" if out["is_loaded_profile"] else "INACTIVE")
    if include_config:
        out["config"] = config
    return out


def _get_row(session, profile_id: Any) -> ProductionScheduleProfileModel:
    if not isinstance(profile_id, str) or not re.fullmatch(r"sp_[0-9a-f]{16}", profile_id):
        raise ProfileNotFound("Unknown schedule profile.")
    row = session.get(ProductionScheduleProfileModel, profile_id)
    if row is None:
        raise ProfileNotFound("Unknown schedule profile.")
    return row


def _name_taken(session, name_key: str, exclude_id: Optional[str] = None) -> bool:
    q = session.query(ProductionScheduleProfileModel).filter(ProductionScheduleProfileModel.name_key == name_key)
    if exclude_id:
        q = q.filter(ProductionScheduleProfileModel.id != exclude_id)
    return q.first() is not None


# -- public operations ----------------------------------------------------------------------------------------

def active_indicator() -> dict[str, Any]:
    spec = _active_spec()
    marker = _marker()
    if not spec:
        return {"has_active_schedule": False, "state": "NO_ACTIVE_SCHEDULE", "profile_id": None, "profile_name": None,
                "unsaved_changes": False, "active_checksum": None, "message": "No active production schedule."}
    active_sum = checksum(spec)
    loaded = None
    match = None
    with cc.SessionLocal() as session:
        if marker and marker.get("profile_id"):
            loaded = session.get(ProductionScheduleProfileModel, marker["profile_id"])
        if loaded is None or loaded.checksum != active_sum:
            match = session.query(ProductionScheduleProfileModel).filter(
                ProductionScheduleProfileModel.checksum == active_sum).order_by(
                ProductionScheduleProfileModel.updated_at_utc.desc()).first()
        loaded_info = (loaded.id, loaded.name, loaded.checksum) if loaded is not None else None
        match_info = (match.id, match.name) if match is not None else None
    base = {"has_active_schedule": True, "active_checksum": active_sum, "active_revision": spec.get("revision"),
            "loaded_at_utc": (marker or {}).get("loaded_at_utc")}
    if loaded_info and loaded_info[2] == active_sum:
        return {**base, "state": "PROFILE_ACTIVE", "profile_id": loaded_info[0], "profile_name": loaded_info[1],
                "unsaved_changes": False, "message": f"Active profile: {loaded_info[1]}"}
    if loaded_info:
        msg = (f"Loaded from '{loaded_info[1]}', but the active schedule was changed afterwards (unsaved changes)."
               + (f" It now matches '{match_info[1]}'." if match_info else ""))
        return {**base, "state": "PROFILE_MODIFIED", "profile_id": loaded_info[0], "profile_name": loaded_info[1],
                "unsaved_changes": match_info is None, "matches_profile": match_info and {"id": match_info[0], "name": match_info[1]},
                "message": msg}
    if match_info:
        return {**base, "state": "MATCHES_PROFILE", "profile_id": match_info[0], "profile_name": match_info[1],
                "unsaved_changes": False, "message": f"Active schedule matches saved profile '{match_info[1]}'."}
    return {**base, "state": "UNSAVED", "profile_id": None, "profile_name": None, "unsaved_changes": True,
            "message": "The active schedule is not saved as a named profile."}


def list_profiles() -> dict[str, Any]:
    indicator = active_indicator()
    marker = _marker()
    with cc.SessionLocal() as session:
        rows = session.query(ProductionScheduleProfileModel).order_by(ProductionScheduleProfileModel.name_key).all()
        profiles = [_public(r, indicator.get("active_checksum"), marker) for r in rows]
    return {"profiles": profiles, "active": indicator, "count": len(profiles)}


def get_profile(profile_id: str) -> dict[str, Any]:
    indicator = active_indicator()
    with cc.SessionLocal() as session:
        return _public(_get_row(session, profile_id), indicator.get("active_checksum"), _marker(), include_config=True)


def save_current(name: Any, description: Any = None) -> dict[str, Any]:
    """Save the ACTIVE schedule as a new named profile. The active schedule is only read."""
    clean, key = clean_name(name)
    desc = _clean_description(description)
    spec = _active_spec()
    if not spec:
        raise ProfileError("There is no active production schedule to save.")
    config = schedule_config(spec)
    check = schedule_service.validate(config)
    if not check["valid"]:
        raise ProfileError("The active schedule does not pass validation: " + "; ".join(check["errors"]))
    now = _ts()
    with cc.SessionLocal() as session:
        if _name_taken(session, key):
            raise ProfileConflict(f"A profile named '{clean}' already exists. Choose another name.")
        if session.query(ProductionScheduleProfileModel).count() >= MAX_PROFILES:
            raise ProfileConflict(f"At most {MAX_PROFILES} profiles can be saved.")
        row = ProductionScheduleProfileModel(id="sp_" + secrets.token_hex(8), name=clean, name_key=key, description=desc,
                                             config_json=json.dumps(config, sort_keys=True), checksum=checksum(config),
                                             schema_version=SCHEMA_VERSION, created_at_utc=now, updated_at_utc=now)
        session.add(row)
        session.commit()
        profile_id = row.id
    # The active schedule now *is* this profile; record that as metadata (the schedule itself is untouched).
    _set_marker({"profile_id": profile_id, "name": clean, "checksum": checksum(config), "loaded_at_utc": now,
                 "source": "saved_from_active"})
    cc.record_change("schedule_profile_saved", None, {"profile_id": profile_id, "name": clean,
                                                     "checksum": checksum(config), "active_revision": spec.get("revision")})
    return get_profile(profile_id)


def rename(profile_id: str, new_name: Any) -> dict[str, Any]:
    clean, key = clean_name(new_name)
    with cc.SessionLocal() as session:
        row = _get_row(session, profile_id)
        if _name_taken(session, key, exclude_id=row.id):
            raise ProfileConflict(f"A profile named '{clean}' already exists. Choose another name.")
        previous = row.name
        if previous == clean:
            return get_profile(profile_id)
        row.name, row.name_key, row.updated_at_utc = clean, key, _ts()
        session.commit()
    marker = _marker()
    if marker and marker.get("profile_id") == profile_id:
        _set_marker({**marker, "name": clean})
    cc.record_change("schedule_profile_renamed", {"profile_id": profile_id, "name": previous},
                     {"profile_id": profile_id, "name": clean})
    return get_profile(profile_id)


def duplicate(profile_id: str, new_name: Any, description: Any = None) -> dict[str, Any]:
    clean, key = clean_name(new_name)
    desc = _clean_description(description)
    now = _ts()
    with cc.SessionLocal() as session:
        src = _get_row(session, profile_id)
        config = _load_config(src)
        if _name_taken(session, key):
            raise ProfileConflict(f"A profile named '{clean}' already exists. Choose another name.")
        if session.query(ProductionScheduleProfileModel).count() >= MAX_PROFILES:
            raise ProfileConflict(f"At most {MAX_PROFILES} profiles can be saved.")
        row = ProductionScheduleProfileModel(id="sp_" + secrets.token_hex(8), name=clean, name_key=key,
                                             description=desc if desc is not None else src.description,
                                             config_json=json.dumps(config, sort_keys=True), checksum=src.checksum,
                                             schema_version=SCHEMA_VERSION, created_at_utc=now, updated_at_utc=now)
        session.add(row)
        session.commit()
        new_id, src_name = row.id, src.name
    cc.record_change("schedule_profile_duplicated", {"profile_id": profile_id, "name": src_name},
                     {"profile_id": new_id, "name": clean})
    return get_profile(new_id)


def delete(profile_id: str, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise ProfileError("Deleting a schedule profile requires explicit confirmation.")
    spec = _active_spec()
    active_sum = checksum(spec) if spec else None
    with cc.SessionLocal() as session:
        row = _get_row(session, profile_id)
        if active_sum and row.checksum == active_sum:
            raise ProfileConflict(f"'{row.name}' is the currently active schedule. Activate another schedule before deleting it.")
        name, row_sum = row.name, row.checksum
        session.delete(row)
        session.commit()
    marker = _marker()
    if marker and marker.get("profile_id") == profile_id:
        _set_marker(None)
    cc.record_change("schedule_profile_deleted", {"profile_id": profile_id, "name": name, "checksum": row_sum}, None)
    return {"deleted": True, "profile_id": profile_id, "name": name}


def _blocking_jobs() -> list[dict[str, Any]]:
    with cc.SessionLocal() as session:
        rows = (session.query(JobModel).filter(JobModel.id.like("cycle\\_%", escape="\\"))
                .filter(JobModel.status.in_(BLOCKING_JOB_STATUSES)).all())
        return [{"job_id": r.id, "status": r.status, "stage": r.current_stage} for r in rows]


def _job_status(job_id: str) -> Optional[str]:
    with cc.SessionLocal() as session:
        row = session.get(JobModel, job_id)
        return row.status if row is not None else None


def safety_check(config: dict[str, Any], now_ist: Optional[datetime] = None) -> list[str]:
    """Reasons why activating ``config`` now would be unsafe. Empty list = safe."""
    reasons: list[str] = []
    for job in _blocking_jobs():
        reasons.append(f"Production job {job['job_id']} is {job['status']} (stage {job['stage'] or 'unknown'}). "
                       "Schedule activation is refused while a production job is active so its timing, worker and "
                       "recovery window are not changed. Try again after it finishes.")
    now = (now_ist or datetime.now(IST_TZ)).astimezone(IST_TZ)
    current = schedule_service.current_cycle_window(now)
    if current:
        target = {c["cycle_id"]: c for c in schedule_config(config)["cycles"]}
        new = target.get(current["cycle_id"])
        start = datetime.strptime(current["production_start_ist"], "%Y-%m-%d %H:%M IST")
        problem = None
        if new is None or not new["enabled"]:
            problem = "removes or disables it"
        elif new["start_time"] != start.strftime("%H:%M"):
            problem = "moves its start time"
        elif int(new["recovery_duration_minutes"]) < int(current["recovery_duration_minutes"]):
            problem = (f"shortens its recovery window from {current['recovery_duration_minutes']} to "
                       f"{new['recovery_duration_minutes']} minutes")
        if problem:
            job_id = f"{current['cycle_id']}_{start:%Y%m%d}_{start:%H%M}"
            status = _job_status(job_id)
            if status != "COMPLETED":
                reasons.append(f"Cycle {current['name']} ({current['cycle_id']}) is inside its recovery window until "
                               f"{current['recovery_deadline_ist']} and its job is {status or 'not created yet'}; "
                               f"the selected profile {problem}. Wait until the window closes or the cycle completes.")
    return reasons


def preview(profile_id: str, now_ist: Optional[datetime] = None) -> dict[str, Any]:
    with cc.SessionLocal() as session:
        row = _get_row(session, profile_id)
        name, row_sum = row.name, row.checksum
        config = _load_config(row)
    check = schedule_service.validate(config)
    active = _active_spec()
    upcoming = cc.compute_upcoming_production_cycles({**config, "schedule_version": 1}, count=6, now_ist=now_ist) if check["valid"] else []
    blockers = safety_check(config, now_ist) if check["valid"] else []
    return {"profile_id": profile_id, "name": name, "checksum": row_sum, "config": config, "summary": summarize(config),
            "validation": {"valid": check["valid"], "errors": check["errors"], "warnings": check["warnings"]},
            "differences": compare(active, config), "upcoming": upcoming,
            "can_activate": check["valid"] and not blockers, "blocked_reasons": blockers,
            "publication_schedule_note": "Loading a production profile never changes the YouTube publication schedule."}


def load(profile_id: str, confirm: bool, expected_checksum: Any, now_ist: Optional[datetime] = None) -> dict[str, Any]:
    """Activate a saved profile through the existing activation path, after validation and safety checks."""
    if confirm is not True:
        raise ProfileError("Loading a schedule profile requires explicit confirmation.")
    with cc.SessionLocal() as session:
        row = _get_row(session, profile_id)
        name, row_sum = row.name, row.checksum
        config = _load_config(row)
    if expected_checksum != row_sum:
        raise ProfileConflict("The profile changed after it was previewed. Preview it again before loading.")
    check = schedule_service.validate(config)
    if not check["valid"]:
        raise ProfileError("The saved profile is not a valid schedule: " + "; ".join(check["errors"]))
    active = _active_spec()
    before = {"revision": (active or {}).get("revision"), "checksum": checksum(active) if active else None}
    now = _ts()
    if active and checksum(active) == row_sum:
        _set_marker({"profile_id": profile_id, "name": name, "checksum": row_sum, "loaded_at_utc": now, "source": "load"})
        cc.record_change("schedule_profile_loaded", before, {"profile_id": profile_id, "name": name,
                                                             "checksum": row_sum, "activation": "not needed (identical)"})
        return {"status": "ALREADY_ACTIVE", "profile_id": profile_id, "name": name, "revision": before["revision"]}
    blockers = safety_check(config, now_ist)
    if blockers:
        raise ActivationUnsafe(blockers)
    result = schedule_service.activate(config, True)
    _set_marker({"profile_id": profile_id, "name": name, "checksum": row_sum, "loaded_at_utc": now, "source": "load"})
    cc.record_change("schedule_profile_loaded", before, {"profile_id": profile_id, "name": name, "checksum": row_sum,
                                                         "revision": result.get("revision"), "version": result.get("version")})
    return {"status": "ACTIVATED", "profile_id": profile_id, "name": name, "revision": result.get("revision"),
            "version": result.get("version"), "warnings": result.get("warnings", [])}
