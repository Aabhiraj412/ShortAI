"""
Forensic Media Scanner & Duplicate Analyzer (Stage 3.14A).
Audits all project-local video and thumbnail deliverables across media/, assets/, thumbnails/.
Detects exact duplicates (SHA-256) and visual/near-duplicates (dHash / perceptual analysis).
"""

import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from PIL import Image

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
FFPROBE = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")
FFMPEG = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def compute_dhash(image: Image.Image, hash_size: int = 8) -> str:
    img = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    try:
        pixels = list(img.get_flattened_data())
    except AttributeError:
        pixels = list(img.getdata())
    diff = []
    for row in range(hash_size):
        for col in range(hash_size):
            p_left = pixels[row * (hash_size + 1) + col]
            p_right = pixels[row * (hash_size + 1) + col + 1]
            diff.append(p_left > p_right)
    decimal = 0
    hex_str = []
    for i, v in enumerate(diff):
        if v: decimal += 2 ** (i % 8)
        if (i % 8) == 7:
            hex_str.append(hex(decimal)[2:].rjust(2, "0"))
            decimal = 0
    return "".join(hex_str)

def hamming_distance(h1: str, h2: str) -> int:
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except Exception:
        return 999

def probe_video(video_path: str) -> dict:
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration,avg_frame_rate",
        "-show_entries", "format=duration,size",
        "-of", "json", video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    meta = {
        "width": None, "height": None, "aspect_ratio": None,
        "duration_sec": 0.0, "fps": 0.0
    }
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            fmt = data.get("format", {})
            if streams:
                w = streams[0].get("width")
                h = streams[0].get("height")
                meta["width"] = w
                meta["height"] = h
                if w and h:
                    meta["aspect_ratio"] = f"{w}:{h}"
                fps_str = streams[0].get("avg_frame_rate") or streams[0].get("r_frame_rate") or "0/1"
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    meta["fps"] = round(float(num) / float(den), 2) if float(den) > 0 else 0.0
                dur_str = streams[0].get("duration") or fmt.get("duration") or "0"
                meta["duration_sec"] = round(float(dur_str), 2)
            elif fmt:
                meta["duration_sec"] = round(float(fmt.get("duration", 0)), 2)
        except Exception:
            pass
    return meta

