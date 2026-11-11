"""
Visual Diversity, Image Repetition Control & Storyboard Audit Policy.
Phase 5, Stage 3.21J Standard.

Enforces:
- Maximum reuse of any single image asset <= 2 times per video.
- Canonical identity tracking via content SHA-256 hash and canonical source asset ID
  (ignoring filename differences and crop/zoom/recolor variations).
- Meaningful visual diversity target (>= 12 distinct visual assets for 18-25 beat Shorts).
- Strict prohibition of stick figures, character rigs, cartoon hosts, and irrelevant animations.
- Fail-closed verification that every referenced asset & animation exists locally in workspace.
"""

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.content.creative_identity_policy import PROHIBITED_CHARACTER_TOKENS

MAX_IMAGE_REUSE = 2
MIN_DISTINCT_ASSETS_FULL_SHORT = 12
PREFERRED_DISTINCT_ASSETS_FULL_SHORT = 15

IMAGE_VISUAL_TYPES: Set[str] = {
    "historical_photo",
    "documentary_image",
    "generated_image",
}

ANIMATION_VISUAL_TYPES: Set[str] = {
    "animated_map",
    "animated_diagram",
    "animated_timeline",
    "animated_infographic",
    "data_visualization",
    "motion_graphic",
    "typography_sequence",
}

APPROVED_VISUAL_TYPES: Set[str] = IMAGE_VISUAL_TYPES | ANIMATION_VISUAL_TYPES | {"outro"}

REQUIRED_BEAT_FIELDS: Tuple[str, ...] = (
    "beat_id",
    "start_sec",
    "end_sec",
    "script_segment",
    "visual_type",
    "visual_description",
    "asset_path",
    "asset_hash",
    "visual_generation_instructions",
    "transition",
    "relevance_score",
    "reuse_count",
)

_IRRELEVANT_ANIMATION_PATTERNS: Tuple[str, ...] = (
    "random explosion",
    "generic sparkle",
    "filler confetti",
    "unrelated stock animation",
    "dancing character",
    "decorative swirl",
)


def compute_asset_sha256(filepath: Optional[str]) -> str:
    if not filepath or not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def resolve_canonical_asset_key(beat: Dict[str, Any]) -> str:
    """
    Resolves canonical asset identity using SHA-256 content hash first,
    then canonical/source asset ID, ignoring crop/zoom/filename differences.
    """
    asset_path = beat.get("asset_path") or beat.get("source_asset_path") or ""
    asset_hash = (beat.get("asset_hash") or "").strip().lower()
    if not asset_hash and asset_path and os.path.isfile(asset_path):
        asset_hash = compute_asset_sha256(asset_path)
    if asset_hash:
        return f"sha256:{asset_hash}"

    canonical_id = (
        beat.get("canonical_asset_id")
        or beat.get("source_asset_id")
        or beat.get("asset_id")
        or ""
    )
    if canonical_id:
        # Strip crop/zoom variant suffixes so cropped copies of the same source count together
        base_id = re.sub(r"(_zoom|_crop|_pan|_tint|_flip)[0-9a-zA-Z_]*$", "", str(canonical_id).strip().lower())
        return f"id:{base_id}"

    if asset_path:
        norm_name = re.sub(r"(_zoom|_crop|_pan|_tint)[0-9a-zA-Z_]*", "", os.path.basename(str(asset_path)).lower())
        return f"path:{norm_name}"

    return f"beat:{beat.get('beat_id', 'unknown')}"


