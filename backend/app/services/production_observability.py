"""
Durable Production Progress & Live Observability Backend for CrazzyDocs.
Phase 5, Stage 3.21J Standard.

Provides:
- Thread-safe, atomic JSON state persistence surviving dashboard refreshes & restarts.
- Individual tracking of all 12 production pipeline stages without fake completion.
- Live counters for asset acquisition/generation/reuse, per-beat video rendering, and thumbnail sub-steps.
- Strict separation of TEST/MOCK events from production/operator dashboard endpoints.
- Secret redaction guard on serialized payloads and progress event logs.
"""

from datetime import datetime, timezone
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT

DATA_DIR = os.path.join(WORKSPACE_ROOT, "backend", "data")
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_STORE_PATH = os.path.join(DATA_DIR, "production_progress_store.json")
TEST_STORE_PATH = os.path.join(DATA_DIR, "test_production_progress_store.json")

PIPELINE_STAGES: List[Tuple[str, str]] = [
    ("topic_discovery", "Topic discovery"),
    ("research", "Research"),
    ("script_generation", "Script generation"),
    ("storyboard_generation", "Storyboard generation"),
    ("asset_acquisition", "Asset acquisition"),
    ("image_generation", "Image generation"),
    ("animation_generation", "Animation generation"),
    ("narration_generation", "Narration generation"),
    ("video_rendering", "Video rendering"),
    ("thumbnail_generation", "Thumbnail generation"),
    ("quality_control", "Quality control"),
    ("finalization", "Finalization"),
]

STAGE_KEYS: List[str] = [s[0] for s in PIPELINE_STAGES]