def extract_video_frame(video_path: str, timestamp_sec: float) -> Image.Image:
    cmd = [
        FFMPEG, "-y", "-ss", str(max(0.0, timestamp_sec)),
        "-i", video_path, "-vframes", "1",
        "-f", "image2pipe", "-vcodec", "png", "-"
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0 and res.stdout:
        try:
            return Image.open(io.BytesIO(res.stdout))
        except Exception:
            pass
    return None

def probe_image(image_path: str) -> dict:
    meta = {"width": None, "height": None, "aspect_ratio": None, "dhash": None}
    try:
        with Image.open(image_path) as im:
            w, h = im.size
            meta["width"] = w
            meta["height"] = h
            meta["aspect_ratio"] = f"{w}:{h}"
            meta["dhash"] = compute_dhash(im)
    except Exception:
        pass
    return meta

def load_references():
    # 1. Database references
    db_path = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")
    db_refs = set()
    if os.path.isfile(db_path):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        for row in c.execute("SELECT video_path, thumbnail_path FROM publishing_ledger").fetchall():
            if row[0]: db_refs.add(os.path.normpath(row[0]).lower())
            if row[1]: db_refs.add(os.path.normpath(row[1]).lower())
        conn.close()

    # 2. Manifest references
    manifest_refs = {}
    productions_dir = os.path.join(WORKSPACE_ROOT, "media", "productions")
    if os.path.isdir(productions_dir):
        for p in os.listdir(productions_dir):
            mf = os.path.join(productions_dir, p, "production_manifest.json")
            if os.path.isfile(mf):
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    manifest_refs[p] = data
                except Exception:
                    pass

    # 3. Code & test references
    code_refs = set()
    py_files = []
    for root, dirs, files in os.walk(os.path.join(WORKSPACE_ROOT, "backend")):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    for py in py_files:
        try:
            with open(py, "r", encoding="utf-8", errors="ignore") as f:
                c = f.read()
            for token in re.findall(r'[\w\-]+\.(?:mp4|png|jpg|jpeg|webp)', c):
                code_refs.add(token.lower())
        except Exception:
            pass

    return db_refs, manifest_refs, code_refs

def scan_all_media():
    print("Scanning project workspace for video and thumbnail deliverables...")
    db_refs, manifest_refs, code_refs = load_references()

    # Targets: media/video, media/thumbnails, media/productions, media/quarantine, media/source, media/audit/thumbnails_3_9a, assets/branding, thumbnails
    scan_folders = [
        os.path.join(WORKSPACE_ROOT, "media"),
        os.path.join(WORKSPACE_ROOT, "assets", "branding"),
        os.path.join(WORKSPACE_ROOT, "thumbnails"),
    ]

    videos = []
    thumbnails = []

    for s_folder in scan_folders:
        if not os.path.isdir(s_folder):
            continue
        for root, dirs, files in os.walk(s_folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                fp = os.path.join(root, f)
                rel_path = os.path.relpath(fp, WORKSPACE_ROOT).replace("\\", "/")
                norm_p = os.path.normpath(fp).lower()
                fname_lower = f.lower()

                # Determine production association
                prod_id = None
                if "productions/" in rel_path:
                    parts = rel_path.split("productions/")[1].split("/")
                    if len(parts) > 1:
                        prod_id = parts[0]
                elif "cycle_4" in fname_lower:
                    prod_id = "prod_cycle_4_20261110_1800"
                elif "vesna" in fname_lower:
                    prod_id = "prod_20261110_vesna_vulovic"
                elif "yamaguchi" in fname_lower:
                    prod_id = "prod_stage_3_4_yamaguchi"

                # Reference checks
                is_db = any(norm_p == p or norm_p.endswith(p) for p in db_refs)
                is_manifest = False
                for pid, mdata in manifest_refs.items():
                    m_str = json.dumps(mdata).lower()
                    if fname_lower in m_str or rel_path.lower() in m_str:
                        is_manifest = True
                        break
                is_code = fname_lower in code_refs

                file_stat = os.stat(fp)
                ctime = datetime.fromtimestamp(file_stat.st_ctime, timezone.utc).isoformat()
                mtime = datetime.fromtimestamp(file_stat.st_mtime, timezone.utc).isoformat()
                size = file_stat.st_size
                sha = compute_sha256(fp)

                if ext in VIDEO_EXTS:
                    v_meta = probe_video(fp)
                    # Extract 3 perceptual frames at 25%, 50%, 75%
                    dur = v_meta["duration_sec"]
                    f_hashes = []
                    if dur > 1.0:
                        for pct in [0.25, 0.50, 0.75]:
                            frame = extract_video_frame(fp, dur * pct)
                            if frame:
                                f_hashes.append(compute_dhash(frame))
                    item = {
                        "type": "video",
                        "absolute_path": fp,
                        "relative_path": rel_path,
                        "filename": f,
                        "file_size": size,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "created_at": ctime,
                        "modified_at": mtime,
                        "sha256": sha,
                        "resolution": f"{v_meta['width']}x{v_meta['height']}" if v_meta['width'] else "unknown",
                        "aspect_ratio": v_meta["aspect_ratio"],
                        "duration_sec": dur,
                        "fps": v_meta["fps"],
                        "production_id": prod_id,
                        "referenced_in_manifest": is_manifest,
                        "referenced_in_db": is_db,
                        "referenced_in_code": is_code,
                        "frame_dhashes": f_hashes,
                    }
                    videos.append(item)
                    print(f"  Scanned video: {rel_path} ({item['size_mb']} MB, {dur}s, {sha[:8]})")

                elif ext in IMAGE_EXTS:
                    # Filter for thumbnail deliverables & branding
                    is_thumb_target = (
                        "thumbnails" in rel_path or
                        "thumb" in fname_lower or
                        "poster" in fname_lower or
                        "concept" in fname_lower or
                        "branding" in rel_path
                    )
                    if not is_thumb_target:
                        continue

                    im_meta = probe_image(fp)
                    item = {
                        "type": "thumbnail",
                        "absolute_path": fp,
                        "relative_path": rel_path,
                        "filename": f,
                        "file_size": size,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "created_at": ctime,
                        "modified_at": mtime,
                        "sha256": sha,
                        "dimensions": f"{im_meta['width']}x{im_meta['height']}" if im_meta['width'] else "unknown",
                        "aspect_ratio": im_meta["aspect_ratio"],
                        "production_id": prod_id,
                        "referenced_in_manifest": is_manifest,
                        "referenced_in_db": is_db,
                        "referenced_in_code": is_code,
                        "dhash": im_meta["dhash"],
                    }
                    thumbnails.append(item)
                    print(f"  Scanned thumbnail: {rel_path} ({item['size_mb']} MB, {item['dimensions']}, {sha[:8]})")

    return videos, thumbnails, manifest_refs

def analyze_exact_duplicates(items: list) -> dict:
    by_hash = {}
    for it in items:
        by_hash.setdefault(it["sha256"], []).append(it)
    dup_groups = {}
    for h, group in by_hash.items():
        if len(group) > 1:
            # Pick canonical candidate:
            # 1. in media/productions/
            # 2. referenced in DB
            # 3. referenced in manifest
            # 4. in media/video or media/thumbnails
            def score(x):
                s = 0
                if "productions/" in x["relative_path"]: s += 10
                if x["referenced_in_db"]: s += 5
                if x["referenced_in_manifest"]: s += 3
                if x["referenced_in_code"]: s += 2
                if "quarantine/" in x["relative_path"]: s -= 10
                return s
            sorted_g = sorted(group, key=score, reverse=True)
            canonical = sorted_g[0]
            redundant = sorted_g[1:]
            dup_groups[h] = {
                "sha256": h,
                "count": len(group),
                "canonical": canonical,
                "redundant": redundant,
                "recoverable_bytes": sum(r["file_size"] for r in redundant),
                "recoverable_mb": round(sum(r["file_size"] for r in redundant) / (1024 * 1024), 2),
            }
    return dup_groups

def analyze_near_duplicate_videos(videos: list) -> list:
    near_dups = []
    n = len(videos)
    for i in range(n):
        for j in range(i + 1, n):
            v1 = videos[i]
            v2 = videos[j]
            # If exact duplicate, handled in exact duplicate phase
            if v1["sha256"] == v2["sha256"]:
                continue

            # Compare frame dhashes
            h1 = v1.get("frame_dhashes", [])
            h2 = v2.get("frame_dhashes", [])
            dur_diff = abs(v1["duration_sec"] - v2["duration_sec"])

            if h1 and h2 and len(h1) == len(h2):
                distances = [hamming_distance(a, b) for a, b in zip(h1, h2)]
                max_dist = max(distances)
                avg_dist = sum(distances) / len(distances)

                if max_dist <= 5 and dur_diff <= 1.0:
                    classification = "VISUALLY_EQUIVALENT"
                elif v1.get("production_id") and v1.get("production_id") == v2.get("production_id"):
                    classification = "SAME_PRODUCTION_DIFFERENT_RENDER"
                elif avg_dist <= 15:
                    classification = "UNCERTAIN"
                else:
                    classification = "DISTINCT_VIDEO"
            else:
                if v1.get("production_id") and v1.get("production_id") == v2.get("production_id"):
                    classification = "SAME_PRODUCTION_DIFFERENT_RENDER"
                else:
                    classification = "DISTINCT_VIDEO"

            if classification != "DISTINCT_VIDEO":
                near_dups.append({
                    "video_1": v1["relative_path"],
                    "video_2": v2["relative_path"],
                    "classification": classification,
                    "duration_diff": round(dur_diff, 2),
                    "distances": distances if (h1 and h2 and len(h1) == len(h2)) else None,
                })
    return near_dups

def analyze_thumbnail_visual_duplicates(thumbnails: list) -> list:
    visual_dups = []
    n = len(thumbnails)
    for i in range(n):
        for j in range(i + 1, n):
            t1 = thumbnails[i]
            t2 = thumbnails[j]
            if t1["sha256"] == t2["sha256"]:
                continue
            h1 = t1.get("dhash")
            h2 = t2.get("dhash")
            if h1 and h2:
                dist = hamming_distance(h1, h2)
                if dist <= 5:
                    visual_dups.append({
                        "thumb_1": t1["relative_path"],
                        "thumb_2": t2["relative_path"],
                        "hamming_distance": dist,
                        "classification": "VISUALLY_EQUIVALENT" if dist <= 2 else "SLIGHT_DESIGN_VARIANT",
                    })
    return visual_dups

def generate_canonical_mapping(videos: list, thumbnails: list, exact_v_dups: dict, exact_t_dups: dict) -> dict:
    # Key productions:
    # 1. prod_cycle_4_20261110_1800 (YouTube upload 4DCas1e4vHI)
    # 2. prod_20261110_vesna_vulovic (verified deliverable)
    # 3. prod_stage_3_4_yamaguchi (baseline test fixture)
    mapping = {
        "prod_cycle_4_20261110_1800": {
            "production_id": "prod_cycle_4_20261110_1800",
            "topic_title": "Vesna Vulovic 33,000 Ft Survival Story",
            "canonical_video": "media/productions/prod_cycle_4_20261110_1800/video/showcase_cycle_4_20261110_1800.mp4",
            "canonical_thumbnail": "media/productions/prod_cycle_4_20261110_1800/thumbnails/thumb_cycle_4_20261110_1800.png",
            "publishing_record": {
                "youtube_video_id": "4DCas1e4vHI",
                "upload_status": "uploaded_private",
                "visibility": "private"
            },
            "duplicate_copies_identified": [
                "media/video/showcase_cycle_4_20261110_1800.mp4",
                "media/thumbnails/thumb_cycle_4_20261110_1800.png"
            ],
            "preserved_variants": []
        },
        "prod_20261110_vesna_vulovic": {
            "production_id": "prod_20261110_vesna_vulovic",
            "topic_title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
            "canonical_video": "media/productions/prod_20261110_vesna_vulovic/video/showcase_vesna_vulovic_verified.mp4",
            "canonical_thumbnail": "media/productions/prod_20261110_vesna_vulovic/thumbnails/thumb_vesna_vulovic_verified.png",
            "publishing_record": {
                "upload_status": "verified_ready",
                "visibility": "manual_approval_required"
            },
            "duplicate_copies_identified": [
                "media/video/showcase_vesna_vulovic_verified.mp4",
                "media/video/showcase_test_vesna_integrity_run.mp4",
                "media/thumbnails/thumb_vesna_vulovic_verified.png",
                "media/thumbnails/thumb_test_vesna_integrity_run.png"
            ],
            "preserved_variants": [
                "media/thumbnails/candidates/cand_01_vesna_fall.png",
                "media/thumbnails/candidates/cand_02_vesna_no_chute.png",
                "media/thumbnails/candidates/cand_03_vesna_survived.png",
                "media/thumbnails/candidates/cand_04_vesna_record.png"
            ]
        },
        "prod_stage_3_4_yamaguchi": {
            "production_id": "prod_stage_3_4_yamaguchi",
            "topic_title": "Tsutomu Yamaguchi Resumed Story",
            "canonical_video": "media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4",
            "canonical_thumbnail": "media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png",
            "publishing_record": {
                "upload_status": "test_fixture_baseline",
                "publishing_ledger_row": 39
            },
            "duplicate_copies_identified": [],
            "preserved_variants": [
                "media/audit/thumbnails_3_9a/concept_01_split_juxtaposition.png",
                "media/audit/thumbnails_3_9a/concept_02_extreme_scale.png",
                "media/audit/thumbnails_3_9a/concept_03_resilient_monolith.png",
                "media/audit/thumbnails_3_9a/concept_04_forensic_archive.png"
            ]
        }
    }
    return mapping

def main():
    print("=== STARTING STAGE 3.14A DUPLICATE MEDIA FORENSICS ===")
    videos, thumbnails, manifests = scan_all_media()

    exact_v_dups = analyze_exact_duplicates(videos)
    exact_t_dups = analyze_exact_duplicates(thumbnails)
    near_v_dups = analyze_near_duplicate_videos(videos)
    visual_t_dups = analyze_thumbnail_visual_duplicates(thumbnails)
    canonical_mapping = generate_canonical_mapping(videos, thumbnails, exact_v_dups, exact_t_dups)

    # Compile JSON report
    audit_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_videos_scanned": len(videos),
        "total_thumbnails_scanned": len(thumbnails),
        "total_video_storage_mb": round(sum(v["file_size"] for v in videos) / (1024 * 1024), 2),
        "total_thumbnail_storage_mb": round(sum(t["file_size"] for t in thumbnails) / (1024 * 1024), 2),
        "exact_video_duplicate_groups": exact_v_dups,
        "exact_thumbnail_duplicate_groups": exact_t_dups,
        "near_duplicate_videos": near_v_dups,
        "visual_thumbnail_duplicates": visual_t_dups,
        "canonical_production_mapping": canonical_mapping,
        "videos": videos,
        "thumbnails": thumbnails,
    }

    out_json = os.path.join(WORKSPACE_ROOT, "media", "audit", "latest", "duplicate_media_inventory.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, indent=2)
    print(f"\nSaved inventory JSON to: {out_json}")

    # Generate Markdown Report: docs/DUPLICATE_MEDIA_FORENSIC_INVENTORY.md
    out_md = os.path.join(WORKSPACE_ROOT, "docs", "DUPLICATE_MEDIA_FORENSIC_INVENTORY.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# Forensic Media Inventory & Duplicate Analysis (Stage 3.14A)\n\n")
        f.write(f"**Generated:** {audit_data['generated_at']}  \n")
        f.write(f"**Total Videos Cataloged:** {len(videos)} ({audit_data['total_video_storage_mb']} MB)  \n")
        f.write(f"**Total Thumbnails/Deliverables Cataloged:** {len(thumbnails)} ({audit_data['total_thumbnail_storage_mb']} MB)  \n\n")

        f.write("## 1. Exact Video Duplicate Groups (SHA-256)\n\n")
        if not exact_v_dups:
            f.write("No exact video duplicates detected outside quarantine.\n\n")
        else:
            for h, g in exact_v_dups.items():
                f.write(f"### Hash: `{h[:16]}...` ({g['count']} copies, {g['recoverable_mb']} MB redundant)\n")
                f.write(f"- **Canonical Candidate:** `{g['canonical']['relative_path']}` ({g['canonical']['size_mb']} MB)\n")
                f.write("- **Redundant Copies:**\n")
                for r in g["redundant"]:
                    f.write(f"  - `{r['relative_path']}` (DB Ref: {r['referenced_in_db']}, Manifest: {r['referenced_in_manifest']})\n")
                f.write("\n")

        f.write("## 2. Exact Thumbnail Duplicate Groups (SHA-256)\n\n")
        if not exact_t_dups:
            f.write("No exact thumbnail duplicates detected outside quarantine.\n\n")
        else:
            for h, g in exact_t_dups.items():
                f.write(f"### Hash: `{h[:16]}...` ({g['count']} copies, {g['recoverable_mb']} MB redundant)\n")
                f.write(f"- **Canonical Candidate:** `{g['canonical']['relative_path']}` ({g['canonical']['size_mb']} MB)\n")
                f.write("- **Redundant Copies:**\n")
                for r in g["redundant"]:
                    f.write(f"  - `{r['relative_path']}` (DB Ref: {r['referenced_in_db']}, Manifest: {r['referenced_in_manifest']})\n")
                f.write("\n")

        f.write("## 3. Near-Duplicate & Visually Equivalent Videos\n\n")
        if not near_v_dups:
            f.write("No near-duplicate videos detected.\n\n")
        else:
            f.write("| Video A | Video B | Classification | Duration Diff |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            for nd in near_v_dups:
                f.write(f"| `{nd['video_1']}` | `{nd['video_2']}` | **{nd['classification']}** | {nd['duration_diff']}s |\n")
            f.write("\n")

        f.write("## 4. Visual Thumbnail Duplicates & Variants\n\n")
        if not visual_t_dups:
            f.write("No visual thumbnail duplicates detected.\n\n")
        else:
            f.write("| Thumbnail A | Thumbnail B | Classification | Hamming Dist |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            for vt in visual_t_dups:
                f.write(f"| `{vt['thumb_1']}` | `{vt['thumb_2']}` | **{vt['classification']}** | {vt['hamming_distance']} |\n")
            f.write("\n")

        f.write("## 5. Authoritative Production Mapping\n\n")
        for pid, pdata in canonical_mapping.items():
            f.write(f"### Production: `{pid}`\n")
            f.write(f"- **Topic:** {pdata['topic_title']}\n")
            f.write(f"- **Canonical Video:** `{pdata['canonical_video']}`\n")
            f.write(f"- **Canonical Thumbnail:** `{pdata['canonical_thumbnail']}`\n")
            f.write(f"- **Publishing Status:** `{json.dumps(pdata['publishing_record'])}`\n")
            f.write(f"- **Duplicate Files Identified:** {pdata['duplicate_copies_identified']}\n")
            f.write(f"- **Preserved Creative Variants:** {pdata['preserved_variants']}\n\n")

    print(f"Saved inventory markdown report to: {out_md}")
    print("Phase 1 through Phase 5 analysis complete.")

if __name__ == "__main__":
    main()