@dataclass
class DiversityAuditResult:
    is_valid: bool
    total_beats: int
    distinct_assets: int
    unique_images: int
    unique_animations: int
    max_reuse_observed: int
    reuse_counts: Dict[str, int] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class VisualDiversityValidator:
    """Validates storyboard allocations against Stage 3.21J visual diversity and persistence rules."""

    @staticmethod
    def validate_storyboard(
        beats: List[Dict[str, Any]],
        *,
        topic_title: str = "",
        max_image_reuse: int = MAX_IMAGE_REUSE,
        check_files_on_disk: bool = True,
        min_distinct_assets: Optional[int] = None,
        workspace_root: Optional[str] = None,
    ) -> DiversityAuditResult:
        violations: List[str] = []
        warnings: List[str] = []

        if not beats:
            return DiversityAuditResult(
                is_valid=False,
                total_beats=0,
                distinct_assets=0,
                unique_images=0,
                unique_animations=0,
                max_reuse_observed=0,
                violations=["Storyboard has zero beats."],
            )

        asset_counts: Counter = Counter()
        image_keys: Set[str] = set()
        animation_keys: Set[str] = set()
        all_keys: Set[str] = set()
        prev_image_key: Optional[str] = None

        topic_tokens = {
            w for w in re.findall(r"[a-z0-9]{4,}", (topic_title or "").lower())
        }

        for idx, raw_beat in enumerate(beats):
            beat = dict(raw_beat) if isinstance(raw_beat, dict) else vars(raw_beat)
            beat_id = beat.get("beat_id") or beat.get("id") or f"beat_{idx + 1:02d}"
            vtype = (beat.get("visual_type") or "").strip().lower()

            if vtype not in APPROVED_VISUAL_TYPES:
                violations.append(f"[{beat_id}] Unsupported or missing visual_type '{vtype}'.")

            if vtype == "outro":
                continue

            # Required structural metadata checks
            for req_field in REQUIRED_BEAT_FIELDS:
                if req_field not in beat or beat.get(req_field) is None or (isinstance(beat.get(req_field), str) and not beat.get(req_field).strip() and req_field != "asset_hash"):
                    if req_field == "reuse_count" and beat.get(req_field) == 0:
                        continue
                    if req_field == "relevance_score" and beat.get(req_field) == 0:
                        continue
                    violations.append(f"[{beat_id}] Missing required beat metadata field '{req_field}'.")

            # Prohibited character / irrelevant animation gate
            searchable = " ".join([
                str(beat.get("visual_description") or ""),
                str(beat.get("visual_generation_instructions") or ""),
                str(beat.get("script_segment") or ""),
                str(beat.get("asset_path") or ""),
            ]).lower()
            for banned in PROHIBITED_CHARACTER_TOKENS:
                if banned in searchable:
                    violations.append(f"[{beat_id}] Prohibited stick-figure/cartoon token '{banned}' detected.")

            if vtype in ANIMATION_VISUAL_TYPES:
                for bad_anim in _IRRELEVANT_ANIMATION_PATTERNS:
                    if bad_anim in searchable:
                        violations.append(f"[{beat_id}] Irrelevant decorative animation motif detected: '{bad_anim}'.")

                rel_score = float(beat.get("relevance_score", 0.0) or 0.0)
                if rel_score < 0.25:
                    violations.append(f"[{beat_id}] Animation relevance_score ({rel_score:.2f}) below minimum 0.25.")

                # Verify animation description/instructions tie to script segment or topic
                seg_tokens = set(re.findall(r"[a-z0-9]{4,}", str(beat.get("script_segment") or "").lower()))
                inst_tokens = set(re.findall(
                    r"[a-z0-9]{4,}",
                    f"{beat.get('visual_description', '')} {beat.get('visual_generation_instructions', '')}".lower(),
                ))
                overlap = (seg_tokens | topic_tokens) & inst_tokens
                if seg_tokens and topic_tokens and not overlap and rel_score < 0.5:
                    violations.append(
                        f"[{beat_id}] Animation '{vtype}' lacks explicit relationship to script segment or topic."
                    )

            asset_path = beat.get("asset_path") or ""
            asset_hash = (beat.get("asset_hash") or "").strip().lower()

            if check_files_on_disk:
                if not asset_path or not os.path.isfile(asset_path) or os.path.getsize(asset_path) < 128:
                    violations.append(f"[{beat_id}] Missing or empty canonical asset file: '{asset_path}'.")
                else:
                    disk_hash = compute_asset_sha256(asset_path)
                    if asset_hash and disk_hash and asset_hash != disk_hash:
                        violations.append(
                            f"[{beat_id}] Asset SHA-256 mismatch for '{asset_path}': expected {asset_hash[:12]}, got {disk_hash[:12]}."
                        )
                    if not asset_hash and disk_hash:
                        beat["asset_hash"] = disk_hash
                    if workspace_root:
                        norm_ws = os.path.abspath(workspace_root).lower()
                        norm_ap = os.path.abspath(asset_path).lower()
                        if norm_ws not in norm_ap:
                            violations.append(
                                f"[{beat_id}] Asset '{asset_path}' stored outside canonical workspace '{workspace_root}'."
                            )

            canonical_key = resolve_canonical_asset_key(beat)
            all_keys.add(canonical_key)

            if vtype in IMAGE_VISUAL_TYPES or (vtype not in ANIMATION_VISUAL_TYPES and asset_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))):
                asset_counts[canonical_key] += 1
                image_keys.add(canonical_key)
                if prev_image_key == canonical_key:
                    warnings.append(f"[{beat_id}] Consecutive image repeat for {canonical_key}.")
                prev_image_key = canonical_key
            else:
                asset_counts[canonical_key] += 1
                if vtype in ANIMATION_VISUAL_TYPES:
                    animation_keys.add(canonical_key)
                prev_image_key = None

        max_reuse = max(asset_counts.values()) if asset_counts else 0
        # Enforce max_image_reuse strictly on every asset (images <= 2, and flag > max_image_reuse)
        for key, cnt in asset_counts.items():
            if cnt > max_image_reuse:
                violations.append(
                    f"Image/Asset repetition policy violation: canonical asset '{key}' used {cnt} times (maximum allowed: {max_image_reuse})."
                )

        content_beats = [b for b in beats if (b.get("visual_type") or "").lower() != "outro"]
        target_min = min_distinct_assets
        if target_min is None and len(content_beats) >= 16:
            target_min = MIN_DISTINCT_ASSETS_FULL_SHORT
        if target_min and len(all_keys) < target_min:
            violations.append(
                f"Insufficient visual diversity: {len(all_keys)} distinct visual assets across {len(content_beats)} beats (minimum required: {target_min})."
            )

        return DiversityAuditResult(
            is_valid=(len(violations) == 0),
            total_beats=len(content_beats),
            distinct_assets=len(all_keys),
            unique_images=len(image_keys),
            unique_animations=len(animation_keys),
            max_reuse_observed=max_reuse,
            reuse_counts=dict(asset_counts),
            violations=violations,
            warnings=warnings,
        )
