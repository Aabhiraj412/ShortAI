"""Stage 3.31 shadow-mode observation report (read-only).

Opens studio.db read-only (``mode=ro``) and summarises the candidate ledger, category classifier v2
shadow predictions and production outcomes for one observation window. It never writes to the
database, never changes configuration and needs no restart.

Usage::

    python backend/scripts/stage331_shadow_observation.py \
        --start 2026-11-24T00:00 --end 2026-11-26T00:00 [--db backend/studio.db] [--json out.json]

Times are Asia/Kolkata. A cycle is eligible when its scheduled slot (``cycle_N_YYYYMMDD_HHMM``) lies in
[start, end). The ledger keeps the *latest* state per candidate, so category v1/v2 comparisons use
the candidates last seen inside the window; per-cycle facts come from ``topic_candidate_events``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
EXPECTED = {"candidate_ledger": "on", "category_classifier_v2": "shadow"}
MUST_BE_OFF = ("production_query_rotation", "gnews_candidates", "wikipedia_in_the_news",
               "exhaustion_recovery", "expanded_categories")
CYCLE_RE = re.compile(r"^cycle_(\d+)_(\d{8})_(\d{4})$")


def _ist(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=IST) if d.tzinfo is None else d.astimezone(IST)


def _slot(cycle_id: str):
    m = CYCLE_RE.match(cycle_id or "")
    if not m:
        return None
    return datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M").replace(tzinfo=IST)


def analyse(db: str, start: datetime, end: datetime, snapshots: str = "") -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    s_utc, e_utc = start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()

    # Eligible slots: every hour in the window (the active profile is the 24-cycle hourly schedule).
    slots, t = [], start.replace(minute=0, second=0, microsecond=0)
    if t < start:
        t += timedelta(hours=1)
    while t < end:
        slots.append(t)
        t += timedelta(hours=1)

    runs = [dict(r) for r in q("select * from topic_discovery_runs")]
    runs = [r for r in runs if _slot(r["run_id"]) and start <= _slot(r["run_id"]) < end]
    run_ids = {r["run_id"] for r in runs}
    jobs = {r["id"]: dict(r) for r in q("select id,status,current_stage,topic_title,error_details,recovery_reason "
                                        "from persistent_jobs where id like 'cycle_%'")
            if _slot(r["id"]) and start <= _slot(r["id"]) < end}
    observed_slots = {_slot(r) for r in run_ids}
    missing = [s.isoformat() for s in slots if s not in observed_slots]
    missing_detail = []
    for s in slots:
        if s in observed_slots:
            continue
        jid = next((j for j in jobs if _slot(j) == s), None)
        missing_detail.append({"slot_ist": s.isoformat(), "job": jid,
                               "job_status": jobs[jid]["status"] if jid else "NO_JOB (scheduler not running?)",
                               "reason": (jobs[jid]["error_details"] or "")[:160] if jid else ""})

    # Configuration per run.
    flag_revisions, config_violations = Counter(), []
    for r in runs:
        f = json.loads(r["flags_json"] or "{}")
        active = {k: v for k, v in f.items() if k != "exhaustion_limits" and v != "off"}
        flag_revisions[json.dumps(active, sort_keys=True)] += 1
        bad = [k for k, v in EXPECTED.items() if f.get(k) != v] + [k for k in MUST_BE_OFF if f.get(k, "off") != "off"]
        if bad:
            config_violations.append({"run": r["run_id"], "flags": bad})

    # Per-cycle candidates and decisions (events are per run).
    per_cycle, reasons, reason_stage = [], Counter(), Counter()
    dup_reasons = Counter()
    for r in sorted(runs, key=lambda x: _slot(x["run_id"])):
        ev = q("select stage,status,reason from topic_candidate_events where run_id=?", r["run_id"])
        st = Counter(e["status"] for e in ev)
        for e in ev:
            if e["status"] == "REJECTED" and e["reason"]:
                reasons[e["reason"]] += 1
                reason_stage[f'{e["stage"]}:{e["reason"]}'] += 1
                if e["stage"] in ("identity_gate", "duplicate_gate"):
                    dup_reasons[e["reason"]] += 1
        notes = json.loads(r["notes_json"] or "{}")
        per_cycle.append({
            "run": r["run_id"], "candidates": r["candidate_count"], "eligible": r["eligible_count"],
            "rejected_events": st.get("REJECTED", 0), "selected": bool(r["selected_candidate_id"]),
            "outcome": r["outcome"], "outcome_reason": (r["outcome_reason"] or "")[:120],
            "events": len(ev), "has_v2_scoring_note": "scoring_v2" in notes,
            "has_demand_shadow_note": "demand_shadow" in notes,
            "wikimedia": (notes.get("wikimedia_requests") or {}).get("stats"),
        })

    # Providers.
    prov = defaultdict(Counter)
    for r in runs:
        for fam, m in json.loads(r["provider_stats_json"] or "{}").items():
            for k, v in m.items():
                if isinstance(v, (int, float)):
                    prov[fam][k] += v
            prov[fam]["runs_present"] += 1
    wm = Counter()
    for c in per_cycle:
        for k, v in (c["wikimedia"] or {}).items():
            wm[k] += v

    # Category v1 vs v2 (shadow) on candidates last seen in the window.
    cands = [dict(r) for r in q("select candidate_id,title,category,category_code,category_v2_json,provider_family,"
                                "selection_status,rejection_reason,last_run_id,first_discovered_at "
                                "from topic_candidate_ledger where last_discovered_at>=? and last_discovered_at<?",
                                s_utc, e_utc)]
    v2_status, pairs, disagree, low_conf, no_v2 = Counter(), Counter(), [], 0, 0
    for c in cands:
        try:
            v2 = json.loads(c["category_v2_json"] or "{}")
        except ValueError:
            v2 = {}
        if not v2:
            no_v2 += 1
            continue
        v2_status[v2.get("status", "?")] += 1
        v1c, v2c = (c["category_code"] or "").upper(), (v2.get("category_code") or "").upper()
        pairs[f"{v1c or '-'}->{v2c or '-'}"] += 1
        if v1c != v2c:
            disagree.append({"title": c["title"], "v1": c["category"], "v2": v2.get("display_name"),
                             "v2_status": v2.get("status"), "confidence": v2.get("confidence"),
                             "selection": c["selection_status"]})
        if isinstance(v2.get("confidence"), (int, float)) and v2["confidence"] < 0.5:
            low_conf += 1
    v2_dist = Counter()
    for c in cands:
        try:
            v2_dist[(json.loads(c["category_v2_json"] or "{}").get("display_name")) or "(none)"] += 1
        except ValueError:
            v2_dist["(unreadable)"] += 1
    sel_disagree = [d for d in disagree if d["selection"] in ("SELECTED", "PRODUCED")]

    # Growth inside the window.
    new_cands = q("select count(*) n from topic_candidate_ledger where first_discovered_at>=? and first_discovered_at<?", s_utc, e_utc)[0]["n"]
    new_events = q("select count(*) n from topic_candidate_events where created_at>=? and created_at<?", s_utc, e_utc)[0]["n"]
    integrity = q("pragma quick_check")[0][0]
    totals = {t: q(f"select count(*) n from {t}")[0]["n"] for t in
              ("topic_candidate_ledger", "topic_candidate_events", "topic_discovery_runs")}

    # Per-run v1/v2 from the hourly snapshots (not lossy), when they exist.
    snap = None
    if snapshots and os.path.isfile(os.path.join(snapshots, "runs.jsonl")):
        s_pairs, s_status, s_dis, s_sel, s_runs, s_preds = Counter(), Counter(), 0, [], set(), 0
        with open(os.path.join(snapshots, "runs.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec["run_id"] not in run_ids:
                    continue
                s_runs.add(rec["run_id"])
                for cnd in rec["candidates"]:
                    v2 = json.loads(cnd.get("category_v2_json") or "{}")
                    if not v2:
                        continue
                    s_preds += 1
                    s_status[v2.get("status", "?")] += 1
                    a1, a2 = (cnd["category_code"] or "-").upper(), (v2.get("category_code") or "-").upper()
                    s_pairs[f"{a1}->{a2}"] += 1
                    if a1 != a2:
                        s_dis += 1
                        if cnd["candidate_id"] == rec["selected_candidate_id"]:
                            s_sel.append({"run": rec["run_id"], "title": cnd["title"], "v1": cnd["category"],
                                          "v2": v2.get("display_name"), "v2_status": v2.get("status")})
        snap = {"runs_snapshotted": len(s_runs), "runs_missing_snapshot": sorted(run_ids - s_runs),
                "predictions": s_preds, "disagreements": s_dis, "v2_status": dict(s_status),
                "pairs": dict(s_pairs.most_common()), "selected_disagreements": s_sel}

    outcomes = Counter((r["outcome"] or "NONE") for r in runs)
    job_outcomes = Counter(j["status"] for j in jobs.values())
    return {
        "window_ist": [start.isoformat(), end.isoformat()],
        "generated_at_ist": datetime.now(IST).isoformat(),
        "complete": datetime.now(IST) >= end,
        "eligible_cycles": len(slots), "observed_cycles": len(run_ids), "missing_cycles": missing,
        "missing_detail": missing_detail,
        "config_revisions": dict(flag_revisions), "config_violations": config_violations,
        "candidate_events_total": sum(c["events"] for c in per_cycle),
        "candidates_per_cycle": [c["candidates"] for c in per_cycle],
        "per_cycle": per_cycle,
        "rejection_reasons": dict(reasons.most_common()), "rejection_stage_reason": dict(reason_stage.most_common()),
        "duplicate_gate_reasons": dict(dup_reasons),
        "unique_candidates_seen": len(cands), "new_candidates_first_seen": new_cands, "new_events": new_events,
        "v2_status": dict(v2_status), "v2_missing": no_v2, "v2_low_confidence_lt_0_5": low_conf,
        "v1_v2_disagreements": len(disagree), "v1_v2_pairs": dict(pairs.most_common()),
        "v2_distribution": dict(v2_dist.most_common()),
        "disagreements_on_selected_or_produced": sel_disagree, "disagreement_samples": disagree[:25],
        "providers": {k: dict(v) for k, v in prov.items()}, "wikimedia_requests": dict(wm),
        "snapshot_category_analysis": snap,
        "run_outcomes": dict(outcomes), "job_statuses": dict(job_outcomes),
        "incomplete_runs": [c["run"] for c in per_cycle if not c["outcome"]],
        "runs_without_v2_scoring_note": [c["run"] for c in per_cycle if not c["has_v2_scoring_note"]],
        "db": {"path": db, "bytes": os.path.getsize(db), "quick_check": integrity, "totals": totals},
    }


def snapshot(db: str, out_dir: str) -> int:
    """Append, once per discovery run, the category v1/v2 state of every candidate that run touched.

    The ledger row is overwritten when a candidate is seen again (a later sighting before enrichment
    blanks ``category_code`` and ``category_v2_json``), so per-run predictions are only recoverable
    while that run is the candidate's latest. Run this hourly between cycles (e.g. at :40) to keep
    them. Read-only on the database; writes only ``<out_dir>/runs.jsonl``."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "runs.jsonl")
    done = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["run_id"])
                except (ValueError, KeyError):
                    pass
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    added = 0
    with open(path, "a", encoding="utf-8") as fh:
        for r in conn.execute("select run_id, selected_candidate_id, outcome from topic_discovery_runs "
                              "where finished_at is not null order by started_at"):
            if r["run_id"] in done:
                continue
            rows = conn.execute("select candidate_id,title,category,category_code,category_v2_json,provider_family,"
                                "selection_status,rejection_reason,rejection_stage from topic_candidate_ledger "
                                "where last_run_id=?", (r["run_id"],)).fetchall()
            fh.write(json.dumps({"run_id": r["run_id"], "captured_at_ist": datetime.now(IST).isoformat(),
                                 "selected_candidate_id": r["selected_candidate_id"], "outcome": r["outcome"],
                                 "candidates": [dict(x) for x in rows]}, ensure_ascii=False) + "\n")
            added += 1
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", help="IST, e.g. 2026-11-24T00:00")
    ap.add_argument("--end", help="IST, exclusive")
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "studio.db"))
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--snapshots", metavar="DIR", default="", help="use DIR/runs.jsonl for per-run v1/v2")
    ap.add_argument("--snapshot", metavar="DIR", help="append per-run category snapshots to DIR/runs.jsonl and exit")
    a = ap.parse_args(argv)
    if a.snapshot:
        print(f"snapshotted {snapshot(os.path.abspath(a.db), a.snapshot)} new run(s)")
        return 0
    if not (a.start and a.end):
        ap.error("--start and --end are required unless --snapshot is given")
    res = analyse(os.path.abspath(a.db), _ist(a.start), _ist(a.end), a.snapshots)
    out = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(out)
    brief = {k: res[k] for k in ("window_ist", "complete", "eligible_cycles", "observed_cycles", "missing_cycles",
                                 "config_revisions", "config_violations", "unique_candidates_seen",
                                 "v1_v2_disagreements", "run_outcomes", "incomplete_runs", "db")}
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(brief, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
