"""Stage 3.31A candidate intelligence ledger.

Records every topic candidate the discovery pipeline sees and what happened to it, so a
selection or rejection can be traced from discovery to production outcome.

Design
------
* ``DiscoveryTrace`` collects decisions in memory during one discovery run. ``NullTrace`` is
  used while the ``candidate_ledger`` flag is off, so the current production path does no
  extra work and writes nothing.
* ``CandidateLedger`` persists a trace in one transaction to four additive SQLite tables in the
  studio database (``CREATE TABLE IF NOT EXISTS`` only; no existing table is touched):

  - ``topic_candidate_ledger``   one row per stable candidate id (latest state + counters)
  - ``topic_candidate_events``   append-only decision history, idempotent per event id
  - ``topic_discovery_runs``     one row per discovery run (provider metrics, rotation, recovery)
  - ``topic_discovery_rotation`` persisted query-rotation counters (3.31C)

* Ids are stable: ``tc_`` + hash(provider family, normalized title). A retried job re-uses the
  same run id (the job id), and event ids hash their content, so retries never duplicate rows.
* Ledger problems are logged and swallowed: bookkeeping can never fail a production.
* Under pytest the live database is refused (the same guard the identity registry uses).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---- vocabulary -----------------------------------------------------------------------------------
DISCOVERED, ELIGIBLE, REJECTED, SELECTED, PRODUCED, FAILED = (
    "DISCOVERED", "ELIGIBLE", "REJECTED", "SELECTED", "PRODUCED", "FAILED")
STATUSES = (DISCOVERED, ELIGIBLE, REJECTED, SELECTED, PRODUCED, FAILED)

# Allowed transitions of the *latest* status of a candidate row. PRODUCED is final: if the same
# subject is rediscovered later, the rejection is kept as an event and the row stays PRODUCED.
_ALLOWED: Dict[Optional[str], Tuple[str, ...]] = {
    None: (DISCOVERED, REJECTED, ELIGIBLE),
    DISCOVERED: (DISCOVERED, REJECTED, ELIGIBLE),
    REJECTED: (DISCOVERED, REJECTED, ELIGIBLE),
    ELIGIBLE: (DISCOVERED, REJECTED, ELIGIBLE, SELECTED),
    SELECTED: (PRODUCED, FAILED, SELECTED),
    FAILED: (DISCOVERED, REJECTED, ELIGIBLE),
    PRODUCED: (),
}

# Structured rejection reasons. A candidate still under evaluation has no reason.
DUPLICATE_EXACT = "DUPLICATE_EXACT"
DUPLICATE_SEMANTIC = "DUPLICATE_SEMANTIC"
INVALID_CATEGORY = "INVALID_CATEGORY"
LOW_RELEVANCE = "LOW_RELEVANCE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
SOURCE_UNRELIABLE = "SOURCE_UNRELIABLE"
ALREADY_PUBLISHED = "ALREADY_PUBLISHED"
RECENTLY_USED = "RECENTLY_USED"
LOW_STORY_POTENTIAL = "LOW_STORY_POTENTIAL"
TOPIC_EXHAUSTION_FILTER = "TOPIC_EXHAUSTION_FILTER"
PROVIDER_FAILURE = "PROVIDER_FAILURE"
POOL_CAPACITY_LIMIT = "POOL_CAPACITY_LIMIT"
REJECTION_REASONS = (
    DUPLICATE_EXACT, DUPLICATE_SEMANTIC, INVALID_CATEGORY, LOW_RELEVANCE, INSUFFICIENT_EVIDENCE,
    SOURCE_UNRELIABLE, ALREADY_PUBLISHED, RECENTLY_USED, LOW_STORY_POTENTIAL, TOPIC_EXHAUSTION_FILTER,
    PROVIDER_FAILURE, POOL_CAPACITY_LIMIT,
)
_DUP_REASONS = {DUPLICATE_EXACT, DUPLICATE_SEMANTIC, RECENTLY_USED, ALREADY_PUBLISHED}

MAX_CANDIDATES_PER_RUN = 300     # bounds one flush; discovery normally sees < 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_title(title: str) -> str:
    from backend.app.content.topic_ledger import normalize_topic_title
    return normalize_topic_title(title or "")


def provider_family(source: str) -> str:
    """'wikipedia_search:history' -> 'wikipedia_search'; 'wikipedia_tfa_1116' -> 'wikipedia_tfa';
    'physorg_space_rss' -> 'physorg_rss'; 'gnews_science' / 'gnews_lead_science' -> 'gnews'."""
    s = (source or "unknown").strip().lower()
    if s.startswith("gnews"):
        return "gnews"
    if s.startswith("physorg"):
        return "physorg_rss"
    s = s.split(":", 1)[0]
    s = re.sub(r"_\d{3,4}$", "", s)
    return s or "unknown"


def candidate_id(source: str, title: str) -> str:
    key = f"{provider_family(source)}|{normalize_title(title)}"
    return "tc_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def classify_duplicate_reason(reason: str) -> str:
    """Maps the free-text reason from ``TopicLedger.check_content_collision`` /
    ``ValidationTopicHistory`` to a structured rejection reason."""
    r = (reason or "").lower()
    if "cooldown" in r or "produced" in r and "days ago" in r:
        return RECENTLY_USED
    if "exact" in r or "fingerprint" in r:
        return DUPLICATE_EXACT
    return DUPLICATE_SEMANTIC


def _source_url(cand: Dict[str, Any]) -> str:
    for s in cand.get("sources") or []:
        m = re.search(r"https?://\S+", str(s))
        if m:
            return m.group(0).rstrip(").,")
    return ""


def default_verification_status(cand: Dict[str, Any]) -> str:
    """Never claims more than the evidence supports. Topic-level fact verification is a later
    sub-stage (3.31g); until then every candidate is labelled by the kind of evidence it carries."""
    v = cand.get("verification")
    if v:
        return str(v).upper()
    fam = provider_family(cand.get("discovery_source", ""))
    if fam.startswith("wikipedia") or fam == "nasa_library":
        return "SINGLE_SOURCE_ENCYCLOPEDIC"
    if fam == "physorg_rss":
        return "SINGLE_SOURCE_PRESS_SUMMARY"
    if fam == "curated":
        return "CURATED_UNVERIFIED"
    return "UNVERIFIED"


# ---- in-memory trace --------------------------------------------------------------------------------
class NullTrace:
    """No-op trace used while the ledger flag is off: the current path does no extra work."""
    enabled = False
    run_id = ""

    def __getattr__(self, name):
        def _noop(*_a, **_k):
            return None
        return _noop


class DiscoveryTrace:
    """Collects candidate decisions and provider metrics for one discovery run."""
    enabled = True

    def __init__(self, run_id: str, job_id: str = "", flags: Optional[Dict[str, Any]] = None):
        self.run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        self.job_id = job_id or self.run_id
        self.flags = flags or {}
        self.started_at = _now()
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.providers: Dict[str, Dict[str, int]] = {}
        self.rotation: Dict[str, Any] = {}
        self.recovery: Dict[str, Any] = {}
        self.notes: Dict[str, Any] = {}

    # provider metrics
    def _p(self, family: str) -> Dict[str, int]:
        return self.providers.setdefault(family, {
            "queries": 0, "articles": 0, "candidates_created": 0, "rejected": 0, "accepted": 0,
            "duplicates": 0, "errors": 0, "unique_accepted": 0})

    def query(self, source: str, articles: int = 0) -> None:
        p = self._p(provider_family(source))
        p["queries"] += 1
        p["articles"] += int(articles or 0)

    def provider_error(self, source: str, error: str = "") -> None:
        fam = provider_family(source)
        self._p(fam)["errors"] += 1
        self.events.append({"candidate_id": None, "stage": "provider", "status": "ERROR",
                            "reason": PROVIDER_FAILURE, "detail": {"provider": fam, "error": str(error)[:200]}})

    # candidates
    def candidate(self, cand: Dict[str, Any], stage: str = "discovery") -> str:
        """Registers a candidate (idempotent within the run) and returns its stable id."""
        src = cand.get("discovery_source") or "unknown"
        cid = candidate_id(src, cand.get("title", ""))
        row = self.candidates.get(cid)
        if row is None:
            if len(self.candidates) >= MAX_CANDIDATES_PER_RUN:
                return cid
            row = {
                "candidate_id": cid, "title": str(cand.get("title", ""))[:500],
                "normalized_title": normalize_title(cand.get("title", "")),
                "summary": str(cand.get("summary") or "")[:1200],
                "category": cand.get("category") or "", "category_code": cand.get("category_code") or "",
                "category_v2": cand.get("category_v2"),
                "provider": src, "provider_family": provider_family(src),
                "source_url": _source_url(cand), "publication_ts": str(cand.get("publication_timestamp") or ""),
                "discovered_at": cand.get("discovery_timestamp") or _now(),
                "entities": list(cand.get("key_entities") or [])[:12],
                "sources": [str(s)[:400] for s in (cand.get("sources") or [])[:8]],
                "verification_status": default_verification_status(cand),
                "score": None, "score_breakdown": None, "duplicate_result": None, "duplicate_match": None,
                "status": DISCOVERED, "rejection_reason": None, "rejection_stage": None,
            }
            self.candidates[cid] = row
            self._p(row["provider_family"])["candidates_created"] += 1
            self.events.append({"candidate_id": cid, "stage": stage, "status": DISCOVERED, "reason": None, "detail": {}})
        else:
            for k in ("category", "category_code", "category_v2", "summary"):
                if cand.get(k):
                    row[k] = cand[k] if k != "summary" else str(cand[k])[:1200]
            if cand.get("sources"):
                row["sources"] = [str(s)[:400] for s in cand["sources"][:8]]
                row["source_url"] = row["source_url"] or _source_url(cand)
            if cand.get("verification"):
                row["verification_status"] = default_verification_status(cand)
        return cid

    def proposal(self, source: str, title: str, blurb: str = "", stage: str = "discovery") -> str:
        return self.candidate({"discovery_source": source, "title": title, "summary": blurb}, stage=stage)

    def score(self, cid: str, score: float, breakdown: Optional[Dict[str, Any]] = None) -> None:
        row = self.candidates.get(cid)
        if row is not None:
            row["score"] = float(score)
            row["score_breakdown"] = dict(breakdown or {})

    def reject(self, cid: str, reason: str, stage: str, detail: Optional[Dict[str, Any]] = None,
               duplicate_match: Optional[str] = None) -> None:
        if reason not in REJECTION_REASONS:
            raise ValueError(f"unknown rejection reason {reason!r}")
        row = self.candidates.get(cid)
        if row is None:
            return
        row.update(status=REJECTED, rejection_reason=reason, rejection_stage=stage)
        if reason in _DUP_REASONS:
            row["duplicate_result"] = reason
            row["duplicate_match"] = (duplicate_match or "")[:400]
        self.events.append({"candidate_id": cid, "stage": stage, "status": REJECTED, "reason": reason,
                            "detail": dict(detail or {}, **({"match": duplicate_match[:400]} if duplicate_match else {}))})

    def eligible(self, cid: str, stage: str = "ranking", rank: Optional[int] = None) -> None:
        row = self.candidates.get(cid)
        if row is None:
            return
        row.update(status=ELIGIBLE, rejection_reason=None, rejection_stage=None, duplicate_result=row.get("duplicate_result") or "NONE")
        self.events.append({"candidate_id": cid, "stage": stage, "status": ELIGIBLE, "reason": None,
                            "detail": {"rank": rank} if rank is not None else {}})

    def select(self, cid: str, stage: str = "selection") -> None:
        row = self.candidates.get(cid)
        if row is None:
            return
        row["status"] = SELECTED
        self.events.append({"candidate_id": cid, "stage": stage, "status": SELECTED, "reason": None, "detail": {}})

    def id_for_title(self, title: str, source: str = "") -> Optional[str]:
        if source:
            cid = candidate_id(source, title)
            if cid in self.candidates:
                return cid
        norm = normalize_title(title)
        for cid, row in self.candidates.items():
            if row["normalized_title"] == norm:
                return cid
        return None

    def note(self, key: str, value: Any) -> None:
        self.notes[key] = value

    def provider_metrics(self) -> Dict[str, Dict[str, int]]:
        """Final per-provider counts derived from candidate outcomes."""
        out = {fam: dict(p, rejected=0, accepted=0, duplicates=0, unique_accepted=0) for fam, p in self.providers.items()}
        by_title: Dict[str, set] = {}
        for row in self.candidates.values():
            by_title.setdefault(row["normalized_title"], set()).add(row["provider_family"])
        for row in self.candidates.values():
            p = out.setdefault(row["provider_family"], {
                "queries": 0, "articles": 0, "candidates_created": 0, "rejected": 0, "accepted": 0,
                "duplicates": 0, "errors": 0, "unique_accepted": 0})
            if row["status"] == REJECTED:
                p["rejected"] += 1
                if row["rejection_reason"] in _DUP_REASONS:
                    p["duplicates"] += 1
            elif row["status"] in (ELIGIBLE, SELECTED):
                p["accepted"] += 1
                if len(by_title.get(row["normalized_title"], ())) == 1:
                    p["unique_accepted"] += 1
        return out

    def summary(self) -> Dict[str, Any]:
        statuses: Dict[str, int] = {}
        reasons: Dict[str, int] = {}
        for row in self.candidates.values():
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
            if row["rejection_reason"]:
                reasons[row["rejection_reason"]] = reasons.get(row["rejection_reason"], 0) + 1
        return {"run_id": self.run_id, "candidates": len(self.candidates), "statuses": statuses,
                "rejection_reasons": reasons, "providers": self.provider_metrics()}


# ---- persistence ------------------------------------------------------------------------------------
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS topic_candidate_ledger (
        candidate_id TEXT PRIMARY KEY,
        normalized_title TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        category_code TEXT NOT NULL DEFAULT '',
        category_v2_json TEXT NOT NULL DEFAULT '{}',
        provider TEXT NOT NULL DEFAULT '',
        provider_family TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        sources_json TEXT NOT NULL DEFAULT '[]',
        publication_ts TEXT NOT NULL DEFAULT '',
        first_discovered_at TEXT NOT NULL,
        last_discovered_at TEXT NOT NULL,
        times_seen INTEGER NOT NULL DEFAULT 1,
        entities_json TEXT NOT NULL DEFAULT '[]',
        score REAL,
        score_breakdown_json TEXT NOT NULL DEFAULT '{}',
        duplicate_result TEXT,
        duplicate_match TEXT,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        selection_status TEXT NOT NULL,
        rejection_reason TEXT,
        rejection_stage TEXT,
        last_run_id TEXT NOT NULL DEFAULT '',
        selected_job_id TEXT,
        final_outcome TEXT,
        final_outcome_reason TEXT,
        updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_tcl_norm ON topic_candidate_ledger(normalized_title)",
    "CREATE INDEX IF NOT EXISTS ix_tcl_status ON topic_candidate_ledger(selection_status)",
    """CREATE TABLE IF NOT EXISTS topic_candidate_events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        candidate_id TEXT,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_tce_cand ON topic_candidate_events(candidate_id)",
    "CREATE INDEX IF NOT EXISTS ix_tce_run ON topic_candidate_events(run_id)",
    """CREATE TABLE IF NOT EXISTS topic_discovery_runs (
        run_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 1,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        flags_json TEXT NOT NULL DEFAULT '{}',
        provider_stats_json TEXT NOT NULL DEFAULT '{}',
        rotation_json TEXT NOT NULL DEFAULT '{}',
        recovery_json TEXT NOT NULL DEFAULT '{}',
        notes_json TEXT NOT NULL DEFAULT '{}',
        candidate_count INTEGER NOT NULL DEFAULT 0,
        eligible_count INTEGER NOT NULL DEFAULT 0,
        selected_candidate_id TEXT,
        outcome TEXT,
        outcome_reason TEXT,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS topic_discovery_rotation (
        provider_key TEXT PRIMARY KEY,
        counter INTEGER NOT NULL DEFAULT 0,
        last_value TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL)""",
)


