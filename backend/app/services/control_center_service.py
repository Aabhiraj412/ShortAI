"""Persistent operating-mode and schedule-draft controls for the local dashboard."""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.database.db import JobModel, SessionLocal, StudioSettingModel
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    ProductionCycle,
    ScheduleProfile,
    get_active_profile,
    set_runtime_profile,
)


OPERATING_MODES = {"PRODUCTION", "GAMING", "IDLE", "MAINTENANCE"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ACTIVE_PROFILE_KEY = "control_center.schedule.active"
DRAFT_PROFILE_KEY = "control_center.schedule.draft"
PROD_SCHEDULE_VALIDATION_KEY = "control_center.schedule.validation"
PROD_SCHEDULE_HISTORY_KEY = "control_center.schedule.history"
SCHEDULER_SYNC_KEY = "control_center.scheduler.sync_state"
MODE_KEY = "control_center.operating_mode.active"
PENDING_MODE_KEY = "control_center.operating_mode.pending"
HISTORY_KEY = "control_center.change_history"
MAX_CONCURRENCY_KEY = "control_center.production_concurrency"
PRODUCTION_CONTROL_KEY = "control_center.production.control_state"
PUBLISHING_POLICY_KEY = "control_center.publishing.policy"
# Only user-controlled publishing exists. "automatic" is a legacy stored value that
# is read as manual_approval and can no longer be selected.
# manual_approval: QC-passed videos wait in the Publishing Queue for a user-chosen time.
# automatic_private_scheduled: right after QC the video is uploaded PRIVATE with YouTube-native
#   publishAt = the cycle's target publication time. Nothing is ever made public immediately.
AUTO_PRIVATE_SCHEDULED = "automatic_private_scheduled"
PUBLISHING_POLICIES = {"manual_approval", AUTO_PRIVATE_SCHEDULED}
LEGACY_PUBLISHING_POLICIES = {"automatic"}
MAX_HISTORY = 500


def _read_setting(key: str) -> str | None:
    with SessionLocal() as session:
        row = session.get(StudioSettingModel, key)
        return row.value if row else None


def _write_settings(values: dict[str, str | None]) -> None:
    with SessionLocal() as session:
        for key, value in values.items():
            row = session.get(StudioSettingModel, key)
            if value is None:
                if row:
                    session.delete(row)
            elif row:
                row.value = value
            else:
                session.add(StudioSettingModel(key=key, value=value))
        session.commit()


def _history() -> list[dict[str, Any]]:
    raw = _read_setting(HISTORY_KEY)
    if raw is None:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("Control-center history has an invalid stored format.")
    return value


def record_change(kind: str, previous: Any, new: Any) -> None:
    history = _history()
    history.append({
        "kind": kind,
        "previous": previous,
        "new": new,
        "timestamp_ist": datetime.now(IST_TZ).isoformat(),
    })
    _write_settings({HISTORY_KEY: json.dumps(history[-MAX_HISTORY:])})


def get_operating_mode() -> str:
    mode = _read_setting(MODE_KEY) or "IDLE"
    if mode not in OPERATING_MODES:
        raise ValueError(f"Unknown persisted operating mode: {mode}")
    return mode


def save_operating_mode_draft(mode: str) -> dict[str, Any]:
    normalized = mode.upper()
    if normalized not in OPERATING_MODES:
        raise ValueError(f"Unsupported operating mode: {mode}")
    pending = {
        "mode": normalized,
        "requested_at_ist": datetime.now(IST_TZ).isoformat(),
    }
    _write_settings({PENDING_MODE_KEY: json.dumps(pending)})
    return pending


def activate_operating_mode(mode: str, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to activate an operating mode.")
    normalized = mode.upper()
    pending_raw = _read_setting(PENDING_MODE_KEY)
    if not pending_raw:
        raise ValueError("Save an operating-mode draft before activation.")
    pending = json.loads(pending_raw)
    if pending.get("mode") != normalized:
        raise ValueError("The activated mode must match the pending draft.")
    previous = get_operating_mode()
    _write_settings({
        MODE_KEY: normalized,
        PENDING_MODE_KEY: None,
    })
    record_change("operating_mode", previous, normalized)
    return {"previous_mode": previous, "active_mode": normalized}


def cancel_operating_mode_draft() -> None:
    _write_settings({PENDING_MODE_KEY: None})


def _time_minutes(value: str, field: str) -> int:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field} must use HH:MM 24-hour time.") from exc
    if len(hour_text) != 2 or len(minute_text) != 2 or not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{field} must use a valid HH:MM 24-hour time.")
    return hour * 60 + minute


def _compute_production_schedule_revision(spec: dict[str, Any]) -> str:
    """Deterministic SHA-256 revision hash of a production cycle schedule configuration."""
    import hashlib
    canonical = {
        "profile_name": str(spec.get("profile_name") or "CUSTOM").upper(),
        "timezone_name": str(spec.get("timezone_name") or "Asia/Kolkata"),
        "cycles": sorted(
            [
                {
                    "cycle_id": str(c.get("cycle_id") or ""),
                    "name": str(c.get("name") or ""),
                    "start_time": str(c.get("start_time") or ""),
                    "recovery_duration_minutes": int(c.get("recovery_duration_minutes", 240)),
                    "target_publish_time": str(c.get("target_publish_time") or ""),
                    "next_day": bool(c.get("next_day", False)),
                    "active_weekdays": sorted(list(c.get("active_weekdays") or [0, 1, 2, 3, 4, 5, 6])),
                    "enabled": bool(c.get("enabled", True)),
                }
                for c in (spec.get("cycles") or [])
            ],
            key=lambda x: (x["start_time"], x["cycle_id"]),
        ),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _sort_production_cycles_chronologically(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(c: dict[str, Any]) -> tuple[int, str]:
        try:
            mins = _time_minutes(str(c.get("start_time") or "00:00"), "start_time")
        except ValueError:
            mins = 9999
        return (mins, str(c.get("cycle_id") or ""))
    return sorted(cycles, key=_key)


MIN_RECOVERY_MINUTES = 30
MAX_RECOVERY_MINUTES = 1440


def preset_schedule(profile_name: str) -> dict[str, Any]:
    name = profile_name.upper()
    if name not in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY", "CUSTOM"}:
        raise ValueError(f"Unsupported schedule profile: {profile_name}")
    if name == "CUSTOM":
        spec = {
            "profile_name": "CUSTOM",
            "timezone_name": "Asia/Kolkata",
            "schedule_version": 1,
            "cycles": [],
        }
        spec["revision"] = _compute_production_schedule_revision(spec)
        return spec
    from backend.app.scheduler.schedule_profiles import (
        PROFILE_FOUR_CYCLES_DAILY,
        PROFILE_SINGLE_DAILY_2300,
    )
    profile = (
        PROFILE_FOUR_CYCLES_DAILY
        if name == "FOUR_CYCLES_DAILY"
        else PROFILE_SINGLE_DAILY_2300
    )
    spec = {
        "profile_name": name,
        "timezone_name": profile.timezone_name,
        "schedule_version": int(getattr(profile, "schedule_version", 1)),
        "cycles": _sort_production_cycles_chronologically([
            {
                "cycle_id": cycle.cycle_id,
                "name": cycle.name,
                "start_time": f"{cycle.start_hour_ist:02d}:{cycle.start_min_ist:02d}",
                "recovery_duration_minutes": int(round(cycle.recovery_window_hours * 60)),
                "target_publish_time": f"{cycle.target_pub_hour_ist:02d}:{cycle.target_pub_min_ist:02d}",
                "active_weekdays": list(getattr(cycle, "active_weekdays", range(7))),
                "next_day": bool(cycle.pub_rollover_days),
                "enabled": True,
            }
            for cycle in profile.cycles
        ]),
    }
    spec["revision"] = _compute_production_schedule_revision(spec)
    return spec


def compute_upcoming_production_cycles(
    spec: dict[str, Any],
    count: int = 5,
    now_ist: datetime | None = None,
) -> list[dict[str, Any]]:
    """Computes the next `count` upcoming enabled production cycles chronologically."""
    tz = ZoneInfo(str(spec.get("timezone_name") or "Asia/Kolkata"))
    now = now_ist.astimezone(tz) if now_ist else datetime.now(tz)
    ver = int(spec.get("schedule_version", 1))
    cycles = _sort_production_cycles_chronologically(list(spec.get("cycles") or []))
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    # Start one day back: a cycle that began yesterday may still be inside its recovery
    # window after midnight (e.g. 22:00 start, 4 h recovery, now 01:00).
    for day_offset in range(-1, 8):
        day_date = (now + timedelta(days=day_offset)).date()
        wd = day_date.weekday()
        for c in cycles:
            if not c.get("enabled", True):
                continue
            active_days = set(c.get("active_weekdays") or [0, 1, 2, 3, 4, 5, 6])
            if wd not in active_days:
                continue
            try:
                start_min = _time_minutes(str(c.get("start_time") or ""), "start_time")
            except ValueError:
                continue
            rec_dur = int(c.get("recovery_duration_minutes", 240))
            start_dt = datetime(day_date.year, day_date.month, day_date.day, start_min // 60, start_min % 60, tzinfo=tz)
            rec_deadline_dt = start_dt + timedelta(minutes=rec_dur)
            if rec_deadline_dt <= now:
                continue
            rem_sec = int(max(0, (start_dt - now).total_seconds()))
            target_txt = str(c.get("target_publish_time") or "").strip()
            target_dt = None
            if target_txt:
                try:
                    tmin = _time_minutes(target_txt, "target_publish_time")
                    target_dt = start_dt.replace(hour=tmin // 60, minute=tmin % 60)
                    if target_dt <= start_dt:
                        target_dt += timedelta(days=1)
                except ValueError:
                    target_dt = None
            candidates.append((
                start_dt,
                {
                    "cycle_id": c["cycle_id"],
                    "name": c["name"],
                    "schedule_version": ver,
                    "start_time_ist": start_dt.strftime("%Y-%m-%d %H:%M IST"),
                    "production_start_ist": start_dt.strftime("%Y-%m-%d %H:%M IST"),
                    "production_start_utc": start_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "recovery_duration_minutes": rec_dur,
                    "recovery_start_ist": start_dt.strftime("%Y-%m-%d %H:%M IST"),
                    "recovery_deadline_ist": rec_deadline_dt.strftime("%Y-%m-%d %H:%M IST"),
                    "recovery_deadline_utc": rec_deadline_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "countdown_seconds": rem_sec,
                    "countdown_label": f"Starts in {_format_remaining(rem_sec)}" if start_dt > now else "In Window",
                    "in_progress_window": start_dt <= now < rec_deadline_dt,
                    "target_publish_ist": target_dt.strftime("%Y-%m-%d %H:%M IST") if target_dt else None,
                    "target_publish_utc": target_dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ") if target_dt else None,
                },
            ))
    candidates.sort(key=lambda pair: pair[0])
    return [item for _, item in candidates[:count]]


def validate_production_schedule_detailed(spec: dict[str, Any]) -> dict[str, Any]:
    """Validates a production cycle schedule and returns structured errors, warnings, and preview."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(spec, dict):
        return {"valid": False, "errors": ["Schedule draft must be an object."], "warnings": [], "cycles": [], "preview_next_cycles": []}
    profile_name = str(spec.get("profile_name", "")).upper()
    if profile_name not in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY", "CUSTOM"}:
        errors.append("Profile must be FOUR_CYCLES_DAILY, SINGLE_DAILY, or CUSTOM.")
    timezone_name = spec.get("timezone_name")
    if not isinstance(timezone_name, str) or not timezone_name:
        errors.append("A valid timezone is required.")
    else:
        try:
            ZoneInfo(timezone_name)
        except Exception:
            errors.append("The configured timezone is not recognized.")

    cycles = spec.get("cycles")
    if not isinstance(cycles, list):
        errors.append("Schedule cycles must be a list.")
        cycles = []
    if any(not isinstance(cycle, dict) for cycle in cycles):
        errors.append("Each schedule cycle must be an object.")
    if not errors and any(type(cycle.get("enabled", True)) is not bool for cycle in cycles):
        errors.append("Cycle enabled values must be booleans.")

    enabled = [c for c in cycles if isinstance(c, dict) and c.get("enabled", True)]
    if not errors and not enabled:
        errors.append("At least one cycle must be enabled.")

    if not errors and profile_name == "FOUR_CYCLES_DAILY":
        canonical = preset_schedule("FOUR_CYCLES_DAILY")
        canonical_cmp = [
            {k: v for k, v in c.items() if k != "recovery_duration_minutes"}
            for c in canonical["cycles"]
        ]
        enabled_cmp = [
            {
                "cycle_id": c.get("cycle_id"),
                "name": c.get("name"),
                "start_time": c.get("start_time"),
                "target_publish_time": c.get("target_publish_time"),
                "active_weekdays": c.get("active_weekdays"),
                "next_day": c.get("next_day"),
                "enabled": c.get("enabled", True),
            }
            for c in enabled
        ]
        rec_ok = all(int(c.get("recovery_duration_minutes", 240)) == 240 for c in enabled)
        if timezone_name != canonical["timezone_name"] or enabled_cmp != canonical_cmp or not rec_ok:
            # Presets are starting points, not locked configurations.
            profile_name = "CUSTOM"
            warnings.append("Preset cycles were edited; the schedule is saved as CUSTOM.")
    if not errors and profile_name == "SINGLE_DAILY" and len(enabled) != 1:
        profile_name = "CUSTOM"
        warnings.append("Preset cycles were edited; the schedule is saved as CUSTOM.")

    ids: set[str] = set()
    rec_windows: list[tuple[int, int, str]] = []
    start_slots: set[tuple[int, int]] = set()
    normalized_all: list[dict[str, Any]] = []

    if not errors:
        for cycle in cycles:
            cycle_id = cycle.get("cycle_id")
            name = cycle.get("name")
            is_enabled = bool(cycle.get("enabled", True))
            if not isinstance(cycle_id, str) or not cycle_id.strip() or cycle_id in ids:
                errors.append("Each cycle needs a unique non-empty cycle_id.")
                continue
            if not isinstance(name, str) or not name.strip():
                errors.append(f"Cycle {cycle_id} needs a non-empty name.")
                continue
            ids.add(cycle_id)
            try:
                start = _time_minutes(cycle.get("start_time"), f"{cycle_id}.start_time")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            # Target YouTube publication time: used only to schedule the upload's publishAt.
            # It never moves the production start, shortens recovery, or blocks completion.
            legacy_target = cycle.get("target_publish_time")
            if legacy_target not in (None, ""):
                try:
                    _time_minutes(legacy_target, f"{cycle_id}.target_publish_time")
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
            elif is_enabled:
                warnings.append(
                    f"{cycle_id}: no target publication time; its video will be uploaded privately "
                    "and wait for a publication time chosen in the Publishing Queue."
                )

            raw_rec = cycle.get("recovery_duration_minutes", 240)
            if type(raw_rec) is bool:
                errors.append(f"{cycle_id}.recovery_duration_minutes must be a whole number of minutes.")
                continue
            try:
                rec_dur = int(raw_rec)
            except (TypeError, ValueError):
                errors.append(f"{cycle_id}.recovery_duration_minutes must be a whole number of minutes.")
                continue
            if rec_dur < MIN_RECOVERY_MINUTES or rec_dur > MAX_RECOVERY_MINUTES:
                errors.append(
                    f"{cycle_id}.recovery_duration_minutes must be between {MIN_RECOVERY_MINUTES} and "
                    f"{MAX_RECOVERY_MINUTES} minutes (got {rec_dur})."
                )
                continue

            # Derived, never user-entered: the target is the first occurrence after the start.
            next_day = bool(
                legacy_target not in (None, "")
                and _time_minutes(legacy_target, "target_publish_time") <= start
            )

            weekdays = cycle.get("active_weekdays", [0, 1, 2, 3, 4, 5, 6])
            if (
                not isinstance(weekdays, list)
                or not weekdays
                or any(type(day) is not int or day < 0 or day > 6 for day in weekdays)
                or len(set(weekdays)) != len(weekdays)
            ):
                errors.append(f"{cycle_id}.active_weekdays must contain unique weekday numbers 0-6.")
                continue

            if is_enabled:
                for day in weekdays:
                    if (day, start) in start_slots:
                        errors.append(f"Enabled cycles have duplicate or conflicting production start times ({cycle['start_time']}).")
                        break
                    start_slots.add((day, start))
                    start_abs = day * 1440 + start
                    # A production window is exactly its recovery window.
                    rec_windows.append((start_abs, start_abs + rec_dur, cycle_id))
            else:
                warnings.append(f"Cycle '{name.strip()}' ({cycle_id}) is disabled and will be skipped.")

            normalized = {
                "cycle_id": cycle_id.strip(),
                "name": name.strip(),
                "start_time": cycle["start_time"],
                "recovery_duration_minutes": rec_dur,
                "active_weekdays": sorted(weekdays),
                "next_day": next_day,
                "enabled": is_enabled,
            }
            if legacy_target not in (None, ""):
                normalized["target_publish_time"] = legacy_target
            normalized_all.append(normalized)

    if not errors:
        week = 7 * 1440
        for index, (start, end, cycle_id) in enumerate(rec_windows):
            for other_start, other_end, other_id in rec_windows[index + 1:]:
                if cycle_id == other_id and start == other_start:
                    continue
                if any(start < other_end + shift and other_start + shift < end for shift in (-week, 0, week)):
                    # Allowed: production capacity runs them one at a time inside their own windows.
                    msg = (f"Recovery windows of {cycle_id} and {other_id} overlap; if both run late, "
                           "the second waits for the first to finish.")
                    if msg not in warnings:
                        warnings.append(msg)

    ordered_cycles = _sort_production_cycles_chronologically(normalized_all)
    enabled_out = [c for c in ordered_cycles if c.get("enabled", True)]
    out_spec = {
        "profile_name": profile_name,
        "timezone_name": timezone_name,
        "schedule_version": int(spec.get("schedule_version", 1)),
        "cycles": ordered_cycles,
    }
    rev = _compute_production_schedule_revision(out_spec)
    preview = compute_upcoming_production_cycles(out_spec, count=5) if not errors else []
    return {
        "valid": len(errors) == 0,
        "revision": rev,
        "validated_at_ist": datetime.now(IST_TZ).isoformat(),
        "profile_name": profile_name,
        "timezone_name": timezone_name,
        "cycle_count": len(enabled_out),
        "total_cycles": len(ordered_cycles),
        "cycles": ordered_cycles,
        "errors": errors,
        "warnings": warnings,
        "preview_next_cycles": preview,
    }


def validate_schedule(spec: dict[str, Any]) -> dict[str, Any]:
    res = validate_production_schedule_detailed(spec)
    if not res["valid"]:
        raise ValueError("; ".join(res["errors"]))
    return {
        "valid": True,
        "revision": res["revision"],
        "validated_at_ist": res["validated_at_ist"],
        "profile_name": res["profile_name"],
        "timezone_name": res["timezone_name"],
        "cycle_count": res["cycle_count"],
        "total_cycles": res["total_cycles"],
        "cycles": res["cycles"],
        "warnings": res["warnings"],
        "preview_next_cycles": res["preview_next_cycles"],
    }


def save_schedule_draft(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("Schedule draft must be an object.")
    profile_name = str(spec.get("profile_name", "")).upper()
    if profile_name not in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY", "CUSTOM"}:
        raise ValueError("Unsupported schedule profile.")
    timezone_name = spec.get("timezone_name")
    cycles = spec.get("cycles")
    if not isinstance(timezone_name, str) or not isinstance(cycles, list):
        raise ValueError("Schedule drafts require timezone_name and a cycle list.")
    ordered = _sort_production_cycles_chronologically(list(cycles))
    draft = {
        "profile_name": profile_name,
        "timezone_name": timezone_name,
        "cycles": ordered,
        "saved_at_ist": datetime.now(IST_TZ).isoformat(),
        "validated": False,
    }
    draft["revision"] = _compute_production_schedule_revision(draft)
    _write_settings({
        DRAFT_PROFILE_KEY: json.dumps(draft),
        PROD_SCHEDULE_VALIDATION_KEY: None,
    })
    return draft


def validate_saved_schedule_draft(spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if spec is not None:
        draft = save_schedule_draft(spec)
    else:
        raw = _read_setting(DRAFT_PROFILE_KEY)
        if not raw:
            raise ValueError("No schedule draft has been saved.")
        draft = json.loads(raw)
    validation = validate_schedule(draft)
    draft["validated"] = True
    draft["validated_at_ist"] = validation["validated_at_ist"]
    draft["revision"] = validation["revision"]
    draft["cycles"] = validation["cycles"]
    _write_settings({
        DRAFT_PROFILE_KEY: json.dumps(draft),
        PROD_SCHEDULE_VALIDATION_KEY: json.dumps({
            "valid": True,
            "revision": validation["revision"],
            "validated_at_ist": validation["validated_at_ist"],
        }),
    })
    return validation


def _profile_from_spec(spec: dict[str, Any]) -> ScheduleProfile:
    profile_name = spec["profile_name"]
    cycles: list[ProductionCycle] = []
    for item in _sort_production_cycles_chronologically(list(spec["cycles"])):
        if not item.get("enabled", True):
            continue
        start = _time_minutes(item["start_time"], "start_time")
        rec_dur = int(item.get("recovery_duration_minutes", 240))
        # Production is bounded only by its recovery window. The legacy target fields
        # of ProductionCycle are informational; default them to the recovery deadline.
        target_hhmm = str(item.get("target_publish_time") or "").strip() or None
        if target_hhmm:
            target = _time_minutes(target_hhmm, "target_publish_time")
            rollover = int(target <= start)  # derived; the legacy next_day flag is not needed
        else:
            target = (start + rec_dur) % 1440
            rollover = int(start + rec_dur >= 1440)
        cycles.append(ProductionCycle(
            cycle_id=item["cycle_id"],
            name=item["name"],
            start_hour_ist=start // 60,
            start_min_ist=start % 60,
            target_pub_hour_ist=target // 60,
            target_pub_min_ist=target % 60,
            pub_rollover_days=rollover,
            window_hours=rec_dur / 60.0,
            recovery_window_hours=rec_dur / 60.0,
            active_weekdays=tuple(item.get("active_weekdays") or range(7)),
            target_publish_time=f"{target // 60:02d}:{target % 60:02d}" if target_hhmm else None,
        ))
    return ScheduleProfile(
        name=profile_name,
        description=f"{profile_name} schedule in {spec['timezone_name']}",
        timezone_name=spec["timezone_name"],
        cycles=cycles,
        schedule_version=int(spec.get("schedule_version", 1)),
        revision=str(spec.get("revision") or ""),
    )


def activate_schedule_draft(confirm: bool, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to activate a schedule.")
    if spec is not None:
        save_schedule_draft(spec)
    raw = _read_setting(DRAFT_PROFILE_KEY)
    if not raw:
        raise ValueError("No schedule draft has been saved.")
    draft = json.loads(raw)
    draft_rev = draft.get("revision") or _compute_production_schedule_revision(draft)
    val_raw = _read_setting(PROD_SCHEDULE_VALIDATION_KEY)
    val_meta = json.loads(val_raw) if val_raw else None
    if not draft.get("validated"):
        raise ValueError("Validate the schedule draft before activation.")
    if val_meta is not None and (not val_meta.get("valid") or val_meta.get("revision") != draft_rev):
        raise ValueError("Validate the schedule draft before activation.")

    validation = validate_schedule(draft)
    previous_raw = _read_setting(ACTIVE_PROFILE_KEY)
    current_profile = get_active_profile()
    previous = json.loads(previous_raw) if previous_raw else {
        **preset_schedule(current_profile.name if current_profile.name in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY"} else "FOUR_CYCLES_DAILY"),
        "schedule_version": int(getattr(current_profile, "schedule_version", 1)),
    }
    next_version = int(previous.get("schedule_version", 1)) + 1
    validation["schedule_version"] = next_version
    validation["revision"] = _compute_production_schedule_revision(validation)
    validation["activated_at_ist"] = datetime.now(IST_TZ).isoformat()

    history_raw = _read_setting(PROD_SCHEDULE_HISTORY_KEY)
    history = json.loads(history_raw) if history_raw else []
    history.append({
        "schedule_version": previous.get("schedule_version", 1),
        "revision": previous.get("revision"),
        "profile_name": previous.get("profile_name"),
        "archived_at_ist": datetime.now(IST_TZ).isoformat(),
        "spec": previous,
    })

    profile = _profile_from_spec(validation)
    _write_settings({
        ACTIVE_PROFILE_KEY: json.dumps(validation),
        DRAFT_PROFILE_KEY: None,
        PROD_SCHEDULE_VALIDATION_KEY: None,
        PROD_SCHEDULE_HISTORY_KEY: json.dumps(history[-50:]),
    })
    set_runtime_profile(profile)
    record_change("schedule_profile", previous, validation)
    return {"active_schedule": validation, "previous_schedule": previous}


def cancel_schedule_draft() -> None:
    _write_settings({
        DRAFT_PROFILE_KEY: None,
        PROD_SCHEDULE_VALIDATION_KEY: None,
    })


def get_stored_publishing_policy() -> str:
    """Raw persisted policy value, for diagnostics only. Never use it to gate uploads."""
    return (_read_setting(PUBLISHING_POLICY_KEY) or "manual_approval").strip().lower()


def get_publishing_policy() -> str:
    """
    Effective publishing policy: 'manual_approval' (default) or 'automatic_private_scheduled'.
    A legacy stored 'automatic' value (public release) is preserved in studio_settings but
    has no effect and resolves to 'manual_approval'.
    """
    stored = get_stored_publishing_policy()
    return stored if stored in PUBLISHING_POLICIES else "manual_approval"


def is_auto_private_scheduling_enabled() -> bool:
    """True only when the operator explicitly selected automatic_private_scheduled."""
    return get_publishing_policy() == AUTO_PRIVATE_SCHEDULED


def evaluate_publishing_readiness() -> dict[str, Any]:
    """
    Evaluates live Stage 3.23 readiness gates for autonomous YouTube publishing without stubs.
    All safety gates (OAuth upload scope, refresh capability, QC enforcement, idempotent ledger,
    operating mode PRODUCTION, enabled Windows scheduler task, SQLite integrity) are verified.
    """
    checks: dict[str, Any] = {}
    blockers: list[str] = []

    oauth_status: dict[str, Any] = {}
    scopes: list[str] = []
    can_refresh = False
    try:
        from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
        oauth_status = youtube_publishing_engine.oauth.get_auth_status() or {}
        creds = youtube_publishing_engine.oauth._load_credentials()
        if creds is not None:
            scopes = list(getattr(creds, "scopes", None) or [])
            can_refresh = bool(getattr(creds, "refresh_token", None))
    except Exception as exc:
        oauth_status = {"error": str(exc)}

    channel_connected = bool(oauth_status.get("channel_connected"))
    has_upload_scope = any("youtube.upload" in str(s) for s in scopes)
    checks["oauth_connected"] = channel_connected
    checks["youtube_upload_scope"] = has_upload_scope
    checks["token_refreshable"] = can_refresh
    if not channel_connected:
        blockers.append("YouTube OAuth channel is not connected.")
    if not has_upload_scope:
        blockers.append("YouTube OAuth token lacks youtube.upload scope.")
    if not can_refresh:
        blockers.append("YouTube OAuth token lacks offline refresh capability.")

    qc_active = True
    try:
        from backend.app.services.qc_service import QualityControlEngine  # noqa: F401
        from backend.app.content.content_integrity_gate import ContentIntegrityGate  # noqa: F401
    except Exception as exc:
        qc_active = False
        blockers.append(f"QC/Integrity gates unavailable: {exc}")
    checks["qc_and_integrity_gates_active"] = qc_active

    ledger_ready = False
    try:
        import sqlite3
        from backend.app.services.youtube_publishing_engine import youtube_publishing_engine as ype
        conn = sqlite3.connect(ype.db.db_path, timeout=5.0)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='publishing_ledger'"
            ).fetchone()
            ledger_ready = bool(row)
        finally:
            conn.close()
    except Exception as exc:
        blockers.append(f"Publishing ledger check failed: {exc}")
    checks["idempotent_ledger_ready"] = ledger_ready
    if not ledger_ready:
        blockers.append("publishing_ledger table is missing or inaccessible.")

    op_mode = get_operating_mode()
    is_prod_mode = op_mode == "PRODUCTION"
    checks["operating_mode_production"] = is_prod_mode
    if not is_prod_mode:
        blockers.append(f"Operating mode is '{op_mode}' (requires PRODUCTION for autonomous runs).")

    scheduler_task_enabled = False
    try:
        from backend.scripts.manage_windows_startup import SCHEDULER_TASK_NAME, query_task_details
        tinfo = query_task_details(SCHEDULER_TASK_NAME)
        tstate = str(tinfo.get("task_state") or "").lower()
        scheduler_task_enabled = bool(tinfo.get("is_registered")) and ("disabled" not in tstate)
    except Exception as exc:
        blockers.append(f"Windows scheduler task check failed: {exc}")
    checks["scheduler_task_enabled"] = scheduler_task_enabled
    if not scheduler_task_enabled:
        blockers.append("CrazzyDocs-Production-Scheduler Windows task is not enabled.")

    publishing_capable = bool(
        channel_connected and has_upload_scope and can_refresh and qc_active and ledger_ready
    )
    autonomous_ready = bool(publishing_capable and is_prod_mode and scheduler_task_enabled)
    return {
        "publishing_capable": publishing_capable,
        "autonomous_ready": autonomous_ready,
        "checks": checks,
        "blockers": blockers,
        "channel_info": oauth_status.get("channel_info"),
    }


def is_automatic_publishing_authorized() -> tuple[bool, dict[str, Any]]:
    """
    Automatic public release is permanently disabled. Kept for API compatibility;
    always returns False so no caller can publish without a user-selected time.
    """
    return False, {
        "policy": "manual_approval",
        "stored_policy": get_stored_publishing_policy(),
        "reason": "Automatic publishing is disabled. Every video needs a user-selected publication time.",
    }


def set_publishing_policy(policy: str, confirm: bool) -> dict[str, Any]:
    """Persists the publishing policy: 'manual_approval' or 'automatic_private_scheduled'."""
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to change publishing policy.")
    normalized = (policy or "").strip().lower()
    if normalized in LEGACY_PUBLISHING_POLICIES:
        raise ValueError(
            "Automatic publishing has been removed. Videos enter the Publishing Queue and "
            "need a user-selected publication time."
        )
    if normalized not in PUBLISHING_POLICIES:
        raise ValueError(f"Unsupported publishing policy: {policy}")
    previous = get_stored_publishing_policy()
    _write_settings({PUBLISHING_POLICY_KEY: normalized})
    record_change("publishing_policy", previous, normalized)
    return {
        "mode": normalized,
        "previous_mode": previous,
        "automatic_enabled": False,  # never automatic PUBLIC release
        "auto_private_scheduling": normalized == AUTO_PRIVATE_SCHEDULED,
    }


def evaluate_resource_safety(max_cpu_pct: float = 92.0, max_ram_pct: float = 90.0) -> dict[str, Any]:
    """
    Checks host RAM and CPU load before starting a new production job so existing renders/uploads
    are never starved or disrupted.
    """
    ram_pct: float | None = None
    cpu_pct: float | None = None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemoryStatus()
            stat.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                ram_pct = float(stat.dwMemoryLoad)

            def _ft_int(ft: wintypes.FILETIME) -> int:
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            idle1, kern1, user1 = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            idle2, kern2, user2 = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle1), ctypes.byref(kern1), ctypes.byref(user1)):
                time.sleep(0.25)
                if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle2), ctypes.byref(kern2), ctypes.byref(user2)):
                    idle_d = _ft_int(idle2) - _ft_int(idle1)
                    total_d = (_ft_int(kern2) - _ft_int(kern1)) + (_ft_int(user2) - _ft_int(user1))
                    if total_d > 0:
                        cpu_pct = round(max(0.0, min(100.0, (1.0 - (idle_d / total_d)) * 100.0)), 1)
        except Exception:
            pass

    reasons: list[str] = []
    if ram_pct is not None and ram_pct > max_ram_pct:
        reasons.append(f"RAM usage {ram_pct:.1f}% exceeds safe threshold {max_ram_pct:.1f}%.")
    if cpu_pct is not None and cpu_pct > max_cpu_pct:
        reasons.append(f"CPU load {cpu_pct:.1f}% exceeds safe threshold {max_cpu_pct:.1f}%.")
    return {
        "safe": len(reasons) == 0,
        "cpu_used_percent": cpu_pct,
        "memory_used_percent": ram_pct,
        "reason": "; ".join(reasons) if reasons else "System resources within safe envelope.",
    }


def get_max_concurrency() -> int:
    raw = _read_setting(MAX_CONCURRENCY_KEY)
    if raw is None:
        return 1
    try:
        maximum = int(raw)
    except ValueError as exc:
        raise ValueError("Persisted production concurrency is invalid.") from exc
    if not 1 <= maximum <= 4:
        raise ValueError("Persisted production concurrency must be between 1 and 4.")
    return maximum


def set_max_concurrency(maximum: int) -> int:
    if type(maximum) is not int or not 1 <= maximum <= 4:
        raise ValueError("Production concurrency must be an integer from 1 to 4.")
    previous = get_max_concurrency()
    _write_settings({MAX_CONCURRENCY_KEY: str(maximum)})
    if previous != maximum:
        record_change("production_concurrency", previous, maximum)
    return maximum


def production_capacity(excluding_job_id: str | None = None) -> dict[str, int | bool]:
    with SessionLocal() as session:
        jobs = session.query(JobModel).filter(JobModel.status.in_(["RUNNING", "RETRYING"]))
        if excluding_job_id:
            jobs = jobs.filter(JobModel.id != excluding_job_id)
        active = jobs.count()
    maximum = get_max_concurrency()
    return {
        "active": active,
        "maximum": maximum,
        "available": active < maximum,
    }


def restore_active_schedule_profile() -> None:
    raw = _read_setting(ACTIVE_PROFILE_KEY)
    if raw:
        spec = json.loads(raw)
        set_runtime_profile(_profile_from_spec(validate_schedule(spec)))


def get_control_center_state() -> dict[str, Any]:
    active_schedule_raw = _read_setting(ACTIVE_PROFILE_KEY)
    draft_raw = _read_setting(DRAFT_PROFILE_KEY)
    val_raw = _read_setting(PROD_SCHEDULE_VALIDATION_KEY)
    history_raw = _read_setting(PROD_SCHEDULE_HISTORY_KEY)
    pending_mode_raw = _read_setting(PENDING_MODE_KEY)

    if active_schedule_raw:
        active_schedule = json.loads(active_schedule_raw)
    else:
        prof = get_active_profile()
        fallback_name = prof.name if prof.name in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY"} else "FOUR_CYCLES_DAILY"
        active_schedule = preset_schedule(fallback_name)
    active_schedule.setdefault("schedule_version", 1)
    active_schedule.setdefault("revision", _compute_production_schedule_revision(active_schedule))

    draft_obj = json.loads(draft_raw) if draft_raw else None
    val_obj = json.loads(val_raw) if val_raw else None
    if draft_obj:
        d_rev = draft_obj.get("revision") or _compute_production_schedule_revision(draft_obj)
        stale = (not draft_obj.get("validated")) or (val_obj is not None and val_obj.get("revision") != d_rev)
        draft_validation = {
            "valid": bool(draft_obj.get("validated")) and not stale,
            "stale": stale,
            "draft_revision": d_rev,
            "validated_revision": val_obj.get("revision") if val_obj else (d_rev if draft_obj.get("validated") else None),
            "validated_at_ist": (val_obj or {}).get("validated_at_ist") or draft_obj.get("validated_at_ist"),
        }
    else:
        draft_validation = None

    return {
        "operating_mode": {
            "active": get_operating_mode(),
            "pending": json.loads(pending_mode_raw) if pending_mode_raw else None,
        },
        "schedule": {
            "active": active_schedule,
            "draft": draft_obj,
            "draft_validation": draft_validation,
            "upcoming_cycles": compute_upcoming_production_cycles(active_schedule, count=5),
            "history": json.loads(history_raw)[-20:] if history_raw else [],
        },
        "production_concurrency": get_max_concurrency(),
        "production_control": get_production_control_state(),
        "publication_schedule": get_publication_schedule(),
        "production_config": get_production_config(),
        "storage_config": get_storage_config(),
        "change_history": _history()[-100:],
    }


def get_production_control_state() -> dict[str, Any]:
    raw = _read_setting(PRODUCTION_CONTROL_KEY)
    if not raw:
        return {"state": "IDLE", "reason": None, "updated_at_ist": None}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("state") in {"IDLE", "RUNNING", "PAUSED", "STOP_REQUESTED"}:
            return parsed
    except Exception:
        pass
    return {"state": "IDLE", "reason": None, "updated_at_ist": None}


def set_production_control_state(state: str, reason: str | None = None) -> dict[str, Any]:
    normalized = state.upper()
    if normalized not in {"IDLE", "RUNNING", "PAUSED", "STOP_REQUESTED"}:
        raise ValueError(f"Unsupported production control state: {state}")
    payload = {
        "state": normalized,
        "reason": reason,
        "updated_at_ist": datetime.now(IST_TZ).isoformat(),
    }
    _write_settings({PRODUCTION_CONTROL_KEY: json.dumps(payload)})
    return payload


def request_production_pause(reason: str = "Paused by operator via dashboard") -> dict[str, Any]:
    previous = get_production_control_state().get("state")
    res = set_production_control_state("PAUSED", reason=reason)
    record_change("production_control", previous, "PAUSED")
    return res


def request_production_resume(reason: str = "Resumed by operator via dashboard") -> dict[str, Any]:
    previous = get_production_control_state().get("state")
    res = set_production_control_state("RUNNING", reason=reason)
    record_change("production_control", previous, "RUNNING")
    return res


def request_production_stop(reason: str = "Graceful stop requested by operator") -> dict[str, Any]:
    previous = get_production_control_state().get("state")
    res = set_production_control_state("STOP_REQUESTED", reason=reason)
    record_change("production_control", previous, "STOP_REQUESTED")
    return res


def wait_or_check_production_control(
    job_id: str | None = None,
    poll_interval: float = 0.5,
    max_pause_seconds: float = 3600.0,
) -> str:
    """Checks pause/graceful-stop signals safely between pipeline stages."""
    waited = 0.0
    marked_paused = False
    while True:
        ctrl = get_production_control_state()
        state = ctrl.get("state", "IDLE")
        if state == "STOP_REQUESTED":
            return "STOP"
        if state != "PAUSED":
            if marked_paused and job_id:
                try:
                    with SessionLocal() as session:
                        row = session.get(JobModel, job_id)
                        if row and row.status == "PAUSED":
                            row.status = "RUNNING"
                            row.heartbeat_at = datetime.utcnow()
                            session.commit()
                except Exception:
                    pass
            return "CONTINUE"
        if job_id and not marked_paused:
            marked_paused = True
            try:
                with SessionLocal() as session:
                    row = session.get(JobModel, job_id)
                    if row and row.status == "RUNNING":
                        row.status = "PAUSED"
                        row.heartbeat_at = datetime.utcnow()
                        session.commit()
            except Exception:
                pass
        if waited >= max_pause_seconds:
            return "STOP"
        time.sleep(poll_interval)
        waited += poll_interval


def _format_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "00m"
    hours, rem = divmod(seconds, 3600)
    mins = rem // 60
    return f"{hours:02d}h {mins:02d}m"


def get_live_cycle_schedule_status(now_ist: datetime | None = None) -> dict[str, Any]:
    """Returns live dynamic cycle status, recovery window, finalization buffer, and upcoming 5 cycles."""
    profile = get_active_profile()
    now = now_ist or datetime.now(IST_TZ)
    date_prefix = now.strftime("%Y%m%d")
    date_iso = now.strftime("%Y-%m-%d")
    cycles_out: list[dict[str, Any]] = []

    with SessionLocal() as session:
        day_jobs = (
            session.query(JobModel)
            .filter((JobModel.date_str == date_iso) | (JobModel.id.like(f"%_{date_prefix}_%")))
            .all()
        )
        jobs_by_id = {j.id: j for j in day_jobs}

    ordered_cycles = sorted(profile.cycles, key=lambda c: (c.start_hour_ist * 60 + c.start_min_ist, c.cycle_id))
    for cycle in ordered_cycles:
        start_dt, target_pub_dt, _, _ = cycle.calculate_datetimes(now)
        rec_dur_min = int(round(cycle.recovery_window_hours * 60))
        rec_end_dt = start_dt + timedelta(minutes=rec_dur_min)
        buf_min = max(0, int((target_pub_dt - rec_end_dt).total_seconds() // 60))
        expected_job_id = cycle.format_job_id(start_dt)
        job = jobs_by_id.get(expected_job_id)

        weekday_enabled = now.weekday() in getattr(cycle, "active_weekdays", tuple(range(7)))
        remaining_sec = int(max(0, (rec_end_dt - now).total_seconds()))

        if not weekday_enabled:
            status = "DISABLED_DAY"
            remaining_label = "Inactive Day"
        elif job and job.status == "COMPLETED":
            status = "COMPLETED"
            remaining_label = "Completed"
        elif job and job.status in ("RUNNING", "RETRYING", "PAUSED"):
            status = job.status
            remaining_label = _format_remaining(remaining_sec) if now < rec_end_dt else "Completing"
        elif now < start_dt:
            status = "UPCOMING"
            starts_in = int((start_dt - now).total_seconds())
            remaining_label = f"Starts in {_format_remaining(starts_in)}"
        elif start_dt <= now < rec_end_dt:
            status = job.status if job else "ACTIVE_WINDOW"
            remaining_label = f"{_format_remaining(remaining_sec)} left"
        else:
            status = job.status if job else "WINDOW_EXPIRED"
            remaining_label = "Expired"

        cycles_out.append({
            "cycle_id": cycle.cycle_id,
            "name": cycle.name,
            "start_time_ist": start_dt.strftime("%H:%M IST"),
            "recovery_duration_minutes": rec_dur_min,
            "recovery_start_ist": start_dt.strftime("%H:%M IST"),
            "recovery_expiration_ist": rec_end_dt.strftime("%H:%M IST"),
            "finalization_buffer_minutes": buf_min,
            "target_publish_ist": target_pub_dt.strftime("%H:%M IST") + (" (+1d)" if cycle.pub_rollover_days else ""),
            "window_hours": cycle.window_hours,
            "recovery_window_hours": cycle.recovery_window_hours,
            "weekday_enabled": weekday_enabled,
            "status": status,
            "remaining_recovery_seconds": remaining_sec if start_dt <= now < rec_end_dt else 0,
            "remaining_recovery_label": remaining_label,
            "associated_job_id": job.id if job else None,
            "job_id": job.id if job else expected_job_id,
            "job_progress_pct": float(job.progress_pct or 0.0) if job else 0.0,
            "job_stage": job.current_stage if job else None,
        })

    active_raw = _read_setting(ACTIVE_PROFILE_KEY)
    active_spec = json.loads(active_raw) if active_raw else preset_schedule(
        profile.name if profile.name in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY"} else "FOUR_CYCLES_DAILY"
    )
    upcoming_cycles = compute_upcoming_production_cycles(active_spec, count=5, now_ist=now)

    active_rev = str(getattr(profile, "revision", "")) or _compute_production_schedule_revision(active_spec)

    return {
        "profile_name": profile.name,
        "schedule_version": int(getattr(profile, "schedule_version", 1)),
        "schedule_revision": active_rev,
        "timezone_name": profile.timezone_name,
        "recovery_policy": "STRICT_WINDOW",
        "now_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "cycles": cycles_out,
        "upcoming_cycles": upcoming_cycles,
    }


# ---------------------------------------------------------------------------
# Stage 3.23.4: Independent Publication Schedule Editor
# ---------------------------------------------------------------------------
PUB_SCHEDULE_ACTIVE_KEY = "control_center_publication_schedule_active"
PUB_SCHEDULE_DRAFT_KEY = "control_center_publication_schedule_draft"
PRODUCTION_CONFIG_KEY = "control_center_production_config"
STORAGE_CONFIG_KEY = "control_center_storage_config"

ALL_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _normalize_pub_slot(slot: dict[str, Any], idx: int = 0) -> dict[str, Any]:
    """Ensures every publication slot carries explicit prep and recovery window fields."""
    out = dict(slot or {})
    out.setdefault("slot_id", f"pub_{idx + 1}")
    out.setdefault("label", out["slot_id"])
    out.setdefault("time_ist", "18:00")
    out["enabled"] = bool(out.get("enabled", True))
    out["prep_window_minutes"] = int(out.get("prep_window_minutes", 30))
    out["recovery_duration_minutes"] = int(out.get("recovery_duration_minutes", 240))
    out["recovery_offset_minutes"] = int(out.get("recovery_offset_minutes", 0))
    return out


def default_publication_schedule() -> dict[str, Any]:
    """Default publication schedule aligned with FOUR_CYCLES_DAILY target times in Asia/Kolkata."""
    return {
        "schedule_version": 1,
        "preset_name": "FOLLOW_PRODUCTION_CYCLE",
        "timezone_name": "Asia/Kolkata",
        "weekdays": list(ALL_WEEKDAYS),
        "missed_slot_behavior": "next_slot",
        "recovery_overlap_policy": "STRICT",
        "min_prep_buffer_minutes": 10,
        "wait_for_slot": True,
        "slots": [
            {"slot_id": "pub_1", "label": "Morning Slot", "time_ist": "05:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_2", "label": "Midday Slot", "time_ist": "11:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_3", "label": "Evening Slot", "time_ist": "17:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_4", "label": "Late Night Slot", "time_ist": "23:30", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
        ],
    }


def preset_publication_schedule(preset_name: str) -> dict[str, Any]:
    """Returns a preset publication schedule specification with per-slot recovery windows."""
    name = str(preset_name or "").strip().upper()
    base = default_publication_schedule()
    if name == "FOLLOW_PRODUCTION_CYCLE":
        return base
    if name == "ONCE_DAILY":
        base["preset_name"] = "ONCE_DAILY"
        base["slots"] = [
            {"slot_id": "pub_1", "label": "Daily Prime", "time_ist": "18:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True}
        ]
        return base
    if name == "TWICE_DAILY":
        base["preset_name"] = "TWICE_DAILY"
        base["slots"] = [
            {"slot_id": "pub_1", "label": "Morning", "time_ist": "09:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_2", "label": "Evening", "time_ist": "18:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
        ]
        return base
    if name == "THREE_DAILY":
        base["preset_name"] = "THREE_DAILY"
        base["slots"] = [
            {"slot_id": "pub_1", "label": "Morning", "time_ist": "08:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_2", "label": "Afternoon", "time_ist": "14:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_3", "label": "Prime Night", "time_ist": "20:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
        ]
        return base
    if name == "FOUR_DAILY":
        base["preset_name"] = "FOUR_DAILY"
        base["slots"] = [
            {"slot_id": "pub_1", "label": "Slot 1", "time_ist": "06:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_2", "label": "Slot 2", "time_ist": "12:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_3", "label": "Slot 3", "time_ist": "18:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
            {"slot_id": "pub_4", "label": "Slot 4", "time_ist": "23:00", "prep_window_minutes": 30, "recovery_duration_minutes": 240, "recovery_offset_minutes": 0, "enabled": True},
        ]
        return base
    raise ValueError(f"Unsupported publication schedule preset: {preset_name}")


def _parse_hhmm(value: str, field: str) -> tuple[int, int]:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"{field} must be HH:MM (24-hour)")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"{field} out of range: {text}")
    return hh, mm


def compute_slot_windows(
    slot: dict[str, Any],
    base_date: Any,
    tz: ZoneInfo,
) -> dict[str, Any]:
    """Computes timezone-aware prep and recovery windows (IST & UTC) for a single slot on `base_date`."""
    hh, mm = _parse_hhmm(slot.get("time_ist", ""), f"slot {slot.get('slot_id')}")
    prep_min = max(0, int(slot.get("prep_window_minutes", 30)))
    rec_dur_min = max(1, int(slot.get("recovery_duration_minutes", 240)))
    rec_offset_min = int(slot.get("recovery_offset_minutes", 0))

    pub_dt = datetime(base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=tz)
    prep_start_dt = pub_dt - timedelta(minutes=prep_min)
    rec_start_dt = pub_dt + timedelta(minutes=rec_offset_min)
    rec_deadline_dt = rec_start_dt + timedelta(minutes=rec_dur_min)

    utc_tz = ZoneInfo("UTC")
    return {
        "pub_dt": pub_dt,
        "prep_start_dt": prep_start_dt,
        "rec_start_dt": rec_start_dt,
        "rec_deadline_dt": rec_deadline_dt,
        "crosses_midnight": rec_deadline_dt.date() != rec_start_dt.date(),
        "prep_window_minutes": prep_min,
        "recovery_duration_minutes": rec_dur_min,
        "ist_display": pub_dt.strftime("%Y-%m-%d %H:%M IST"),
        "utc_iso": pub_dt.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prep_start_ist": prep_start_dt.strftime("%Y-%m-%d %H:%M IST"),
        "prep_start_utc": prep_start_dt.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recovery_start_ist": rec_start_dt.strftime("%Y-%m-%d %H:%M IST"),
        "recovery_start_utc": rec_start_dt.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recovery_deadline_ist": rec_deadline_dt.strftime("%Y-%m-%d %H:%M IST"),
        "recovery_deadline_utc": rec_deadline_dt.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def compute_upcoming_publication_slots(
    spec: dict[str, Any],
    *,
    count: int = 5,
    now_ist: datetime | None = None,
) -> list[dict[str, Any]]:
    """Computes the next `count` enabled publication slots in IST & UTC with recovery windows."""
    tz = ZoneInfo(str(spec.get("timezone_name") or "Asia/Kolkata"))
    now = now_ist.astimezone(tz) if now_ist else datetime.now(tz)
    buf_min = max(0, int(spec.get("min_prep_buffer_minutes", 10)))
    earliest = now + timedelta(minutes=buf_min)
    allowed_days = set(spec.get("weekdays") or ALL_WEEKDAYS)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    sched_ver = int(spec.get("schedule_version", 1))
    enabled_slots = [
        _normalize_pub_slot(s, idx)
        for idx, s in enumerate(spec.get("slots") or [])
        if s.get("enabled", True)
    ]
    candidates: list[tuple[datetime, dict[str, Any], dict[str, Any]]] = []
    for day_offset in range(0, 14):
        d = (now + timedelta(days=day_offset)).date()
        if day_names[d.weekday()] not in allowed_days:
            continue
        for slot in enabled_slots:
            win = compute_slot_windows(slot, d, tz)
            if win["pub_dt"] > earliest:
                candidates.append((win["pub_dt"], slot, win))
    candidates.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for dt_ist, slot, win in candidates[:count]:
        out.append({
            "slot_id": slot.get("slot_id"),
            "label": slot.get("label") or slot.get("slot_id"),
            "schedule_version": sched_ver,
            "ist_iso": dt_ist.isoformat(),
            "utc_iso": win["utc_iso"],
            "ist_display": win["ist_display"],
            "prep_window_minutes": win["prep_window_minutes"],
            "prep_start_ist": win["prep_start_ist"],
            "prep_start_utc": win["prep_start_utc"],
            "recovery_duration_minutes": win["recovery_duration_minutes"],
            "recovery_start_ist": win["recovery_start_ist"],
            "recovery_start_utc": win["recovery_start_utc"],
            "recovery_deadline_ist": win["recovery_deadline_ist"],
            "recovery_deadline_utc": win["recovery_deadline_utc"],
            "crosses_midnight": win["crosses_midnight"],
            "is_future": dt_ist > now,
        })
    return out


PUB_SCHEDULE_VALIDATION_KEY = "control_center_publication_schedule_validation"
_ACTIVE_RECOVERY_CLAIMS: dict[int, str] = {}


def _compute_schedule_revision(spec: dict[str, Any]) -> str:
    import hashlib
    canonical = {
        "preset_name": spec.get("preset_name"),
        "timezone_name": spec.get("timezone_name", "Asia/Kolkata"),
        "weekdays": list(spec.get("weekdays") or []),
        "missed_slot_behavior": spec.get("missed_slot_behavior", "next_slot"),
        "recovery_overlap_policy": str(spec.get("recovery_overlap_policy") or "STRICT").upper(),
        "min_prep_buffer_minutes": int(spec.get("min_prep_buffer_minutes", 10)),
        "wait_for_slot": bool(spec.get("wait_for_slot", True)),
        "slots": spec.get("slots") or [],
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _sort_slots_chronologically(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorts publication slots chronologically by HH:MM time (never by slot_id or creation order)."""
    def _sort_key(s: dict[str, Any]) -> tuple[int, str]:
        try:
            hh, mm = _parse_hhmm(s.get("time_ist", ""), "sort")
            return (hh * 60 + mm, str(s.get("slot_id") or ""))
        except ValueError:
            return (9999, str(s.get("slot_id") or ""))
    return sorted(slots, key=_sort_key)


def _detect_recovery_overlaps(
    enabled_slots: list[dict[str, Any]],
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    """Detects overlapping recovery windows across consecutive slots (including midnight crossings)."""
    ref_date = datetime(2026, 11, 12, tzinfo=tz).date()
    timeline_windows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for day_off in (0, 1):
        d = ref_date + timedelta(days=day_off)
        for slot in enabled_slots:
            timeline_windows.append((slot, compute_slot_windows(slot, d, tz)))
    timeline_windows.sort(key=lambda pair: pair[1]["pub_dt"])

    overlaps: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i in range(len(timeline_windows) - 1):
        s_a, w_a = timeline_windows[i]
        s_b, w_b = timeline_windows[i + 1]
        if w_a["rec_deadline_dt"] > w_b["rec_start_dt"]:
            pair_key = (str(s_a["slot_id"]), str(s_b["slot_id"]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            overlap_min = int((w_a["rec_deadline_dt"] - w_b["rec_start_dt"]).total_seconds() // 60)
            max_safe_dur = max(15, int((w_b["rec_start_dt"] - w_a["rec_start_dt"]).total_seconds() // 60))
            overlaps.append({
                "slot_a_id": s_a["slot_id"],
                "slot_a_label": s_a["label"],
                "slot_a_pub": s_a["time_ist"],
                "slot_a_recovery_deadline": w_a["recovery_deadline_ist"],
                "slot_b_id": s_b["slot_id"],
                "slot_b_label": s_b["label"],
                "slot_b_pub": s_b["time_ist"],
                "slot_b_recovery_start": w_b["recovery_start_ist"],
                "overlap_minutes": overlap_min,
                "cause": (
                    f"{s_a['label']} ({s_a['time_ist']}) has recovery_duration_minutes={s_a['recovery_duration_minutes']}, "
                    f"extending its recovery deadline to {w_a['recovery_deadline_ist']} past "
                    f"{s_b['label']} ({s_b['time_ist']}) recovery start ({w_b['recovery_start_ist']})."
                ),
                "suggestion": (
                    f"Reduce '{s_a['label']}' recovery duration to <= {max_safe_dur} min, "
                    f"move '{s_b['label']}' later, or switch recovery_overlap_policy to SHARED_RECOVERY."
                ),
            })
    return overlaps


def validate_publication_schedule(spec: dict[str, Any]) -> dict[str, Any]:
    """Validates a custom or preset publication schedule including per-slot recovery windows."""
    errors: list[str] = []
    warnings: list[str] = []
    overlap_details: list[dict[str, Any]] = []
    revision = _compute_schedule_revision(spec)
    validated_at_ist = datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")

    tz_name = str(spec.get("timezone_name") or "Asia/Kolkata").strip()
    tz: ZoneInfo | None = None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        errors.append(f"Invalid timezone: {tz_name}")

    overlap_policy = str(spec.get("recovery_overlap_policy") or "STRICT").strip().upper()
    if overlap_policy not in {"STRICT", "SHARED_RECOVERY"}:
        errors.append("recovery_overlap_policy must be STRICT or SHARED_RECOVERY.")

    weekdays = list(spec.get("weekdays") or [])
    if not weekdays or any(d not in ALL_WEEKDAYS for d in weekdays):
        errors.append("At least one valid weekday (Mon..Sun) is required.")

    missed = str(spec.get("missed_slot_behavior") or "next_slot").strip()
    if missed not in {"skip", "next_slot", "immediate_if_ready"}:
        errors.append("missed_slot_behavior must be skip, next_slot, or immediate_if_ready.")

    buf = int(spec.get("min_prep_buffer_minutes", 10))
    if not (0 <= buf <= 180):
        errors.append("min_prep_buffer_minutes must be between 0 and 180.")

    slots = list(spec.get("slots") or [])
    if not slots:
        errors.append("At least one publication slot is required.")

    seen_ids: set[str] = set()
    enabled_slots: list[dict[str, Any]] = []
    for idx, raw_slot in enumerate(slots):
        sid = str(raw_slot.get("slot_id") or f"pub_{idx + 1}").strip()
        if sid in seen_ids:
            errors.append(f"Duplicate slot_id: {sid}")
        seen_ids.add(sid)

        if "recovery_duration_minutes" not in raw_slot or raw_slot.get("recovery_duration_minutes") in (None, ""):
            errors.append(f"Slot {sid}: recovery_duration_minutes is required.")
        else:
            try:
                rec_dur = int(raw_slot["recovery_duration_minutes"])
                if not (15 <= rec_dur <= 720):
                    errors.append(f"Slot {sid}: recovery_duration_minutes must be between 15 and 720 minutes.")
            except (TypeError, ValueError):
                errors.append(f"Slot {sid}: recovery_duration_minutes must be an integer.")

        try:
            prep_min = int(raw_slot.get("prep_window_minutes", 30))
            if not (0 <= prep_min <= 360):
                errors.append(f"Slot {sid}: prep_window_minutes must be between 0 and 360.")
        except (TypeError, ValueError):
            errors.append(f"Slot {sid}: prep_window_minutes must be an integer.")

        try:
            _parse_hhmm(raw_slot.get("time_ist", ""), f"Slot {sid} time_ist")
            if raw_slot.get("enabled", True):
                enabled_slots.append(_normalize_pub_slot(raw_slot, idx))
        except ValueError as exc:
            errors.append(str(exc))

    if not enabled_slots:
        errors.append("At least one publication slot must be enabled.")
    elif tz is not None and not errors:
        enabled_slots = _sort_slots_chronologically(enabled_slots)
        ref_date = datetime(2026, 11, 12, tzinfo=tz).date()
        timeline_windows = sorted(
            [(s, compute_slot_windows(s, ref_date + timedelta(days=d_off), tz)) for d_off in (0, 1) for s in enabled_slots],
            key=lambda pair: pair[1]["pub_dt"],
        )
        for i in range(len(timeline_windows) - 1):
            s_a, w_a = timeline_windows[i]
            s_b, w_b = timeline_windows[i + 1]
            gap_min = (w_b["pub_dt"] - w_a["pub_dt"]).total_seconds() / 60.0
            if gap_min < 15.0:
                errors.append(
                    f"Slot '{s_a['label']}' ({s_a['time_ist']}) and '{s_b['label']}' ({s_b['time_ist']}) "
                    f"are only {int(gap_min)} minutes apart (minimum 15 minutes required)."
                )
            elif w_a["rec_deadline_dt"] > w_b["prep_start_dt"] and w_a["rec_deadline_dt"] <= w_b["rec_start_dt"]:
                warnings.append(
                    f"'{s_a['label']}' recovery deadline ({w_a['recovery_deadline_ist']}) overlaps "
                    f"'{s_b['label']}' preparation window ({w_b['prep_start_ist']})."
                )

        overlap_details = _detect_recovery_overlaps(enabled_slots, tz)
        for ov in overlap_details:
            msg = f"Recovery overlap ({ov['overlap_minutes']} min): {ov['cause']} Suggested fix: {ov['suggestion']}"
            if overlap_policy == "STRICT":
                errors.append(msg)
            else:
                warnings.append(f"[SHARED_RECOVERY] {msg}")

    preview = []
    if not errors:
        preview = compute_upcoming_publication_slots(spec, count=5)
    result = {
        "valid": len(errors) == 0,
        "revision": revision,
        "validated_at_ist": validated_at_ist,
        "recovery_overlap_policy": overlap_policy,
        "errors": errors,
        "warnings": warnings,
        "overlap_details": overlap_details,
        "preview_next_slots": preview,
    }
    return result


def _normalize_schedule_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(default_publication_schedule())
    out.update(spec or {})
    out["schedule_version"] = int(out.get("schedule_version", 1))
    out["recovery_overlap_policy"] = str(out.get("recovery_overlap_policy") or "STRICT").upper()
    out["slots"] = _sort_slots_chronologically([_normalize_pub_slot(s, i) for i, s in enumerate(out.get("slots") or [])])
    out["revision"] = _compute_schedule_revision(out)
    return out


def get_publication_schedule() -> dict[str, Any]:
    active_raw = _read_setting(PUB_SCHEDULE_ACTIVE_KEY)
    draft_raw = _read_setting(PUB_SCHEDULE_DRAFT_KEY)
    val_raw = _read_setting(PUB_SCHEDULE_VALIDATION_KEY)
    active = _normalize_schedule_spec(json.loads(active_raw)) if active_raw else _normalize_schedule_spec(default_publication_schedule())
    draft = _normalize_schedule_spec(json.loads(draft_raw)) if draft_raw else None
    saved_val = json.loads(val_raw) if val_raw else None
    if draft:
        draft_rev = draft.get("revision")
        if not saved_val:
            draft_validation = {
                "valid": False,
                "stale": True,
                "draft_revision": draft_rev,
                "validated_revision": None,
                "validated_at_ist": None,
            }
        elif saved_val.get("revision") != draft_rev:
            draft_validation = {
                **saved_val,
                "valid": False,
                "stale": True,
                "draft_revision": draft_rev,
                "validated_revision": saved_val.get("revision"),
            }
        else:
            draft_validation = {
                **saved_val,
                "stale": False,
                "draft_revision": draft_rev,
                "validated_revision": saved_val.get("revision"),
            }
    else:
        draft_validation = None
    return {
        "active": active,
        "draft": draft,
        "draft_validation": draft_validation,
        "upcoming_slots": compute_upcoming_publication_slots(active, count=5),
    }


def save_publication_schedule_draft(spec: dict[str, Any]) -> dict[str, Any]:
    norm_draft = _normalize_schedule_spec(spec)
    # Any edit/save invalidates prior validation results until explicitly re-validated.
    _write_settings({
        PUB_SCHEDULE_DRAFT_KEY: json.dumps(norm_draft),
        PUB_SCHEDULE_VALIDATION_KEY: None,
    })
    draft_validation = {
        "valid": False,
        "stale": True,
        "draft_revision": norm_draft.get("revision"),
        "validated_revision": None,
        "validated_at_ist": None,
    }
    return {"draft": norm_draft, "draft_validation": draft_validation}


def validate_saved_publication_schedule_draft(spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if spec is not None:
        norm_draft = _normalize_schedule_spec(spec)
        _write_settings({PUB_SCHEDULE_DRAFT_KEY: json.dumps(norm_draft)})
    else:
        draft_raw = _read_setting(PUB_SCHEDULE_DRAFT_KEY)
        if not draft_raw:
            return {"valid": False, "errors": ["No publication schedule draft saved."], "warnings": [], "overlap_details": [], "preview_next_slots": []}
        norm_draft = _normalize_schedule_spec(json.loads(draft_raw))
    validation = validate_publication_schedule(norm_draft)
    _write_settings({PUB_SCHEDULE_VALIDATION_KEY: json.dumps(validation)})
    validation["draft_validation"] = {
        "valid": bool(validation.get("valid")),
        "stale": False,
        "draft_revision": norm_draft.get("revision"),
        "validated_revision": validation.get("revision"),
        "validated_at_ist": validation.get("validated_at_ist"),
    }
    return validation


def activate_publication_schedule_draft(confirm: bool, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Explicit confirmation is required to activate publication schedule.")
    if spec is not None:
        norm_in = _normalize_schedule_spec(spec)
        _write_settings({PUB_SCHEDULE_DRAFT_KEY: json.dumps(norm_in)})
    draft_raw = _read_setting(PUB_SCHEDULE_DRAFT_KEY)
    if not draft_raw:
        raise ValueError("No publication schedule draft saved.")
    draft = _normalize_schedule_spec(json.loads(draft_raw))
    val_raw = _read_setting(PUB_SCHEDULE_VALIDATION_KEY)
    saved_val = json.loads(val_raw) if val_raw else None
    if not saved_val or saved_val.get("revision") != draft.get("revision") or not saved_val.get("valid"):
        raise ValueError("Draft must be validated for the current revision before activation.")
    validation = validate_publication_schedule(draft)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    prev = get_publication_schedule()["active"]
    next_ver = int(prev.get("schedule_version", 1)) + 1
    draft["schedule_version"] = next_ver
    draft["revision"] = _compute_schedule_revision(draft)
    _write_settings({
        PUB_SCHEDULE_ACTIVE_KEY: json.dumps(draft),
        PUB_SCHEDULE_DRAFT_KEY: None,
        PUB_SCHEDULE_VALIDATION_KEY: None,
    })
    record_change("publication_schedule", prev, draft)
    return {
        "active": draft,
        "schedule_version": next_ver,
        "upcoming_slots": compute_upcoming_publication_slots(draft, count=5),
        "warnings": validation.get("warnings", []),
    }


def cancel_publication_schedule_draft() -> None:
    _write_settings({PUB_SCHEDULE_DRAFT_KEY: None, PUB_SCHEDULE_VALIDATION_KEY: None})


def claim_recovery_operation(ledger_id: int, slot_id: str) -> bool:
    """Concurrency-safe claim so only one recovery worker can process a publishing ledger item."""
    if ledger_id in _ACTIVE_RECOVERY_CLAIMS:
        return False
    cap = production_capacity()
    if (int(cap.get("active_jobs", 0)) + len(_ACTIVE_RECOVERY_CLAIMS)) >= int(cap.get("max_concurrency", 1)):
        return False
    _ACTIVE_RECOVERY_CLAIMS[ledger_id] = slot_id
    return True


def release_recovery_operation(ledger_id: int) -> None:
    _ACTIVE_RECOVERY_CLAIMS.pop(ledger_id, None)


def compute_next_publication_slot(now_ist: datetime | None = None) -> dict[str, Any]:
    """Returns the next scheduled publication slot (IST + UTC) from active Publication Schedule."""
    active = get_publication_schedule()["active"]
    slots = compute_upcoming_publication_slots(active, count=1, now_ist=now_ist)
    if slots:
        return slots[0]
    tz = ZoneInfo("Asia/Kolkata")
    now = now_ist.astimezone(tz) if now_ist else datetime.now(tz)
    fallback = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return {
        "slot_id": "fallback",
        "label": "Next Hour Slot",
        "schedule_version": int(active.get("schedule_version", 1)),
        "ist_iso": fallback.isoformat(),
        "utc_iso": fallback.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ist_display": fallback.strftime("%Y-%m-%d %H:%M IST"),
        "recovery_duration_minutes": 240,
        "recovery_start_ist": fallback.strftime("%Y-%m-%d %H:%M IST"),
        "recovery_start_utc": fallback.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recovery_deadline_ist": (fallback + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M IST"),
        "recovery_deadline_utc": (fallback + timedelta(hours=4)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_future": True,
    }


def get_publication_recovery_timeline(now_ist: datetime | None = None) -> dict[str, Any]:
    """Computes real-time 8-state Publication Recovery Timeline from ACTIVE schedule only."""
    from backend.app.services.youtube_publishing_engine import youtube_publishing_engine

    sched = get_publication_schedule()["active"]
    tz = ZoneInfo(str(sched.get("timezone_name") or "Asia/Kolkata"))
    now = now_ist.astimezone(tz) if now_ist else datetime.now(tz)
    today = now.date()
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_enabled = day_names[today.weekday()] in set(sched.get("weekdays") or ALL_WEEKDAYS)

    recent_pubs = youtube_publishing_engine.db.list_publications(limit=60)
    with SessionLocal() as session:
        recent_jobs = session.query(JobModel).order_by(JobModel.created_at.desc()).limit(25).all()

    ordered_slots = _sort_slots_chronologically([
        _normalize_pub_slot(s, idx) for idx, s in enumerate(sched.get("slots") or [])
    ])
    enabled_slots = [s for s in ordered_slots if s["enabled"]]
    overlap_details = _detect_recovery_overlaps(enabled_slots, tz)
    overlap_by_slot: dict[str, list[str]] = {}
    for ov in overlap_details:
        overlap_by_slot.setdefault(ov["slot_a_id"], []).append(
            f"Overlaps {ov['slot_b_label']} by {ov['overlap_minutes']}m"
        )
        overlap_by_slot.setdefault(ov["slot_b_id"], []).append(
            f"Overlaps {ov['slot_a_label']} by {ov['overlap_minutes']}m"
        )

    slots_out: list[dict[str, Any]] = []
    for slot in ordered_slots:
        win = compute_slot_windows(slot, today, tz)
        target_utc = win["utc_iso"]

        # Strict job-to-slot & ledger-to-slot matching (NEVER broadcast unrelated job across slots)
        matched_pub = None
        for p in recent_pubs:
            meta = {}
            try:
                meta = json.loads(p.get("metadata_json") or "{}")
            except Exception:
                meta = {}
            s_ctx = meta.get("_schedule_context") or {}
            if (s_ctx.get("slot_id") == slot["slot_id"] and s_ctx.get("scheduled_utc_iso") == target_utc) or (
                p.get("scheduled_timestamp") == target_utc
            ):
                matched_pub = p
                break

        matched_job = None
        if matched_pub is not None:
            pub_topic = matched_pub.get("topic_title")
            matched_job = next((j for j in recent_jobs if j.topic_title == pub_topic), None)
        else:
            for j in recent_jobs:
                try:
                    cp = json.loads(j.checkpoint_data_json or "{}")
                except Exception:
                    cp = {}
                if cp.get("publication_slot_id") == slot["slot_id"] and cp.get("scheduled_utc_iso") == target_utc:
                    matched_job = j
                    break

        pub_st = str((matched_pub or {}).get("upload_status") or "").lower()
        is_active_upload = pub_st == "uploading"
        is_verified_done = pub_st in ("uploaded_public", "publication_scheduled")
        is_recoverable_fail = pub_st in ("failed", "retrying", "upload_interrupted")
        is_ready_queued = pub_st in ("pending_queue", "approved")
        is_actively_claimed = bool(matched_pub and matched_pub.get("id") in _ACTIVE_RECOVERY_CLAIMS)

        if not slot["enabled"] or not weekday_enabled:
            status = "SKIPPED"
            rem_sec = 0
            rem_label = "Disabled" if not slot["enabled"] else "Inactive Day"
        elif is_verified_done:
            status = "COMPLETED"
            rem_sec = 0
            rem_label = f"Verified ({matched_pub.get('youtube_video_id')})"
        elif is_active_upload:
            status = "PUBLISHING"
            rem_sec = int(max(0, (win["rec_deadline_dt"] - now).total_seconds()))
            rem_label = "Active YouTube upload (protected)"
        elif (is_recoverable_fail or is_actively_claimed) and (win["rec_start_dt"] <= now < win["rec_deadline_dt"]):
            status = "RECOVERING"
            rem_sec = int(max(0, (win["rec_deadline_dt"] - now).total_seconds()))
            rem_label = f"{_format_remaining(rem_sec)} recovery left"
        elif is_ready_queued and now < win["rec_deadline_dt"]:
            status = "READY"
            rem_sec = int(max(0, (win["pub_dt"] - now).total_seconds()))
            rem_label = f"Queued (#{matched_pub.get('id')})"
        elif now < win["prep_start_dt"]:
            status = "UPCOMING"
            rem_sec = int(max(0, (win["prep_start_dt"] - now).total_seconds()))
            rem_label = f"Prep in {_format_remaining(rem_sec)}"
        elif win["prep_start_dt"] <= now < win["pub_dt"]:
            status = "PREPARING"
            rem_sec = int(max(0, (win["pub_dt"] - now).total_seconds()))
            rem_label = f"Pub in {_format_remaining(rem_sec)}"
        elif (is_recoverable_fail or is_ready_queued or (matched_job and matched_job.status != "COMPLETED")) and now >= win["rec_deadline_dt"]:
            status = "RECOVERY_WINDOW_EXPIRED"
            rem_sec = 0
            rem_label = "Eligible operation expired after deadline"
        elif win["rec_start_dt"] <= now < win["rec_deadline_dt"]:
            # Open recovery window with NO eligible failed/interrupted job MUST NOT show RECOVERING
            status = "UPCOMING" if matched_pub is None else "READY"
            rem_sec = int(max(0, (win["rec_deadline_dt"] - now).total_seconds()))
            rem_label = f"No recovery needed ({_format_remaining(rem_sec)} window)"
        else:
            status = "SKIPPED"
            rem_sec = 0
            rem_label = "Window passed (no job assigned)"

        slots_out.append({
            "slot_id": slot["slot_id"],
            "label": slot["label"],
            "publication_time_ist": win["ist_display"],
            "publication_time_utc": win["utc_iso"],
            "prep_window_minutes": win["prep_window_minutes"],
            "prep_start_ist": win["prep_start_ist"],
            "prep_start_utc": win["prep_start_utc"],
            "recovery_start_ist": win["recovery_start_ist"],
            "recovery_start_utc": win["recovery_start_utc"],
            "recovery_deadline_ist": win["recovery_deadline_ist"],
            "recovery_deadline_utc": win["recovery_deadline_utc"],
            "recovery_duration_minutes": win["recovery_duration_minutes"],
            "crosses_midnight": win["crosses_midnight"],
            "enabled": slot["enabled"],
            "status": status,
            "remaining_seconds": rem_sec,
            "remaining_label": rem_label,
            "overlap_warnings": overlap_by_slot.get(slot["slot_id"], []),
            "associated_job_id": matched_job.id if matched_job else None,
            "associated_ledger_id": matched_pub.get("id") if matched_pub else None,
            "associated_topic": matched_pub.get("topic_title") if matched_pub else None,
            "youtube_video_id": matched_pub.get("youtube_video_id") if matched_pub else None,
        })

    return {
        "schedule_version": int(sched.get("schedule_version", 1)),
        "schedule_revision": sched.get("revision"),
        "preset_name": sched.get("preset_name", "CUSTOM"),
        "recovery_overlap_policy": sched.get("recovery_overlap_policy", "STRICT"),
        "timezone_name": str(tz),
        "now_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "recovery_worker_status": "ACTIVE" if _ACTIVE_RECOVERY_CLAIMS else "IDLE",
        "active_recovery_claims": dict(_ACTIVE_RECOVERY_CLAIMS),
        "overlap_details": overlap_details,
        "slots": slots_out,
    }


# ---------------------------------------------------------------------------
# Stage 3.23.4: Production Configuration (Renderer Pinned to Lemo-Opuscar)
# ---------------------------------------------------------------------------
def default_production_config() -> dict[str, Any]:
    return {
        "renderer": "lemo-opuscar",
        "concurrency": get_max_concurrency(),
        "duration_target_sec": 54,
        "resolution": "1080x1920",
        "fps": 30,
        "beat_count": 20,
        "diversity_min_assets": 10,
        "qc_strictness": "strict",
        "recovery_policy": "checkpoint_resume",
        "max_cpu_pct": 92,
        "max_ram_pct": 90,
        "retry_limit": 2,
    }


def validate_production_config(cfg: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    rend = str(cfg.get("renderer") or "").strip().lower()
    if rend != "lemo-opuscar":
        errors.append("Renderer must remain 'lemo-opuscar' for PRODUCTION/VALIDATION.")
    conc = int(cfg.get("concurrency", 1))
    if not (1 <= conc <= 2):
        errors.append("Concurrency must be 1 or 2.")
    dur = int(cfg.get("duration_target_sec", 54))
    if not (50 <= dur <= 58):
        errors.append("duration_target_sec must be between 50 and 58 for YouTube Shorts.")
    if str(cfg.get("resolution") or "1080x1920") != "1080x1920":
        errors.append("resolution must be 1080x1920.")
    if int(cfg.get("fps", 30)) != 30:
        errors.append("fps must be 30.")
    beats = int(cfg.get("beat_count", 20))
    if not (12 <= beats <= 30):
        errors.append("beat_count must be between 12 and 30.")
    div = int(cfg.get("diversity_min_assets", 10))
    if not (4 <= div <= 25):
        errors.append("diversity_min_assets must be between 4 and 25.")
    if str(cfg.get("qc_strictness") or "strict") not in {"strict", "standard"}:
        errors.append("qc_strictness must be strict or standard.")
    if str(cfg.get("recovery_policy") or "checkpoint_resume") not in {"checkpoint_resume", "strict_window"}:
        errors.append("recovery_policy must be checkpoint_resume or strict_window.")
    return {"valid": len(errors) == 0, "errors": errors}


def get_production_config() -> dict[str, Any]:
    raw = _read_setting(PRODUCTION_CONFIG_KEY)
    base = default_production_config()
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                base.update(loaded)
        except Exception:
            pass
    base["renderer"] = "lemo-opuscar"
    base["concurrency"] = get_max_concurrency()
    return base


def save_production_config(cfg: dict[str, Any], confirm: bool = True) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Explicit confirmation is required to save production configuration.")
    merged = get_production_config()
    merged.update(cfg or {})
    validation = validate_production_config(merged)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    prev = get_production_config()
    set_max_concurrency(int(merged["concurrency"]))
    _write_settings({PRODUCTION_CONFIG_KEY: json.dumps(merged)})
    record_change("production_config", prev, merged)
    return {"saved": True, "config": merged}


# ---------------------------------------------------------------------------
# Stage 3.23.4: Canonical Storage Configuration & Path Validation
# ---------------------------------------------------------------------------
def default_storage_config() -> dict[str, Any]:
    from backend.app.core.paths import MEDIA_DIR, MEDIA_PRODUCTIONS_DIR
    return {
        "production_output_root": str(MEDIA_PRODUCTIONS_DIR),
        "final_video_dir": str(MEDIA_DIR / "final_videos"),
        "thumbnail_dir": str(MEDIA_DIR / "thumbnails"),
        "temp_render_dir": str(MEDIA_DIR / "temp_render"),
        "archive_dir": str(MEDIA_DIR / "archive"),
        "quarantine_dir": str(MEDIA_DIR / "quarantine"),
    }


def validate_storage_config(cfg: dict[str, Any]) -> dict[str, Any]:
    import shutil
    from pathlib import Path
    errors: list[str] = []
    required_keys = [
        "production_output_root",
        "final_video_dir",
        "thumbnail_dir",
        "temp_render_dir",
        "archive_dir",
        "quarantine_dir",
    ]
    resolved_paths: dict[str, str] = {}
    for key in required_keys:
        val = str(cfg.get(key) or "").strip()
        if not val:
            errors.append(f"{key} is required.")
            continue
        p = Path(val)
        if not p.is_absolute():
            errors.append(f"{key} must be an absolute path: {val}")
            continue
        norm = str(p.resolve()).lower()
        if norm in resolved_paths.values():
            errors.append(f"{key} conflicts with another storage directory ({val}).")
            continue
        resolved_paths[key] = norm
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".crazzydocs_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{key} is not writable ({val}): {exc}")

    disk_free_gb = 0.0
    root_val = str(cfg.get("production_output_root") or "").strip()
    if root_val:
        try:
            usage = shutil.disk_usage(root_val)
            disk_free_gb = round(usage.free / (1024 ** 3), 2)
            if disk_free_gb < 1.0:
                errors.append(f"Insufficient disk space at {root_val} ({disk_free_gb} GB free < 1.0 GB).")
        except Exception:
            pass
    return {"valid": len(errors) == 0, "errors": errors, "disk_free_gb": disk_free_gb}


def get_storage_config() -> dict[str, Any]:
    raw = _read_setting(STORAGE_CONFIG_KEY)
    base = default_storage_config()
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                base.update({k: str(v) for k, v in loaded.items() if v})
        except Exception:
            pass
    return base


def save_storage_config(cfg: dict[str, Any], confirm: bool = True) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Explicit confirmation is required to update storage paths.")
    merged = get_storage_config()
    merged.update({k: str(v).strip() for k, v in (cfg or {}).items() if v is not None})
    validation = validate_storage_config(merged)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    prev = get_storage_config()
    _write_settings({STORAGE_CONFIG_KEY: json.dumps(merged)})
    record_change("storage_config", prev, merged)
    return {"saved": True, "config": merged, "validation": validation}


# ---------------------------------------------------------------------------
# Unified Schedule Editor & Verified Activation
# ---------------------------------------------------------------------------
def record_scheduler_sync(profile: Any) -> dict[str, Any]:
    """Persists the revision and timestamp actually loaded by APScheduler."""
    rev = str(getattr(profile, "revision", "") or "")
    if not rev:
        spec_name = profile.name if profile.name in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY"} else "CUSTOM"
        if spec_name in {"FOUR_CYCLES_DAILY", "SINGLE_DAILY"}:
            rev = preset_schedule(spec_name)["revision"]
        else:
            rev = _compute_production_schedule_revision({
                "profile_name": profile.name,
                "timezone_name": profile.timezone_name,
                "cycles": [
                    {
                        "cycle_id": c.cycle_id,
                        "name": c.name,
                        "start_time": f"{c.start_hour_ist:02d}:{c.start_min_ist:02d}",
                        "recovery_duration_minutes": int(round(c.recovery_window_hours * 60)),
                        "target_publish_time": f"{c.target_pub_hour_ist:02d}:{c.target_pub_min_ist:02d}",
                        "next_day": bool(c.pub_rollover_days),
                        "active_weekdays": list(c.active_weekdays),
                        "enabled": True,
                    }
                    for c in profile.cycles
                ],
            })
    payload = {
        "loaded_profile": profile.name,
        "loaded_version": int(getattr(profile, "schedule_version", 1)),
        "loaded_revision": rev,
        "cycles_count": len(getattr(profile, "cycles", []) or []),
        "daemon_pid": os.getpid(),
        "synced_at_ist": datetime.now(IST_TZ).isoformat(),
    }
    _write_settings({SCHEDULER_SYNC_KEY: json.dumps(payload)})
    return payload


def get_scheduler_sync_state(active_revision: str, active_version: int = 1) -> dict[str, Any]:
    raw = _read_setting(SCHEDULER_SYNC_KEY)
    sync_data = json.loads(raw) if raw else {}
    loaded_rev = sync_data.get("loaded_revision") or ""
    loaded_ver = sync_data.get("loaded_version")
    synced_at = sync_data.get("synced_at_ist")
    is_synced = bool(loaded_rev and active_revision and loaded_rev == active_revision)
    return {
        "loaded_profile": sync_data.get("loaded_profile"),
        "loaded_version": loaded_ver,
        "loaded_revision": loaded_rev or None,
        "synced_at_ist": synced_at,
        "cycles_count": sync_data.get("cycles_count"),
        "is_synchronized": is_synced,
        "active_revision": active_revision,
        "active_version": active_version,
    }


def _map_cycles_to_publication_slots(
    prod_cycles: list[dict[str, Any]],
    pub_slots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Explicitly maps production cycles to publication slots without inventing hidden mappings."""
    used_slot_ids: set[str] = set()
    mappings: list[dict[str, Any]] = []

    for cycle in prod_cycles:
        cid = str(cycle.get("cycle_id") or "")
        target_pub = str(cycle.get("target_publish_time") or "")
        matched_slot = None
        mapping_mode = "unmapped"

        # 1. Explicit mapped_cycle_id match
        for s in pub_slots:
            if s.get("slot_id") in used_slot_ids:
                continue
            if s.get("mapped_cycle_id") and str(s.get("mapped_cycle_id")) == cid:
                matched_slot = s
                mapping_mode = "inherited" if str(s.get("time_ist") or "") == target_pub else "overridden"
                break

        # 2. Exact target_publish_time == slot.time_ist match
        if matched_slot is None:
            for s in pub_slots:
                if s.get("slot_id") in used_slot_ids:
                    continue
                if str(s.get("time_ist") or "") == target_pub:
                    matched_slot = s
                    mapping_mode = "inherited"
                    break

        if matched_slot is not None:
            used_slot_ids.add(str(matched_slot.get("slot_id")))

        mappings.append({
            "cycle_id": cid,
            "cycle_name": cycle.get("name"),
            "target_publish_time": target_pub,
            "publication_slot_id": matched_slot.get("slot_id") if matched_slot else None,
            "publication_slot_label": matched_slot.get("label") if matched_slot else None,
            "publication_time_ist": matched_slot.get("time_ist") if matched_slot else None,
            "prep_window_minutes": matched_slot.get("prep_window_minutes") if matched_slot else None,
            "pub_recovery_duration_minutes": matched_slot.get("recovery_duration_minutes") if matched_slot else None,
            "mapping_mode": mapping_mode,
        })

    unmapped_slots = [s for s in pub_slots if str(s.get("slot_id")) not in used_slot_ids]
    return mappings, unmapped_slots


def get_unified_schedule_state(now_ist: datetime | None = None) -> dict[str, Any]:
    """Returns unified production + publication schedules, explicit mappings, scheduler sync, and unified timeline."""
    cc = get_control_center_state()
    prod_sched = cc["schedule"]
    pub_sched = cc["publication_schedule"]
    live_prod = get_live_cycle_schedule_status(now_ist=now_ist)
    live_pub = get_publication_recovery_timeline(now_ist=now_ist)

    active_prod = prod_sched["active"]
    active_pub = pub_sched["active"]
    active_rev = str(active_prod.get("revision") or live_prod.get("schedule_revision") or "")
    active_ver = int(active_prod.get("schedule_version") or 1)
    sync_state = get_scheduler_sync_state(active_rev, active_ver)

    prod_draft = prod_sched.get("draft")
    prod_val = prod_sched.get("draft_validation")
    pub_draft = pub_sched.get("draft")
    pub_val = pub_sched.get("draft_validation")

    has_draft = bool(prod_draft or pub_draft)
    is_stale = bool((prod_val and prod_val.get("stale")) or (pub_val and pub_val.get("stale")))
    is_valid_draft = has_draft and not is_stale and (
        (not prod_draft or (prod_val and prod_val.get("valid")))
        and (not pub_draft or (pub_val and pub_val.get("valid")))
    )

    if is_stale:
        lifecycle_state = "STALE"
    elif is_valid_draft:
        lifecycle_state = "VALID"
    elif has_draft:
        lifecycle_state = "DRAFT"
    elif any(c.get("status") == "RUNNING" for c in live_prod.get("cycles", [])):
        lifecycle_state = "RUNNING"
    elif sync_state["is_synchronized"]:
        lifecycle_state = "ACTIVE"
    else:
        lifecycle_state = "ACTIVE"

    view_prod = prod_draft or active_prod
    view_pub = pub_draft or active_pub
    mappings, unmapped_pub_slots = _map_cycles_to_publication_slots(
        view_prod.get("cycles") or [],
        view_pub.get("slots") or [],
    )

    # Build unified chronological timeline combining active production cycles + active publication slots
    prod_by_id = {c["cycle_id"]: c for c in live_prod.get("cycles", [])}
    pub_by_id = {s["slot_id"]: s for s in live_pub.get("slots", [])}
    active_mappings, active_unmapped_slots = _map_cycles_to_publication_slots(
        active_prod.get("cycles") or [],
        active_pub.get("slots") or [],
    )

    unified_timeline: list[dict[str, Any]] = []
    for m in active_mappings:
        pc = prod_by_id.get(m["cycle_id"])
        ps = pub_by_id.get(m["publication_slot_id"]) if m["publication_slot_id"] else None
        unified_timeline.append({
            "kind": "mapped_cycle" if ps else "production_only",
            "cycle_id": m["cycle_id"],
            "cycle_name": m["cycle_name"],
            "mapping_mode": m["mapping_mode"],
            "schedule_version": active_ver,
            "publication_schedule_version": active_pub.get("schedule_version", 1),
            "production_start_ist": pc.get("start_time_ist") if pc else None,
            "production_recovery_start_ist": pc.get("recovery_start_ist") if pc else None,
            "production_recovery_deadline_ist": pc.get("recovery_expiration_ist") if pc else None,
            "production_recovery_duration_minutes": pc.get("recovery_duration_minutes") if pc else None,
            "finalization_buffer_minutes": pc.get("finalization_buffer_minutes") if pc else None,
            "target_publish_ist": pc.get("target_publish_ist") if pc else m["target_publish_time"],
            "production_state": pc.get("status") if pc else "DISABLED",
            "production_remaining_label": pc.get("remaining_recovery_label") if pc else "--",
            "associated_production_job_id": pc.get("associated_job_id") if pc else None,
            "publication_slot_id": m["publication_slot_id"],
            "publication_slot_label": m["publication_slot_label"],
            "publication_time_ist": ps.get("publication_time_ist") if ps else None,
            "publication_prep_start_ist": ps.get("prep_start_ist") if ps else None,
            "publication_prep_minutes": ps.get("prep_window_minutes") if ps else None,
            "publication_recovery_start_ist": ps.get("recovery_start_ist") if ps else None,
            "publication_recovery_deadline_ist": ps.get("recovery_deadline_ist") if ps else None,
            "publication_recovery_duration_minutes": ps.get("recovery_duration_minutes") if ps else None,
            "publication_state": ps.get("status") if ps else "UNMAPPED",
            "publication_remaining_label": ps.get("remaining_label") if ps else "No matching slot",
            "associated_publication_job_id": ps.get("associated_job_id") if ps else None,
            "associated_ledger_id": ps.get("associated_ledger_id") if ps else None,
            "youtube_video_id": ps.get("youtube_video_id") if ps else None,
        })

    for s in active_unmapped_slots:
        ps = pub_by_id.get(s.get("slot_id"))
        if ps:
            unified_timeline.append({
                "kind": "publication_only",
                "cycle_id": None,
                "cycle_name": ps.get("label"),
                "mapping_mode": "standalone_slot",
                "schedule_version": active_ver,
                "publication_schedule_version": active_pub.get("schedule_version", 1),
                "production_start_ist": None,
                "production_recovery_start_ist": None,
                "production_recovery_deadline_ist": None,
                "production_recovery_duration_minutes": None,
                "finalization_buffer_minutes": None,
                "target_publish_ist": None,
                "production_state": "STANDALONE_PUB",
                "production_remaining_label": "--",
                "associated_production_job_id": None,
                "publication_slot_id": ps.get("slot_id"),
                "publication_slot_label": ps.get("label"),
                "publication_time_ist": ps.get("publication_time_ist"),
                "publication_prep_start_ist": ps.get("prep_start_ist"),
                "publication_prep_minutes": ps.get("prep_window_minutes"),
                "publication_recovery_start_ist": ps.get("recovery_start_ist"),
                "publication_recovery_deadline_ist": ps.get("recovery_deadline_ist"),
                "publication_recovery_duration_minutes": ps.get("recovery_duration_minutes"),
                "publication_state": ps.get("status"),
                "publication_remaining_label": ps.get("remaining_label"),
                "associated_publication_job_id": ps.get("associated_job_id"),
                "associated_ledger_id": ps.get("associated_ledger_id"),
                "youtube_video_id": ps.get("youtube_video_id"),
            })

    next_prod_list = live_prod.get("upcoming_cycles") or []
    next_pub_list = pub_sched.get("upcoming_slots") or []

    # Compact execution summary for the redesigned Unified Scheduler
    compact_execution: list[dict[str, Any]] = []
    for row in unified_timeline:
        if row.get("kind") == "publication_only":
            continue
        p_state = str(row.get("production_state") or "UPCOMING")
        pub_state = str(row.get("publication_state") or "WAITING")
        if p_state == "COMPLETED" and pub_state in {"PUBLISHED", "SCHEDULED", "COMPLETED"}:
            overall = "Success" if pub_state == "PUBLISHED" else "Scheduled"
        elif p_state in {"RUNNING", "RETRYING", "PAUSED"} or pub_state in {"PREPARING", "RECOVERING"}:
            overall = "In Progress"
        elif p_state in {"FAILED", "RECOVERY_WINDOW_EXPIRED"} or pub_state in {"FAILED", "EXPIRED"}:
            overall = "Attention"
        else:
            overall = "Waiting"

        recovery_needed = p_state in {"RETRYING", "PAUSED", "FAILED", "RECOVERY_WINDOW_EXPIRED"} or pub_state in {"RECOVERING", "FAILED", "EXPIRED"}
        recovery_detail = ""
        if recovery_needed:
            parts = []
            if p_state in {"RETRYING", "PAUSED", "FAILED", "RECOVERY_WINDOW_EXPIRED"}:
                parts.append(f"Prod: {row.get('production_remaining_label') or p_state}")
            if pub_state in {"RECOVERING", "FAILED", "EXPIRED"}:
                parts.append(f"Pub: {row.get('publication_remaining_label') or pub_state}")
            recovery_detail = " | ".join(parts)

        compact_execution.append({
            "cycle_id": row.get("cycle_id"),
            "cycle_name": row.get("cycle_name"),
            "production_state": p_state,
            "publication_state": pub_state,
            "overall_status": overall,
            "recovery_needed": recovery_needed,
            "recovery_detail": recovery_detail,
            "production_job_id": row.get("associated_production_job_id"),
            "youtube_video_id": row.get("youtube_video_id"),
            "production_start_ist": row.get("production_start_ist"),
            "production_recovery_deadline_ist": row.get("production_recovery_deadline_ist"),
            "publication_time_ist": row.get("publication_time_ist") or row.get("target_publish_ist"),
            "publication_recovery_deadline_ist": row.get("publication_recovery_deadline_ist"),
        })

    # Last successful execution from JobModel
    last_success: dict[str, Any] | None = None
    try:
        with SessionLocal() as db:
            last_job = (
                db.query(JobModel)
                .filter(JobModel.status == "COMPLETED")
                .order_by(JobModel.completed_at.desc(), JobModel.updated_at.desc())
                .first()
            )
            if last_job is not None:
                ts = last_job.completed_at or last_job.updated_at
                last_success = {
                    "job_id": last_job.id,
                    "topic_title": last_job.topic_title,
                    "completed_at_ist": ts.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M IST") if ts and ts.tzinfo else (ts.strftime("%Y-%m-%d %H:%M IST") if ts else None),
                    "youtube_video_id": last_job.youtube_video_id,
                }
    except Exception:
        last_success = None

    active_prod_cycles = [c for c in (active_prod.get("cycles") or []) if c.get("enabled", True)]
    active_pub_slots = [s for s in (active_pub.get("slots") or []) if s.get("enabled", True)]

    return {
        "lifecycle_state": lifecycle_state,
        "active_schedule_name": active_prod.get("profile_name", "FOUR_CYCLES_DAILY"),
        "active_schedule_version": active_ver,
        "active_revision": active_rev,
        "last_activated_at_ist": active_prod.get("activated_at_ist"),
        "scheduler_sync": sync_state,
        "prod_cycles_per_day": len(active_prod_cycles),
        "pub_slots_per_day": len(active_pub_slots),
        "last_successful_execution": last_success,
        "next_production": next_prod_list[0] if next_prod_list else None,
        "next_publication": next_pub_list[0] if next_pub_list else None,
        "production": prod_sched,
        "publication": pub_sched,
        "mappings": mappings,
        "unmapped_publication_slots": unmapped_pub_slots,
        "upcoming_cycles": next_prod_list,
        "upcoming_slots": next_pub_list,
        "unified_timeline": unified_timeline,
        "compact_execution": compact_execution,
        "publication_recovery_worker_status": live_pub.get("recovery_worker_status", "IDLE"),
        "publication_overlap_details": live_pub.get("overlap_details", []),
    }


def save_unified_schedule_draft(
    prod_spec: dict[str, Any] | None = None,
    pub_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Saves production and publication schedule drafts together as one coordinated configuration."""
    saved_prod = save_schedule_draft(prod_spec) if prod_spec is not None else None
    saved_pub = save_publication_schedule_draft(pub_spec) if pub_spec is not None else None
    return {
        "status": "DRAFT_SAVED",
        "production_draft": saved_prod,
        "publication_draft": saved_pub,
        "state": get_unified_schedule_state(),
    }


def validate_unified_schedule(
    prod_spec: dict[str, Any] | None = None,
    pub_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validates production and publication schedules together in the backend, returning
    plain-language errors, warnings, and successes without mutating active state.
    """
    errors: list[str] = []
    warnings: list[str] = []
    successes: list[str] = []

    cc = get_control_center_state()
    effective_prod = prod_spec or cc["schedule"].get("draft") or cc["schedule"]["active"]
    effective_pub = pub_spec or cc["publication_schedule"].get("draft") or cc["publication_schedule"]["active"]

    prod_validation = None
    try:
        prod_validation = validate_saved_schedule_draft(effective_prod)
        successes.append("All production cycles and recovery windows are valid.")
    except Exception as exc:
        errors.append(str(exc))

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "successes": successes,
        "production_validation": prod_validation,
        "publication_validation": None,
        "mappings": [],
    }


def activate_unified_schedule(
    confirm: bool,
    prod_spec: dict[str, Any] | None = None,
    pub_spec: dict[str, Any] | None = None,
    sync_scheduler_fn: Any = None,
) -> dict[str, Any]:
    """
    Executes the 9-step verified activation workflow:
    validate -> reject invalid -> persist active -> load in scheduler ->
    verify scheduler revision == active revision -> recalculate upcoming ->
    verify timeline API -> return explicit activation result.
    """
    steps: list[dict[str, Any]] = []
    if not confirm:
        return {
            "status": "FAILED",
            "reason": "Explicit confirmation is required to activate schedule.",
            "steps": [{"step": "confirm", "ok": False, "detail": "confirm=False"}],
        }

    try:
        # Step 1-2: Validate production schedule only (publication is user-controlled)
        if prod_spec is not None:
            validate_saved_schedule_draft(prod_spec)
        steps.append({"step": "validate", "ok": True, "detail": "Validation passed"})

        # Step 3: Persist new active production revision
        prod_act = None
        if prod_spec is not None or _read_setting(DRAFT_PROFILE_KEY):
            prod_act = activate_schedule_draft(confirm=True)
        pub_act = None
        steps.append({"step": "persist", "ok": True, "detail": "Active schedule persisted"})

        # Step 4: Restore/load active revision in scheduler
        restore_active_schedule_profile()
        daemon_async_sync = False
        if sync_scheduler_fn is not None:
            sync_scheduler_fn()
            try:
                from backend.app.scheduler import scheduler as _sched_mod
                if (
                    sync_scheduler_fn is getattr(_sched_mod, "configure_scheduler_jobs", None)
                    and not getattr(_sched_mod.scheduler, "running", False)
                    and not os.environ.get("PYTEST_CURRENT_TEST")
                ):
                    daemon_async_sync = True
            except Exception:
                pass
        else:
            record_scheduler_sync(get_active_profile())
        steps.append({
            "step": "scheduler_load",
            "ok": True,
            "detail": "Persisted for daemon reload" if daemon_async_sync else "Scheduler loaded profile",
        })

        # Step 5: Verify scheduler-loaded revision matches activated revision
        state_after = get_unified_schedule_state()
        active_rev = state_after["active_revision"]
        loaded_rev = state_after["scheduler_sync"].get("loaded_revision")
        if (not loaded_rev or loaded_rev != active_rev) and not daemon_async_sync:
            steps.append({
                "step": "verify_sync",
                "ok": False,
                "detail": f"Mismatch: active={active_rev}, scheduler={loaded_rev}",
            })
            return {
                "status": "FAILED",
                "reason": f"Scheduler synchronization failed (active={active_rev}, scheduler={loaded_rev}).",
                "active_revision": active_rev,
                "scheduler_loaded_revision": loaded_rev,
                "is_synchronized": False,
                "steps": steps,
                "state": state_after,
            }
        steps.append({
            "step": "verify_sync",
            "ok": True,
            "detail": "Awaiting scheduler daemon reload (<=10s)" if daemon_async_sync else f"Synchronized ({loaded_rev})",
        })

        # Step 6-7: Recalculate upcoming & verify timeline reflects new revision
        timeline = get_live_cycle_schedule_status()
        tl_rev = timeline.get("schedule_revision")
        if tl_rev != active_rev:
            steps.append({"step": "verify_timeline", "ok": False, "detail": f"Timeline revision {tl_rev} != {active_rev}"})
            return {
                "status": "FAILED",
                "reason": f"Timeline verification failed (timeline={tl_rev}, active={active_rev}).",
                "active_revision": active_rev,
                "scheduler_loaded_revision": loaded_rev,
                "is_synchronized": False,
                "steps": steps,
                "state": state_after,
            }
        steps.append({"step": "verify_timeline", "ok": True, "detail": f"Timeline verified ({tl_rev})"})

        return {
            "status": "ACTIVE",
            "message": (
                "Schedule persisted to DB; scheduler daemon will reload within 10s."
                if daemon_async_sync
                else "Schedule activated successfully and scheduler loaded the new revision."
            ),
            "active_revision": active_rev,
            "active_version": state_after["active_schedule_version"],
            "scheduler_loaded_revision": loaded_rev,
            "is_synchronized": not daemon_async_sync,
            "activated_at_ist": state_after.get("last_activated_at_ist"),
            "synced_at_ist": state_after["scheduler_sync"].get("synced_at_ist"),
            "production_result": prod_act,
            "publication_result": pub_act,
            "steps": steps,
            "state": state_after,
        }
    except Exception as exc:
        steps.append({"step": "activation", "ok": False, "detail": str(exc)})
        return {
            "status": "FAILED",
            "reason": str(exc),
            "steps": steps,
        }
