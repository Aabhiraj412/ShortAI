"""
Cleanly replaces r"D:\AI Arts\AI" with r"D:\Agents\CrazzyDocs" across active code files.
"""

import os

DEST_ROOT = r"D:\Agents\CrazzyDocs"
OLD_ROOT = r"D:\AI Arts\AI"

files_to_fix = [
    os.path.join("backend", "app", "content", "topic_ledger.py"),
    os.path.join("backend", "app", "rendering", "compositing", "compositor.py"),
    os.path.join("backend", "app", "rendering", "engines", "clean_slate_renderer.py"),
    os.path.join("backend", "app", "rendering", "engines", "vesna_fast_renderer.py"),
    os.path.join("backend", "app", "rendering", "engines", "yamaguchi_fast_renderer.py"),
    os.path.join("backend", "app", "rendering", "quality", "frame_validator.py"),
    os.path.join("backend", "app", "rendering", "quality", "quality_gate.py"),
    os.path.join("backend", "app", "services", "tts_service.py"),
    os.path.join("backend", "tests", "test_production_duration_standard.py"),
    os.path.join("backend", "tests", "test_stage_3_10_resilience.py"),
    os.path.join("backend", "tests", "test_stage_3_11_real_recovery.py"),
    os.path.join("backend", "tests", "test_job_manager.py"),
]

for rel_f in files_to_fix:
    abs_f = os.path.join(DEST_ROOT, rel_f)
    if os.path.isfile(abs_f):
        with open(abs_f, "r", encoding="utf-8") as f:
            content = f.read()
        new_content = content.replace(OLD_ROOT, DEST_ROOT).replace("D:/AI Arts/AI", "D:/Agents/CrazzyDocs")
        if new_content != content:
            with open(abs_f, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[REPLACED] {rel_f}")
        else:
            print(f"[NO MATCH] {rel_f}")
    else:
        print(f"[NOT FOUND] {rel_f}")

print("Fix completed.")
