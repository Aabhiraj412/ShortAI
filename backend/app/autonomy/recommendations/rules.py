"""Pattern detection, root-cause hypotheses and recommendation text: deterministic, explainable rules.

Every rule reads only ``Signals`` (Stage 3.32A data) and returns ``Finding`` objects or an "insufficient evidence"
note. A rule never invents data: when a metric is unavailable it reports insufficient evidence instead. Each finding
separates the *observation* (what the data shows, always a fact) from the *hypothesis* (why it may be happening),
whose strength is CONFIRMED (the data itself shows the mechanism), STRONG or WEAK.

Rules never act. Suggested actions are text for the operator; none changes retry policy, schedules, OAuth, flags,
duplicate or quality checks.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.app.autonomy.recommendations.signals import Signals

CATEGORIES = {
    "production_reliability": "Production reliability",
    "renderer": "Renderer intelligence",
    "topic": "Topic intelligence",
    "api_provider": "API and provider intelligence",
    "recovery": "Recovery intelligence",
    "content_quality": "Content quality intelligence",
}
STRENGTHS = ("CONFIRMED", "STRONG", "WEAK")
JOB_RE = re.compile(r"^(cycle_\d+)_(\d{8})_(\d{4})$")


@dataclass
class Finding:
    rule_id: str
    category: str
    subject: str
    title: str
    description: str
    problem: str
    evidence: List[Dict[str, Any]]
    hypothesis: str
    hypothesis_strength: str
    action: str
    benefit: str
    risk: str = "LOW"                       # risk of carrying out the suggested action
    severity: str = "medium"                # critical | high | medium | low
    occurrences: int = 1
    sample_size: int = 1
    min_sample: int = 1
    consistency: float = 1.0                # 0..1: how uniform the evidence is
    completeness: float = 1.0               # 0..1: data availability of the sources used
    impact: float = 0.5                     # 0..1: production impact
    affected_jobs: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    related_events: List[Dict[str, Any]] = field(default_factory=list)
    last_seen: Optional[str] = None
    supersedes: List[str] = field(default_factory=list)
    ttl_hours: int = 72

    def __post_init__(self):
        assert self.category in CATEGORIES, self.category
        assert self.hypothesis_strength in STRENGTHS, self.hypothesis_strength
        self.affected_jobs = sorted(set(self.affected_jobs))[:25]

    @property
    def key(self) -> str:
        return f"{self.rule_id}:{self.subject}"


@dataclass
class RuleOutput:
    findings: List[Finding] = field(default_factory=list)
    insufficient: List[Dict[str, str]] = field(default_factory=list)

    def skip(self, rule_id: str, reason: str) -> None:
        self.insufficient.append({"rule_id": rule_id, "reason": reason})


def _ev(fact: str, value: Any, source: str) -> Dict[str, Any]:
    return {"fact": fact, "value": value, "source": source}


def _parse(ts: Any) -> Optional[datetime]:
    from backend.app.services.operational_intelligence import _parse_ts
    return _parse_ts(ts)


def _pct(a: float, b: float) -> Optional[float]:
    return round(100.0 * a / b, 1) if b else None


def _minutes(start: Any, end: Any) -> Optional[float]:
    s, e = _parse(start), _parse(end)
    return (e - s).total_seconds() / 60.0 if s and e and e >= s else None


def _latest(items: List[Dict[str, Any]], key: str = "at") -> Optional[str]:
    vals = [i.get(key) for i in items if i.get(key)]
    return max(vals) if vals else None


# ---------------------------------------------------------------------------------------------------------------
# A. Production reliability
# ---------------------------------------------------------------------------------------------------------------
FINISHED = ("COMPLETED", "FAILED", "RECOVERY_WINDOW_EXPIRED", "ABORTED_PRE_RESET")


def rule_failure_rate_rising(sig: Signals, out: RuleOutput) -> None:
    rid = "prod.failure_rate_rising"
    jobs = [j for j in sig.bundle.get("jobs", []) if j.get("status") in FINISHED]
    cutoff = sig.now - timedelta(hours=24)
    recent = [j for j in jobs if (_parse(j.get("created_at")) or sig.now) >= cutoff]
    base = [j for j in jobs if j not in recent]
    if len(recent) < 4 or len(base) < 8:
        out.skip(rid, f"needs ≥4 finished cycles in the last 24 h and ≥8 before (have {len(recent)} and {len(base)})")
        return
    fr = lambda js: _pct(sum(1 for j in js if j["status"] != "COMPLETED"), len(js))
    r, b = fr(recent), fr(base)
    if r - b < 20:
        return
    failed = [j["id"] for j in recent if j["status"] != "COMPLETED"]
    out.findings.append(Finding(
        rid, "production_reliability", "window", "Production failures are increasing",
        f"{len(failed)} of {len(recent)} cycles finished in the last 24 h did not complete ({r}%), against {b}% over the "
        f"rest of the {sig.window_days}-day window.",
        f"Failure rate rose by {round(r - b, 1)} percentage points in the last 24 hours.",
        [_ev("Failure rate, last 24 h", f"{r}%", "persistent_jobs"), _ev("Failure rate, earlier in window", f"{b}%", "persistent_jobs"),
         _ev("Cycles finished, last 24 h", len(recent), "persistent_jobs")],
        "A new or worsening failure cause started recently; the failure patterns below show which causes grew.", "WEAK",
        "Compare the failure patterns of the last 24 hours with earlier days in the Operations tab and address the cause that grew.",
        "Restores the earlier completion rate.", severity="high", occurrences=len(failed), sample_size=len(recent),
        min_sample=4, consistency=min(1.0, (r - b) / 50), completeness=sig.completeness("production_health"), impact=0.9,
        affected_jobs=failed, last_seen=max((j.get("updated_at") or "") for j in recent) or None, ttl_hours=24))


def rule_recovery_expirations(sig: Signals, out: RuleOutput) -> None:
    rid = "prod.recovery_window_expirations"
    m = sig.metrics("production_health")
    if not sig.available("production_health"):
        out.skip(rid, "production health unavailable")
        return
    expired, finished = int(m.get("recovery_window_expired") or 0), int(m.get("finished") or 0)
    if finished < 10 or expired < 5:
        out.skip(rid, f"needs ≥10 finished cycles and ≥5 expirations (have {finished} and {expired})")
        return
    share = _pct(expired, finished)
    if share < 20:
        return
    jobs = [j for j in sig.bundle.get("jobs", []) if j.get("status") == "RECOVERY_WINDOW_EXPIRED"]
    stages = Counter((j.get("current_stage") or "NOT_RECORDED") for j in jobs)
    recorded = {k: v for k, v in stages.items() if k not in ("NOT_RECORDED", "RECOVERY_WINDOW_EXPIRED", "FAILED")}
    top = max(recorded.items(), key=lambda kv: kv[1]) if recorded else None
    if top and top[1] >= 0.6 * expired:
        hyp, strength = (f"Most expirations ({top[1]} of {expired}) stopped at stage {top[0]}; delays at that stage "
                         "are the most likely cause."), "STRONG"
    else:
        hyp, strength = ("Cycles run out of recovery time, but the stage at expiry is mostly not recorded, so the "
                         "delay cannot be attributed to one stage from this data."), "WEAK"
    out.findings.append(Finding(
        rid, "production_reliability", "window", "Many cycles exhaust their recovery windows",
        f"{expired} of {finished} finished production cycles ({share}%) ended because their recovery window expired.",
        "Recovery-window expirations are a major source of lost productions.",
        [_ev("Expired cycles", expired, "persistent_jobs"), _ev("Finished cycles", finished, "persistent_jobs"),
         _ev("Expiry share", f"{share}%", "production_health"), _ev("Stage at expiry", dict(stages.most_common(5)), "persistent_jobs")],
        hyp, strength,
        "Investigate recurring stage delays and the recovery-window allocation for the affected cycles. "
        "Do not change recovery deadlines automatically.",
        "More cycles complete inside their windows.", risk="MEDIUM",
        severity="critical" if share >= 35 else "high", occurrences=expired, sample_size=finished, min_sample=10,
        consistency=min(1.0, share / 50), completeness=sig.completeness("production_health"), impact=1.0,
        affected_jobs=[j["id"] for j in jobs], last_seen=_latest(jobs, "updated_at"),
        observations=[f"{n} expired at stage {s}" for s, n in stages.most_common(3)]))


def rule_stage_failures(sig: Signals, out: RuleOutput) -> None:
    rid = "prod.repeated_stage_failure"
    if not sig.available("production_health"):
        out.skip(rid, "production health unavailable")
        return
    dist = {k: int(v) for k, v in (sig.metrics("production_health").get("stage_failure_distribution") or {}).items()
            if k != "NOT_RECORDED"}
    total = sum(dist.values())
    if total < 3:
        out.skip(rid, f"needs ≥3 failures with a recorded stage (have {total})")
        return
    for stage, n in dist.items():
        if n < 3 or n / total < 0.4:
            continue
        jobs = [j for j in sig.bundle.get("jobs", []) if j.get("current_stage") == stage
                and j.get("status") in ("FAILED", "RECOVERY_WINDOW_EXPIRED")]
        causes = Counter(f["cause"] for f in sig.failures if f.get("stage") == stage)
        out.findings.append(Finding(
            rid, "production_reliability", stage, f"Failures concentrate at the {stage} stage",
            f"{n} of {total} failures with a recorded stage stopped at {stage}."
            + (f" Most common cause there: {causes.most_common(1)[0][0]}." if causes else ""),
            f"Repeated failures at stage {stage}.",
            [_ev("Failures at stage", n, "persistent_jobs"), _ev("Failures with a recorded stage", total, "persistent_jobs"),
             _ev("Causes at this stage", dict(causes.most_common(5)), "failure_intelligence")],
            (f"The {stage} step has a recurring problem; the dominant cause '{causes.most_common(1)[0][0]}' is the "
             "first candidate." if causes else f"The {stage} step has a recurring problem."), "STRONG" if causes else "WEAK",
            f"Review the failure messages at {stage} (Operations tab, failure patterns) and fix the dominant cause.",
            "Removes a repeated failure point.", severity="high" if n >= 5 else "medium", occurrences=n, sample_size=total,
            min_sample=3, consistency=n / total, completeness=sig.completeness("production_health"), impact=0.8,
            affected_jobs=[j["id"] for j in jobs], last_seen=_latest(jobs, "updated_at")))


def _durations(sig: Signals) -> List[Tuple[str, float]]:
    out = []
    for j in sig.bundle.get("jobs", []):
        if j.get("status") == "COMPLETED":
            d = _minutes(j.get("actual_start_at"), j.get("completed_at"))
            if d is not None:
                out.append((j["id"], d))
    return out


def rule_abnormal_duration(sig: Signals, out: RuleOutput) -> None:
    rid = "prod.abnormal_duration"
    d = [x for x in _durations(sig) if x[1] <= 24 * 60]           # re-armed completions days later are recovery, not duration
    if len(d) < 6:
        out.skip(rid, f"needs ≥6 completed cycles with start and completion times (have {len(d)})")
        return
    med = statistics.median(v for _, v in d)
    slow = [(j, v) for j, v in d if v > max(3 * med, med + 30)]
    if len(slow) < 3:
        return
    out.findings.append(Finding(
        rid, "production_reliability", "window", "Some productions take far longer than usual",
        f"{len(slow)} of {len(d)} completed cycles took more than 3× the median production time ({round(med, 1)} min).",
        "Abnormally long production durations.",
        [_ev("Median production minutes", round(med, 1), "persistent_jobs"),
         _ev("Slow completions (minutes)", {j: round(v, 1) for j, v in slow[:8]}, "persistent_jobs")],
        "Slow productions usually wait on a stage (rendering, voice-over, uploads) or on retries; per-stage timings "
        "for these jobs show which.", "WEAK",
        "Open the slow jobs' stage timings and look for a common slow stage.",
        "Shorter production time leaves more of the recovery window for retries.",
        severity="medium", occurrences=len(slow), sample_size=len(d), min_sample=6, consistency=len(slow) / len(d),
        completeness=sig.completeness("production_health"), impact=0.5, affected_jobs=[j for j, _ in slow]))


def rule_near_deadline(sig: Signals, out: RuleOutput) -> None:
    rid = "prod.approaching_deadline"
    windows = sig.recovery_minutes_by_cycle
    if not windows:
        out.skip(rid, "active schedule recovery windows unavailable")
        return
    close, total = [], 0
    for jid, dur in _durations(sig):
        m = JOB_RE.match(jid)
        w = windows.get(m.group(1)) if m else None
        if not w or dur > 24 * 60:
            continue
        total += 1
        if dur >= 0.8 * w:
            close.append((jid, round(dur, 1), w))
    if total < 6:
        out.skip(rid, f"needs ≥6 completed cycles that map to the active schedule (have {total})")
        return
    if len(close) < 3 or len(close) / total < 0.15:
        return
    out.findings.append(Finding(
        rid, "production_reliability", "window", "Productions finish close to their recovery deadline",
        f"{len(close)} of {total} completed cycles used at least 80% of their recovery window.",
        "Jobs repeatedly approach their recovery deadlines; a small delay would make them expire.",
        [_ev("Near-deadline completions", {j: f"{d} of {w} min" for j, d, w in close[:8]}, "persistent_jobs + schedule")],
        "The recovery windows are tight for the current production time.", "STRONG",
        "Review whether these cycles' recovery windows match their real production time (operator decision; nothing is changed automatically).",
        "Fewer expirations from small delays.", risk="MEDIUM", severity="medium", occurrences=len(close), sample_size=total,
        min_sample=6, consistency=len(close) / total, impact=0.7, affected_jobs=[j for j, _, _ in close]))


# ---------------------------------------------------------------------------------------------------------------
# B. Renderer intelligence
# ---------------------------------------------------------------------------------------------------------------
RENDER_CAUSES = ("ffmpeg", "renderer", "render_timeout", "file_lock", "media_acquisition", "renderer_compliance")
CAUSE_TEXT = {
    "ffmpeg": ("FFmpeg failure", "Inspect the FFmpeg filter graph and input media of the failing step."),
    "renderer": ("Lemo-Opuscar renderer failure", "Inspect Lemo-Opuscar start-up and the render log of the affected jobs."),
    "render_timeout": ("Rendering timeout", "Compare render durations and machine load at the time of the timeouts."),
    "file_lock": ("Windows file-locking error",
                  "Find what holds the file (antivirus, indexer, preview, another process) and whether cleanup runs while files are open."),
    "media_acquisition": ("Media acquisition failure", "Check the media providers' responses and limits for the affected jobs."),
    "renderer_compliance": ("Renderer compliance block", "Check why a non-approved renderer was selected (QC block is correct; do not bypass it)."),
}


def _determinism(pattern: Dict[str, Any], jobs: List[str]) -> Tuple[str, str, bool]:
    """(hypothesis, strength, deterministic) for one failure pattern."""
    t = pattern.get("template", "").lower()
    classes = set(pattern.get("classes") or [pattern.get("class")])
    if "padded dimensions cannot be smaller" in t:
        return ("The pad filter is asked for an output smaller than the input frame. The same geometry error repeats "
                "with identical wording, so it is deterministic (input-dependent), not transient; retrying the same "
                "media will fail the same way.", "CONFIRMED", True)
    if "window.ready" in t or "still waiting for window" in t:
        return ("The renderer page never signalled READY. This can be a page-script error (deterministic) or "
                "start-up contention (transient); the data does not distinguish them.", "WEAK", False)
    if "winerror 32" in t or "being used by another process" in t:
        return ("Another process held the file open. File locks are usually environmental and transient on "
                "Windows, but repeats point to a process that regularly holds these files.", "WEAK", False)
    if classes & {"DETERMINISTIC", "PERMANENT"}:
        return ("The retry policy classifies this error as non-transient and the same message repeats.", "STRONG", True)
    if pattern.get("count", 0) >= 3 and len(set(jobs)) >= 2:
        return ("The identical error repeats across different jobs, which suggests a deterministic cause even though "
                "the retry policy classifies it as transient.", "WEAK", True)
    return ("The error is classified as transient by the retry policy.", "WEAK", False)


def rule_renderer_patterns(sig: Signals, out: RuleOutput) -> None:
    rid = "render.failure_pattern"
    fi = sig.metrics("failure_intelligence")
    if not sig.available("failure_intelligence"):
        out.skip(rid, "failure intelligence unavailable")
        return
    for p in fi.get("patterns") or []:
        if p.get("cause") not in RENDER_CAUSES or int(p.get("count") or 0) < 2:
            continue
        jobs = list(p.get("jobs") or p.get("example_jobs") or [])
        hyp, strength, deterministic = _determinism(p, jobs)
        label, action = CAUSE_TEXT[p["cause"]]
        t = p.get("template", "")
        if "padded dimensions cannot be smaller" in t.lower():
            label, action = "FFmpeg padding dimension failure", ("Inspect the Priority B adaptation dimension calculation "
                                                                "(pad target versus input frame size) for the affected media.")
        retry_note = ""
        if deterministic and p.get("class") == "TRANSIENT":
            retry_note = (" The retry policy classifies it as TRANSIENT, so it may be retried without effect; "
                          "the retry policy is not changed automatically.")
        subject = f"{p['cause']}:{t[:60]}"
        out.findings.append(Finding(
            rid, "renderer", subject, f"{label} repeated {p['count']}×",
            f"{label} detected {p['count']} times in the last {sig.window_days} days. Message: \"{t[:160]}\".{retry_note}",
            f"Repeated {label.lower()}.",
            [_ev("Occurrences", p["count"], "failure_intelligence"), _ev("Retry-policy class(es)", p.get("classes"), "retry_policy"),
             _ev("Normalized message", t[:200], "failure_intelligence"), _ev("First / last seen", [p.get("first_seen"), p.get("last_seen")], "failure_intelligence")],
            hyp, strength, action, "Removes a recurring render failure and wasted retries.",
            severity="high" if deterministic or p["count"] >= 4 else "medium", occurrences=int(p["count"]),
            sample_size=int(p["count"]), min_sample=2, consistency=1.0, completeness=sig.completeness("failure_intelligence"),
            impact=0.8, affected_jobs=jobs, last_seen=p.get("last_seen")))


def rule_render_duration(sig: Signals, out: RuleOutput) -> None:
    rid = "render.abnormal_duration"
    durs = []
    for p in sig.bundle.get("productions", []):
        st = ((p.get("stages") or {}).get("video_rendering") or {})
        if st.get("status") == "completed":
            d = _minutes(st.get("started_at"), st.get("completed_at"))
            if d is not None:
                durs.append((p["id"], d * 60))
    if len(durs) < 6:
        out.skip(rid, f"needs ≥6 completed renders with timings (have {len(durs)})")
        return
    med = statistics.median(v for _, v in durs)
    slow = [(j, v) for j, v in durs if v > 2 * med]
    if len(slow) < 2:
        return
    out.findings.append(Finding(
        rid, "renderer", "window", "Some renders take far longer than usual",
        f"{len(slow)} of {len(durs)} renders took more than twice the median render time ({round(med)} s).",
        "Abnormal rendering duration.",
        [_ev("Median render seconds", round(med), "production_progress_store"),
         _ev("Slow renders (seconds)", {j: round(v) for j, v in slow[:8]}, "production_progress_store")],
        "Slow renders often coincide with machine load or larger scenes; the data here does not show which.", "WEAK",
        "Compare the slow renders' scene counts and the machine load at the time.", "Predictable render times.",
        severity="low", occurrences=len(slow), sample_size=len(durs), min_sample=6, consistency=len(slow) / len(durs),
        impact=0.3, affected_jobs=[j for j, _ in slow]))


def rule_media_acquisition(sig: Signals, out: RuleOutput) -> None:
    rid = "render.media_acquisition"
    m = sig.metrics("renderer_reliability")
    failed, unique = m.get("media_acquisitions_failed"), m.get("media_unique_images")
    if failed is None:
        out.skip(rid, "media acquisition counts unavailable (progress store)")
        return
    if failed < 10:
        return
    out.findings.append(Finding(
        rid, "renderer", "window", "Many media acquisitions fail",
        f"{failed} media acquisitions failed in the window" + (f" while {unique} unique images were obtained." if unique is not None else "."),
        "Frequent media acquisition failures.",
        [_ev("Failed acquisitions", failed, "production_progress_store"), _ev("Unique images obtained", unique, "production_progress_store")],
        "Some media sources fail or return unusable items; productions still completed using other media.", "WEAK",
        "Review which media providers fail most often in the affected productions' asset logs.",
        "Fewer wasted requests and more varied visuals.", severity="low", occurrences=int(failed), sample_size=int(failed),
        min_sample=10, consistency=0.7, completeness=sig.completeness("renderer_reliability"), impact=0.3))


# ---------------------------------------------------------------------------------------------------------------
# C. Topic intelligence (Stage 3.31 candidate ledger)
# ---------------------------------------------------------------------------------------------------------------
def _topic(sig: Signals, out: RuleOutput, rid: str) -> Optional[Dict[str, Any]]:
    if not sig.available("topic_intelligence"):
        out.skip(rid, "candidate ledger data unavailable (Stage 3.31 candidate_ledger flag off or no runs)")
        return None
    return sig.metrics("topic_intelligence")


def rule_topic_exhaustion(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.exhaustion"
    fails = [f for f in sig.failures if f.get("cause") == "topic_exhaustion"]
    if len(fails) < 2:
        return
    out.findings.append(Finding(
        rid, "topic", "window", "Topic exhaustion stops productions",
        f"{len(fails)} productions failed because every discovered topic had already been produced.",
        "Topic discovery runs out of new topics.",
        [_ev("Exhaustion failures", len(fails), "failure_intelligence"), _ev("Example message", fails[-1]["template"][:200], "persistent_jobs")],
        "Discovery returns mostly topics that were already used; the candidate pool is too narrow for the production rate.",
        "STRONG", "Inspect discovery diversity (providers, query rotation) for the affected cycles. Duplicate checks stay as they are.",
        "Fewer lost cycles.", severity="high", occurrences=len(fails), sample_size=len(fails), min_sample=2,
        completeness=sig.completeness("failure_intelligence"), impact=1.0,
        affected_jobs=[f["job_id"] for f in fails if f.get("job_id")], last_seen=_latest(fails)))


def rule_low_acceptance(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.low_acceptance"
    m = _topic(sig, out, rid)
    if m is None:
        return
    reasons = {k: int(v) for k, v in (m.get("rejection_reasons") or {}).items()}
    rejected = sum(reasons.values())
    acc = m.get("acceptance_pct")
    if acc is None or rejected < 30:
        out.skip(rid, f"needs ≥30 candidate decisions (have {rejected} rejections)")
        return
    top_reason, top_n = max(reasons.items(), key=lambda kv: kv[1])
    if acc < 20:
        out.findings.append(Finding(
            rid, "topic", "window", "Few discovered candidates are accepted",
            f"Only {acc}% of evaluated candidates were accepted. The most common rejection reason is {top_reason} "
            f"({top_n} of {rejected}).",
            "Low candidate acceptance rate.",
            [_ev("Acceptance", f"{acc}%", "topic_candidate_events"), _ev("Rejection reasons", reasons, "topic_candidate_events")],
            f"Discovery queries return many candidates that fail '{top_reason}'; query or source quality, not the filter, "
            "is the likely limit.", "WEAK",
            "Review the queries and sources that produce most of the rejected candidates.",
            "More eligible topics per discovery run.", severity="medium", occurrences=rejected, sample_size=rejected,
            min_sample=30, consistency=top_n / rejected, completeness=sig.completeness("topic_intelligence"), impact=0.6))
    if top_n / rejected >= 0.6:
        out.findings.append(Finding(
            "topic.dominant_rejection_reason", "topic", top_reason, f"Most rejections share one reason: {top_reason}",
            f"{top_n} of {rejected} rejected candidates ({_pct(top_n, rejected)}%) were rejected for {top_reason}.",
            "One rejection reason dominates.",
            [_ev("Rejections for this reason", top_n, "topic_candidate_events"), _ev("All rejections", rejected, "topic_candidate_events")],
            f"A systematic mismatch between what discovery returns and the {top_reason} criterion.", "STRONG",
            f"Inspect sample candidates rejected for {top_reason} to see whether the source or the criterion is off.",
            "Targets the largest source of wasted candidates.", severity="low", occurrences=top_n, sample_size=rejected,
            min_sample=30, consistency=top_n / rejected, completeness=sig.completeness("topic_intelligence"), impact=0.4))


def _provider_stats(sig: Signals) -> Dict[str, Dict[str, int]]:
    m = sig.metrics("topic_intelligence")
    return {k: {kk: int(vv or 0) for kk, vv in (v or {}).items() if isinstance(vv, (int, float))}
            for k, v in (m.get("provider_contribution") or {}).items()}


def _latest_run_stats(sig: Signals) -> Tuple[Optional[str], Dict[str, Dict[str, int]]]:
    import json
    runs = sig.bundle.get("runs") or []
    if not runs:
        return None, {}
    r = runs[-1]
    try:
        return r.get("run_id") or r.get("job_id"), json.loads(r.get("provider_stats_json") or "{}") or {}
    except ValueError:
        return r.get("run_id"), {}


def rule_provider_concentration(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.provider_concentration"
    if _topic(sig, out, rid) is None:
        return
    stats = _provider_stats(sig)
    accepted = {k: v.get("accepted", 0) for k, v in stats.items()}
    total = sum(accepted.values())
    if total < 10:
        out.skip(rid, f"needs ≥10 accepted candidates (have {total})")
        return
    top, n = max(accepted.items(), key=lambda kv: kv[1])
    if n / total < 0.6:
        return
    out.findings.append(Finding(
        rid, "topic", top, f"Eligible topics come mostly from {top}",
        f"{n} of {total} accepted candidates ({_pct(n, total)}%) came from {top}.",
        "Provider contribution is heavily concentrated.",
        [_ev("Accepted per provider", accepted, "topic_discovery_runs")],
        f"Other providers return few usable candidates, so discovery depends on {top}; an outage or narrow coverage "
        "there would starve production.", "STRONG",
        "Inspect production query rotation and discovery diversity across providers.",
        "Less dependence on one source; fewer exhaustion failures.", severity="medium", occurrences=n, sample_size=total,
        min_sample=10, consistency=n / total, completeness=sig.completeness("topic_intelligence"), impact=0.6))


def rule_unproductive_providers(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.unproductive_provider"
    if _topic(sig, out, rid) is None:
        return
    run_id, latest = _latest_run_stats(sig)
    for prov, v in _provider_stats(sig).items():
        created, acc, q = v.get("candidates_created", 0), v.get("accepted", 0), v.get("queries", 0)
        if created >= 10 and acc == 0:
            lv = latest.get(prov) or {}
            evid = [_ev("Candidates created in window", created, "topic_discovery_runs"), _ev("Accepted in window", 0, "topic_discovery_runs"),
                    _ev("Queries", q, "topic_discovery_runs")]
            latest_txt = ""
            if int(lv.get("candidates_created") or 0) >= 5 and int(lv.get("accepted") or 0) == 0:
                latest_txt = f" All {lv['candidates_created']} {prov} candidates were rejected in the latest discovery run ({run_id})."
                evid.append(_ev("Latest run", {"run": run_id, "created": lv.get("candidates_created"), "accepted": 0}, "topic_discovery_runs"))
            out.findings.append(Finding(
                rid, "topic", prov, f"{prov} contributes no eligible candidates",
                f"{prov} produced {created} candidates in {sig.window_days} days and none was accepted.{latest_txt}",
                "A provider is queried but never contributes an eligible candidate.", evid,
                f"{prov}'s queries or result type do not match the eligibility criteria.", "STRONG",
                f"Inspect the {prov} queries and the rejection reasons of its candidates; consider whether the provider "
                "is worth its requests (no provider is disabled automatically).",
                "Fewer wasted requests; better discovery diversity.", severity="low", occurrences=created, sample_size=created,
                min_sample=10, consistency=1.0, completeness=sig.completeness("topic_intelligence"), impact=0.3,
                supersedes=[f"topic.unproductive_queries:{prov}"]))
        elif q >= 3 and v.get("articles", 0) > 0 and created == 0:
            out.findings.append(Finding(
                "topic.unproductive_queries", "topic", prov, f"{prov} queries return articles but no candidates",
                f"{prov} ran {q} queries returning {v['articles']} articles, but none became a candidate.",
                "Unproductive search queries.",
                [_ev("Queries", q, "topic_discovery_runs"), _ev("Articles", v["articles"], "topic_discovery_runs"),
                 _ev("Candidates created", 0, "topic_discovery_runs")],
                "Every result is filtered before candidate creation (format, recency or relevance); the filter step "
                "for this provider is the place to look.", "WEAK",
                f"Check how {prov} results are filtered before candidate creation.", "Turns spent requests into candidates.",
                severity="low", occurrences=q, sample_size=q, min_sample=3, completeness=sig.completeness("topic_intelligence"),
                impact=0.2))


def rule_latest_run_rejections(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.latest_run_rejections"
    if _topic(sig, out, rid) is None:
        return
    run_id, latest = _latest_run_stats(sig)
    if not latest:
        out.skip(rid, "no discovery run in window")
        return
    accepted = {k: _provider_stats(sig).get(k, {}).get("accepted", 0) for k in _provider_stats(sig)}
    total_acc = sum(accepted.values())
    top = max(accepted.items(), key=lambda kv: kv[1]) if accepted else (None, 0)
    for prov, v in latest.items():
        created, acc = int((v or {}).get("candidates_created") or 0), int((v or {}).get("accepted") or 0)
        if created < 10 or acc:
            continue
        conc = ""
        if top[0] and top[0] != prov and total_acc and top[1] / total_acc >= 0.6:
            conc = f" Over the window, {top[1]} of {total_acc} accepted candidates came from {top[0]}."
        out.findings.append(Finding(
            rid, "topic", prov, f"All {created} {prov} candidates were rejected in the latest run",
            f"All {created} {prov} candidates were rejected in the latest discovery run ({run_id}).{conc}",
            "A provider's whole contribution to the latest discovery run was rejected.",
            [_ev("Latest run", run_id, "topic_discovery_runs"), _ev("Created / accepted", [created, 0], "topic_discovery_runs"),
             _ev("Accepted per provider in window", accepted, "topic_discovery_runs")],
            "This provider's current queries return candidates that do not meet the eligibility criteria"
            + (", and discovery depends on one provider." if conc else "."), "STRONG" if conc else "WEAK",
            "Inspect production query rotation and discovery diversity for this provider.",
            "More eligible candidates per run.", severity="low", occurrences=created, sample_size=created, min_sample=10,
            consistency=1.0, completeness=sig.completeness("topic_intelligence"), impact=0.4, ttl_hours=24))


def rule_classifier_disagreement(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.classifier_disagreement"
    m = _topic(sig, out, rid)
    if m is None:
        return
    comp = {k: int(v) for k, v in (m.get("classifier_v1_vs_v2") or {}).items()}
    total = sum(comp.values())
    pct = m.get("classifier_disagreement_pct")
    if total < 10 or pct is None:
        out.skip(rid, f"needs ≥10 candidates classified by both classifiers (have {total})")
        return
    if pct < 50:
        return
    out.findings.append(Finding(
        rid, "topic", "v1_vs_v2", "Shadow classifier disagrees with the production classifier",
        f"Classifier v2 (shadow) disagrees with v1 on {pct}% of {total} candidates.",
        "Category classification disagreement.",
        [_ev("Comparison", comp, "topic_candidate_ledger"), _ev("Examples", (m.get("classifier_disagreements") or [])[:5], "topic_candidate_ledger")],
        "The two classifiers use different category vocabularies or rules; the disagreement does not show which is right.",
        "WEAK", "Review a sample of disagreements before any decision to promote classifier v2 (it stays in shadow).",
        "An informed decision on the classifier.", severity="low", occurrences=total, sample_size=total, min_sample=10,
        consistency=pct / 100, completeness=sig.completeness("topic_intelligence"), impact=0.2))


def rule_duplicate_pressure(sig: Signals, out: RuleOutput) -> None:
    rid = "topic.duplicate_detection"
    m = _topic(sig, out, rid)
    if m is None:
        return
    reasons = {k: int(v) for k, v in (m.get("rejection_reasons") or {}).items()}
    recent = reasons.get("RECENTLY_USED", 0)
    stats = _provider_stats(sig)
    dups = sum(v.get("duplicates", 0) for v in stats.values())
    created = sum(v.get("candidates_created", 0) for v in stats.values())
    exhaustion = sum(1 for f in sig.failures if f.get("cause") == "topic_exhaustion")
    if created < 20:
        out.skip(rid, f"needs ≥20 candidates (have {created})")
        return
    if exhaustion == 0 or (recent + dups) / created < 0.15:
        return
    out.findings.append(Finding(
        rid, "topic", "window", "Already-used topics reach late pipeline stages",
        f"{recent} candidates were rejected as recently used and {dups} were duplicates, while {exhaustion} productions "
        "still failed at the integrity gate because every topic had been used.",
        "Potential weakness in early duplicate detection.",
        [_ev("RECENTLY_USED rejections", recent, "topic_candidate_events"), _ev("Duplicates", dups, "topic_discovery_runs"),
         _ev("Integrity-gate exhaustion failures", exhaustion, "failure_intelligence")],
        "Early duplicate filtering may not catch every previously produced topic, so the later integrity gate has to "
        "refuse them; this is a hypothesis, the gate itself is working.", "WEAK",
        "Compare the discovery-stage duplicate check with the integrity gate's identity check on the affected runs. "
        "Neither check is changed or bypassed automatically.",
        "Earlier rejection of used topics; fewer exhaustion failures.", severity="medium", occurrences=recent + dups,
        sample_size=created, min_sample=20, consistency=0.5, completeness=sig.completeness("topic_intelligence"), impact=0.6))


# ---------------------------------------------------------------------------------------------------------------
# D. API and provider intelligence
# ---------------------------------------------------------------------------------------------------------------
def rule_youtube_errors(sig: Signals, out: RuleOutput) -> None:
    rid = "api.youtube_errors"
    if not sig.available("api_intelligence"):
        out.skip(rid, "API intelligence unavailable")
        return
    for key, n in (sig.metrics("api_intelligence").get("youtube_errors") or {}).items():
        method, _, cat = key.partition(":")
        n = int(n)
        if "scope" in cat or "auth" in cat or "permission" in cat:
            out.findings.append(Finding(
                rid, "api_provider", key, f"{method} fails with {cat}",
                f"{n} YouTube {method} request(s) failed because of {cat}.",
                "OAuth scope / authorization error on a YouTube operation.",
                [_ev("Failed requests", n, "youtube_api_requests"), _ev("Method", method, "youtube_api_requests"), _ev("Error category", cat, "youtube_api_requests")],
                f"The connected OAuth token does not include the permission {method} needs.", "CONFIRMED",
                f"Review whether the workflow actually needs {method} and its permission. OAuth scopes are not changed automatically.",
                "Removes failing requests or restores the intended feature.", risk="HIGH",
                severity="medium", occurrences=n, sample_size=n, min_sample=1, completeness=sig.completeness("api_intelligence"), impact=0.4))
        elif n >= 2:
            out.findings.append(Finding(
                rid, "api_provider", key, f"Repeated YouTube {method} errors ({cat})",
                f"{n} YouTube {method} requests failed with {cat}.", "Repeated YouTube API errors.",
                [_ev("Failed requests", n, "youtube_api_requests")],
                f"A recurring {cat} condition on {method}.", "WEAK", f"Inspect the {method} failures in the YouTube request log.",
                "Fewer failed API calls.", severity="medium" if n >= 5 else "low", occurrences=n, sample_size=n, min_sample=2,
                completeness=sig.completeness("api_intelligence"), impact=0.4))


def rule_quota(sig: Signals, out: RuleOutput) -> None:
    rid = "api.quota_pressure"
    if not sig.available("api_intelligence"):
        out.skip(rid, "API intelligence unavailable")
        return
    m = sig.metrics("api_intelligence")
    for bucket, q in (m.get("youtube_quota_today_pacific") or {}).items():
        pct = q.get("used_pct_estimate")
        if q.get("google_block_active") or (pct is not None and pct >= 80):
            out.findings.append(Finding(
                rid, "api_provider", bucket, f"YouTube quota pressure: {q.get('label') or bucket}",
                (f"Google has blocked the {bucket} bucket for today." if q.get("google_block_active") else
                 f"Estimated usage of the {bucket} bucket is {pct}% of the documented default limit today (Pacific day)."),
                "YouTube API quota anomaly.",
                [_ev("Estimated units used", q.get("used_estimate"), "youtube_api_requests (estimate)"),
                 _ev("Documented default limit", q.get("daily_limit_default"), "youtube_quota.BUCKETS"),
                 _ev("Google block active", bool(q.get("google_block_active")), "youtube_quota_blocks")],
                ("Google reported the quota as exhausted." if q.get("google_block_active") else
                 "Usage is approaching the default limit; the actual Google limit is not visible (estimate)."),
                "CONFIRMED" if q.get("google_block_active") else "WEAK",
                "Check which operations used the quota today; a quota-extension request needs your explicit decision.",
                "Avoids blocked uploads.", risk="MEDIUM", severity="high", occurrences=1, sample_size=1, min_sample=1,
                completeness=sig.completeness("api_intelligence"), impact=0.9, ttl_hours=24))
    daily = m.get("youtube_daily_units") or {}
    totals = {d: sum(int(x) for x in v.values()) for d, v in daily.items()}
    days = sorted(totals)
    if len(days) >= 4:
        today, prev = totals[days[-1]], [totals[d] for d in days[:-1]]
        med = statistics.median(prev)
        if med > 0 and today > 2 * med and today >= 200:
            out.findings.append(Finding(
                "api.request_volume", "api_provider", "youtube_units", "Unusual YouTube API request volume",
                f"Today's estimated YouTube units ({today}) are more than twice the median of previous days ({round(med)}).",
                "Unusual request volume.", [_ev("Units by day (estimate)", totals, "youtube_api_requests")],
                "A retry loop or a new polling path may be issuing extra requests.", "WEAK",
                "Inspect today's requests by method in the YouTube request log.", "Protects the daily quota.",
                severity="medium", occurrences=today, sample_size=len(days), min_sample=4, completeness=sig.completeness("api_intelligence"),
                impact=0.5, ttl_hours=24))


def rule_provider_errors(sig: Signals, out: RuleOutput) -> None:
    rid = "api.provider_errors"
    if not sig.available("api_intelligence"):
        out.skip(rid, "API intelligence unavailable")
        return
    for prov, v in (sig.metrics("api_intelligence").get("research_provider_requests") or {}).items():
        q, e = int(v.get("queries") or 0), int(v.get("errors") or 0)
        if e >= 3 and q and e / q >= 0.3:
            out.findings.append(Finding(
                rid, "api_provider", prov, f"{prov} requests fail often",
                f"{e} of {q} {prov} requests failed in the window.", "Provider request failures.",
                [_ev("Errors", e, "topic_discovery_runs"), _ev("Queries", q, "topic_discovery_runs")],
                "The provider is unreliable or rate-limiting these requests.", "WEAK",
                f"Check the {prov} error details and rate limits.", "More reliable discovery.",
                severity="medium", occurrences=e, sample_size=q, min_sample=3, consistency=e / q, completeness=sig.completeness("api_intelligence"), impact=0.4))
    gw = [f for f in sig.failures if re.search(r"\b504\b|gateway time-?out", f.get("message", ""), re.I)]
    if len(gw) >= 2:
        out.findings.append(Finding(
            "api.http_504", "api_provider", "504", "Repeated HTTP 504 (gateway timeout) errors",
            f"{len(gw)} failures in the window contain an HTTP 504 response.", "Repeated HTTP 504 errors.",
            [_ev("Occurrences", len(gw), "persistent_jobs / lifecycle events"), _ev("Example", gw[-1]["template"][:200], "failure_intelligence")],
            "An upstream service timed out; 504s are usually transient but repeated ones point at one endpoint.", "WEAK",
            "Identify the endpoint returning 504 and whether requests to it can be made lighter or less frequent.",
            "Fewer failed attempts.", severity="medium", occurrences=len(gw), sample_size=len(gw), min_sample=2,
            completeness=sig.completeness("failure_intelligence"), impact=0.5,
            affected_jobs=[f["job_id"] for f in gw if f.get("job_id")], last_seen=_latest(gw)))


# ---------------------------------------------------------------------------------------------------------------
# E. Recovery intelligence
# ---------------------------------------------------------------------------------------------------------------
def rule_window_consumed_by_cycle(sig: Signals, out: RuleOutput) -> None:
    rid = "recovery.window_consumed"
    by_cycle: Dict[str, List[str]] = defaultdict(list)
    for j in sig.bundle.get("jobs", []):
        m = JOB_RE.match(j.get("id", ""))
        if m and j.get("status") == "RECOVERY_WINDOW_EXPIRED":
            by_cycle[m.group(1)].append(j["id"])
    repeat = {c: j for c, j in by_cycle.items() if len(j) >= 2}
    if not repeat:
        return
    ranked = sorted(repeat.items(), key=lambda kv: -len(kv[1]))
    worst_cycle, worst = ranked[0]
    total = sum(len(j) for j in repeat.values())
    names = ", ".join(f"{c} ({len(j)}×)" for c, j in ranked[:8])
    # One recommendation for the whole pattern (not one per cycle), so the dashboard is not flooded.
    out.findings.append(Finding(
        rid, "recovery", "by_cycle", f"{len(repeat)} cycle(s) repeatedly consume their whole recovery window",
        f"{len(repeat)} cycles expired at least twice in {sig.window_days} days: {names}.",
        "Cycles repeatedly use their entire recovery window.",
        [_ev("Expirations per cycle", {c: len(j) for c, j in ranked}, "persistent_jobs"),
         _ev("Recovery windows (min)", {c: sig.recovery_minutes_by_cycle.get(c) for c, _ in ranked[:8]}, "active schedule")],
        (f"{worst_cycle} stands out ({len(worst)} expirations): something specific to its time slot (load, quota timing "
         "or a slow dependency) may delay it." if len(worst) >= 2 * max(2, total / len(repeat)) or len(repeat) == 1 else
         "Expirations are spread across many time slots, which points to a shared cause rather than one slot."), "WEAK",
        f"Compare the expired runs of {worst_cycle} and the other listed cycles with their completed runs "
        "(stage reached, time of day, concurrent work).",
        "Fewer lost productions.", severity="high" if len(worst) >= 4 or total >= 10 else "medium", occurrences=total,
        sample_size=total, min_sample=2, consistency=len(worst) / total, completeness=sig.completeness("production_health"),
        impact=0.7, affected_jobs=[j for _, js in ranked for j in js]))


def rule_ineffective_retries(sig: Signals, out: RuleOutput) -> None:
    rid = "recovery.ineffective_retries"
    m = sig.metrics("recovery_analytics")
    if not sig.available("recovery_analytics"):
        out.skip(rid, "recovery analytics unavailable")
        return
    need, failed_after = int(m.get("jobs_needing_recovery") or 0), int(m.get("failed_after_recovery") or 0)
    if need < 5:
        out.skip(rid, f"needs ≥5 cycles that needed recovery (have {need})")
        return
    if failed_after / need >= 0.4:
        repeat = [j["id"] for j in sig.bundle.get("jobs", []) if int(j.get("retry_count") or 0) >= 2
                  and j.get("status") in ("FAILED", "RECOVERY_WINDOW_EXPIRED")]
        out.findings.append(Finding(
            rid, "recovery", "window", "Retries often do not recover the cycle",
            f"{failed_after} of {need} cycles that needed recovery still failed or expired"
            + (f"; {len(repeat)} of them failed again after two or more retries." if repeat else "."),
            "Ineffective retries / repeated failure after retry.",
            [_ev("Needed recovery", need, "recovery_analytics"), _ev("Failed after recovery", failed_after, "recovery_analytics"),
             _ev("Retry count distribution", m.get("retry_count_distribution"), "persistent_jobs")],
            "Many of these failures are deterministic (the same error repeats), so retrying cannot fix them.", "WEAK",
            "For cycles that failed after retries, check whether the error was deterministic; fix the cause instead of "
            "relying on retries. The retry policy is not changed automatically.",
            "Less recovery time spent on hopeless retries.", severity="medium", occurrences=failed_after, sample_size=need,
            min_sample=5, consistency=failed_after / need, completeness=sig.completeness("recovery_analytics"), impact=0.6,
            affected_jobs=repeat))


def rule_long_recovery(sig: Signals, out: RuleOutput) -> None:
    rid = "recovery.long_duration"
    m = sig.metrics("recovery_analytics")
    med = m.get("median_time_to_recover_minutes")
    if med is None:
        out.skip(rid, "no recovered cycle with a recorded failure in window")
        return
    if med <= 240:
        return
    within = int(m.get("recovered_within_60_min") or 0)
    rec = int(m.get("recovered") or 0)
    rearms = int(m.get("rearm_events") or 0)
    out.findings.append(Finding(
        rid, "recovery", "window", "Recoveries take a very long time",
        f"Median time from first failure to completion is {round(med)} minutes; {within} of {rec} recoveries finished within 60 minutes.",
        "Abnormally long recovery duration.",
        [_ev("Median minutes to recover", round(med), "recovery_analytics"), _ev("Recovered within 60 min", within, "recovery_analytics"),
         _ev("Re-arm events", rearms, "production_lifecycle_events")],
        ("Re-armed quota handoffs completed days later dominate this figure." if rearms else
         "Recovered cycles wait a long time before completing."), "STRONG" if rearms else "WEAK",
        "Check whether long recoveries are publication handoffs (expected) or production retries (worth investigating).",
        "A clearer picture of recovery effectiveness.", severity="low", occurrences=rec, sample_size=rec, min_sample=3,
        completeness=sig.completeness("recovery_analytics"), impact=0.3))


# ---------------------------------------------------------------------------------------------------------------
# F. Content quality
# ---------------------------------------------------------------------------------------------------------------
def rule_qc_failures(sig: Signals, out: RuleOutput) -> None:
    rid = "quality.qc_failures"
    fi = sig.metrics("failure_intelligence")
    if not sig.available("failure_intelligence"):
        out.skip(rid, "failure intelligence unavailable")
        return
    for p in fi.get("patterns") or []:
        if p.get("cause") != "qc" or int(p.get("count") or 0) < 3:
            continue
        t = p.get("template", "")
        duration = "duration" in t.lower() and "shorts" in t.lower()
        out.findings.append(Finding(
            rid, "content_quality", t[:60], ("Videos fail QC for duration" if duration else "Repeated QC failure") + f" ({p['count']}×)",
            f"Quality control rejected {p['count']} videos with: \"{t[:160]}\".", "Repeated quality-control failures.",
            [_ev("Occurrences", p["count"], "failure_intelligence"), _ev("Class(es)", p.get("classes"), "retry_policy")],
            ("Scripts or narration pacing produce videos outside the Shorts duration limit; QC is catching it correctly."
             if duration else "The same QC check fails repeatedly for the same reason."), "STRONG",
            ("Inspect script length and narration pacing against the Shorts duration limit. QC stays as it is."
             if duration else "Inspect the failing QC check's inputs on the affected jobs. QC stays as it is."),
            "Fewer rejected renders and wasted production time.", severity="high" if p["count"] >= 5 else "medium",
            occurrences=int(p["count"]), sample_size=int(p["count"]), min_sample=3,
            completeness=sig.completeness("failure_intelligence"), impact=0.8,
            affected_jobs=list(p.get("jobs") or []), last_seen=p.get("last_seen")))


def rule_script_source(sig: Signals, out: RuleOutput) -> None:
    rid = "quality.script_fallback"
    scripts = {k: int(v) for k, v in (sig.metrics("api_intelligence").get("scripts_by_source") or {}).items()}
    total = sum(scripts.values())
    if total < 5:
        out.skip(rid, f"needs ≥5 produced scripts with a recorded source (have {total})")
        return
    fallback = sum(v for k, v in scripts.items() if "fallback" in k or "extractive" in k)
    if fallback / total < 0.5:
        return
    out.findings.append(Finding(
        rid, "content_quality", "window", "Most scripts come from the fallback writer",
        f"{fallback} of {total} scripts in the window were written by the extractive fallback rather than the primary writer.",
        "Production consistency: the primary script writer is rarely used.",
        [_ev("Scripts by source", scripts, "creative_identity_registry")],
        "The primary (Gemini) writer is unavailable, failing or not configured for most productions.", "WEAK",
        "Check why the primary script writer was not used (configuration, errors or quota).",
        "More consistent script quality.", severity="medium", occurrences=fallback, sample_size=total, min_sample=5,
        consistency=fallback / total, completeness=sig.completeness("api_intelligence"), impact=0.5))


def rule_category_repetition(sig: Signals, out: RuleOutput) -> None:
    rid = "quality.repeated_creative_pattern"
    cats = Counter(r.get("category") or "unknown" for r in sig.bundle.get("registry", [])
                   if r.get("status") in ("PRODUCED", "HISTORICAL_PRODUCED", "SELECTED"))
    total = sum(cats.values())
    if total < 8:
        out.skip(rid, f"needs ≥8 produced or selected topics (have {total})")
        return
    top, n = cats.most_common(1)[0]
    if n / total < 0.5:
        return
    out.findings.append(Finding(
        rid, "content_quality", top, f"Content concentrates on one category: {top}",
        f"{n} of {total} produced or selected topics are in {top}.", "Repeated creative pattern.",
        [_ev("Categories", dict(cats.most_common(8)), "creative_identity_registry")],
        "Discovery sources favour this category.", "WEAK",
        "Decide whether this concentration is intended; if not, review discovery sources.", "More varied content.",
        severity="low", occurrences=n, sample_size=total, min_sample=8, consistency=n / total, impact=0.2))


def rule_engagement_data(sig: Signals, out: RuleOutput) -> None:
    # YouTube Analytics engagement / retention and hook-quality metrics are not part of the Stage 3.32A data.
    out.skip("quality.engagement_and_hooks",
             "YouTube Analytics engagement, retention and hook-quality data are not available; no recommendation is made")


RULES: Tuple[Callable[[Signals, RuleOutput], None], ...] = (
    rule_failure_rate_rising, rule_recovery_expirations, rule_stage_failures, rule_abnormal_duration, rule_near_deadline,
    rule_renderer_patterns, rule_render_duration, rule_media_acquisition,
    rule_topic_exhaustion, rule_low_acceptance, rule_provider_concentration, rule_unproductive_providers,
    rule_latest_run_rejections, rule_classifier_disagreement, rule_duplicate_pressure,
    rule_youtube_errors, rule_quota, rule_provider_errors,
    rule_window_consumed_by_cycle, rule_ineffective_retries, rule_long_recovery,
    rule_qc_failures, rule_script_source, rule_category_repetition, rule_engagement_data,
)


def evaluate(sig: Signals) -> RuleOutput:
    out = RuleOutput()
    for rule in RULES:
        try:
            rule(sig, out)
        except Exception as exc:                       # one broken rule never stops the others
            out.skip(rule.__name__, f"rule error: {type(exc).__name__}")
    seen = {}
    for f in out.findings:                             # one finding per key (first wins)
        seen.setdefault(f.key, f)
    out.findings = list(seen.values())
    return out
