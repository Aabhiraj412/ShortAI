"""Stage 3.32B action registry: the closed catalogue of everything autonomy may ever be asked to do.

* An action type that is not in ``REGISTRY`` does not exist: every entry point rejects it.
* An action type marked ``prohibited`` can never be proposed, approved or executed, at any level, with any
  approval. The prohibited entries are code constants; no API, configuration or database row can change them.
* Only permitted action types have handlers (``handlers.HANDLERS``); an import-time check refuses to load if a
  handler is ever registered for a prohibited or unknown type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, Mapping, Optional, Tuple


class Level(IntEnum):
    """Autonomy levels, ordered. The effective level of an action is min(global level, category level)."""
    OFF = 0                  # nothing: no observations, no recommendations, no execution
    OBSERVE = 1              # read state and record observations; no proposals
    RECOMMEND = 2            # propose actions into the approval queue; never execute
    APPROVED_EXECUTION = 3   # execute permitted actions that hold a valid explicit or standing approval

    @classmethod
    def parse(cls, value: Any) -> "Level":
        if isinstance(value, Level):
            return value
        if isinstance(value, str) and value.strip().upper() in cls.__members__:
            return cls[value.strip().upper()]
        raise ValueError(f"unknown autonomy level: {value!r}; one of {', '.join(cls.__members__)}")


RISK_READ_ONLY = "READ_ONLY"
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_PROHIBITED = "PROHIBITED"

# Required approval level.
APPROVAL_STANDING_ALLOWED = "STANDING_OR_EXPLICIT"   # an operator may grant a time-limited standing approval
APPROVAL_EXPLICIT_EACH = "EXPLICIT_EACH"            # every request needs its own explicit human approval
APPROVAL_NEVER = "NEVER"                            # prohibited: no approval of any kind is accepted

CATEGORIES: Dict[str, str] = {
    "reporting": "Generate operational reports from read-only production data.",
    "recommendations": "Record recommendations for the operator to review.",
    "diagnostics": "Run read-only diagnostics.",
    "notifications": "Notify the operator through the local dashboard inbox (no e-mail or external message).",
    "community_drafts": "Prepare YouTube Community post drafts locally. Never publishes.",
    "self_healing": "Stage 3.32D: diagnose operational failures and run allow-listed, verifiable, low-impact recovery "
                    "actions. Never retries or re-renders jobs, restarts processes or changes configuration.",
}


@dataclass(frozen=True)
class Field:
    kind: type
    required: bool = False
    max_len: Optional[int] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    choices: Optional[Tuple[str, ...]] = None
    pattern: Optional[str] = None


@dataclass(frozen=True)
class ActionSpec:
    action_type: str
    category: str
    description: str
    risk: str
    required_permission: str
    approval: str
    preconditions: Tuple[str, ...]
    timeout_seconds: float
    retry_limit: int
    verification: str
    rollback: str
    prohibited: bool = False
    payload_fields: Mapping[str, Field] = field(default_factory=dict)
    request_ttl_hours: float = 24.0
    daily_limit: int = 0
    guards: str = ""          # for prohibited entries: the production functions / routes the prohibition covers

    def public(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type, "category": self.category, "description": self.description,
            "risk": self.risk, "required_permission": self.required_permission,
            "required_approval": self.approval, "preconditions": list(self.preconditions),
            "timeout_seconds": self.timeout_seconds, "retry_limit": self.retry_limit,
            "verification": self.verification, "rollback": self.rollback, "prohibited": self.prohibited,
            "request_ttl_hours": self.request_ttl_hours, "daily_limit": self.daily_limit,
            "payload_fields": {k: {"type": f.kind.__name__, "required": f.required, "max_len": f.max_len,
                                   "min": f.min_value, "max": f.max_value,
                                   "choices": list(f.choices) if f.choices else None}
                               for k, f in self.payload_fields.items()},
            "guards": self.guards,
        }


# Preconditions every execution re-checks immediately before running (control_plane.ControlPlane._gate).
COMMON_PRECONDITIONS = (
    "no_kill_switch_active",
    "effective_level_is_APPROVED_EXECUTION",
    "approval_valid_signed_and_unexpired",
    "payload_hash_matches_approval",
    "daily_limit_not_reached",
    "not_already_executing",
)

DIAGNOSTICS = ("ops_overview", "ops_daily_summary", "audit_chain_verify", "kill_switch_status")
SELF_HEALING_DIAGNOSTICS = ("collect_log_context", "inspect_media_metadata", "check_disk_space", "inspect_file_locks",
                            "validate_ffmpeg_filter", "generate_diagnostic_report")
# Key-less, read-only providers only (no credentials are ever used by self-healing).
SELF_HEALING_PROVIDERS = ("Openverse", "Wikimedia Commons", "NASA Image Library", "Internet Archive")

PERMITTED: Tuple[ActionSpec, ...] = (
    ActionSpec(
        action_type="report.operational.generate", category="reporting",
        description="Generate an operational report (Stage 3.32A daily summary) and save it as a local JSON file.",
        risk=RISK_LOW, required_permission="autonomy.reporting.execute", approval=APPROVAL_STANDING_ALLOWED,
        preconditions=COMMON_PRECONDITIONS + ("database_readable",),
        timeout_seconds=60, retry_limit=1,
        verification="The report file exists inside data/autonomy/reports, parses as JSON and matches the recorded SHA-256.",
        rollback="Compensation: delete the report file this execution created (only that file).",
        payload_fields={"date": Field(str, pattern=r"^\d{4}-\d{2}-\d{2}$", max_len=10),
                        "days": Field(int, min_value=1, max_value=30)},
        request_ttl_hours=24, daily_limit=24),
    ActionSpec(
        action_type="recommendation.record", category="recommendations",
        description="Record a recommendation for the operator. Text only; nothing is executed.",
        risk=RISK_READ_ONLY, required_permission="autonomy.recommendations.execute", approval=APPROVAL_STANDING_ALLOWED,
        preconditions=COMMON_PRECONDITIONS,
        timeout_seconds=10, retry_limit=0,
        verification="The execution result holds the recommendation title and text exactly as approved.",
        rollback="None needed: the recommendation is a record. It can be cancelled before execution.",
        payload_fields={"title": Field(str, required=True, max_len=160), "text": Field(str, required=True, max_len=2000),
                        "evidence_ref": Field(str, max_len=200)},
        request_ttl_hours=72, daily_limit=100),
    ActionSpec(
        action_type="diagnostic.readonly.run", category="diagnostics",
        description="Run one allow-listed read-only diagnostic.",
        risk=RISK_LOW, required_permission="autonomy.diagnostics.execute", approval=APPROVAL_STANDING_ALLOWED,
        preconditions=COMMON_PRECONDITIONS,
        timeout_seconds=60, retry_limit=1,
        verification="The diagnostic returned a result object naming the diagnostic with status ok.",
        rollback="None needed: diagnostics are read-only.",
        payload_fields={"diagnostic": Field(str, required=True, choices=DIAGNOSTICS),
                        "days": Field(int, min_value=1, max_value=30)},
        request_ttl_hours=24, daily_limit=96),
    ActionSpec(
        action_type="operator.notify", category="notifications",
        description="Add a notification to the local operator inbox shown in the dashboard. No e-mail or external message.",
        risk=RISK_LOW, required_permission="autonomy.notifications.execute", approval=APPROVAL_STANDING_ALLOWED,
        preconditions=COMMON_PRECONDITIONS,
        timeout_seconds=10, retry_limit=1,
        verification="The inbox file contains a line for this request id with the approved subject.",
        rollback="Compensation: append a retraction line for the request id (the inbox is append-only).",
        payload_fields={"subject": Field(str, required=True, max_len=120), "message": Field(str, required=True, max_len=2000),
                        "severity": Field(str, choices=("info", "low", "medium", "high"))},
        request_ttl_hours=24, daily_limit=48),
    ActionSpec(
        action_type="community.draft.prepare", category="community_drafts",
        description="Prepare a YouTube Community post DRAFT as a local file. It is never published or uploaded.",
        risk=RISK_MEDIUM, required_permission="autonomy.community_drafts.execute", approval=APPROVAL_EXPLICIT_EACH,
        preconditions=COMMON_PRECONDITIONS,
        timeout_seconds=30, retry_limit=1,
        verification="The draft file exists inside data/autonomy/community_drafts with status DRAFT, publishable false and the approved text.",
        rollback="Compensation: delete the draft file this execution created (only that file).",
        payload_fields={"text": Field(str, required=True, max_len=1500), "topic": Field(str, max_len=200),
                        "source_job_id": Field(str, max_len=100, pattern=r"^[A-Za-z0-9_.:-]+$")},
        request_ttl_hours=48, daily_limit=6),
    # -- Stage 3.32D self-healing ---------------------------------------------------------------------------------
    # Low-risk, read-only diagnostics. The handler reads only; files it writes go to data/autonomy/self_healing.
    ActionSpec(
        action_type="selfheal.diagnostic.run", category="self_healing",
        description="Run one allow-listed read-only diagnostic for a self-healing incident (logs, media metadata, "
                    "disk space, file locks, FFmpeg filter parameters, diagnostic report).",
        risk=RISK_LOW, required_permission="autonomy.self_healing.execute", approval=APPROVAL_STANDING_ALLOWED,
        preconditions=COMMON_PRECONDITIONS + ("incident_exists", "resource_lock_acquired", "paths_inside_media_or_assets"),
        timeout_seconds=60, retry_limit=1,
        verification="The diagnostic result names the diagnostic and incident with status ok (a report file must exist "
                     "inside data/autonomy/self_healing/reports and match its SHA-256).",
        rollback="None needed: read-only. A diagnostic report file it created is removed if verification fails.",
        payload_fields={"incident_id": Field(str, required=True, pattern=r"^inc_[0-9a-f]{12}$", max_len=16),
                        "diagnostic": Field(str, required=True, choices=SELF_HEALING_DIAGNOSTICS)},
        request_ttl_hours=24, daily_limit=96),
    # Potentially reversible recovery actions: existing services only, explicit approval for every request.
    ActionSpec(
        action_type="selfheal.provider.retry_readonly", category="self_healing",
        description="Availability check: ONE uncached read-only request to the provider's public search endpoint (the "
                    "endpoint the production client uses) to confirm it has recovered. Nothing is downloaded, written "
                    "or cached.",
        risk=RISK_MEDIUM, required_permission="autonomy.self_healing.execute", approval=APPROVAL_EXPLICIT_EACH,
        preconditions=COMMON_PRECONDITIONS + ("incident_exists", "resource_lock_acquired", "provider_allow_listed",
                                              "query_from_incident_evidence"),
        timeout_seconds=45, retry_limit=0,
        verification="HTTP 200 with JSON in the provider's documented result structure (record count stored). Errors, "
                     "redirects off the provider host, cached or malformed responses are failed verifications.",
        rollback="None needed: a read-only search.",
        payload_fields={"incident_id": Field(str, required=True, pattern=r"^inc_[0-9a-f]{12}$", max_len=16),
                        "provider": Field(str, required=True, choices=SELF_HEALING_PROVIDERS),
                        "query": Field(str, required=True, max_len=100, pattern=r"^[A-Za-z0-9 ,.'()&:-]{2,100}$")},
        request_ttl_hours=12, daily_limit=12),
    ActionSpec(
        action_type="selfheal.asset.reacquire_staged", category="self_healing",
        description="Re-download one failed media asset into a quarantined staging folder "
                    "(data/autonomy/self_healing/staging) with a guarded downloader: HTTPS, allow-listed public hosts on "
                    "every redirect hop, 25 MB / 60 s caps, signature + decode validation. Never placed into a job.",
        risk=RISK_MEDIUM, required_permission="autonomy.self_healing.execute", approval=APPROVAL_EXPLICIT_EACH,
        preconditions=COMMON_PRECONDITIONS + ("incident_exists", "resource_lock_acquired", "url_https_allow_listed_host",
                                              "url_recorded_in_incident_evidence", "job_not_running_or_recovering"),
        timeout_seconds=90, retry_limit=0,
        verification="The staged file exists inside the staging folder, passes the downloader's signature and decode "
                     "checks, has positive dimensions and matches its SHA-256 on re-read.",
        rollback="Compensation: delete this execution's staging folder (only that folder).",
        payload_fields={"incident_id": Field(str, required=True, pattern=r"^inc_[0-9a-f]{12}$", max_len=16),
                        "asset_url": Field(str, required=True, max_len=500, pattern=r"^https://[^\s<>\"']{4,490}$")},
        request_ttl_hours=12, daily_limit=12),
)


def _prohibited(action_type: str, description: str, guards: str) -> ActionSpec:
    return ActionSpec(action_type=action_type, category="prohibited", description=description, risk=RISK_PROHIBITED,
                      required_permission="none (never grantable)", approval=APPROVAL_NEVER, preconditions=(),
                      timeout_seconds=0, retry_limit=0, verification="n/a", rollback="n/a", prohibited=True,
                      guards=guards)


PROHIBITED: Tuple[ActionSpec, ...] = (
    _prohibited("production.scheduler.start", "Start the production scheduler.",
                "control_center_service scheduler enable / production start; /api/control-center/scheduler/enable; /api/control-center/production/start"),
    _prohibited("production.scheduler.stop", "Stop, pause or resume the production scheduler.",
                "/api/control-center/scheduler/disable; /api/control-center/production/stop|pause|resume"),
    _prohibited("production.cycle.create", "Create or trigger production cycles or jobs.",
                "run_cycle_generation; /api/pipeline/run; /api/jobs/run; /api/jobs/recover"),
    _prohibited("production.schedule.change", "Change production or publication schedules.",
                "schedule draft / activate; unified-schedule activate; publication-schedule activate; reload_scheduler"),
    _prohibited("render.worker.launch", "Launch additional render workers or change worker concurrency.",
                "render workers; /api/control-center/concurrency"),
    _prohibited("worker.lock.bypass", "Bypass or release worker / server locks.", "ServerLock; worker single-flight locks"),
    _prohibited("recovery.deadline.change", "Change, extend or bypass recovery deadlines or the retry policy.",
                "recovery_settings; retry_policy; operator retry deadline checks"),
    _prohibited("topic_history.modify", "Modify topic history or the creative-identity registry.",
                "backend/app/content/topic_history.json; creative_identity_registry; content_ledger"),
    _prohibited("duplicate_detection.bypass", "Bypass duplicate detection.", "creative identity / duplicate checks"),
    _prohibited("quality_checks.bypass", "Bypass content-quality, QC, integrity or renderer-compliance checks.",
                "qc_service; visual_quality_gate; renderer compliance"),
    _prohibited("oauth.credentials.change", "Change OAuth clients, credentials or tokens, or disconnect the channel.",
                "youtube_connection_service; secure_storage; .env.local"),
    _prohibited("oauth.scopes.change", "Request or change OAuth scopes.", "YouTube OAuth consent flow"),
    _prohibited("youtube.video.publish", "Upload, publish, reschedule or delete YouTube videos.",
                "upload_short; update_or_reschedule_remote_video; publishing queue approve"),
    _prohibited("youtube.community.publish", "Publish a public YouTube Community post.", "any Community posts API call"),
    _prohibited("production.config.modify", "Modify production configuration, feature flags, operating mode or publishing policy.",
                "creative_policy_config.json; /api/control-center/production-config; operating-mode; publishing policy"),
    _prohibited("process.daemon.restart", "Restart or stop the production daemon.", "/api/control-center/deployment/restart"),
    _prohibited("process.dashboard.restart", "Restart or stop the dashboard.", "CrazzyDocs-Health-Dashboard task; run_dashboard.py"),
    _prohibited("production.data.delete", "Delete production data, media or records.", "studio.db tables; media; junk cleaner live mode"),
    _prohibited("git.branch.change", "Change Git branches.", "git checkout / switch"),
    _prohibited("git.destructive", "Perform destructive Git operations (reset, force-push, clean, rebase).", "git reset / push --force / clean"),
    _prohibited("autonomy.self_policy.change", "Let autonomy change its own levels, standing approvals or kill switches, or approve its own proposals.",
                "ControlPlane.set_level / grant_standing_approval / kill switch (human operators only)"),
    # Stage 3.32D: high-impact recovery stays with the existing scheduler and the operator.
    _prohibited("recovery.job.retry", "Retry, resume or re-run an entire production job.",
                "retry_policy re-arm; operator_retry_service.request_retry; /api/jobs/recover; scheduler dispatch"),
    _prohibited("render.video.rerender", "Re-render a video or regenerate production media inside a job.",
                "rendering engines; Lemo-Opuscar; explainer_animation_engine; production workspace"),
    _prohibited("render.worker.restart", "Restart, stop or kill render workers or production jobs.",
                "worker_registry; job_manager; process kill"),
    _prohibited("production.asset.replace", "Place, replace or delete assets inside a production job's media folder.",
                "media/productions/<job>/assets; asset_registry"),
    _prohibited("autonomy.code.execute", "Run shell commands, arbitrary Python, or commands built from recommendation "
                "or AI-generated text.", "subprocess with shell; eval / exec; command construction from free text"),
)

REGISTRY: Dict[str, ActionSpec] = {s.action_type: s for s in PERMITTED + PROHIBITED}
assert len(REGISTRY) == len(PERMITTED) + len(PROHIBITED), "duplicate action type in registry"
assert all(s.category in CATEGORIES for s in PERMITTED)
assert all(not s.prohibited for s in PERMITTED) and all(s.prohibited for s in PROHIBITED)


class UnknownActionError(ValueError):
    pass


class ProhibitedActionError(PermissionError):
    pass


class PayloadValidationError(ValueError):
    pass


def lookup(action_type: Any) -> ActionSpec:
    """Return the spec of a known action type. Raises for unknown types; prohibited types are returned and
    must be refused by the caller (``require_permitted`` does both)."""
    if not isinstance(action_type, str) or action_type not in REGISTRY:
        raise UnknownActionError(f"unknown action type: {str(action_type)[:80]!r}")
    return REGISTRY[action_type]


def require_permitted(action_type: Any) -> ActionSpec:
    spec = lookup(action_type)
    if spec.prohibited:
        raise ProhibitedActionError(f"action type {action_type!r} is permanently prohibited for autonomy")
    return spec


def validate_payload(spec: ActionSpec, payload: Any) -> Dict[str, Any]:
    """Strict validation: a dict with only declared keys, correct types and bounds. Returns a clean copy."""
    import re
    if not isinstance(payload, dict):
        raise PayloadValidationError("payload must be an object")
    unknown = set(payload) - set(spec.payload_fields)
    if unknown:
        raise PayloadValidationError(f"unknown payload field(s): {', '.join(sorted(map(str, unknown)))[:200]}")
    clean: Dict[str, Any] = {}
    for name, f in spec.payload_fields.items():
        if name not in payload or payload[name] is None:
            if f.required:
                raise PayloadValidationError(f"missing required field: {name}")
            continue
        value = payload[name]
        if f.kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise PayloadValidationError(f"{name} must be an integer")
            if f.min_value is not None and value < f.min_value or f.max_value is not None and value > f.max_value:
                raise PayloadValidationError(f"{name} must be between {f.min_value} and {f.max_value}")
        elif f.kind is str:
            if not isinstance(value, str):
                raise PayloadValidationError(f"{name} must be a string")
            value = value.strip()
            if f.required and not value:
                raise PayloadValidationError(f"{name} must not be empty")
            if f.max_len is not None and len(value) > f.max_len:
                raise PayloadValidationError(f"{name} is longer than {f.max_len} characters")
            if f.choices and value not in f.choices:
                raise PayloadValidationError(f"{name} must be one of {', '.join(f.choices)}")
            if f.pattern and not re.fullmatch(f.pattern, value):
                raise PayloadValidationError(f"{name} has an invalid format")
            if any(ord(c) < 32 and c not in "\n\t" for c in value):
                raise PayloadValidationError(f"{name} contains control characters")
        clean[name] = value
    return clean
