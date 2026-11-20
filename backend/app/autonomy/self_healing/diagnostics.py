"""Diagnostic engine: gathers the recorded evidence for one incident and correlates it with history.

Evidence collected (each item is copied from a production record, never inferred): error message, exception type,
pipeline stage, job id, attempt number, timestamps, log context (lifecycle events + the progress record's error
summary), related earlier failures with the same signature, renderer information, recorded media/asset metadata and
the job's recovery history. Anything that was not recorded is listed under ``missing``.

Correlation answers, from counts only: the common signature, affected stages, occurrences, distinct jobs, whether the
same input dimensions recur, whether earlier retries succeeded, and whether the failure is likely deterministic.

Root-cause statements are explicit about their status:
  CONFIRMED_MECHANISM - the error text states *what* failed (e.g. a pad filter smaller than its input); *why* the
                        inputs were like that is still a hypothesis;
  SUSPECTED           - supported by recurrence or a strong signature, not proven;
  UNKNOWN             - insufficient evidence.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from backend.app.autonomy.self_healing import classification as C

DIM_RX = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
FILTER_DIM_RX = re.compile(r"\b(pad|scale|crop|s)=(\d{1,5}):(\d{1,5})")
MAX_LOG_LINES = 25
MAX_RELATED = 15


def _dims(texts: Sequence[str]) -> List[str]:
    found = []
    for t in texts:
        for m in FILTER_DIM_RX.finditer(t or ""):
            found.append(f"{m.group(1)}={m.group(2)}:{m.group(3)}")
        for m in DIM_RX.finditer(t or ""):
            found.append(f"{m.group(1)}x{m.group(2)}")
    return found


def _log_context(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for e in (job.get("events") or [])[-15:]:
        lines.append({"at": e.get("at"), "source": "lifecycle_event", "text": f"{e.get('type')} {e.get('error') or ''}".strip()[:300]})
    err = ((job.get("progress") or {}).get("error") or {})
    if err.get("summary"):
        for ln in str(err["summary"]).splitlines()[:10]:
            if ln.strip():
                lines.append({"at": None, "source": f"progress_error[{err.get('stage')}]", "text": ln.strip()[:300]})
    return lines[-MAX_LOG_LINES:]


class DiagnosticEngine:
    def diagnose(self, incident: Dict[str, Any], classification: C.Classification, job: Dict[str, Any],
                 history: Sequence[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
        sig = classification.signature
        same = [h for h in history if h.get("signature") == sig]
        related = [h for h in same if h.get("job_id") != incident.get("job_id")]
        stages = Counter(h.get("stage") or "NOT_RECORDED" for h in same) or Counter({incident.get("stage") or "NOT_RECORDED": 1})
        jobs = sorted({h.get("job_id") for h in same if h.get("job_id")})
        attempts = job.get("attempt_history") or []
        same_attempts = [a for a in attempts if C.normalize(a.get("message") or "") == classification.template]
        full_texts = [job.get("error_details") or "", incident.get("message") or ""]
        full_texts += [h.get("reason") or "" for h in job.get("failure_history") or []]
        full_texts += [((job.get("progress") or {}).get("error") or {}).get("summary") or ""]
        dims_by_job: Dict[str, List[str]] = {}
        own = _dims(full_texts)
        if own and incident.get("job_id"):
            dims_by_job[incident["job_id"]] = sorted(set(own))
        for h in related:
            d = _dims([h.get("message") or ""])
            if d and h.get("job_id"):
                dims_by_job.setdefault(h["job_id"], sorted(set(d)))
        dim_counts = Counter(d for ds in dims_by_job.values() for d in ds)
        recurring_dims = [d for d, n in dim_counts.items() if n >= 2]
        recovered_jobs = sorted({h.get("job_id") for h in same if h.get("recovered_later")})
        retries_attempted = max(int(job.get("retry_count") or 0), len(attempts))
        missing = list(job.get("missing") or [])
        if not dims_by_job:
            missing.append("input / filter dimensions (not present in any recorded error text)")
        if not attempts:
            missing.append("attempt history")

        outcome_note = classification.subtype in ("deadline_outcome", "no_error_text")
        likely_det = (not outcome_note) and (classification.failure_class == C.DETERMINISTIC or (
            len(same) >= C.RECURRENCE_MIN_OCCURRENCES and len(jobs) >= C.RECURRENCE_MIN_JOBS and not recovered_jobs))
        correlation = {
            "signature": sig, "template": classification.template,
            "occurrences": len(same) or 1, "distinct_jobs": len(jobs) or (1 if incident.get("job_id") else 0),
            "job_ids": jobs[:20], "affected_stages": dict(stages.most_common(6)),
            "same_dimensions_recur": (bool(recurring_dims) if dims_by_job else None),
            "recurring_dimensions": recurring_dims[:10], "dimensions_by_job": dict(list(dims_by_job.items())[:10]),
            "retries_attempted_this_job": retries_attempted,
            "same_error_on_retry_this_job": len(same_attempts),
            "jobs_where_a_later_attempt_succeeded": recovered_jobs[:20],
            "previous_retries_succeeded": (bool(recovered_jobs) if same else None),
            "likely_deterministic": bool(likely_det),
        }
        findings: List[Dict[str, Any]] = []
        n = correlation["occurrences"]
        findings.append({"text": f"The same error signature occurred {n} time{'s' if n != 1 else ''} across "
                                 f"{correlation['distinct_jobs']} job(s) in the analysis window.", "strength": "OBSERVED"})
        if correlation["affected_stages"]:
            top, cnt = stages.most_common(1)[0]
            findings.append({"text": f"Most occurrences were recorded at stage {top} ({cnt} of {sum(stages.values())}).",
                             "strength": "OBSERVED"})
        if same_attempts and len(same_attempts) >= 2:
            findings.append({"text": f"This job failed with the same error on {len(same_attempts)} attempts; retrying did not "
                                     "change the outcome.", "strength": "OBSERVED"})
        if correlation["previous_retries_succeeded"] is True:
            findings.append({"text": f"A later attempt succeeded for {len(recovered_jobs)} affected job(s).", "strength": "OBSERVED"})
        elif correlation["previous_retries_succeeded"] is False and n >= 2:
            findings.append({"text": "No affected job completed after this error.", "strength": "OBSERVED"})
        if correlation["same_dimensions_recur"]:
            findings.append({"text": f"The same dimensions recur across jobs: {', '.join(recurring_dims[:4])}.", "strength": "OBSERVED"})
        elif correlation["same_dimensions_recur"] is None and classification.subtype in ("ffmpeg_filter", "media_dimensions"):
            findings.append({"text": "The input and filter dimensions were not recorded in the error text, so recurring "
                                     "inputs cannot be checked from history (the validate_ffmpeg_filter diagnostic can "
                                     "inspect the logs).", "strength": "INSUFFICIENT_EVIDENCE"})
        for note in classification.notes:
            findings.append({"text": note, "strength": "CLASSIFICATION"})

        if classification.failure_class == C.UNKNOWN:
            root = {"status": "UNKNOWN", "statement": "Insufficient evidence to state a cause."}
        elif classification.certainty == "CONFIRMED" and classification.basis.startswith("signature"):
            root = {"status": "CONFIRMED_MECHANISM",
                    "statement": f"Confirmed from the error text: {classification.mechanism} Why the inputs led to this is "
                                 "not proven by the recorded data."}
        else:
            root = {"status": "SUSPECTED",
                    "statement": f"Suspected ({classification.certainty.lower()} evidence, basis {classification.basis}): "
                                 f"{classification.mechanism}"}
        evidence = {
            "error_message": (job.get("error_details") or incident.get("message") or "")[:2000],
            "exception_type": classification.error_type or None,
            "pipeline_stage": incident.get("stage") or job.get("stage"),
            "job_id": incident.get("job_id"),
            "job_status": job.get("status"),
            "attempt_number": retries_attempted or None,
            "first_seen_utc": incident.get("first_seen_utc"), "last_seen_utc": incident.get("last_seen_utc"),
            "log_context": _log_context(job),
            "related_failures": [{"job_id": h.get("job_id"), "at": h.get("at"), "stage": h.get("stage"),
                                  "recovered_later": h.get("recovered_later")} for h in related[-MAX_RELATED:]],
            "renderer": job.get("renderer"),
            "media_metadata": {"assets": (job.get("progress") or {}).get("assets"),
                               "rendering": (job.get("progress") or {}).get("rendering")} if job.get("progress") else None,
            "recovery_history": {"attempts": attempts[-10:], "recovery_reason": job.get("recovery_reason"),
                                 "cycle_state": job.get("cycle_state"),
                                 "state_transitions": (job.get("state_transitions") or [])[-10:]},
            "topic_title": job.get("topic_title"),
            "event_log_path": (job.get("progress") or {}).get("event_log_path"),
        }
        collected = ["error_message", "exception_type", "pipeline_stage", "job_id", "attempt_number", "log_context",
                     "related_failures", "renderer", "media_metadata", "recovery_history"]
        present = sum(1 for k in collected if evidence.get(k) not in (None, [], {}, ""))
        return {"classification": classification.to_dict(), "evidence": evidence, "correlation": correlation,
                "findings": findings, "root_cause": root, "missing": missing,
                "completeness": round(present / len(collected), 2), "diagnosed_at_utc": now.isoformat(),
                "action_findings": {}}
