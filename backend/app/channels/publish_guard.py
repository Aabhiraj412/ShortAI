"""Stage 4.03: the one authoritative publishing check for every channel (English and Hindi).

Every upload reaches exactly one ``videos().insert`` per channel (English: ``YouTubePublishingEngine.upload_short``;
Hindi: ``HindiPublisher.upload``). Immediately before that call both run this module:

* ``evaluate(channel_key, item, registry=...)`` lists EVERY unmet condition (never only the first):
  channel status, verified identity, publishing authorization, per-video approval, current QC, file integrity
  (resolved path inside the channel's media area, SHA-256 equal to the QC/approved hash) and scheduling.
* ``confirm_identity(registry, key, live_channel_id)`` compares the channel that the upload token actually belongs to
  (from a ``channels.list?mine=true`` response the upload already makes) with the registry's verified identity.

The dashboard uses ``evaluate`` without a live identity (read only, no API call) to show why an item is blocked.
Nothing here grants anything: authorization and approval are written only by their own audited functions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGLISH, HINDI = "en", "hi"
PUBLISH_MIN_LEAD = timedelta(minutes=15)


class IdentityRefused(PermissionError):
    """The upload token does not belong to the channel the registry verified (nothing was uploaded)."""


@dataclass
class Decision:
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"eligible": self.eligible, "reasons": list(self.reasons), "checks": dict(self.checks)}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_inside(path: Any, root: Path) -> Optional[Path]:
    """The real path of ``path`` if it is an existing regular file inside ``root`` (symlinks and ``..`` resolved
    first, so neither can escape), else None."""
    try:
        real = Path(str(path or "")).resolve(strict=True)
        base = Path(root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not real.is_file() or base not in real.parents:
        return None
    return real


def is_localized_media(path: Any, media_dir: Path) -> bool:
    """True when ``path`` resolves into ``media/localizations`` (localized output never enters the English queue)."""
    try:
        real = Path(str(path or "")).resolve()
        loc = (Path(media_dir) / "localizations").resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return real == loc or loc in real.parents


def _media_dir() -> Path:
    from backend.app.core.paths import MEDIA_DIR
    return Path(MEDIA_DIR)


def _status_reasons(ch: Dict[str, Any], label: str) -> List[str]:
    reasons = []
    if ch.get("status") != "ACTIVE":
        reasons.append(f"{label} channel is not active ({ch.get('status')}: {ch.get('status_reason') or ''})".strip())
    if ch.get("role") != "MASTER" and not ch.get("youtube_channel_id"):
        reasons.append(f"{label} channel identity is not verified")
    return reasons


def evaluate(channel_key: str, item: Dict[str, Any], *, registry, media_dir: Optional[Path] = None,
             now: Optional[datetime] = None, schedule_enabled: Optional[bool] = None) -> Decision:
    """All publishing conditions for one item, each evaluated (no short circuit).

    English ``item``: ``video_path``, ``visibility``, ``publish_at`` (aware datetime or None) and, when the item is a
    ledger entry, ``ledger`` (``upload_status``, ``video_path``, ``video_hash``) plus ``allowed_statuses``.
    Hindi ``item``: a ``hindi_publications`` row (``video_path``, ``qc_sha256``, ``approved_at_utc``,
    ``approved_sha256``, ``scheduled_at_utc``, ``state``) plus ``localization_status``.
    """
    now = now or datetime.now(timezone.utc)
    media_dir = Path(media_dir) if media_dir else _media_dir()
    ch = registry.get(channel_key)
    reasons: List[str] = []
    checks: Dict[str, bool] = {}

    def check(name: str, ok: bool, why: str) -> None:
        checks[name] = bool(ok)
        if not ok:
            reasons.append(why)

    st = _status_reasons(ch, "English" if channel_key == ENGLISH else "Hindi")
    checks["channel_active"] = not any("not active" in r for r in st)
    checks["identity_verified"] = not any("not verified" in r for r in st)
    reasons.extend(st)

    if channel_key == ENGLISH:
        path = item.get("video_path")
        ledger = item.get("ledger")
        check("not_localized_media", not is_localized_media(path, media_dir),
              "localized media cannot be uploaded to the English channel")
        exists = bool(path) and Path(str(path)).is_file()
        check("file_exists", exists, "the video file does not exist")
        if ledger is not None:
            allowed = item.get("allowed_statuses") or {"approved"}
            check("approved", ledger.get("upload_status") in allowed, "the upload is not approved")
            same = False
            try:
                same = Path(str(ledger.get("video_path"))).resolve() == Path(str(path)).resolve()
            except (OSError, RuntimeError, ValueError):
                pass
            check("path_matches_ledger", same, "the file path does not match the approved ledger entry")
            check("hash_matches", exists and ledger.get("video_hash") == sha256_file(path),
                  "the video changed after it was approved (SHA-256 mismatch)")
        pa = item.get("publish_at")
        if pa is not None:
            check("private_scheduled", item.get("visibility") == "private",
                  "a scheduled publication must be uploaded private")
            check("publish_at_future", pa > now, "the publish time is not in the future")
        return Decision(not reasons, reasons, checks)

    # ---- Hindi (and any later localized channel) --------------------------------------------------------------
    cfg = ch.get("publishing_config") or {}
    check("publishing_authorized", cfg.get("publishing_authorized") is True,
          "Hindi publishing is not authorized (operator authorization required)")
    if schedule_enabled is not None:
        check("schedule_enabled", schedule_enabled, "the Hindi publishing schedule is disabled")
    check("qc_passed", item.get("localization_status") == "QC_PASSED",
          f"the localization has not passed QC (status {item.get('localization_status')})")
    qc_hash = item.get("qc_sha256")
    check("qc_hash_recorded", bool(qc_hash), "no QC-verified SHA-256 is recorded for this video")
    check("approved", bool(item.get("approved_at_utc") and item.get("approved_sha256")),
          "this video has not been approved for publishing")
    real = resolve_inside(item.get("video_path"), media_dir / "localizations" / channel_key)
    check("path_inside_channel_media", real is not None,
          "the video file is missing or outside the Hindi media folder")
    current = sha256_file(real) if real is not None else None
    check("hash_matches_qc", bool(current and qc_hash and current == qc_hash),
          "the video file differs from the QC-passed output (SHA-256 mismatch)")
    check("hash_matches_approval", bool(current and item.get("approved_sha256") == current),
          "the video file differs from the approved file (SHA-256 mismatch)")
    at = item.get("scheduled_at_utc")
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00")) if at else None
    except ValueError:
        when = None
    check("scheduled_ahead", bool(when and when >= now + PUBLISH_MIN_LEAD),
          "no publish time at least 15 minutes ahead")
    return Decision(not reasons, reasons, checks)


def live_channel_id(response: Any) -> Optional[str]:
    """The channel id in a ``channels.list?mine=true`` response, or None (exactly one channel, id a string)."""
    items = (response or {}).get("items") if isinstance(response, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        return None
    cid = items[0].get("id")
    return cid if isinstance(cid, str) and cid else None


def confirm_identity(registry, channel_key: str, live_id: Optional[str], *, actor: str = "upload") -> str:
    """Refuses (IdentityRefused) unless the upload token's channel is the registry's verified channel. Delegates
    the state change to the registry (English mismatch -> BLOCKED IDENTITY_CHANGED; Hindi = English -> BLOCKED)."""
    return registry.confirm_upload_identity(channel_key, live_id, actor=actor)
