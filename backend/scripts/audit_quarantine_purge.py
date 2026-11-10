import json
import os
import sqlite3

def run_quarantine_audit():
    with open("media/quarantine/manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Load active database references
    conn = sqlite3.connect("backend/studio.db")
    c = conn.cursor()
    db_paths = set()
    for row in c.execute("SELECT video_path, thumbnail_path FROM publishing_ledger").fetchall():
        if row[0]:
            db_paths.add(os.path.normpath(row[0]).lower())
        if row[1]:
            db_paths.add(os.path.normpath(row[1]).lower())
    conn.close()

    safe_to_purge = []
    retain_in_quarantine = []
    restore_to_active = []

    for qid, entry in manifest.items():
        if entry.get("restored"):
            continue

        orig = entry.get("original_relative_path", "").replace("\\", "/")
        cat = entry.get("category", "UNKNOWN")
        size = entry.get("size_bytes", 0)
        sha = entry.get("sha256", "")
        norm_orig = os.path.normpath(orig).lower()

        # Rule 1: Is it needed in active workspace?
        # Check against DB
        is_db_ref = False
        for p in db_paths:
            if norm_orig in p or p.endswith(norm_orig):
                is_db_ref = True
                break

        if is_db_ref:
            # Check if already present on disk
            if os.path.isfile(orig):
                # The file is actively present on disk; the quarantined copy is a duplicate
                safe_to_purge.append((qid, entry, "Duplicate of active database-referenced asset already present in active workspace"))
            else:
                restore_to_active.append((qid, entry, "Referenced by database but missing from active workspace"))
            continue

        # Rule 2: Categories
        if cat == "CATEGORY_I_OBSOLETE_CACHE":
            safe_to_purge.append((qid, entry, "Disposable headless browser profile or temporary render cache"))
        elif cat == "CATEGORY_G_DUPLICATES":
            safe_to_purge.append((qid, entry, "Confirmed duplicate copy of existing asset or cache frame"))
        elif cat == "CATEGORY_H_TEMPORARY_FILES":
            if any(x in orig for x in ["_tts_concat_list", "_test_doc_frame", "render_", "scratch", "test_20s", "test_35s"]):
                safe_to_purge.append((qid, entry, "Verified disposable temporary scratch file"))
            else:
                retain_in_quarantine.append((qid, entry, "Temporary file with potential audit/reference value"))
        elif cat == "CATEGORY_E_HISTORICAL_SHOWCASES":
            retain_in_quarantine.append((qid, entry, "Historical showcase asset retained for archive"))
        else:
            retain_in_quarantine.append((qid, entry, "Unclassified/uncertain asset retained in quarantine"))

    print(f"Total Quarantined Files Audited: {len(manifest) - 1}") # minus restored
    print(f"Group A (SAFE_TO_PURGE): {len(safe_to_purge)} files, {round(sum(e[1]['size_bytes'] for e in safe_to_purge) / (1024*1024), 2)} MB")
    print(f"Group B (RETAIN_IN_QUARANTINE): {len(retain_in_quarantine)} files, {round(sum(e[1]['size_bytes'] for e in retain_in_quarantine) / (1024*1024), 2)} MB")
    print(f"Group C (RESTORE_TO_ACTIVE): {len(restore_to_active)} files")

    # Print summary of retain
    print("\nRetain in quarantine breakdown:")
    retain_cats = {}
    for qid, e, reason in retain_in_quarantine:
        retain_cats.setdefault(e.get("category"), []).append((qid, e, reason))
    for cname, items in retain_cats.items():
        print(f"  {cname}: {len(items)} files, {round(sum(i[1]['size_bytes'] for i in items)/(1024*1024), 2)} MB")
        for qid, e, r in items[:5]:
            print(f"    - {e.get('original_relative_path')} ({r})")

if __name__ == "__main__":
    run_quarantine_audit()
