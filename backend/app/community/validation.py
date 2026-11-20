"""Independent validation of Community draft text and image.

Every check returns ``{"check", "ok", "severity", "detail"}`` where severity is:
* ``error``   -> the draft is REJECTED (cannot be approved);
* ``review``  -> NEEDS_OPERATOR_REVIEW (a claim or provenance a person must confirm);
* ``warning`` -> VALID_WITH_WARNINGS.
Only a draft with no failed check is VALID. Approval of anything else needs an explicit, recorded acknowledgement.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.app.community import content as C
from backend.app.community import images as I
from backend.app.community.store import NEEDS_OPERATOR_REVIEW, V_REJECTED, VALID, VALID_WITH_WARNINGS

MAX_CHARS = 500
MIN_CHARS = 40
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_IMAGE_BYTES = 8 * 1024
MIN_IMAGE_SIDE = 600
MAX_IMAGE_SIDE = 4096
DUPLICATE_DAYS = 180
NEAR_DUPLICATE = 0.6
SIMILAR = 0.45
SIMILAR_IMAGE_BITS = 6

_SPAM = re.compile(r"\b(subscribe|smash|like and share|like & share|click (here|the link)|link in (bio|description)|"
                   r"giveaway|free money|act now|limited time|don'?t miss|follow for more|turn on (post )?notifications|"
                   r"ring the bell|100 ?%|guaranteed)\b|!{2,}|\?{3,}", re.I)
_SENSATIONAL = re.compile(r"\b(shocking|mind[- ]?blowing|you won'?t believe|scientists (are )?baffled|first[- ]ever|"
                          r"never before|proven|proves|miracle|cure[sd]?|secret they|nobody knows|unbelievable|"
                          r"breaking)\b", re.I)
_INAPPROPRIATE = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|cunt\w*|nigg\w*|fag\w*|retard\w*|porn\w*|nsfw|"
                            r"kill yourself|kys)\b", re.I)
# Subjects where a short engagement post can easily read as taking sides or trivialising harm: never blocked,
# always routed to a person (NEEDS_OPERATOR_REVIEW).
_SENSITIVE = re.compile(r"\b(jew\w*|judais\w*|muslim\w*|islam\w*|christian\w*|hindu\w*|sikh\w*|buddhis\w*|religio\w*|"
                        r"ethnic\w*|racial|racism|genocide|holocaust|massacre\w*|terror\w*|suicide|abortion|"
                        r"election\w*|politic\w*|dissident\w*|war crimes?|slavery|refugee\w*|ancestry)\b", re.I)
_URL = re.compile(r"(?:https?://|www\.)[^\s)>\]]+", re.I)
_OWN_VIDEO_URL = re.compile(r"^https://(?:www\.)?(?:youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_\-]{11})$")
_NUMBER = re.compile(r"\d[\d,.]*")
_ENGAGEMENT = re.compile(r"\?|\b(comment|comments|reply|replies|vote|tell us|share your|your turn|over to you)\b", re.I)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _c(check: str, ok: bool, severity: str = "error", detail: str = "") -> Dict[str, Any]:
    return {"check": check, "ok": bool(ok), "severity": severity, "detail": detail[:300]}


def _norm_num(tok: str) -> str:
    return tok.replace(",", "").rstrip(".")


def sanitize_text(text: str) -> str:
    """Operator-edited text: control characters removed, newlines normalised, outer whitespace trimmed."""
    t = _CONTROL.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(ln.strip() for ln in t.split("\n")).strip()


def validate_text(text: str, *, corpus: str, allowed_numbers: Sequence[str] = (), public_video_ids: Sequence[str] = (),
                  recent: Sequence[Dict[str, Any]] = (), topic_key: str = "", now: Optional[datetime] = None,
                  draft_id: Optional[str] = None, opening_key: Optional[str] = None,
                  question_key: Optional[str] = None, cta_key: Optional[str] = None,
                  credit_line: Optional[str] = None, topic_title: str = "") -> List[Dict[str, Any]]:
    t = text or ""
    # The licence credit line is quoted metadata, not a claim: it is excluded from the claim checks below.
    claims_text = t.replace(credit_line, " ") if credit_line else t
    checks: List[Dict[str, Any]] = []
    checks.append(_c("not_empty", bool(t.strip()), "error", "the post has no text" if not t.strip() else ""))
    checks.append(_c("length", MIN_CHARS <= len(t) <= MAX_CHARS, "error",
                     f"{len(t)} characters (allowed {MIN_CHARS}-{MAX_CHARS})"))
    urls = _URL.findall(t)
    bad_urls = [u for u in urls if not (_OWN_VIDEO_URL.match(u) and _OWN_VIDEO_URL.match(u).group(1) in public_video_ids)]
    checks.append(_c("urls", not bad_urls, "error",
                     f"only links to our own public videos are allowed: {', '.join(bad_urls)[:200]}" if bad_urls else ""))
    spam = _SPAM.findall(t)
    checks.append(_c("spam_wording", not spam, "error", "spam-like wording" if spam else ""))
    letters = [ch for ch in t if ch.isalpha()]
    caps = sum(1 for ch in letters if ch.isupper()) / len(letters) if len(letters) >= 20 else 0.0
    checks.append(_c("excessive_capitals", caps <= 0.3, "warning", f"{caps:.0%} capital letters" if caps > 0.3 else ""))
    emojis = len(_EMOJI.findall(t))
    checks.append(_c("emoji_count", emojis <= 3, "warning", f"{emojis} emoji" if emojis > 3 else ""))
    bad = _INAPPROPRIATE.findall(t)
    checks.append(_c("inappropriate_content", not bad, "error", "inappropriate wording" if bad else ""))
    # unsupported claims: every number must be in the research corpus (or be an explicit quiz option)
    known = {_norm_num(x) for x in _NUMBER.findall(corpus or "")} | {_norm_num(x) for x in allowed_numbers}
    text_wo_urls = _URL.sub(" ", claims_text)
    invented = sorted({_norm_num(x) for x in _NUMBER.findall(text_wo_urls)} - known - {""})
    checks.append(_c("numbers_supported", not invented, "review",
                     f"numbers not found in the research: {', '.join(invented)[:200]}" if invented else ""))
    try:
        from backend.app.content.number_words import unsupported_claims
        spelled = unsupported_claims(_NUMBER.sub(" ", text_wo_urls), corpus or "")
    except Exception:
        spelled = []
    checks.append(_c("spelled_numbers_supported", not spelled, "review",
                     f"spelled-out quantities not in the research: {', '.join(spelled)[:200]}" if spelled else ""))
    sensitive = sorted({m.group(0).lower() for m in _SENSITIVE.finditer(claims_text + "\n" + (topic_title or ""))})
    checks.append(_c("sensitive_subject", not sensitive, "review",
                     f"sensitive subject ({', '.join(sensitive)[:120]}): a person should confirm the post is "
                     "appropriate for a community post" if sensitive else ""))
    sens = [m.group(0) for m in _SENSATIONAL.finditer(claims_text) if m.group(0).lower() not in (corpus or "").lower()]
    checks.append(_c("no_sensational_claims", not sens, "warning",
                     f"sensational or absolute wording not in the research: {', '.join(sens)[:200]}" if sens else ""))
    try:
        from backend.app.content.hook_evaluator import _GENERIC, _THROAT
        guide = bool(_GENERIC.search(t)) or bool(_THROAT.search(t))
    except Exception:
        guide = False
    checks.append(_c("channel_guidelines", not guide, "warning",
                     "generic opener or filler phrase (channel editorial rules)" if guide else ""))
    sents = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if len(s.strip()) > 12]
    words = re.findall(r"[a-z']+", t.lower())
    run = any(words[i] == words[i + 1] == words[i + 2] for i in range(len(words) - 2))
    rep = len(sents) != len(set(sents)) or run
    checks.append(_c("internal_repetition", not rep, "warning", "repeated sentence or word run" if rep else ""))
    checks.append(_c("engagement_element", bool(_ENGAGEMENT.search(t)), "warning",
                     "" if _ENGAGEMENT.search(t) else "no question or invitation to respond"))
    # history: duplicates and repetitive patterns
    fp = C.fingerprint(t)
    grams = C.trigrams(t)
    now = now or datetime.now().astimezone()
    dup, near, similar = None, None, None
    same_topic, same_open, same_q, same_cta = None, None, None, None
    opening = C.opening_of(t)
    for d in recent:
        if d.get("id") == draft_id:
            continue
        created = d.get("created_at")
        if created and now - created > timedelta(days=DUPLICATE_DAYS):
            continue
        other = d.get("text") or ""
        if d.get("text_fingerprint") == fp:
            dup = dup or d.get("id")
        j = C.jaccard(grams, C.trigrams(other))
        if j >= NEAR_DUPLICATE:
            near = near or (d.get("id"), round(j, 2))
        elif j >= SIMILAR:
            similar = similar or (d.get("id"), round(j, 2))
        recent7 = created and now - created <= timedelta(days=7)
        if topic_key and d.get("topic_key") == topic_key and created and now - created <= timedelta(days=C.TOPIC_REUSE_DAYS):
            same_topic = same_topic or d.get("id")
        if recent7 and opening and C.opening_of(other) == opening:
            same_open = same_open or d.get("id")
        if recent7 and question_key and d.get("question_key") == question_key:
            same_q = same_q or d.get("id")
        if created and now - created <= timedelta(days=3) and cta_key and d.get("cta_key") == cta_key:
            same_cta = same_cta or d.get("id")
    checks.append(_c("duplicate_draft", not dup, "error", f"identical to draft {dup}" if dup else ""))
    checks.append(_c("near_duplicate", not near, "error",
                     f"{int(near[1] * 100)}% overlap with draft {near[0]}" if near else ""))
    checks.append(_c("similar_wording", not similar, "warning",
                     f"{int(similar[1] * 100)}% overlap with draft {similar[0]}" if similar else ""))
    checks.append(_c("topic_not_recent", not same_topic, "review",
                     f"same topic as draft {same_topic} within {C.TOPIC_REUSE_DAYS} days" if same_topic else ""))
    checks.append(_c("opening_varied", not same_open, "warning",
                     f"same opening as draft {same_open} this week" if same_open else ""))
    checks.append(_c("question_varied", not same_q, "warning",
                     f"same question as draft {same_q} this week" if same_q else ""))
    checks.append(_c("cta_varied", not same_cta, "warning",
                     f"same call to action as draft {same_cta} in the last 3 days" if same_cta else ""))
    return checks


def validate_image(record: Optional[Dict[str, Any]], path: Optional[Path], *, images_root: Path, text: str,
                   recent_images: Sequence[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    if not record or path is None:
        return [_c("image_present", False, "review", "no image is attached")]
    exists = path.is_file() and I.inside(path, images_root) and not path.is_symlink()
    checks.append(_c("image_exists", exists, "error", "" if exists else "image file missing or outside the image folder"))
    if not exists:
        return checks
    size = path.stat().st_size
    checks.append(_c("image_file_size", MIN_IMAGE_BYTES <= size <= MAX_IMAGE_BYTES, "error",
                     f"{size} bytes (allowed {MIN_IMAGE_BYTES}-{MAX_IMAGE_BYTES})"))
    try:
        st = I.image_stats(path)
    except I.ImageError as exc:
        checks.append(_c("image_decodes", False, "error", f"corrupt or unreadable: {exc}"))
        return checks
    checks.append(_c("image_decodes", True))
    checks.append(_c("image_format", st["format"] in ("JPEG", "PNG"), "error", f"format {st['format']}"))
    w, h = st["width"], st["height"]
    checks.append(_c("image_dimensions", MIN_IMAGE_SIDE <= min(w, h) and max(w, h) <= MAX_IMAGE_SIDE, "error", f"{w}x{h}"))
    checks.append(_c("image_aspect_ratio", abs(w / h - 1.0) <= 0.01 if h else False, "error",
                     f"{w}x{h}; Community images use 1:1"))
    checks.append(_c("image_not_blank", st["stddev"] >= 8, "warning", f"low detail (stddev {st['stddev']})"))
    sha = I.sha256_file(path)
    checks.append(_c("image_integrity", sha == record.get("sha256"), "error",
                     "" if sha == record.get("sha256") else "file changed after it was recorded"))
    dup = next((r for r in recent_images if r.get("sha256") == sha), None)
    checks.append(_c("image_not_duplicate", dup is None, "error",
                     f"identical image used by draft {dup['draft_id']}" if dup else ""))
    sim = next((r for r in recent_images if r.get("sha256") != sha and I.hamming(r.get("dhash"), st["dhash"]) <= SIMILAR_IMAGE_BITS), None)
    checks.append(_c("image_not_similar", sim is None, "warning",
                     f"visually similar to the image of draft {sim['draft_id']}" if sim else ""))
    prov = record.get("provenance") or {}
    has_prov = bool(record.get("method")) and bool(prov.get("method")) and bool(prov.get("source"))
    checks.append(_c("image_provenance", has_prov, "review", "" if has_prov else "image source is not recorded"))
    if record.get("method") == "licensed_asset":
        lic = I.normalise_licence(record.get("licence"))
        checks.append(_c("image_licence", lic is not None, "error",
                         f"licence {record.get('licence')!r} is not accepted for reuse" if lic is None else lic))
        if lic in I.ATTRIBUTION_REQUIRED:
            credit = (record.get("attribution") or "").strip()
            ok = bool(credit) and credit in (text or "")
            checks.append(_c("image_attribution", ok, "error",
                             "" if ok else "the licence requires an image credit in the post text"))
    return checks


def outcome(checks: Sequence[Dict[str, Any]]) -> str:
    failed = [c for c in checks if not c["ok"]]
    if any(c["severity"] == "error" for c in failed):
        return V_REJECTED
    if any(c["severity"] == "review" for c in failed):
        return NEEDS_OPERATOR_REVIEW
    if failed:
        return VALID_WITH_WARNINGS
    return VALID


def summary(text_checks: Sequence[Dict[str, Any]], image_checks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    allc = list(text_checks) + list(image_checks)
    return {"status": outcome(allc), "text": list(text_checks), "image": list(image_checks),
            "errors": [c for c in allc if not c["ok"] and c["severity"] == "error"],
            "reviews": [c for c in allc if not c["ok"] and c["severity"] == "review"],
            "warnings": [c for c in allc if not c["ok"] and c["severity"] == "warning"]}
