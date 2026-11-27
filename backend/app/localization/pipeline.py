"""Stage 4.02: the Hindi localization pipeline for one completed English Short (resumable, never publishes).

State lives in the Stage 4.01 ``content_localizations`` row (status, step, attempts, error kind, artifacts, QC);
artifacts live in ``media/localizations/hi/<source_job_id>/``, separate from the English originals:

    translate (LIGHT) -> script_hi.json            Hindi adaptation + script QC
    voice     (LIGHT) -> voice/voice_hi.wav       per-scene Hindi voice, pronunciation, validation
    render    (HEAVY) -> pop_info_film/silent, final MP4, subtitles_hi.ass   (inside the shared render slot)
    qc        (LIGHT) -> qc_hi.json               Hindi QC

Statuses: PLANNED, IN_PROGRESS, WAITING (an infrastructure wait: provider, voice or render slot; retried without
redoing finished steps), QC_PASSED, QC_REJECTED (creative; final), FAILED (technical after its attempts).
Publishing is never reachable from here: a QC_PASSED video waits for the separately authorized milestone.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

STEPS = ("translate", "voice", "render", "qc")
TERMINAL = ("QC_PASSED", "QC_REJECTED", "FAILED")
KIND = "hindi_short_localization"
MAX_LENGTH_CORRECTIONS = 2       # automatic shortenings driven by the measured voice length
LENGTH_MARGIN_SEC = 3.0          # aim this far under the voice limit when shortening
COORDINATOR_STEPS = [{"name": "translate", "weight": "LIGHT", "estimate_minutes": 3},
                     {"name": "voice", "weight": "LIGHT", "estimate_minutes": 3},
                     {"name": "render", "weight": "HEAVY", "estimate_minutes": 12},
                     {"name": "qc", "weight": "LIGHT", "estimate_minutes": 4}]


class InfrastructureWait(RuntimeError):
    """Retry later (provider/voice unavailable, render error): completed steps are kept."""


class ProviderWait(InfrastructureWait):
    """The translation provider is unavailable until ``retry_at`` (free-tier quota / rate limit / overload). Waiting
    does not use up the localization's or the coordinator job's attempts; no model is called before ``retry_at``."""

    def __init__(self, message: str, retry_at: Optional[datetime]):
        super().__init__(message)
        self.retry_at = retry_at


MAX_PROVIDER_WAIT_HOURS = 48     # a localization still waiting for the provider after this long fails (retryable)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HindiLocalizer:
    def __init__(self, registry=None, *, media_dir: Optional[Path] = None, translate_fn: Optional[Callable] = None,
                 voice_fn: Optional[Callable] = None, render_fn: Optional[Callable] = None,
                 qc_fn: Optional[Callable] = None, ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None,
                 workspace_root: Optional[Path] = None, target_key: str = "hi"):
        from backend.app.channels.registry import get_registry
        self.reg = registry or get_registry()
        if media_dir is None:
            from backend.app.core.paths import MEDIA_DIR
            media_dir = Path(MEDIA_DIR)
        self.media_dir = Path(media_dir)
        self.target = target_key
        self.translate_fn = translate_fn or self._default_translate
        self.voice_fn = voice_fn or self._default_voice
        self.render_fn = render_fn or self._default_render
        self.qc_fn = qc_fn or self._default_qc
        if ffmpeg is None or ffprobe is None:
            from backend.app.core.paths import get_ffmpeg_path, get_ffprobe_path
            ffmpeg, ffprobe = ffmpeg or get_ffmpeg_path(), ffprobe or get_ffprobe_path()
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        if workspace_root is None:
            from backend.app.core.paths import PROJECT_ROOT_STR
            workspace_root = Path(PROJECT_ROOT_STR)
        self.workspace_root = Path(workspace_root)

    # -- storage ------------------------------------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        return self.reg._connect(True)

    def get(self, loc_id: int) -> Dict[str, Any]:
        self.reg.ensure()
        conn = self.reg._connect(False)
        try:
            r = conn.execute("SELECT * FROM content_localizations WHERE id=?", (int(loc_id),)).fetchone()
        finally:
            conn.close()
        if r is None:
            raise KeyError(f"localization {loc_id} not found")
        d = dict(r)
        d["artifacts"] = json.loads(d.get("artifacts_json") or "{}")
        d["qc"] = json.loads(d["qc_json"]) if d.get("qc_json") else None
        return d

    def _update(self, loc_id: int, **values: Any) -> None:
        if "artifacts" in values:
            values["artifacts_json"] = json.dumps(values.pop("artifacts"), ensure_ascii=False)
        if "qc" in values:
            values["qc_json"] = json.dumps(values.pop("qc"), ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in values)
        conn = self._conn()
        try:
            conn.execute(f"UPDATE content_localizations SET {cols}, updated_at_utc=?, version=version+1 WHERE id=?",
                         (*values.values(), _now(), int(loc_id)))
        finally:
            conn.close()

    def work_dir(self, loc: Dict[str, Any]) -> Path:
        d = self.media_dir / "localizations" / loc["target_channel_key"] / loc["source_job_id"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def source_dir(self, loc: Dict[str, Any]) -> Path:
        return self.media_dir / "productions" / loc["source_job_id"]

    # -- planning -------------------------------------------------------------------------------------------------
    def plan(self, source_job_id: str, *, actor: str = "operator") -> Dict[str, Any]:
        res = self.reg.plan_localization(source_job_id, self.target, actor=actor)
        loc = res["localization"]
        src = self.source_dir(loc)
        missing = [n for n in (f"scripts/script_{source_job_id}.json", "pop_info_film/plan.json") if not (src / n).is_file()]
        if missing:
            self._update(loc["id"], status="FAILED", error_kind="SOURCE_MISSING",
                         error=f"English source artifacts missing: {', '.join(missing)}")
        return {"duplicate": res["duplicate"], "localization": self.get(loc["id"])}

    # -- steps ----------------------------------------------------------------------------------------------------
    def run_step(self, loc_id: int, step: str) -> Dict[str, Any]:
        loc = self.get(loc_id)
        if loc["status"] in TERMINAL:
            return {"skipped": True, "status": loc["status"]}
        arts = dict(loc["artifacts"])
        self._update(loc_id, status="IN_PROGRESS", step=step)
        try:
            if step == "translate":
                arts.update(self.translate_fn(loc, self.work_dir(loc), arts))
            elif step == "voice":
                arts.update(self.voice_fn(loc, self.work_dir(loc), arts))
            elif step == "render":
                arts.update(self.render_fn(loc, self.work_dir(loc), arts))
            elif step == "qc":
                qc = self.qc_fn(loc, self.work_dir(loc), arts)
                (self.work_dir(loc) / "qc_hi.json").write_text(json.dumps(qc, ensure_ascii=False, indent=1), encoding="utf-8")
                status = ("QC_PASSED" if qc["passed"] else
                          "QC_REJECTED" if qc["classification"] == "CREATIVE_REJECTION" else "FAILED")
                self._update(loc_id, status=status, step="qc", qc=qc, artifacts=arts,
                             error_kind=None if qc["passed"] else qc["classification"],
                             error=None if qc["passed"] else "; ".join((qc["creative"] + qc["technical"])[:4]))
                return {"status": status, "qc": qc}
            else:
                raise ValueError(f"unknown step {step}")
        except CreativeRejection as exc:
            self._update(loc_id, status="QC_REJECTED", error_kind="CREATIVE_REJECTION", error=str(exc)[:500],
                         artifacts=arts)
            return {"status": "QC_REJECTED", "error": str(exc)}
        except ProviderWait as exc:
            self._update(loc_id, status="WAITING", error_kind="PROVIDER_WAIT", error=str(exc)[:500], artifacts=arts)
            raise
        except InfrastructureWait as exc:
            self._update(loc_id, status="WAITING", error_kind=type(exc).__name__, error=str(exc)[:500],
                         attempts=int(loc["attempts"]) + 1, artifacts=arts)
            raise
        except Exception as exc:
            self._update(loc_id, status="WAITING", error_kind="TECHNICAL", error=f"{type(exc).__name__}: {str(exc)[:400]}",
                         attempts=int(loc["attempts"]) + 1, artifacts=arts)
            raise InfrastructureWait(f"{step}: {type(exc).__name__}: {str(exc)[:300]}") from exc
        self._update(loc_id, status="IN_PROGRESS", step=step, artifacts=arts, error_kind=None, error=None)
        return {"status": "IN_PROGRESS", "step": step}

    def reopen(self, loc_id: int, *, actor: str, from_step: Optional[str] = None) -> Dict[str, Any]:
        """Retry a WAITING / FAILED localization (completed steps are kept), or, with ``from_step`` (e.g.
        "translate" after a creative rejection), archive that step's and later steps' outputs into
        ``attempt_<n>/`` (never deleted) and redo them."""
        loc = self.get(loc_id)
        if loc["status"] == "QC_PASSED" and not from_step:
            # A defect found after QC (e.g. on-screen text QC could not see) needs an explicit redo step; the passed
            # outputs are archived, never deleted.
            raise ValueError("this localization already passed Hindi QC; redo a step explicitly (e.g. 'render')")
        if loc["status"] == "QC_REJECTED" and not from_step:
            raise ValueError("a creative rejection needs a new adaptation: retry from 'translate'")
        if loc["status"] == "IN_PROGRESS":
            raise ValueError("this localization is running")
        arts = dict(loc["artifacts"])
        if from_step:
            if from_step not in STEPS:
                raise ValueError(f"unknown step {from_step}")
            wd = self.work_dir(loc)
            archive = wd / f"attempt_{int(loc['attempts']) + 1}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            archive.mkdir(parents=True, exist_ok=True)
            names = {"translate": ["script_hi.json"], "voice": ["voice"], "render": ["pop_info_film", "audio",
                     f"hindi_{loc['source_job_id']}.mp4", "subtitles_hi.ass", "subtitle_check.png"], "qc": ["qc_hi.json"]}
            for step in STEPS[STEPS.index(from_step):]:
                for n in names[step]:
                    if (wd / n).exists():
                        (wd / n).rename(archive / n)
            keep = {"translate": [], "voice": ["script_hi"], "render": ["script_hi", "voice_meta"],
                    "qc": ["script_hi", "voice_meta", "plan_hi", "film_dir", "final_mp4", "subtitles_hi", "subtitle_check"]}
            arts = {k: v for k, v in arts.items() if k in keep[from_step]}
        self._update(loc_id, status="PLANNED", error_kind=None, error=None, artifacts=arts, qc=None)
        return self.get(loc_id)

    def mark_failed(self, loc_id: int, reason: str) -> None:
        loc = self.get(loc_id)
        if loc["status"] not in TERMINAL:
            self._update(loc_id, status="FAILED", error_kind=loc.get("error_kind") or "TECHNICAL", error=reason[:500])

    # -- default step implementations ----------------------------------------------------------------------------
    def _english(self, loc: Dict[str, Any]):
        src = self.source_dir(loc)
        script = json.loads((src / "scripts" / f"script_{loc['source_job_id']}.json").read_text(encoding="utf-8"))
        plan = json.loads((src / "pop_info_film" / "plan.json").read_text(encoding="utf-8"))
        return script, plan

    def _default_translate(self, loc, wd: Path, arts) -> Dict[str, Any]:
        out = wd / "script_hi.json"
        if out.is_file():                                   # retry: the adaptation is already done
            return {"script_hi": str(out)}
        self._translate_to(loc, out)
        return {"script_hi": str(out)}

    def _translate_to(self, loc, out: Path, *, scale: float = 1.0, feedback: Sequence[str] = (),
                      length_corrections: int = 0) -> Dict[str, Any]:
        from backend.app.content.grounded_script_writer import default_client, model_chain
        from backend.app.localization import script_qc, translator
        script, plan = self._english(loc)
        req = translator.build_request(topic=plan.get("topic") or loc.get("source_topic_title") or "",
                                       category=plan.get("category") or "", script=script, plan=plan, scale=scale)
        client, model = default_client()
        if client is None:
            raise InfrastructureWait("no Gemini key configured (translation provider unavailable)")
        models = translator.hindi_models(model_chain(model))
        try:
            hindi, attempts = translator.translate(req, client=client, models=models, validate=script_qc.validate,
                                                   initial_feedback=feedback)
        except translator.ProviderUnavailable as exc:
            raise ProviderWait(str(exc), getattr(exc, "retry_at", None)) from exc
        except translator.TranslationRejected as exc:
            raise CreativeRejection("translation: " + "; ".join(exc.problems[:5])) from exc
        doc = {"source_job_id": loc["source_job_id"], "language": "hi", "model": hindi.pop("_model", None),
               "request": req, "hindi": hindi, "attempts": attempts, "created_at_utc": _now(),
               "length_corrections": length_corrections, "scale": scale}
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        return doc

    def _default_voice(self, loc, wd: Path, arts) -> Dict[str, Any]:
        """Synthesizes the voice. When even the fastest natural rate is over the limit, the script is shortened
        automatically with the MEASURED duration as evidence (a corrective change, not a blind retry), at most
        MAX_LENGTH_CORRECTIONS times; every earlier attempt is archived. Nothing over-length reaches the renderer."""
        from backend.app.localization import voice
        meta = wd / "voice" / "voice_hi.json"
        if meta.is_file():
            return {"voice_meta": str(meta)}
        script_path = Path(arts["script_hi"])
        while True:
            doc = json.loads(script_path.read_text(encoding="utf-8"))
            done = int(doc.get("length_corrections") or 0)
            try:
                voice.synthesize(doc["hindi"]["scenes"], doc["hindi"].get("terms") or [], wd / "voice", ffmpeg=self.ffmpeg)
                return {"voice_meta": str(meta)}
            except voice.VoiceUnavailable as exc:
                raise InfrastructureWait(str(exc)) from exc
            except voice.ScriptTooLong as exc:
                if done >= MAX_LENGTH_CORRECTIONS:
                    raise CreativeRejection(f"{exc} after {done} automatic shortening(s); the adaptation needs a "
                                            "human edit or a shorter source") from exc
                limit = voice.MAX_TOTAL_SEC - LENGTH_MARGIN_SEC
                scale = float(doc.get("scale") or 1.0) * limit / exc.seconds
                words = sum(len(str(s.get("narration_tts") or "").split()) for s in doc["hindi"]["scenes"])
                feedback = [f"MEASURED: your previous Hinglish narration ({words} words) takes {exc.seconds:.1f} s to speak "
                            f"even at the fastest natural voice rate; it must take at most {limit:.0f} s. Write about "
                            f"{int(words * limit / exc.seconds)} words in total, at most max_words per scene: shorter, "
                            "simpler sentences with the same facts, numbers and names."]
                arch = wd / f"length_attempt_{done + 1}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                arch.mkdir(parents=True, exist_ok=True)
                shutil.move(str(script_path), str(arch / script_path.name))     # archived, never deleted
                if (wd / "voice").exists():
                    shutil.move(str(wd / "voice"), str(arch / "voice"))          # old clips must never be reused
                self._update(loc["id"], error=f"automatic shortening {done + 1}: {exc}")
                self._translate_to(loc, script_path, scale=scale, feedback=feedback, length_corrections=done + 1)

    def _default_render(self, loc, wd: Path, arts) -> Dict[str, Any]:
        from backend.app.localization import render
        final = wd / f"hindi_{loc['source_job_id']}.mp4"
        if final.is_file() and arts.get("final_mp4"):
            return {}
        doc = json.loads(Path(arts["script_hi"]).read_text(encoding="utf-8"))
        vmeta = json.loads(Path(arts["voice_meta"]).read_text(encoding="utf-8"))
        _script, eplan = self._english(loc)
        plan = render.build_hindi_plan(eplan, doc["hindi"], vmeta["timings"])
        film = render.package_film(self.source_dir(loc) / "pop_info_film", wd / "pop_info_film", plan)
        silent = film / "silent_hi.mp4"
        if not silent.is_file():
            render.render_film(film, self.workspace_root, silent)
        render.mux(self.ffmpeg, silent, Path(vmeta["voice_path"]), plan, wd / "audio", final)
        ass = render.write_ass(plan, wd / "subtitles_hi.ass")
        mid = float(plan["scenes"][min(2, len(plan["scenes"]) - 1)]["start_sec"]) + 0.5
        check = render.render_check_ass(self.ffmpeg, Path(ass["path"]), mid, wd / "subtitle_check.png")
        return {"plan_hi": str(film / "plan.json"), "film_dir": str(film), "final_mp4": str(final),
                "subtitles_hi": ass["path"], "subtitle_check": check}

    def _default_qc(self, loc, wd: Path, arts) -> Dict[str, Any]:
        from backend.app.localization import qc, script_qc
        doc = json.loads(Path(arts["script_hi"]).read_text(encoding="utf-8"))
        plan = json.loads(Path(arts["plan_hi"]).read_text(encoding="utf-8"))
        vmeta = json.loads(Path(arts["voice_meta"]).read_text(encoding="utf-8"))
        typo: List[Dict[str, Any]] = []
        try:   # the existing layout probe + visual quality gate (text boxes measured on the page)
            from backend.app.rendering.pop_info.pipeline import probe_layout
            from backend.app.services import visual_quality_gate as vqg
            times = sorted({round(s["start_sec"] + min(2.4, (s["end_sec"] - s["start_sec"]) * 0.7), 2) for s in plan["scenes"]})
            layout = probe_layout(Path(arts["film_dir"]), times)
            report = vqg.analyse(arts["final_mp4"], ffmpeg=self.ffmpeg, ffprobe=self.ffprobe, plan=plan,
                                 layout_audit=layout["samples"])
            typo = [{"t": i.t, "message": i.message} for i in report.issues
                    if getattr(i, "category", None) == "typography" and getattr(i, "severity", "") == "major"]
        except Exception as exc:
            logger.warning("Hindi layout probe unavailable (%s); typography not measured", type(exc).__name__)
        return qc.evaluate(mp4=arts["final_mp4"], plan=plan, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
                           script_problems=script_qc.validate(doc["request"], doc["hindi"]),
                           subtitle_check=arts.get("subtitle_check"), typography_issues=typo,
                           voice_total_sec=vmeta.get("total_sec"),
                           subtitles_text=(Path(arts["subtitles_hi"]).read_text(encoding="utf-8-sig")
                                           if arts.get("subtitles_hi") and Path(arts["subtitles_hi"]).is_file() else None))


class CreativeRejection(RuntimeError):
    """The content does not meet the localization quality rules (final; never retried as infrastructure)."""


# -- coordinator integration -------------------------------------------------------------------------------------

def executor(ctx) -> Dict[str, Any]:
    """Runs one step of a localization inside the Stage 3.37 coordinator (HEAVY render steps hold the shared
    render slot; the coordinator admits them only in Shorts gaps and within the resource thresholds)."""
    loc_id = int((ctx.job.get("checkpoint") or {}).get("localization_id") or ctx.checkpoint.get("localization_id"))
    ctx.checkpoint["localization_id"] = loc_id
    loc = HindiLocalizer()
    try:
        res = loc.run_step(loc_id, ctx.step["name"])
    except ProviderWait as exc:
        from backend.app.scheduler.production_coordinator import StepDeferred
        row = loc.get(loc_id)
        created = _parse_utc(row.get("created_at_utc"))
        if created and datetime.now(timezone.utc) - created > timedelta(hours=MAX_PROVIDER_WAIT_HOURS):
            loc.mark_failed(loc_id, f"translation provider unavailable for more than {MAX_PROVIDER_WAIT_HOURS} h")
            raise
        until = exc.retry_at or datetime.now(timezone.utc) + timedelta(minutes=15)
        raise StepDeferred(str(exc), until) from exc
    except InfrastructureWait:
        if int(ctx.job.get("attempts") or 0) + 1 >= int(ctx.job.get("max_attempts") or 3):
            loc.mark_failed(loc_id, "infrastructure failures exhausted the coordinator attempts")
        raise
    ctx.checkpoint[f"step_{ctx.step['name']}"] = res.get("status")
    return {}


def overview(localizer: Optional[HindiLocalizer] = None, coordinator=None, limit: int = 30) -> Dict[str, Any]:
    """Dashboard view: each localization with its steps, QC, artifacts (presence only) and publishing eligibility."""
    loc = localizer or HindiLocalizer()
    reg = loc.reg
    if coordinator is None:
        from backend.app.scheduler.production_coordinator import get_coordinator
        coordinator = get_coordinator()
    jobs = {}
    try:
        for j in coordinator.list_jobs(limit=200):
            if j.get("kind") == KIND:
                lid = (j.get("checkpoint") or {}).get("localization_id")
                if lid is not None and (lid not in jobs or j["enqueued_at_utc"] > jobs[lid]["enqueued_at_utc"]):
                    jobs[lid] = {k: j.get(k) for k in ("id", "state", "next_step", "attempts", "error", "enqueued_at_utc")}
    except Exception:
        pass
    rows = []
    for r in reg.localizations(limit=limit):
        d = loc.get(r["id"])
        arts = d["artifacts"]
        qc = d["qc"] or {}
        rows.append({"id": d["id"], "source_job_id": d["source_job_id"], "source_topic_title": d["source_topic_title"],
                     "target_channel_key": d["target_channel_key"], "language": d["language"], "status": d["status"],
                     "step": d.get("step"), "attempts": d.get("attempts"), "error_kind": d.get("error_kind"),
                     "error": d.get("error"),
                     "script": "ready" if arts.get("script_hi") else "pending",
                     "voice": "ready" if arts.get("voice_meta") else "pending",
                     "render": "ready" if arts.get("final_mp4") else "pending",
                     "video_available": bool(arts.get("final_mp4") and Path(arts["final_mp4"]).is_file()),
                     "qc": {k: qc.get(k) for k in ("passed", "classification", "creative", "technical", "measured")} if qc else None,
                     "coordinator_job": jobs.get(d["id"]),
                     "publishing": reg.publishing_eligibility(d["target_channel_key"], d)})
    return {"localizations": rows,
            "note": "Hindi localizes completed English Shorts only. Publishing is never automatic and stays blocked until "
                    "the Hindi channel is verified, active and Hindi publishing is explicitly authorized."}


def enqueue(source_job_id: str, *, operator: str, coordinator=None, localizer: Optional[HindiLocalizer] = None) -> Dict[str, Any]:
    """Plans (idempotent) and queues one localization in the coordinator's LOCALIZATION queue."""
    from backend.app.scheduler.production_coordinator import LOCALIZATION, get_coordinator
    loc = (localizer or HindiLocalizer()).plan(source_job_id, actor=operator)
    row = loc["localization"]
    if row["status"] in TERMINAL:
        return {"localization": row, "queued": False, "reason": f"localization is {row['status']}"}
    coord = coordinator or get_coordinator()
    job_id = f"loc_{row['target_channel_key']}_{source_job_id}_a{int(row['attempts'])}"
    res = coord.enqueue(kind=KIND, title=f"Hindi: {row.get('source_topic_title') or source_job_id}",
                        steps=COORDINATOR_STEPS, requested_by=operator, priority=40, job_id=job_id,
                        queue=LOCALIZATION, checkpoint={"localization_id": row["id"]})
    return {"localization": row, "queued": not res["duplicate"], "coordinator_job": res["job"]["id"]}


