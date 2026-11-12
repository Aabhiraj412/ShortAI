"""
Visual Diversity, Image Repetition Control & Storyboard Audit Policy.
Phase 5, Stage 3.21N Standard.

Enforces:
- Prefer a single use of each source photograph; permit justified reuse only within policy limits.
- Canonical identity tracking via content SHA-256 hash and canonical source asset ID
  (ignoring filename differences and crop/zoom/recolor variations).
- Meaningful visual diversity target (>= 12 distinct visual assets for 18-25 beat Shorts).
- Strict prohibition of stick figures, character rigs, cartoon hosts, and irrelevant animations.
- Fail-closed verification that every referenced asset & animation exists locally in workspace.
"""

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps, ImageStat

from backend.app.content.creative_identity_policy import PROHIBITED_CHARACTER_TOKENS

MAX_IMAGE_REUSE = 2
PREFERRED_IMAGE_REUSE = 1
MAX_PERCEPTUAL_HASH_DISTANCE = 6
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


def normalize_source_identity(value: Optional[str]) -> str:
    """Canonicalizes source URLs and IDs so filename or URL variants do not evade deduplication."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        path = unquote(parsed.path)
        path = re.sub(r"/thumb/", "/", path, flags=re.IGNORECASE)
        path = re.sub(r"(\.(?:jpe?g|png|gif|webp|svg))/.+$", r"\1", path, flags=re.IGNORECASE)
        return f"url:{parsed.netloc.lower()}{path}"
    return re.sub(r"(_zoom|_crop|_pan|_tint|_flip)[0-9a-zA-Z_]*$", "", raw.lower())


_DCT_COSINES = tuple(
    tuple(math.cos((2 * sample + 1) * frequency * math.pi / 64.0) for sample in range(32))
    for frequency in range(8)
)


def _perceptual_hash(image: Image.Image) -> int:
    gray = ImageOps.fit(
        image.convert("L"),
        (32, 32),
        method=Image.Resampling.LANCZOS,
    )
    pixels = list(gray.tobytes())
    coefficients: List[float] = []
    for v in range(8):
        for u in range(8):
            total = 0.0
            for y in range(32):
                row_offset = y * 32
                y_factor = _DCT_COSINES[v][y]
                for x in range(32):
                    total += pixels[row_offset + x] * _DCT_COSINES[u][x] * y_factor
            coefficients.append(total)
    median = sorted(coefficients[1:])[len(coefficients[1:]) // 2]
    bits = 0
    for coefficient in coefficients[1:]:
        bits = (bits << 1) | int(coefficient > median)
    return bits


@lru_cache(maxsize=256)
def _cached_image_fingerprints(path: str, file_size: int, modified_ns: int) -> Tuple[int, ...]:
    del file_size, modified_ns
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = image.size
    hashes: List[int] = []
    for scale in (1.0, 0.82, 0.64):
        crop_width = max(1, int(width * scale))
        crop_height = max(1, int(height * scale))
        positions = ((0.0, 0.0),) if scale == 1.0 else (
            (0.0, 0.0), (0.5, 0.0), (1.0, 0.0),
            (0.0, 0.5), (0.5, 0.5), (1.0, 0.5),
            (0.0, 1.0), (0.5, 1.0), (1.0, 1.0),
        )
        for x_fraction, y_fraction in positions:
            left = int((width - crop_width) * x_fraction)
            top = int((height - crop_height) * y_fraction)
            crop = image.crop((left, top, left + crop_width, top + crop_height))
            hashes.append(_perceptual_hash(crop))
    return tuple(hashes)


def compute_image_perceptual_hashes(filepath: Optional[str]) -> Tuple[int, ...]:
    """Returns full-frame and multi-scale crop hashes for resize, tint, and crop detection."""
    if not filepath or not os.path.isfile(filepath):
        return ()
    path = os.path.abspath(filepath)
    stat = os.stat(path)
    return _cached_image_fingerprints(path, stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=256)
def _cached_image_texture(path: str, file_size: int, modified_ns: int) -> float:
    del file_size, modified_ns
    with Image.open(path) as source:
        gray = ImageOps.fit(source.convert("L"), (32, 32), method=Image.Resampling.LANCZOS)
    return ImageStat.Stat(gray).stddev[0]


def find_image_duplicate_reason(
    first_path: Optional[str],
    second_path: Optional[str],
    *,
    first_source_identity: Optional[str] = None,
    second_source_identity: Optional[str] = None,
    max_hash_distance: int = MAX_PERCEPTUAL_HASH_DISTANCE,
) -> Optional[str]:
    """Returns why two image assets represent the same source, or None when no match is found."""
    first_identity = normalize_source_identity(first_source_identity)
    second_identity = normalize_source_identity(second_source_identity)
    if first_identity and first_identity == second_identity:
        return "source_identity"
    first_sha = compute_asset_sha256(first_path)
    second_sha = compute_asset_sha256(second_path)
    if first_sha and first_sha == second_sha:
        return "sha256"
    first_hashes = compute_image_perceptual_hashes(first_path)
    second_hashes = compute_image_perceptual_hashes(second_path)
    if first_hashes and second_hashes:
        first_stat = os.stat(first_path)
        second_stat = os.stat(second_path)
        first_texture = _cached_image_texture(os.path.abspath(first_path), first_stat.st_size, first_stat.st_mtime_ns)
        second_texture = _cached_image_texture(os.path.abspath(second_path), second_stat.st_size, second_stat.st_mtime_ns)
        if first_texture < 3.0 and second_texture < 3.0:
            return None
        distance = min((a ^ b).bit_count() for a in first_hashes for b in second_hashes)
        if distance <= max_hash_distance:
            return f"perceptual_hash:{distance}"
    return None


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
    unique_photographic_assets: int = 0
    unique_video_assets: int = 0
    unique_maps: int = 0
    unique_diagrams: int = 0
    generated_images: int = 0
    animations: int = 0
    reused_source_images: int = 0
    near_duplicate_groups: int = 0
    max_source_image_reuse: int = 0
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class VisualDiversityValidator:
    """Validates storyboard allocations against Stage 3.21N source-image diversity rules."""

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
        video_keys: Set[str] = set()
        map_keys: Set[str] = set()
        diagram_keys: Set[str] = set()
        generated_keys: Set[str] = set()
        all_keys: Set[str] = set()
        source_image_records: List[Dict[str, Any]] = []
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
                if vtype == "generated_image":
                    generated_keys.add(canonical_key)
                if prev_image_key == canonical_key:
                    warnings.append(f"[{beat_id}] Consecutive image repeat for {canonical_key}.")
                prev_image_key = canonical_key
            else:
                asset_counts[canonical_key] += 1
                if vtype in ANIMATION_VISUAL_TYPES:
                    animation_keys.add(canonical_key)
                    video_keys.add(canonical_key)
                prev_image_key = None

            if "map" in vtype or "map" in str(beat.get("required_asset_type") or "").lower():
                map_keys.add(canonical_key)
            if "diagram" in vtype or "diagram" in str(beat.get("required_asset_type") or "").lower():
                diagram_keys.add(canonical_key)

            is_source_photo = (
                vtype in {"historical_photo", "documentary_image"}
                or bool(beat.get("source_image_sha256"))
                or str(beat.get("sourcing_priority") or "").startswith(("A_", "C_"))
            )
            if is_source_photo:
                source_path = (
                    beat.get("source_asset_path")
                    or beat.get("source_image_path")
                    or (asset_path if vtype in {"historical_photo", "documentary_image"} else "")
                )
                source_identity = (
                    beat.get("source_image_identity")
                    or beat.get("original_source_id")
                    or beat.get("source_asset_id")
                    or beat.get("source_url")
                    or beat.get("source_image_sha256")
                    or beat.get("canonical_asset_id")
                    or ""
                )
                if not source_identity and source_path:
                    source_identity = compute_asset_sha256(source_path)
                source_image_records.append({
                    "beat_id": beat_id,
                    "beat": beat,
                    "path": source_path,
                    "identity": source_identity,
                    "sha256": beat.get("source_image_sha256") or compute_asset_sha256(source_path),
                })

        max_reuse = max(asset_counts.values()) if asset_counts else 0
        # Enforce max_image_reuse strictly on every asset (images <= 2, and flag > max_image_reuse)
        for key, cnt in asset_counts.items():
            if cnt > max_image_reuse:
                violations.append(
                    f"Image/Asset repetition policy violation: canonical asset '{key}' used {cnt} times (maximum allowed: {max_image_reuse})."
                )

        parent = list(range(len(source_image_records)))

        def find_root(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first: int, second: int) -> None:
            first_root, second_root = find_root(first), find_root(second)
            if first_root != second_root:
                parent[second_root] = first_root

        near_duplicate_pairs: Set[Tuple[int, int]] = set()
        for first_idx, first in enumerate(source_image_records):
            for second_idx in range(first_idx + 1, len(source_image_records)):
                second = source_image_records[second_idx]
                reason = find_image_duplicate_reason(
                    first["path"],
                    second["path"],
                    first_source_identity=first["identity"],
                    second_source_identity=second["identity"],
                )
                if reason:
                    union(first_idx, second_idx)
                    if reason.startswith("perceptual_hash:") and first["sha256"] != second["sha256"]:
                        near_duplicate_pairs.add((first_idx, second_idx))

        source_image_groups: Dict[int, List[int]] = {}
        for index in range(len(source_image_records)):
            source_image_groups.setdefault(find_root(index), []).append(index)

        reused_source_images = 0
        max_source_image_reuse = 0
        near_duplicate_roots = {
            find_root(first_idx)
            for first_idx, _ in near_duplicate_pairs
        }
        for root, indices in source_image_groups.items():
            occurrence_count = len(indices)
            max_source_image_reuse = max(max_source_image_reuse, occurrence_count)
            reused_source_images += max(0, occurrence_count - 1)
            if occurrence_count > max_image_reuse:
                violations.append(
                    f"Source photograph group is used {occurrence_count} times (maximum allowed: {max_image_reuse})."
                )
            for repeated_idx in indices[1:]:
                repeated = source_image_records[repeated_idx]
                beat = repeated["beat"]
                if occurrence_count > 1 and not (
                    str(beat.get("reuse_reason") or "").strip()
                    and str(beat.get("narrative_purpose") or "").strip()
                    and beat.get("reuse_alternatives_exhausted") is True
                ):
                    violations.append(
                        f"[{repeated['beat_id']}] Reused source photograph needs a narrative reason and confirmation that relevant alternatives were exhausted."
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
            max_reuse_observed=max(max_reuse, max_source_image_reuse),
            reuse_counts=dict(asset_counts),
            unique_photographic_assets=len(source_image_groups),
            unique_video_assets=len(video_keys),
            unique_maps=len(map_keys),
            unique_diagrams=len(diagram_keys),
            generated_images=len(generated_keys),
            animations=len(animation_keys),
            reused_source_images=reused_source_images,
            near_duplicate_groups=len(near_duplicate_roots),
            max_source_image_reuse=max_source_image_reuse,
            violations=violations,
            warnings=warnings,
        )
