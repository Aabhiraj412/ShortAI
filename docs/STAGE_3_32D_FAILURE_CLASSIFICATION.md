# Stage 3.32D: Failure Classification

**Source:** `backend/app/autonomy/self_healing/classification.py`.

**Six classes:** TRANSIENT, DETERMINISTIC, RESOURCE, CONFIGURATION, DATA, UNKNOWN.

Each classification records:
- its **certainty**: CONFIRMED (the error text states the mechanism), STRONG (a well-known signature of that class) or WEAK;
- its **basis**: `signature`, `signature+recurrence`, `recurrence` or `none`;
- the rule that matched;
- the signature;
- the existing retry policy's own class, for comparison only.

**The retry policy is never changed.**

## 1. Signature rules (first match wins)

| Class | Rule | Certainty | Matches (abridged) |
|---|---|---|---|
| DETERMINISTIC | ffmpeg.pad_smaller_than_input | CONFIRMED | "padded dimensions cannot be smaller than input" |
| DETERMINISTIC | ffmpeg.invalid_filter | STRONG | error parsing/initializing filter, no such filter, failed to configure pad |
| DETERMINISTIC | media.invalid_dimensions | STRONG | width/height not divisible by 2, invalid frame size |
| DETERMINISTIC | quality.qc_rule | CONFIRMED | QC failure, BLOCKED_QC, "duration … is outside" |
| DETERMINISTIC | renderer.compliance | CONFIRMED | renderer-compliance gate |
| DETERMINISTIC | content.integrity_gate | CONFIRMED | integrity gate, DuplicateCreativeIdentity |
| DETERMINISTIC | content.validation | STRONG | invalid research, script validation |
| DETERMINISTIC | code.defect | STRONG | NameError, ImportError, AttributeError, TypeError, … (exception type) |
| RESOURCE | resource.disk_full | CONFIRMED | no space left, errno 28, WinError 112 |
| RESOURCE | resource.memory | CONFIRMED | MemoryError, out of memory, paging file too small |
| RESOURCE | resource.file_lock | STRONG | WinError 32/33, "being used by another process", sharing violation |
| RESOURCE | resource.file_handles | CONFIRMED | too many open files, errno 24 |
| RESOURCE | resource.youtube_quota | CONFIRMED | quota exceeded, "daily … quota … reached" (external capacity) |
| CONFIGURATION | config.oauth_scope | CONFIRMED | insufficient_scope |
| CONFIGURATION | config.oauth_grant | CONFIRMED | invalid_grant, token expired or revoked, invalid_client |
| CONFIGURATION | config.credentials | STRONG | missing credentials, client_secrets, API key not valid, 401/403 |
| CONFIGURATION | config.missing_setting | STRONG | not configured, missing required setting |
| DATA | data.corrupt_media | CONFIRMED | moov atom not found, invalid data found, cannot identify image |
| DATA | data.missing_asset | STRONG | file not found for a media/asset file, missing required asset |
| DATA | data.invalid_metadata | STRONG | JSONDecodeError, malformed metadata |
| TRANSIENT | net.http_5xx | STRONG | HTTP/status 500/502/503/504 (with context; a bare number does not match) |
| TRANSIENT | net.rate_limited | STRONG | 429, too many requests |
| TRANSIENT | net.connection | STRONG | connection reset/refused, DNS failure, SSL EOF |
| TRANSIENT | provider.unavailable | STRONG | a named provider plus timeout or unavailability |
| TRANSIENT | net.timeout | WEAK | "timed out" / "timeout" without further context |

## 2. Recurrence (distinguishes deterministic from temporary)

Recurrence is counted from the same signature in the 7-day history, as of the failure's time.

| Situation | Result |
|---|---|
| TRANSIENT-looking signature, ≥ 3 occurrences across ≥ 2 jobs, no affected job ever completed | **DETERMINISTIC**, WEAK, basis `signature+recurrence` ("suspected deterministic") |
| TRANSIENT signature, and a later attempt succeeded for an affected job | stays **TRANSIENT**; WEAK becomes STRONG |
| No signature, same recurrence as above | **DETERMINISTIC**, WEAK, basis `recurrence` |
| No signature, no recurrence | **UNKNOWN**: "insufficient evidence" |
| No error text | **UNKNOWN** (`no_error_text`); nothing is proposed |
| "Recovery window expired …" / "Cycle is no longer in the active schedule" | **UNKNOWN** (`deadline_outcome`): an outcome note, not an error, and never escalated by recurrence |

**A failure is never TRANSIENT merely because it happened in production.** The existing retry policy defaults unknown errors to TRANSIENT; Stage 3.32D reports them as UNKNOWN.

## 3. Stored text

Error messages, templates and error types are passed through the control plane's `redact` before they are stored or returned. Secret-looking keys and values, such as `?key=AIza…`, become `[redacted]` (final audit F4).

## 4. Signature

`sig_` + SHA-256 of (3.32A cause, normalized template), where normalization removes the retry-policy prefix, a leading exception type, paths, addresses, ids, numbers and quoted strings. So the following all produce one signature:
- `[TRANSIENT] RuntimeError: X`
- `RuntimeError: X`
- `X`

## 5. Live examples (dry run on a scratch copy of the 2026-11-20 backup)

| Error | Retry policy said | 3.32D | Certainty |
|---|---|---|---|
| FFmpeg "Padded dimensions cannot be smaller than input" (3 attempts, never recovered) | TRANSIENT | DETERMINISTIC | CONFIRMED |
| [WinError 32] file in use by another process | PERMANENT | RESOURCE (file lock) | STRONG |
| "Daily YouTube API quota limit reached" (15 jobs, later published) | TRANSIENT | RESOURCE (external quota) | CONFIRMED |
| "Recovery window expired at …; reconciliation occurred …" (27 jobs) | TRANSIENT | UNKNOWN (deadline outcome) | WEAK |