def live_db_refused(db_path: str) -> bool:
    """True when running under pytest against the live studio database."""
    try:
        from backend.app.content.topic_ledger import DB_PATH as live
    except Exception:
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and os.path.abspath(db_path) == os.path.abspath(live)


class CandidateLedger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.disabled_reason: Optional[str] = None
        if live_db_refused(db_path):
            self.disabled_reason = "live database refused under pytest"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        for stmt in _SCHEMA:
            conn.execute(stmt)
        return conn

    @property
    def usable(self) -> bool:
        return self.disabled_reason is None

    # -- writes ------------------------------------------------------------------------------------
    def flush(self, trace: DiscoveryTrace, *, eligible_count: Optional[int] = None) -> bool:
        """Persists a run in one transaction. Returns False (and logs) on any database problem."""
        if not self.usable or not getattr(trace, "enabled", False):
            return False
        now = _now()
        try:
            with self._connect() as conn:
                for row in trace.candidates.values():
                    self._upsert_candidate(conn, row, trace.run_id, now)
                for ev in trace.events:
                    self._event(conn, trace.run_id, ev, now)
                metrics = trace.provider_metrics()
                elig = eligible_count if eligible_count is not None else sum(
                    1 for r in trace.candidates.values() if r["status"] in (ELIGIBLE, SELECTED))
                conn.execute(
                    """INSERT INTO topic_discovery_runs (run_id, job_id, attempts, started_at, finished_at, flags_json,
                           provider_stats_json, rotation_json, recovery_json, notes_json, candidate_count, eligible_count, updated_at)
                       VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(run_id) DO UPDATE SET
                           -- a new discovery pass (retry of the same job) has a new trace start time;
                           -- re-saving the same pass (identity gate, selection) is not another attempt
                           attempts=attempts + (excluded.started_at != topic_discovery_runs.started_at),
                           started_at=excluded.started_at, finished_at=excluded.finished_at,
                           flags_json=excluded.flags_json, provider_stats_json=excluded.provider_stats_json,
                           rotation_json=excluded.rotation_json, recovery_json=excluded.recovery_json,
                           notes_json=excluded.notes_json, candidate_count=excluded.candidate_count,
                           eligible_count=excluded.eligible_count, updated_at=excluded.updated_at""",
                    (trace.run_id, trace.job_id, trace.started_at, now, json.dumps(trace.flags, default=str),
                     json.dumps(metrics), json.dumps(trace.rotation, default=str), json.dumps(trace.recovery, default=str),
                     json.dumps(trace.notes, default=str), len(trace.candidates), elig, now))
            return True
        except Exception as exc:
            logger.warning("Candidate ledger write skipped (%s: %s)", type(exc).__name__, str(exc)[:160])
            return False

    def _upsert_candidate(self, conn: sqlite3.Connection, row: Dict[str, Any], run_id: str, now: str) -> None:
        cur = conn.execute("SELECT selection_status, last_run_id FROM topic_candidate_ledger WHERE candidate_id=?",
                           (row["candidate_id"],)).fetchone()
        new_status = row["status"]
        if cur is None:
            conn.execute(
                """INSERT INTO topic_candidate_ledger (candidate_id, normalized_title, title, summary, category, category_code,
                       category_v2_json, provider, provider_family, source_url, sources_json, publication_ts, first_discovered_at,
                       last_discovered_at, times_seen, entities_json, score, score_breakdown_json, duplicate_result, duplicate_match,
                       verification_status, selection_status, rejection_reason, rejection_stage, last_run_id, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["candidate_id"], row["normalized_title"], row["title"], row["summary"], row["category"],
                 row["category_code"], json.dumps(row.get("category_v2") or {}, default=str), row["provider"],
                 row["provider_family"], row["source_url"], json.dumps(row["sources"]), row["publication_ts"],
                 row["discovered_at"], now, json.dumps(row["entities"]), row["score"],
                 json.dumps(row["score_breakdown"] or {}, default=str), row["duplicate_result"], row["duplicate_match"],
                 row["verification_status"], new_status, row["rejection_reason"], row["rejection_stage"], run_id, now))
            return
        old_status, last_run = cur["selection_status"], cur["last_run_id"]
        seen_inc = 0 if last_run == run_id else 1          # a retried run is not a new sighting
        if new_status in _ALLOWED.get(old_status, ()):
            status_sql = (new_status, row["rejection_reason"], row["rejection_stage"])
        else:
            status_sql = None
            if old_status != new_status:
                self._event(conn, run_id, {"candidate_id": row["candidate_id"], "stage": "ledger", "status": "TRANSITION_IGNORED",
                                           "reason": None, "detail": {"from": old_status, "to": new_status}}, now)
        conn.execute(
            """UPDATE topic_candidate_ledger SET title=?, summary=?, category=?, category_code=?, category_v2_json=?,
                   source_url=COALESCE(NULLIF(?, ''), source_url), sources_json=?, last_discovered_at=?, times_seen=times_seen+?,
                   entities_json=?, score=COALESCE(?, score), score_breakdown_json=?, duplicate_result=COALESCE(?, duplicate_result),
                   duplicate_match=COALESCE(?, duplicate_match), verification_status=?, last_run_id=?, updated_at=?
               WHERE candidate_id=?""",
            (row["title"], row["summary"], row["category"], row["category_code"],
             json.dumps(row.get("category_v2") or {}, default=str), row["source_url"], json.dumps(row["sources"]), now, seen_inc,
             json.dumps(row["entities"]), row["score"], json.dumps(row["score_breakdown"] or {}, default=str),
             row["duplicate_result"], row["duplicate_match"], row["verification_status"], run_id, now, row["candidate_id"]))
        if status_sql is not None:
            conn.execute("UPDATE topic_candidate_ledger SET selection_status=?, rejection_reason=?, rejection_stage=? WHERE candidate_id=?",
                         (*status_sql, row["candidate_id"]))

    def _event(self, conn: sqlite3.Connection, run_id: str, ev: Dict[str, Any], now: str) -> None:
        detail = json.dumps(ev.get("detail") or {}, sort_keys=True, default=str)
        key = "|".join(str(x) for x in (run_id, ev.get("candidate_id"), ev.get("stage"), ev.get("status"), ev.get("reason"), detail))
        eid = "te_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        conn.execute("INSERT OR IGNORE INTO topic_candidate_events (event_id, run_id, candidate_id, stage, status, reason, detail_json, created_at)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (eid, run_id, ev.get("candidate_id"), ev.get("stage"), ev.get("status"), ev.get("reason"), detail, now))

    def mark_selected(self, run_id: str, cid: str, job_id: str) -> bool:
        return self._transition(run_id, cid, SELECTED, job_id=job_id, stage="selection")

    def mark_outcome(self, run_id: str, outcome: str, reason: str = "") -> bool:
        """Records the production outcome (PRODUCED / FAILED) for the run's selected candidate."""
        if outcome not in (PRODUCED, FAILED) or not self.usable:
            return False
        try:
            with self._connect() as conn:
                run = conn.execute("SELECT selected_candidate_id FROM topic_discovery_runs WHERE run_id=?", (run_id,)).fetchone()
                conn.execute("UPDATE topic_discovery_runs SET outcome=?, outcome_reason=?, updated_at=? WHERE run_id=?",
                             (outcome, (reason or "")[:500], _now(), run_id))
            cid = run["selected_candidate_id"] if run else None
            if cid:
                return self._transition(run_id, cid, outcome, reason=reason, stage="production")
            return run is not None
        except Exception as exc:
            logger.warning("Candidate ledger outcome skipped (%s)", type(exc).__name__)
            return False

    def _transition(self, run_id: str, cid: str, status: str, *, job_id: str = "", reason: str = "", stage: str) -> bool:
        if not self.usable:
            return False
        now = _now()
        try:
            with self._connect() as conn:
                cur = conn.execute("SELECT selection_status FROM topic_candidate_ledger WHERE candidate_id=?", (cid,)).fetchone()
                if cur is None:
                    return False
                ok = status in _ALLOWED.get(cur["selection_status"], ())
                if ok:
                    if status == SELECTED:
                        conn.execute("UPDATE topic_candidate_ledger SET selection_status=?, selected_job_id=?, rejection_reason=NULL,"
                                     " rejection_stage=NULL, updated_at=? WHERE candidate_id=?", (status, job_id, now, cid))
                        conn.execute("UPDATE topic_discovery_runs SET selected_candidate_id=?, updated_at=? WHERE run_id=?", (cid, now, run_id))
                    else:
                        conn.execute("UPDATE topic_candidate_ledger SET selection_status=?, final_outcome=?, final_outcome_reason=?,"
                                     " updated_at=? WHERE candidate_id=?", (status, status, (reason or "")[:500], now, cid))
                self._event(conn, run_id, {"candidate_id": cid, "stage": stage, "status": status if ok else "TRANSITION_IGNORED",
                                           "reason": None, "detail": {"to": status, "from": cur["selection_status"],
                                                                     **({"reason": reason[:300]} if reason else {})}}, now)
                return ok
        except Exception as exc:
            logger.warning("Candidate ledger transition skipped (%s)", type(exc).__name__)
            return False

    # -- reads ------------------------------------------------------------------------------------
    def get(self, cid: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM topic_candidate_ledger WHERE candidate_id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def events(self, cid: Optional[str] = None, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        q, a = "SELECT * FROM topic_candidate_events WHERE 1=1", []
        if cid:
            q, a = q + " AND candidate_id=?", a + [cid]
        if run_id:
            q, a = q + " AND run_id=?", a + [run_id]
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(q + " ORDER BY created_at, event_id", a)]

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM topic_discovery_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def count(self, table: str = "topic_candidate_ledger") -> int:
        if table not in ("topic_candidate_ledger", "topic_candidate_events", "topic_discovery_runs", "topic_discovery_rotation"):
            raise ValueError(table)
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ---- rotation state (3.31C) -------------------------------------------------------------------------
class RotationStore:
    """Persisted, deterministic rotation counters keyed by provider (e.g. 'wikipedia_search:history').

    ``peek`` reads the counter; ``commit`` writes only the keys whose request succeeded, in one
    transaction, so a failed provider request never advances or corrupts the state."""

    def __init__(self, db_path: Optional[str]):
        self.db_path = db_path
        self.memory: Dict[str, int] = {}
        self.persistent = bool(db_path) and not live_db_refused(db_path or "")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        for stmt in _SCHEMA:
            conn.execute(stmt)
        return conn

    def peek(self, key: str) -> int:
        if key in self.memory:
            return self.memory[key]
        val = 0
        if self.persistent:
            try:
                with self._connect() as conn:
                    r = conn.execute("SELECT counter FROM topic_discovery_rotation WHERE provider_key=?", (key,)).fetchone()
                val = int(r[0]) if r else 0
            except Exception as exc:
                logger.warning("Rotation state unreadable (%s); using 0", type(exc).__name__)
        self.memory[key] = val
        return val

    def commit(self, advances: Dict[str, Tuple[int, str]]) -> bool:
        """advances: key -> (new_counter, last_value). Only successful requests belong here."""
        for k, (v, _) in advances.items():
            self.memory[k] = int(v)
        if not self.persistent or not advances:
            return False
        try:
            now = _now()
            with self._connect() as conn:
                for k, (v, last) in advances.items():
                    conn.execute("INSERT INTO topic_discovery_rotation (provider_key, counter, last_value, updated_at) VALUES (?,?,?,?)"
                                 " ON CONFLICT(provider_key) DO UPDATE SET counter=excluded.counter, last_value=excluded.last_value,"
                                 " updated_at=excluded.updated_at", (k, int(v), str(last)[:300], now))
            return True
        except Exception as exc:
            logger.warning("Rotation state not saved (%s); counters kept in memory only", type(exc).__name__)
            return False
