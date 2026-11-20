"""Structured failure classification (Stage 3.32D).

Six classes: TRANSIENT, DETERMINISTIC, RESOURCE, CONFIGURATION, DATA, UNKNOWN.

Two kinds of evidence, applied in order:

1. **Error signature**: an ordered catalogue of message / exception-type rules. The first matching rule gives the
   class and a certainty:
     CONFIRMED - the error text itself states the mechanism (e.g. FFmpeg "padded dimensions cannot be smaller than
                 input", "No space left on device", "insufficient_scope");
     STRONG    - a well-known signature of that class (e.g. HTTP 503, WinError 32);
     WEAK      - a generic hint (e.g. "timed out" without more context).
2. **Historical recurrence** of the same signature (same normalized template), across jobs and retries:
     * a TRANSIENT-looking signature that recurred >= 3 times across >= 2 jobs and was never followed by a
       successful attempt is re-classified DETERMINISTIC (basis "recurrence", certainty WEAK - suspected);
     * an unmatched signature with that same recurrence becomes DETERMINISTIC (WEAK) instead of UNKNOWN;
     * a TRANSIENT signature that was later followed by a successful attempt keeps TRANSIENT and is strengthened.

A failure is never TRANSIENT merely because it happened during production; with no signature and no recurrence
evidence it is UNKNOWN. The existing retry policy's own classification is reported alongside for comparison and is
never changed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

TRANSIENT = "TRANSIENT"
DETERMINISTIC = "DETERMINISTIC"
RESOURCE = "RESOURCE"
CONFIGURATION = "CONFIGURATION"
DATA = "DATA"
UNKNOWN = "UNKNOWN"
CLASSES = (TRANSIENT, DETERMINISTIC, RESOURCE, CONFIGURATION, DATA, UNKNOWN)
CERTAINTIES = ("CONFIRMED", "STRONG", "WEAK")

# Recurrence thresholds for "same error keeps happening and retries never helped".
RECURRENCE_MIN_OCCURRENCES = 3
RECURRENCE_MIN_JOBS = 2


@dataclass(frozen=True)
class SignatureRule:
    rule_id: str
    klass: str
    certainty: str
    pattern: str
    mechanism: str                # what the evidence shows (stated as an observation, not a root cause)
    subtype: str = ""
    error_types: Tuple[str, ...] = ()

    def matches(self, message: str, error_type: str) -> bool:
        if self.error_types and error_type in self.error_types and not self.pattern:
            return True
        return bool(self.pattern) and re.search(self.pattern, message, re.I) is not None


# Ordered: specific rules first; the first match wins.
SIGNATURE_RULES: Tuple[SignatureRule, ...] = (
    # -- DETERMINISTIC: the same inputs + the same code fail the same way ------------------------------------------
    SignatureRule("ffmpeg.pad_smaller_than_input", DETERMINISTIC, "CONFIRMED",
                  r"padded dimensions cannot be smaller than input",
                  "FFmpeg refused a pad filter whose target size is smaller than the scaled input.", "ffmpeg_filter"),
    SignatureRule("ffmpeg.invalid_filter", DETERMINISTIC, "STRONG",
                  r"error (parsing|initializing|reinitializing) (a )?filter|no such filter|invalid filter|"
                  r"filtergraph .* (invalid|error)|failed to configure (input|output) pad|option not found",
                  "FFmpeg rejected the filter graph it was given.", "ffmpeg_filter"),
    SignatureRule("media.invalid_dimensions", DETERMINISTIC, "STRONG",
                  r"(width|height) not divisible by 2|invalid (frame )?(size|dimensions)|dimensions? .* (must|should) be (even|positive)",
                  "The encoder rejected the frame dimensions.", "media_dimensions"),
    SignatureRule("quality.qc_rule", DETERMINISTIC, "CONFIRMED",
                  r"qc failure|blocked_qc|duration [\d.]+s is outside",
                  "A quality-control rule rejected the output; the same output fails the same rule.", "qc"),
    SignatureRule("renderer.compliance", DETERMINISTIC, "CONFIRMED",
                  r"renderer compliance|not an approved production renderer",
                  "The renderer-compliance gate refused the renderer.", "renderer_compliance"),
    SignatureRule("content.integrity_gate", DETERMINISTIC, "CONFIRMED",
                  r"integrity gate|duplicatecreativeidentity|every discovered topic was already",
                  "The content-integrity gate refused a repeated subject.", "integrity_gate"),
    SignatureRule("content.validation", DETERMINISTIC, "STRONG",
                  r"invalid research|script validation|check_a_topic_narration",
                  "Research or script validation rejected the content.", "content_validation"),
    SignatureRule("code.defect", DETERMINISTIC, "STRONG", "",
                  "A programming error type was raised; it recurs until the code changes.", "code_defect",
                  error_types=("NameError", "ImportError", "ModuleNotFoundError", "AttributeError", "TypeError",
                               "SyntaxError", "IndentationError", "UnboundLocalError", "NotImplementedError",
                               "AssertionError", "RecursionError")),
    # -- RESOURCE: local capacity or contention -------------------------------------------------------------------
    SignatureRule("resource.disk_full", RESOURCE, "CONFIRMED",
                  r"no space left on device|disk (is )?full|errno 28|winerror 112|not enough space on the disk",
                  "The disk ran out of space.", "disk"),
    SignatureRule("resource.memory", RESOURCE, "CONFIRMED",
                  r"\bmemoryerror\b|out of memory|cannot allocate memory|std::bad_alloc|winerror 8\b|"
                  r"not enough memory|paging file is too small",
                  "The process could not allocate memory.", "memory", error_types=("MemoryError",)),
    SignatureRule("resource.file_lock", RESOURCE, "STRONG",
                  r"winerror 32|winerror 33|being used by another process|sharing violation|lock violation|"
                  r"resource busy|text file busy",
                  "A file was held open by another process or thread.", "file_lock"),
    SignatureRule("resource.file_handles", RESOURCE, "CONFIRMED",
                  r"too many open files|errno 24|winerror 4\b",
                  "The process ran out of file handles.", "file_handles"),
    SignatureRule("resource.youtube_quota", RESOURCE, "CONFIRMED",
                  r"quota exceeded|quotaexceeded|uploadlimitexceeded|daily limit|quota limit (reached|exceeded)|"
                  r"daily [^.]{0,30}quota|quota [^.]{0,20}(reached|exhausted)",
                  "An external quota was exhausted (a capacity limit, not a code fault).", "external_quota"),
    # -- CONFIGURATION ---------------------------------------------------------------------------------------------
    SignatureRule("config.oauth_scope", CONFIGURATION, "CONFIRMED",
                  r"insufficient_scope|insufficient authentication scopes|access_token_scope_insufficient",
                  "The OAuth token lacks a scope the call requires.", "oauth_scope"),
    SignatureRule("config.oauth_grant", CONFIGURATION, "CONFIRMED",
                  r"invalid_grant|token (has been )?(expired or )?revoked|invalid_client|unauthorized_client",
                  "The OAuth grant or client was rejected.", "oauth_grant"),
    SignatureRule("config.credentials", CONFIGURATION, "STRONG",
                  r"missing credentials|client_secrets|api key not valid|invalid api key|api_key_invalid|"
                  r"\b401\b.*unauthori[sz]ed|unauthori[sz]ed.*\b401\b|permission_denied|\b403\b.*forbidden",
                  "A provider rejected or did not receive valid credentials.", "credentials"),
    SignatureRule("config.missing_setting", CONFIGURATION, "STRONG",
                  r"not configured|missing required (setting|configuration|environment)|invalid configuration|"
                  r"configuration error|environment variable .* (missing|not set)",
                  "A required setting is missing or invalid.", "setting"),
    # -- DATA ------------------------------------------------------------------------------------------------------
    SignatureRule("data.corrupt_media", DATA, "CONFIRMED",
                  r"moov atom not found|invalid data found when processing input|corrupt(ed)? (file|image|input|stream)|"
                  r"truncated (file|image)|cannot identify image file|decompressionbomb|failed magic bytes",
                  "An input media file is corrupt or not decodable.", "corrupt_media"),
    SignatureRule("data.missing_asset", DATA, "STRONG",
                  r"(no such file|file not found|filenotfounderror|does not exist).*\.(mp4|mov|png|jpe?g|webp|gif|wav|mp3|m4a|ass|srt|json)|"
                  r"missing required (asset|file|media)|asset .* (missing|not found)",
                  "A required production asset was missing.", "missing_asset"),
    SignatureRule("data.invalid_metadata", DATA, "STRONG",
                  r"invalid metadata|jsondecodeerror|expecting value: line \d+|malformed (json|metadata)",
                  "Recorded metadata could not be parsed.", "metadata"),
    # -- TRANSIENT -------------------------------------------------------------------------------------------------
    SignatureRule("net.http_5xx", TRANSIENT, "STRONG",
                  r"(http|status|error|code)[^.\n]{0,12}\b(500|502|503|504)\b|\b(500|502|503|504)\b[^.\n]{0,12}"
                  r"(error|server|gateway|service)|bad gateway|service unavailable|gateway time-?out|internal server error",
                  "A remote service returned a server error.", "http_5xx"),
    SignatureRule("net.rate_limited", TRANSIENT, "STRONG", r"\b429\b|too many requests|rate ?limit",
                  "A remote service asked the client to slow down.", "rate_limit"),
    SignatureRule("net.connection", TRANSIENT, "STRONG",
                  r"connection (reset|refused|aborted|closed)|getaddrinfo|name or service not known|"
                  r"temporary failure in name resolution|network is unreachable|remote end closed|"
                  r"temporarily unavailable|ssl: (unexpected_eof|eof)|eof occurred in violation",
                  "The network connection to a remote service failed.", "connection"),
    SignatureRule("provider.unavailable", TRANSIENT, "STRONG",
                  r"(openverse|wikimedia|commons|nasa|archive\.org|internet archive|pexels|pixabay|gnews|gemini)"
                  r"[^.]{0,60}(timeout|timed out|unavailable|connection|http 5)",
                  "A content provider was unavailable.", "provider"),
    SignatureRule("net.timeout", TRANSIENT, "WEAK", r"timed out|timeout",
                  "An operation exceeded its time limit; the cause is not recorded.", "timeout"),
)

_PREFIX_RX = re.compile(r"^\s*\[(TRANSIENT|PERMANENT|DETERMINISTIC|POST_RENDER_FAILURE|RENDER_FAILURE)\]\s*", re.I)
DEADLINE_OUTCOME_RX = re.compile(r"^\s*(recovery window (expired|ended)|cycle is no longer in the active schedule)", re.I)
_TYPE_RX = re.compile(r"^\s*([A-Z][A-Za-z]+(?:Error|Exception|Identity|Failure|Blocked|Exit))\s*:")


@dataclass
class Classification:
    failure_class: str
    certainty: str
    basis: str                      # signature | signature+recurrence | recurrence | none
    rule_id: Optional[str]
    subtype: str
    mechanism: str                  # observation derived from the error text
    signature: str                  # stable id of the normalized error template
    template: str
    error_type: str
    retry_policy_class: Optional[str]   # what the existing retry policy said (reported, never changed)
    recurrence: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize(message: str) -> str:
    """The 3.32A template (paths, addresses, ids, numbers, quotes removed) without the retry-policy prefix or a
    leading exception type, so "[TRANSIENT] RuntimeError: X", "RuntimeError: X" and "X" share one signature."""
    from backend.app.services.operational_intelligence import error_template, pattern_key
    text = _PREFIX_RX.sub("", message or "")
    text = re.sub(r"^\s*[A-Z][A-Za-z]+(?:Error|Exception|Failure|Exit)\s*:\s*", "", text)
    return pattern_key(error_template(text))


def signature_of(message: str, cause: str = "") -> str:
    tpl = normalize(message)
    return "sig_" + hashlib.sha256(f"{cause}|{tpl}".encode("utf-8")).hexdigest()[:16]


def error_type_of(message: str, given: str = "") -> str:
    """The exception type written in the message wins; a generic placeholder ("Error") is not evidence of a type."""
    m = _TYPE_RX.match(_PREFIX_RX.sub("", message or ""))
    if m:
        return m.group(1)
    return given if given and given != "Error" else ""


def _retry_policy_class(message: str) -> Optional[str]:
    m = _PREFIX_RX.match(message or "")
    if m:
        return m.group(1).upper()
    try:
        from backend.app.scheduler.retry_policy import classify_error
        return classify_error(error_type=error_type_of(message), message=_PREFIX_RX.sub("", message or ""))[0]
    except Exception:
        return None


def match_signature(message: str, error_type: str = "") -> Optional[SignatureRule]:
    text = _PREFIX_RX.sub("", message or "")
    for rule in SIGNATURE_RULES:
        if rule.matches(text, error_type):
            return rule
    return None


def recurrence_stats(signature: str, history: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """history: failure occurrences (each {signature, job_id, at}) plus, per job, whether a later attempt
    succeeded (``recovered_jobs``). Pure counting; nothing inferred beyond the counts."""
    same = [h for h in history if h.get("signature") == signature]
    jobs = sorted({h.get("job_id") for h in same if h.get("job_id")})
    recovered = sorted({h.get("job_id") for h in same if h.get("recovered_later")})
    return {"occurrences": len(same), "jobs": len(jobs), "job_ids": jobs[:20], "recovered_jobs": len(recovered),
            "recovered_job_ids": recovered[:20],
            "first_seen": min((h.get("at") or "" for h in same), default=None) or None,
            "last_seen": max((h.get("at") or "" for h in same), default=None) or None}


def classify(message: str, *, error_type: str = "", cause: str = "",
             history: Sequence[Dict[str, Any]] = ()) -> Classification:
    message = message or ""
    etype = error_type_of(message, error_type)
    sig = signature_of(message, cause)
    tpl = normalize(message)
    rec = recurrence_stats(sig, history) if history else {"occurrences": 0, "jobs": 0, "recovered_jobs": 0}
    recurring_unrecovered = (rec["occurrences"] >= RECURRENCE_MIN_OCCURRENCES and rec["jobs"] >= RECURRENCE_MIN_JOBS
                             and rec["recovered_jobs"] == 0)
    rule = match_signature(message, etype)
    notes: List[str] = []
    if cause == "recovery_window_expired" or DEADLINE_OUTCOME_RX.search(message):
        # A reconciliation note recording that the job ran out of time: an outcome, not an error. It is never
        # escalated by recurrence (dozens of expired jobs share this note without sharing a cause).
        return Classification(UNKNOWN, "WEAK", "none", "outcome.recovery_window", "deadline_outcome",
                              "The job reached the end of its recovery window; this note records the outcome, not the "
                              "error that caused it.", sig, tpl, etype, _retry_policy_class(message), rec,
                              ["Not an error signature. Any underlying failure of this job is recorded as its own incident."])
    if not message.strip():
        return Classification(UNKNOWN, "WEAK", "none", None, "no_error_text",
                              "No error text was recorded for this failure.", sig, tpl, etype, None, rec,
                              ["Insufficient evidence: the failure has no recorded error message."])
    if rule is None:
        if recurring_unrecovered:
            notes.append(f"No known signature, but the same error recurred {rec['occurrences']} times across "
                         f"{rec['jobs']} jobs and no later attempt succeeded: suspected deterministic.")
            return Classification(DETERMINISTIC, "WEAK", "recurrence", None, "recurring_unknown",
                                  "The same unrecognised error keeps recurring.", sig, tpl, etype,
                                  _retry_policy_class(message), rec, notes)
        notes.append("No known signature and no recurrence evidence: insufficient evidence to classify.")
        return Classification(UNKNOWN, "WEAK", "none", None, "unmatched", "The error does not match a known signature.",
                              sig, tpl, etype, _retry_policy_class(message), rec, notes)
    klass, certainty, basis = rule.klass, rule.certainty, "signature"
    if klass == TRANSIENT:
        if recurring_unrecovered:
            notes.append(f"The signature looks transient, but it recurred {rec['occurrences']} times across "
                         f"{rec['jobs']} jobs and no later attempt succeeded: re-classified as suspected deterministic.")
            klass, certainty, basis = DETERMINISTIC, "WEAK", "signature+recurrence"
        elif rec.get("recovered_jobs"):
            notes.append(f"A later attempt succeeded for {rec['recovered_jobs']} affected job(s), which supports a "
                         "temporary cause.")
            certainty = "STRONG" if certainty == "WEAK" else certainty
            basis = "signature+recurrence"
    elif klass == DETERMINISTIC and rec.get("occurrences", 0) >= 2 and rec.get("recovered_jobs", 0) == 0:
        notes.append(f"Recurred {rec['occurrences']} times with no successful retry, consistent with a deterministic defect.")
        basis = "signature+recurrence"
    rp = _retry_policy_class(message)
    if rp and rp in ("TRANSIENT",) and klass != TRANSIENT:
        notes.append(f"The existing retry policy recorded this as {rp}; Stage 3.32D classifies it as {klass}. "
                     "The retry policy is not changed.")
    return Classification(klass, certainty, basis, rule.rule_id, rule.subtype, rule.mechanism, sig, tpl, etype, rp,
                          rec, notes)
