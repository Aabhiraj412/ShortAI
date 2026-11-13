"""
Per-production cache of expensive pre-render stage results.

A retry of the same production (same workspace) reuses the script, narration, media
acquisition and storyboard of the earlier attempt instead of regenerating them, as
long as:
  - the stage was recorded with the same input fingerprint, and
  - every file the result references still exists with the size it had when saved.
Anything else (missing, changed, unreadable, different inputs) recomputes the stage.

Files live in <production>/reports/stage_cache/<stage>.pkl plus a JSON sidecar.
They are written only by this process into the production's own workspace.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_FORMAT_VERSION = 1


def fingerprint(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:24]


class StageArtifactCache:
    def __init__(self, cache_dir: str | os.PathLike):
        self.dir = Path(cache_dir)

    def _paths(self, stage: str) -> Tuple[Path, Path]:
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in stage)
        return self.dir / f"{safe}.pkl", self.dir / f"{safe}.json"

    def load(self, stage: str, inputs_fp: str) -> Tuple[bool, Any, str]:
        """Returns (hit, value, reason)."""
        data_p, meta_p = self._paths(stage)
        if not (data_p.is_file() and meta_p.is_file()):
            return False, None, "not cached"
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, None, "unreadable metadata"
        if meta.get("version") != CACHE_FORMAT_VERSION:
            return False, None, "format version changed"
        if meta.get("inputs_fingerprint") != inputs_fp:
            return False, None, "inputs changed"
        for rec in meta.get("files", []):
            p = rec.get("path")
            try:
                size = os.path.getsize(p)
            except (OSError, TypeError):
                return False, None, f"missing file {p}"
            if size <= 0 or size != rec.get("size"):
                return False, None, f"changed file {p}"
        try:
            with open(data_p, "rb") as f:
                value = pickle.load(f)
        except Exception as exc:  # corrupt / incompatible class layout
            return False, None, f"unreadable data ({type(exc).__name__})"
        return True, value, "reused"

    def save(self, stage: str, inputs_fp: str, value: Any, files: Iterable[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data_p, meta_p = self._paths(stage)
        file_recs = []
        for p in sorted({str(f) for f in files if f}):
            try:
                file_recs.append({"path": p, "size": os.path.getsize(p)})
            except OSError:
                continue  # an optional file that is not there is not part of the contract
        tmp = data_p.with_suffix(".pkl.tmp")
        with open(tmp, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, data_p)
        meta_p.write_text(json.dumps({
            "version": CACHE_FORMAT_VERSION,
            "stage": stage,
            "inputs_fingerprint": inputs_fp,
            "saved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "files": file_recs,
        }, indent=1), encoding="utf-8")

    def get_or_compute(
        self,
        stage: str,
        inputs_fp: str,
        compute: Callable[[], Any],
        files_of: Callable[[Any], Iterable[str]] = lambda _v: (),
    ) -> Tuple[Any, bool]:
        """Returns (value, reused)."""
        hit, value, reason = self.load(stage, inputs_fp)
        if hit:
            logger.info("[RENDER RECOVERY] Reusing cached %s from the previous attempt.", stage)
            return value, True
        if reason != "not cached":
            logger.info("[RENDER RECOVERY] Regenerating %s: %s.", stage, reason)
        value = compute()
        try:
            self.save(stage, inputs_fp, value, files_of(value))
        except Exception:
            logger.warning("[RENDER RECOVERY] Could not cache %s; a retry will regenerate it.", stage, exc_info=True)
        return value, False


def media_files(assets: Iterable[Any]) -> list[str]:
    out = []
    for a in assets or []:
        for attr in ("local_path", "portrait_path"):
            p = getattr(a, attr, None)
            if p and os.path.isfile(p):
                out.append(p)
    return out


def storyboard_files(beats: Iterable[dict]) -> list[str]:
    out = []
    for b in beats or []:
        for key in ("image_path", "asset_path", "local_path", "media_path"):
            p = (b or {}).get(key)
            if p and isinstance(p, str) and os.path.isfile(p):
                out.append(p)
    return out
