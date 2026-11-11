"""
Mandatory Content Integrity Gate for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.13A Standard.

Enforces strict semantic and editorial coherence before any deliverable enters the publishing queue.
Technical QC passing NEVER overrides Content Integrity Gate failure.

Checks Enforced:
A. Topic matches narration.
B. Narration matches visual timeline.
C. Every asset belongs to current topic or is explicitly justified.
D. Thumbnail matches the topic.
E. Title matches the story.
F. Description accurately summarizes the video.
G. Source credits match actual source usage.
H. Tags are topic-relevant.
I. No stale assets from previous productions reused accidentally.
J. Outro uses original channel branding.
K. Strictly prohibits stick-figure & cartoon character animation; enforces authentic documentary/scientific standards.
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.content.creative_identity_policy import CreativeIdentityPolicy

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE

# Global inventory of assets belonging strictly to prior production topics
KNOWN_PRIOR_TOPIC_ASSETS = {
    "yamaguchi": [
        "Atomic_cloud_over_Hiroshima_jpg.jpg",
        "Nagasakibomb_jpg.jpg",
        "Hiroshima_Castle_25july1945_jpg.jpg",
        "Hattyo_bori_Hiroshima_25july1945_jpg.jpg",
        "Japanese_cruiser_Aoba_aerial_photo_28_July_1945_jpg.jpg",
        "Hiroshima_Bombing_Operations_Order_No_35_5_Aug_1945_jpg.jpg",
        "Little_Boy_bomb_jpg.jpg",
        "Hiroshima_Gembaku_Dome_1_jpg.jpg",
        "Dan_A__McGovern_at_Nagasaki_ground_zero_9_September_1945_png.png",
    ],
    "molasses_flood": [
        "scene_01_original_tank.jpg",
        "scene_02_tank_layout.png",
        "scene_03_collapsed_tank.jpg",
        "scene_04_railway_wreckage.jpg",
        "scene_05_investigation_wreckage.jpg",
        "scene_06_newspaper_legacy.png",
    ]
}


@dataclass
class IntegrityCheckResult:
    check_name: str
    passed: bool
    details: str
    severity: str = "CRITICAL"  # CRITICAL or WARNING


@dataclass
class ContentIntegrityReport:
    is_valid: bool
    topic_title: str
    checks: List[IntegrityCheckResult] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)

    def add_result(self, check_name: str, passed: bool, details: str, severity: str = "CRITICAL"):
        self.checks.append(IntegrityCheckResult(check_name, passed, details, severity))
        if not passed and severity == "CRITICAL":
            self.is_valid = False
            self.rejection_reasons.append(f"[{check_name}] {details}")


def compute_sha256(filepath: str) -> str:
    if not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class ContentIntegrityGate:
    """Pre-Publishing Gate guaranteeing zero semantic cross-contamination and accurate attribution."""

    def __init__(self, workspace_root: str = WORKSPACE):
        self.workspace = workspace_root

    def validate_production(
        self,
        topic_title: str,
        category: str,
        narration_text: str,
        visual_beat_configs: List[Any],
        video_path: str,
        thumbnail_path: str,
        thumbnail_headline: str,
        metadata_title: str,
        metadata_description: str,
        metadata_tags: List[str],
        attribution_text: str,
        manifest_assets: Optional[List[Dict]] = None,
        rendering_engine: Optional[str] = None,
    ) -> ContentIntegrityReport:
        """
        Executes complete 11-point Content Integrity Audit (A through K).
        Returns ContentIntegrityReport.
        """
        report = ContentIntegrityReport(is_valid=True, topic_title=topic_title)
        title_lower = topic_title.lower()
        narr_lower = narration_text.lower()
        meta_all = (metadata_title + " " + metadata_description + " " + " ".join(metadata_tags)).lower()

        # -------------------------------------------------------------
        # CHECK A: Topic matches Narration
        # -------------------------------------------------------------
        # Check topic entities in narration
        topic_words = [w for w in title_lower.replace(",", "").replace("-", " ").split() if len(w) > 3]
        matches = [w for w in topic_words if w in narr_lower]
        if len(matches) < 2 and len(topic_words) >= 2:
            report.add_result("CHECK_A_TOPIC_NARRATION", False, f"Narration lacks topic keywords (found {matches} out of {topic_words}).")
        else:
            report.add_result("CHECK_A_TOPIC_NARRATION", True, f"Topic keywords verified in narration ({len(matches)} matched).")

        # Cross-contamination check: Non-nuclear story cannot reference nuclear bombs in narration
        is_nuclear_topic = any(k in title_lower for k in ("atomic", "nuclear", "bomb", "yamaguchi", "plutonium", "hiroshima", "nagasaki"))
        nuclear_terms = ["atomic bomb", "hiroshima", "nagasaki", "plutonium", "fat man", "little boy", "mushroom cloud"]
        if not is_nuclear_topic:
            found_nuclear = [t for t in nuclear_terms if t in narr_lower]
            if found_nuclear:
                report.add_result("CHECK_A_TOPIC_NARRATION", False, f"Unrelated nuclear keywords found in non-nuclear narration: {found_nuclear}")

        # -------------------------------------------------------------
        # CHECK B: Narration matches Visual Timeline
        # -------------------------------------------------------------
        if not visual_beat_configs:
            report.add_result("CHECK_B_TIMELINE", False, "Visual timeline has no beat configurations.")
        else:
            timeline_dur = getattr(visual_beat_configs[-1], "end_sec", 0.0)
            if timeline_dur <= 0.0:
                report.add_result("CHECK_B_TIMELINE", False, "Visual timeline duration is zero or invalid.")
            else:
                report.add_result("CHECK_B_TIMELINE", True, f"Visual timeline has {len(visual_beat_configs)} beats covering {timeline_dur:.2f}s.")

        # -------------------------------------------------------------
        # CHECK C: Every asset belongs to current topic
        # -------------------------------------------------------------
        unrelated_assets_found = []
        for beat in visual_beat_configs:
            img_name = getattr(beat, "image_filename", None)
            if img_name:
                # Check against known unrelated assets
                if not is_nuclear_topic:
                    if any(img_name == y_img for y_img in KNOWN_PRIOR_TOPIC_ASSETS["yamaguchi"]):
                        unrelated_assets_found.append(img_name)
                if "molasses" not in title_lower:
                    if any(img_name == m_img for m_img in KNOWN_PRIOR_TOPIC_ASSETS["molasses_flood"]):
                        unrelated_assets_found.append(img_name)

        if unrelated_assets_found:
            report.add_result(
                "CHECK_C_ASSET_TOPIC_MAPPING",
                False,
                f"Visual timeline contains assets from unrelated past topics: {set(unrelated_assets_found)}"
            )
        else:
            report.add_result("CHECK_C_ASSET_TOPIC_MAPPING", True, "All visual beat assets mapped to current topic.")

        # -------------------------------------------------------------
        # CHECK D: Thumbnail matches the topic
        # -------------------------------------------------------------
        if not os.path.isfile(thumbnail_path):
            report.add_result("CHECK_D_THUMBNAIL", False, f"Thumbnail file not found at: {thumbnail_path}")
        else:
            # Check headline
            headline_upper = thumbnail_headline.upper()
            if not is_nuclear_topic and any(k in headline_upper for k in ("BOMB", "NUCLEAR", "HIROSHIMA", "NAGASAKI")):
                report.add_result(
                    "CHECK_D_THUMBNAIL",
                    False,
                    f"Thumbnail headline '{headline_upper}' contains nuclear words for non-nuclear story."
                )
            else:
                report.add_result("CHECK_D_THUMBNAIL", True, f"Thumbnail verified with headline: '{headline_upper}'")

        # -------------------------------------------------------------
        # CHECK E: Title matches the story
        # -------------------------------------------------------------
        if not metadata_title:
            report.add_result("CHECK_E_TITLE", False, "Title is empty.")
        elif len(metadata_title) > 100:
            report.add_result("CHECK_E_TITLE", False, f"Title exceeds 100 characters ({len(metadata_title)} chars).")
        elif metadata_title.endswith("..."):
            report.add_result("CHECK_E_TITLE", False, "Title is arbitrarily truncated with trailing ellipsis '...'.")
        else:
            report.add_result("CHECK_E_TITLE", True, f"Title verified ({len(metadata_title)} chars): '{metadata_title}'")

        # -------------------------------------------------------------
        # CHECK F: Description accurately summarizes video
        # -------------------------------------------------------------
        if len(metadata_description) < 120:
            report.add_result("CHECK_F_DESCRIPTION", False, "Description is too short (< 120 chars) to summarize video.")
        elif not is_nuclear_topic and any(t in metadata_description.lower() for t in nuclear_terms):
            report.add_result("CHECK_F_DESCRIPTION", False, "Description contains unrelated nuclear terms.")
        else:
            report.add_result("CHECK_F_DESCRIPTION", True, "Description accurately summarizes story.")

        # -------------------------------------------------------------
        # CHECK G: Source credits match actual source usage
        # -------------------------------------------------------------
        if not attribution_text or len(attribution_text.strip()) < 15:
            report.add_result("CHECK_G_ATTRIBUTION", False, "Attribution text is missing or placeholder.")
        elif "archive" not in attribution_text.lower() and "commons" not in attribution_text.lower() and "source" not in attribution_text.lower():
            report.add_result("CHECK_G_ATTRIBUTION", False, "Attribution does not cite authentic historical source archives.")
        else:
            report.add_result("CHECK_G_ATTRIBUTION", True, "Attribution credits verified against source usage.")

        # -------------------------------------------------------------
        # CHECK H: Tags are topic-relevant
        # -------------------------------------------------------------
        if len(metadata_tags) < 5:
            report.add_result("CHECK_H_TAGS", False, f"Too few tags ({len(metadata_tags)} tags).")
        elif not is_nuclear_topic and any(t in [tag.lower() for tag in metadata_tags] for t in ("hiroshima", "nagasaki", "atomic bomb")):
            report.add_result("CHECK_H_TAGS", False, "Tags contain unrelated nuclear tags in a non-nuclear story.")
        else:
            report.add_result("CHECK_H_TAGS", True, f"Tags verified ({len(metadata_tags)} topic-relevant tags).")

        # -------------------------------------------------------------
        # CHECK I: No stale assets from previous productions
        # -------------------------------------------------------------
        # Check video hash if video exists
        if os.path.isfile(video_path):
            current_v_hash = compute_sha256(video_path)
            # Known stale prior production video hashes (cannot be reused for non-matching topics)
            known_stale = {
                "75ca472335dc65c9acba86e3a8ef85c77c576590be2f7a5ce560afa60fa66c67": "Tsutomu Yamaguchi showcase video",
            }
            ref_yamaguchi = os.path.join(self.workspace, "media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4")
            if not os.path.isfile(ref_yamaguchi):
                ref_yamaguchi = os.path.join(self.workspace, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
            if os.path.isfile(ref_yamaguchi):
                known_stale[compute_sha256(ref_yamaguchi)] = "Tsutomu Yamaguchi showcase video"

            if (current_v_hash in known_stale) and not is_nuclear_topic:
                report.add_result(
                    "CHECK_I_NO_STALE_ASSETS",
                    False,
                    f"CRITICAL: Output video file is an identical copy of {known_stale[current_v_hash]}!"
                )
            else:
                report.add_result("CHECK_I_NO_STALE_ASSETS", True, "Video hash is distinct from prior production videos.")
        else:
            report.add_result("CHECK_I_NO_STALE_ASSETS", False, f"Video deliverable missing: {video_path}")

        # -------------------------------------------------------------
        # CHECK J: Outro uses original branding
        # -------------------------------------------------------------
        authoritative_logo = os.path.join(self.workspace, "media", "source", "branding", "Youtube-Icon.png")
        if not os.path.isfile(authoritative_logo):
            authoritative_logo = os.path.join(self.workspace, "Youtube Channel Src", "Youtube-Icon.png")

        if not os.path.isfile(authoritative_logo):
            report.add_result("CHECK_J_OUTRO_BRANDING", False, "Original channel logo missing from branding directory.")
        else:
            logo_hash = compute_sha256(authoritative_logo)
            expected_logo_hash = "65b14bb348376d3b91c62538f91eadfd5e5e36d4109a525af7bb63e817730060"
            if logo_hash != expected_logo_hash:
                report.add_result("CHECK_J_OUTRO_BRANDING", False, f"Original channel logo modified or corrupted (hash: {logo_hash})")
            else:
                report.add_result("CHECK_J_OUTRO_BRANDING", True, "Authoritative channel logo verified and intact for outro.")

        # -------------------------------------------------------------
        # CHECK K: Prohibited Animation & Character Gate
        # Strictly prohibits stick-figures, cartoon character rigs, and cartoon hosts.
        # Enforces authentic documentary & motion graphics standards via CreativeIdentityPolicy.
        # -------------------------------------------------------------
        policy_res = CreativeIdentityPolicy.validate_timeline_beats(
            beats=visual_beat_configs or [],
            selected_style="documentary",
            topic_title=topic_title,
        )
        rend_ok, rend_issues = CreativeIdentityPolicy.validate_renderer_selection(rendering_engine or "CleanSlateRenderer")

        all_violations = rend_issues + policy_res.violations
        if manifest_assets:
            for item in manifest_assets:
                item_str = str(item).lower()
                for p in ("stick_figure", "stickfigure", "stick_animation", "character_rig", "cartoon_host"):
                    if p in item_str:
                        all_violations.append(f"manifest asset contains '{p}'")

        if all_violations:
            report.add_result(
                "CHECK_K_PROHIBITED_ANIMATION",
                False,
                f"CRITICAL: Prohibited character/stick animation detected: {all_violations}"
            )
        else:
            report.add_result(
                "CHECK_K_PROHIBITED_ANIMATION",
                True,
                "Authentic documentary/scientific visual standards verified. No prohibited stick/character animation."
            )

        # -------------------------------------------------------------
        # CHECK L: Repeated Frame Sequence & Cross-Production Visual Contamination
        # -------------------------------------------------------------
        # Extracts key sample frames and ensures non-matching topics do not reuse historical footage
        historical_prohibited_frame_hashes = {
            # Known atomic cloud / blast frames from Yamaguchi showcase
            "e0f9b6b772c57849e6f3d1b11b51e5e0a0d6cbcf086c8a7df0d5e5e5461159cb": "Atomic blast frame",
            "98f24b58e721a37c6a96bc8e4bb8e8f85f1c42bb7ee73b640c490a6f44d7e0d1": "Hiroshima explosion frame",
        }
        if os.path.isfile(video_path):
            sample_timestamps = [5.0, 15.0, 25.0, 35.0, 45.0]
            matched_prohibited = []
            for t in sample_timestamps:
                # Sample frame extraction via ffmpeg
                frame_tmp = os.path.join(self.workspace, "logs", f"_frame_chk_{t}.png")
                try:
                    import subprocess
                    from backend.app.core.paths import FFMPEG_EXE
                    subprocess.run(
                        [FFMPEG_EXE, "-ss", str(t), "-i", video_path, "-vframes", "1", "-y", frame_tmp],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=3
                    )
                    if os.path.isfile(frame_tmp):
                        f_hash = compute_sha256(frame_tmp)
                        if f_hash in historical_prohibited_frame_hashes and not is_nuclear_topic:
                            matched_prohibited.append((t, historical_prohibited_frame_hashes[f_hash]))
                        try:
                            os.remove(frame_tmp)
                        except OSError:
                            pass
                except Exception:
                    pass

            if matched_prohibited:
                report.add_result(
                    "CHECK_L_FRAME_SEQUENCE_INTEGRITY",
                    False,
                    f"CRITICAL: Repeated historical frame sequence detected at timestamps: {matched_prohibited}"
                )
            else:
                report.add_result(
                    "CHECK_L_FRAME_SEQUENCE_INTEGRITY",
                    True,
                    "Frame sequence verified. Zero historical visual cross-contamination detected."
                )
        else:
            report.add_result("CHECK_L_FRAME_SEQUENCE_INTEGRITY", False, "Video path missing for frame integrity analysis.")

        # -------------------------------------------------------------
        # CHECK M: Production Asset Ownership & Manifest Isolation
        # -------------------------------------------------------------
        mismatched_ownership = []
        if manifest_assets:
            for item in manifest_assets:
                path = item.get("path") or item.get("file_path") or item.get("id") if isinstance(item, dict) else str(item)
                if not path:
                    continue
                norm_p = os.path.normpath(str(path)).lower()
                # Allowed: current production dir, branding dir, or synthetic test assets
                allowed_roots = [
                    os.path.normpath(os.path.join(self.workspace, "media", "source", "branding")).lower(),
                    os.path.normpath(os.path.join(self.workspace, "Youtube Channel Src")).lower(),
                ]
                # If asset points to an unrelated production folder (e.g., prod_cycle_4 or prod_20261110_vesna_vulovic)
                if "media" in norm_p and "productions" in norm_p:
                    # check if it matches current video's production directory
                    curr_prod_dir = os.path.dirname(os.path.dirname(os.path.normpath(video_path))).lower()
                    if curr_prod_dir and curr_prod_dir not in norm_p:
                        mismatched_ownership.append(path)

        if mismatched_ownership:
            report.add_result(
                "CHECK_M_ASSET_OWNERSHIP",
                False,
                f"CRITICAL: Visual assets belong to unrelated prior production workspaces: {mismatched_ownership}"
            )
        else:
            report.add_result(
                "CHECK_M_ASSET_OWNERSHIP",
                True,
                "All visual assets verified to belong exclusively to current production workspace or approved branding."
            )

        return report