_SECRET_PATTERNS = [
    re.compile(r"(AIza[0-9A-Za-z\-_]{20,})"),
    re.compile(r"(ya29\.[0-9A-Za-z\-_]+)"),
    re.compile(r"(sk_(?:live|test)_[0-9A-Za-z\-_]{8,}|sk-[0-9A-Za-z\-_]{12,})"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z\-_.=]+"),
    re.compile(r"(?i)(api[_-]?key|client[_-]?secret|refresh[_-]?token|password)\s*[:=]\s*[^\s,;]+"),
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_secrets(text: Optional[str]) -> str:
    if not text:
        return ""
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def is_test_production(production_id: Optional[str], execution_context: Optional[str]) -> bool:
    ctx = (execution_context or "").strip().upper()
    pid = (production_id or "").strip().lower()
    if ctx in ("TEST", "MOCK", "UNIT_TEST"):
        return True
    if pid.startswith("test_") or pid.startswith("mock_") or pid.startswith("pytest_"):
        return True
    return False


def _default_stages_state() -> Dict[str, Dict[str, Any]]:
    return {
        key: {
            "key": key,
            "label": label,
            "status": "pending",
            "progress": 0.0,
            "operation": "",
            "started_at": None,
            "updated_at": None,
            "completed_at": None,
            "error": None,
        }
        for key, label in PIPELINE_STAGES
    }


def _recompute_overall_progress(stages: Dict[str, Dict[str, Any]]) -> float:
    total = 0.0
    for key in STAGE_KEYS:
        st = stages.get(key, {})
        if st.get("status") == "completed":
            total += 100.0
        elif st.get("status") in ("running", "failed"):
            total += max(0.0, min(99.0, float(st.get("progress", 0.0))))
    return round(total / max(1, len(STAGE_KEYS)), 1)


class ProductionObservabilityStore:
    """Thread-safe, disk-backed production progress & event tracker."""

    def __init__(self, store_path: str = DEFAULT_STORE_PATH, test_store_path: str = TEST_STORE_PATH):
        self.store_path = store_path
        self.test_store_path = test_store_path
        self._lock = threading.RLock()
        self._prod_state: Dict[str, Any] = self._load_file(self.store_path)
        self._test_state: Dict[str, Any] = self._load_file(self.test_store_path)

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {"productions": {}, "order": []}

    def _load_file(self, path: str) -> Dict[str, Any]:
        if not os.path.isfile(path):
            return self._empty_state()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("productions"), dict):
                data.setdefault("order", list(data["productions"].keys()))
                return data
        except Exception:
            pass
        return self._empty_state()

    def _save_file(self, path: str, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)

    def reload_from_disk(self) -> None:
        """Reloads persisted JSON state from disk (verifying restart durability)."""
        with self._lock:
            self._prod_state = self._load_file(self.store_path)
            self._test_state = self._load_file(self.test_store_path)

    def _select_store(
        self, production_id: str, execution_context: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any], bool]:
        """Selects production/validation store vs isolated test store."""
        if os.environ.get("PYTEST_CURRENT_TEST") and os.path.abspath(self.store_path) == os.path.abspath(DEFAULT_STORE_PATH):
            return self.test_store_path, self._test_state, True
        if execution_context is not None:
            is_test = is_test_production(production_id, execution_context)
        elif production_id in self._test_state["productions"]:
            is_test = True
        elif production_id in self._prod_state["productions"]:
            is_test = False
        else:
            is_test = is_test_production(production_id, None)

        if is_test:
            return self.test_store_path, self._test_state, True
        return self.store_path, self._prod_state, False

    def _append_event(
        self,
        record: Dict[str, Any],
        event_type: str,
        stage: Optional[str],
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        ev = {
            "timestamp": _utc_iso(),
            "production_id": record["production_id"],
            "execution_context": record["execution_context"],
            "event_type": event_type,
            "stage": stage,
            "overall_status": record["overall_status"],
            "overall_progress": record["overall_progress"],
            "message": redact_secrets(message),
            "payload": payload or {},
        }
        events: List[Dict[str, Any]] = record.setdefault("events", [])
        events.append(ev)
        if len(events) > 200:
            del events[: len(events) - 200]

        event_log_path = record.get("event_log_path")
        if event_log_path:
            try:
                os.makedirs(os.path.dirname(event_log_path), exist_ok=True)
                with open(event_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except Exception:
                pass

    def _update_eta(self, record: Dict[str, Any]) -> None:
        if record["overall_status"] != "running":
            record["estimated_remaining_sec"] = 0.0 if record["overall_status"] == "completed" else None
            return
        pct = float(record.get("overall_progress", 0.0))
        started_monotonic = record.get("_started_monotonic")
        if started_monotonic and pct >= 5.0 and pct < 100.0:
            elapsed = max(0.5, time.monotonic() - float(started_monotonic))
            est_total = elapsed * (100.0 / pct)
            record["estimated_remaining_sec"] = round(max(1.0, est_total - elapsed), 1)
        else:
            record["estimated_remaining_sec"] = None

    def start_production(
        self,
        production_id: str,
        topic: str = "Discovering topic...",
        execution_context: str = "PRODUCTION",
        event_log_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            path, state, _ = self._select_store(production_id, execution_context)
            now = _utc_iso()
            record: Dict[str, Any] = {
                "production_id": production_id,
                "topic": redact_secrets(topic),
                "execution_context": str(execution_context).upper(),
                "overall_status": "running",
                "overall_progress": 0.0,
                "current_stage": "topic_discovery",
                "current_stage_progress": 0.0,
                "current_operation": "Initializing autonomous production pipeline...",
                "started_at": now,
                "last_updated": now,
                "completed_at": None,
                "estimated_remaining_sec": None,
                "_started_monotonic": time.monotonic(),
                "error": None,
                "stages": _default_stages_state(),
                "assets": {
                    "unique_images": 0,
                    "generated_images": 0,
                    "animations": 0,
                    "failed_acquisitions": 0,
                    "reused_images": 0,
                    "max_allowed_reuse": 2,
                    "status": "PENDING",
                },
                "rendering": {
                    "current_operation": "Waiting for storyboard...",
                    "current_beat_index": 0,
                    "current_beat_id": None,
                    "completed_scenes": 0,
                    "total_scenes": 0,
                    "progress_pct": 0.0,
                    "ffmpeg_status": "IDLE",
                    "output_path": None,
                    "last_updated": now,
                },
                "thumbnail": {
                    "status": "PENDING",
                    "image_generation_status": "PENDING",
                    "composition_status": "PENDING",
                    "text_layout_status": "PENDING",
                    "validation_status": "PENDING",
                    "output_path": None,
                    "preview_url": None,
                    "headline": None,
                    "last_updated": now,
                },
                "event_log_path": event_log_path,
                "events": [],
            }
            state["productions"][production_id] = record
            if production_id in state["order"]:
                state["order"].remove(production_id)
            state["order"].append(production_id)
            # Keep bounded history of last 30 runs
            while len(state["order"]) > 30:
                oldest = state["order"].pop(0)
                state["productions"].pop(oldest, None)

            self._append_event(record, "PRODUCTION_STARTED", "topic_discovery", f"Started {production_id}")
            self._save_file(path, state)
            return self._public_view(record)

    def bind_event_log_path(self, production_id: str, event_log_path: str) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            record["event_log_path"] = event_log_path
            # Flush existing events to the newly created workspace JSONL file
            try:
                os.makedirs(os.path.dirname(event_log_path), exist_ok=True)
                with open(event_log_path, "w", encoding="utf-8") as f:
                    for ev in record.get("events", []):
                        f.write(json.dumps(ev) + "\n")
            except Exception:
                pass
            self._save_file(path, state)

    def update_topic(self, production_id: str, topic: str) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            record["topic"] = redact_secrets(topic)
            record["last_updated"] = _utc_iso()
            self._save_file(path, state)

    def start_stage(self, production_id: str, stage: str, operation: str = "", progress: float = 5.0) -> None:
        self.update_stage(production_id, stage, status="running", progress=progress, operation=operation)

    def update_stage(
        self,
        production_id: str,
        stage: str,
        *,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        operation: Optional[str] = None,
    ) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            st = record["stages"].get(stage)
            if not st:
                return
            now = _utc_iso()
            if status:
                st["status"] = status
                if status == "running" and not st["started_at"]:
                    st["started_at"] = now
                if status == "completed":
                    st["progress"] = 100.0
                    st["completed_at"] = now
            if progress is not None and st["status"] != "completed":
                st["progress"] = round(max(0.0, min(100.0, float(progress))), 1)
            if operation is not None:
                safe_op = redact_secrets(operation)
                st["operation"] = safe_op
                record["current_operation"] = safe_op
            st["updated_at"] = now

            record["current_stage"] = stage
            record["current_stage_progress"] = st["progress"]
            record["overall_progress"] = _recompute_overall_progress(record["stages"])
            record["last_updated"] = now
            self._update_eta(record)
            self._append_event(
                record,
                f"STAGE_{st['status'].upper()}",
                stage,
                st["operation"] or f"{st['label']} ({st['status']})",
                {"stage_progress": st["progress"]},
            )
            self._save_file(path, state)

    def complete_stage(self, production_id: str, stage: str, operation: str = "") -> None:
        self.update_stage(production_id, stage, status="completed", progress=100.0, operation=operation or f"{stage} complete")

    def update_assets(
        self,
        production_id: str,
        *,
        unique_images: Optional[int] = None,
        generated_images: Optional[int] = None,
        animations: Optional[int] = None,
        failed_acquisitions: Optional[int] = None,
        reused_images: Optional[int] = None,
        max_allowed_reuse: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            assets = record["assets"]
            if unique_images is not None:
                assets["unique_images"] = int(unique_images)
            if generated_images is not None:
                assets["generated_images"] = int(generated_images)
            if animations is not None:
                assets["animations"] = int(animations)
            if failed_acquisitions is not None:
                assets["failed_acquisitions"] = int(failed_acquisitions)
            if reused_images is not None:
                assets["reused_images"] = int(reused_images)
            if max_allowed_reuse is not None:
                assets["max_allowed_reuse"] = int(max_allowed_reuse)
            if status is not None:
                assets["status"] = status
            now = _utc_iso()
            record["last_updated"] = now
            self._append_event(
                record,
                "ASSET_PROGRESS",
                record.get("current_stage"),
                f"Assets: {assets['unique_images']} acquired, {assets['generated_images']} generated, {assets['animations']} animations",
                dict(assets),
            )
            self._save_file(path, state)

    def update_rendering(
        self,
        production_id: str,
        *,
        current_operation: Optional[str] = None,
        current_beat_index: Optional[int] = None,
        current_beat_id: Optional[str] = None,
        completed_scenes: Optional[int] = None,
        total_scenes: Optional[int] = None,
        progress_pct: Optional[float] = None,
        ffmpeg_status: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            rend = record["rendering"]
            if current_operation is not None:
                rend["current_operation"] = redact_secrets(current_operation)
                record["current_operation"] = rend["current_operation"]
            if current_beat_index is not None:
                rend["current_beat_index"] = int(current_beat_index)
            if current_beat_id is not None:
                rend["current_beat_id"] = current_beat_id
            if completed_scenes is not None:
                rend["completed_scenes"] = int(completed_scenes)
            if total_scenes is not None:
                rend["total_scenes"] = int(total_scenes)
            if progress_pct is not None:
                rend["progress_pct"] = round(max(0.0, min(100.0, float(progress_pct))), 1)
            elif rend["total_scenes"] > 0:
                rend["progress_pct"] = round((rend["completed_scenes"] / rend["total_scenes"]) * 100.0, 1)
            if ffmpeg_status is not None:
                rend["ffmpeg_status"] = ffmpeg_status
            if output_path is not None:
                rend["output_path"] = output_path

            now = _utc_iso()
            rend["last_updated"] = now
            st = record["stages"].get("video_rendering")
            if st and st["status"] != "completed":
                st["status"] = "running"
                if not st["started_at"]:
                    st["started_at"] = now
                st["progress"] = rend["progress_pct"]
                st["operation"] = rend["current_operation"]
                st["updated_at"] = now
                record["current_stage"] = "video_rendering"
                record["current_stage_progress"] = st["progress"]
            record["overall_progress"] = _recompute_overall_progress(record["stages"])
            record["last_updated"] = now
            self._update_eta(record)
            self._append_event(
                record,
                "RENDER_PROGRESS",
                "video_rendering",
                rend["current_operation"],
                {
                    "completed_scenes": rend["completed_scenes"],
                    "total_scenes": rend["total_scenes"],
                    "render_pct": rend["progress_pct"],
                    "ffmpeg_status": rend["ffmpeg_status"],
                },
            )
            self._save_file(path, state)

    def update_thumbnail(
        self,
        production_id: str,
        *,
        status: Optional[str] = None,
        image_generation_status: Optional[str] = None,
        composition_status: Optional[str] = None,
        text_layout_status: Optional[str] = None,
        validation_status: Optional[str] = None,
        output_path: Optional[str] = None,
        preview_url: Optional[str] = None,
        headline: Optional[str] = None,
        stage_progress: Optional[float] = None,
    ) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            th = record["thumbnail"]
            if status is not None:
                th["status"] = status
            if image_generation_status is not None:
                th["image_generation_status"] = image_generation_status
            if composition_status is not None:
                th["composition_status"] = composition_status
            if text_layout_status is not None:
                th["text_layout_status"] = text_layout_status
            if validation_status is not None:
                th["validation_status"] = validation_status
            if output_path is not None:
                th["output_path"] = output_path
            if preview_url is not None:
                th["preview_url"] = preview_url
            if headline is not None:
                th["headline"] = headline

            now = _utc_iso()
            th["last_updated"] = now
            st = record["stages"].get("thumbnail_generation")
            if st and st["status"] != "completed":
                st["status"] = "running"
                if not st["started_at"]:
                    st["started_at"] = now
                if stage_progress is not None:
                    st["progress"] = round(max(0.0, min(100.0, float(stage_progress))), 1)
                st["operation"] = f"Thumbnail {th['status']} (comp={th['composition_status']}, text={th['text_layout_status']})"
                st["updated_at"] = now
                record["current_stage"] = "thumbnail_generation"
                record["current_stage_progress"] = st["progress"]
                record["current_operation"] = st["operation"]

            record["overall_progress"] = _recompute_overall_progress(record["stages"])
            record["last_updated"] = now
            self._update_eta(record)
            self._append_event(
                record,
                "THUMBNAIL_PROGRESS",
                "thumbnail_generation",
                f"Thumbnail: {th['status']} ({th['validation_status']})",
                dict(th),
            )
            self._save_file(path, state)

    def complete_production(self, production_id: str, operation: str = "Production complete and verified") -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            now = _utc_iso()
            record["overall_status"] = "completed"
            record["current_stage"] = "finalization"
            record["current_stage_progress"] = 100.0
            record["current_operation"] = redact_secrets(operation)
            record["completed_at"] = now
            record["last_updated"] = now
            record["estimated_remaining_sec"] = 0.0
            if "finalization" in record["stages"]:
                record["stages"]["finalization"]["status"] = "completed"
                record["stages"]["finalization"]["progress"] = 100.0
                record["stages"]["finalization"]["completed_at"] = now
                record["stages"]["finalization"]["updated_at"] = now
            record["overall_progress"] = 100.0
            self._append_event(record, "PRODUCTION_COMPLETED", "finalization", operation)
            self._save_file(path, state)

    def fail_production(
        self,
        production_id: str,
        stage: str,
        error_summary: str,
        retry_possible: bool = True,
        safe_to_resume: bool = True,
    ) -> None:
        with self._lock:
            path, state, _ = self._select_store(production_id)
            record = state["productions"].get(production_id)
            if not record:
                return
            now = _utc_iso()
            safe_err = redact_secrets(error_summary)
            record["overall_status"] = "failed"
            record["current_stage"] = stage
            record["current_operation"] = f"Failed at {stage}: {safe_err[:120]}"
            record["last_updated"] = now
            record["estimated_remaining_sec"] = None
            record["error"] = {
                "stage": stage,
                "summary": safe_err,
                "timestamp": now,
                "retry_possible": bool(retry_possible),
                "safe_to_resume": bool(safe_to_resume),
            }
            st = record["stages"].get(stage)
            if st:
                st["status"] = "failed"
                st["error"] = safe_err
                st["updated_at"] = now
            self._append_event(record, "PRODUCTION_FAILED", stage, safe_err, record["error"])
            self._save_file(path, state)

    def _public_view(self, record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not record:
            return None
        copy = json.loads(json.dumps(record))
        copy.pop("_started_monotonic", None)
        return copy

    def get_production(self, production_id: str, include_tests: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.reload_from_disk()
            if production_id in self._prod_state["productions"]:
                return self._public_view(self._prod_state["productions"][production_id])
            if include_tests and production_id in self._test_state["productions"]:
                return self._public_view(self._test_state["productions"][production_id])
            return None

    def get_active_production(self, include_tests: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.reload_from_disk()
            states = [self._prod_state] + ([self._test_state] if include_tests else [])
            for st in states:
                for pid in reversed(st["order"]):
                    rec = st["productions"].get(pid)
                    if rec and rec.get("overall_status") == "running":
                        return self._public_view(rec)
            return None

    def get_latest_production(self, include_tests: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.reload_from_disk()
            active = self.get_active_production(include_tests=include_tests)
            if active:
                return active
            candidates: List[Dict[str, Any]] = []
            for pid in self._prod_state["order"]:
                rec = self._prod_state["productions"].get(pid)
                if rec:
                    candidates.append(rec)
            if include_tests:
                for pid in self._test_state["order"]:
                    rec = self._test_state["productions"].get(pid)
                    if rec:
                        candidates.append(rec)
            if not candidates:
                return None
            candidates.sort(key=lambda r: r.get("last_updated") or "", reverse=True)
            return self._public_view(candidates[0])

    def get_history(self, limit: int = 20, include_tests: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            self.reload_from_disk()
            items: List[Dict[str, Any]] = []
            for pid in reversed(self._prod_state["order"]):
                rec = self._prod_state["productions"].get(pid)
                if rec:
                    items.append(self._public_view(rec))
            if include_tests:
                for pid in reversed(self._test_state["order"]):
                    rec = self._test_state["productions"].get(pid)
                    if rec:
                        items.append(self._public_view(rec))
            items.sort(key=lambda r: r.get("last_updated") or "", reverse=True)
            return items[:limit]

    def get_dashboard_payload(self, include_tests: bool = False) -> Dict[str, Any]:
        active = self.get_active_production(include_tests=include_tests)
        latest = active or self.get_latest_production(include_tests=include_tests)
        history = self.get_history(limit=15, include_tests=include_tests)
        return {
            "has_active": active is not None,
            "active_production": active,
            "displayed_production": latest,
            "history": [
                {
                    "production_id": h["production_id"],
                    "topic": h["topic"],
                    "execution_context": h["execution_context"],
                    "overall_status": h["overall_status"],
                    "overall_progress": h["overall_progress"],
                    "current_stage": h["current_stage"],
                    "assets": h["assets"],
                    "started_at": h["started_at"],
                    "last_updated": h["last_updated"],
                    "completed_at": h.get("completed_at"),
                }
                for h in history
            ],
            "stages_schema": [{"key": k, "label": lbl} for k, lbl in PIPELINE_STAGES],
        }


production_observability = ProductionObservabilityStore()