AUTO_ACTOR = "localization-policy"
AUTO_SOURCE_MAX_AGE_HOURS = 26


def auto_enqueue_next(*, coordinator=None, localizer: Optional[HindiLocalizer] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Bounded autonomous Hinglish localization (remediation closure; local, zero cost, reversible: files only under
    media/localizations, never published). Enqueues the newest completed English Short that has no localization yet,
    only when the LOCALIZATION queue is enabled and idle and fewer than ``localization_auto_daily_max`` were
    auto-enqueued this IST day. Rendering still waits for the shared slot in Shorts gaps; QC, duplicate mapping and
    the publishing block are unchanged."""
    from backend.app.scheduler.production_coordinator import LOCALIZATION, QUEUED, RUNNING, get_coordinator
    coord = coordinator or get_coordinator()
    cfg = coord.config()
    cap = int(cfg.get("localization_auto_daily_max") or 0)
    if not cfg.get("localization_enabled") or cap <= 0:
        return {"queued": False, "reason": "automatic localization is off (localization_enabled / daily max 0)"}
    jobs = coord.list_jobs(limit=200)
    if any(j["queue"] == LOCALIZATION and j["state"] in (QUEUED, RUNNING) for j in jobs):
        return {"queued": False, "reason": "a localization is already queued or running"}
    now = now or datetime.now(timezone.utc)
    ist_day = (now + timedelta(hours=5, minutes=30)).date().isoformat()
    today = [j for j in jobs if j["queue"] == LOCALIZATION and j.get("requested_by") == AUTO_ACTOR
             and _ist_date(j.get("enqueued_at_utc")) == ist_day]
    if len(today) >= cap:
        return {"queued": False, "reason": f"daily automatic localization limit reached ({cap})"}
    loc = localizer or HindiLocalizer()
    with sqlite3.connect(Path(loc.reg.db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10) as c:
        since = (now - timedelta(hours=AUTO_SOURCE_MAX_AGE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        rows = c.execute("SELECT id FROM persistent_jobs WHERE status='COMPLETED' AND created_at >= ? "
                         "ORDER BY created_at DESC LIMIT 30", (since,)).fetchall()
        try:
            done = {r[0] for r in c.execute("SELECT source_job_id FROM content_localizations WHERE target_channel_key=?",
                                            (loc.target,))}
        except sqlite3.OperationalError:
            done = set()
    for (job_id,) in rows:
        if job_id in done or str(job_id).startswith(("test_", "mock_")):
            continue
        src = loc.media_dir / "productions" / job_id
        if not (src / "pop_info_film" / "plan.json").is_file() or not (src / "scripts" / f"script_{job_id}.json").is_file():
            continue
        res = enqueue(job_id, operator=AUTO_ACTOR, coordinator=coord, localizer=loc)
        return dict(res, source_job_id=job_id)
    return {"queued": False, "reason": "no completed English Short without a localization"}


def _parse_utc(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ist_date(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d + timedelta(hours=5, minutes=30)).date().isoformat()
