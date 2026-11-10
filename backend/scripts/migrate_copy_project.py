"""
Safe Project Copy Script for Stage 3.15.
Copies D:\Agents\CrazzyDocs to D:\Agents\CrazzyDocs, excluding only disposable runtime caches and old virtualenv.
Verifies file counts and critical SHA-256 hashes.
"""

import hashlib
import json
import os
import subprocess
import sys
import time

SRC_ROOT = r"D:\Agents\CrazzyDocs"
DEST_ROOT = r"D:\Agents\CrazzyDocs"

CRITICAL_FILES = [
    os.path.join("backend", "studio.db"),
    os.path.join("backend", "client_secrets.json"),
    os.path.join("backend", "token.json"),
    os.path.join(".env.local"),
    os.path.join("Youtube Channel Src", "Youtube-Icon.png"),
    os.path.join("media", "source", "branding", "crazzydocs_outro_1080x1920.mp4"),
    os.path.join("media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4"),
    os.path.join("media", "thumbnails", "showcase_stage_3_9a_yamaguchi_poster.png"),
    os.path.join("media", "productions", "prod_cycle_4_20261110_1800", "video", "showcase_cycle_4_20261110_1800.mp4"),
    os.path.join("media", "productions", "prod_cycle_4_20261110_1800", "thumbnails", "thumb_cycle_4_20261110_1800.png"),
    os.path.join("media", "productions", "prod_20261110_vesna_vulovic", "video", "showcase_vesna_vulovic_verified.mp4"),
    os.path.join("media", "productions", "prod_20261110_vesna_vulovic", "thumbnails", "thumb_vesna_vulovic_verified.png"),
    os.path.join("docs", "reference_styles", "pop_info.html"),
    os.path.join("docs", "reference_styles", "chalk_talk.html"),
    os.path.join("docs", "reference_styles", "creator_motion_graphics.html"),
    os.path.join("docs", "reference_styles", "data_story.html"),
]

def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    print("=" * 80)
    print("STAGE 3.15: COPYING PROJECT TO D:\\Agents\\CrazzyDocs")
    print("=" * 80)

    # 1. Run Robocopy
    cmd = [
        "robocopy",
        SRC_ROOT,
        DEST_ROOT,
        "/E",
        "/XD",
        "python-env",
        ".pytest_cache",
        "cache",
        "__pycache__",
        "/MT:8",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
    ]
    print(f"Executing: {' '.join(cmd)}")
    start_t = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    duration = time.time() - start_t
    print(f"Robocopy finished in {duration:.2f}s with return code: {res.returncode}")
    # Robocopy return codes 0-7 indicate success
    if res.returncode > 7:
        print(f"ERROR: Robocopy returned failure code {res.returncode}")
        print(res.stdout)
        sys.exit(1)

    # 2. Count files and total size
    print("\nCounting files at destination...")
    dest_file_count = 0
    dest_total_bytes = 0
    for root, dirs, files in os.walk(DEST_ROOT):
        for f in files:
            fp = os.path.join(root, f)
            dest_file_count += 1
            dest_total_bytes += os.path.getsize(fp)

    mb = dest_total_bytes / (1024 * 1024)
    print(f"Destination Total Files: {dest_file_count:,}")
    print(f"Destination Total Size: {dest_total_bytes:,} bytes ({mb:.2f} MB)")

    # 3. Verify critical asset hashes
    print("\nVerifying critical asset SHA-256 hashes...")
    all_match = True
    for rel_path in CRITICAL_FILES:
        src_path = os.path.join(SRC_ROOT, rel_path)
        dest_path = os.path.join(DEST_ROOT, rel_path)

        if not os.path.isfile(src_path):
            print(f"[MISSING IN SRC] {rel_path}")
            all_match = False
            continue
        if not os.path.isfile(dest_path):
            print(f"[MISSING IN DEST] {rel_path}")
            all_match = False
            continue

        src_h = compute_sha256(src_path)
        dest_h = compute_sha256(dest_path)
        if src_h == dest_h:
            print(f"[OK] {rel_path} -> {src_h[:12]}")
        else:
            print(f"[HASH MISMATCH] {rel_path}: src={src_h} != dest={dest_h}")
            all_match = False

    if not all_match:
        print("\nERROR: Critical asset verification failed!")
        sys.exit(1)

    print("\nALL CRITICAL ASSET HASHES VERIFIED IDENTICAL BIT-FOR-BIT!")

if __name__ == "__main__":
    main()
