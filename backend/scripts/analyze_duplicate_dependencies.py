import json
import os
from pathlib import Path
from collections import defaultdict

WORKSPACE_ROOT = Path(r"D:\Agents\CrazzyDocs")
VERIFIED_JSON = WORKSPACE_ROOT / "docs" / "STAGE_3_21D_CLEANUP_CANDIDATES_VERIFIED.json"

with open(VERIFIED_JSON, "r", encoding="utf-8") as f:
    d = json.load(f)

files = d["files"]
all_by_path = {x["path"]: x for x in files}
redundant_files = [x for x in files if x["duplicate_role"] == "redundant_copy"]
print(f"Total redundant duplicate copies to inspect: {len(redundant_files)}")

# Collect all references across backend and docs
search_dirs = [WORKSPACE_ROOT / "backend", WORKSPACE_ROOT / "docs", WORKSPACE_ROOT / "media"]
extensions = {".py", ".html", ".js", ".css", ".json", ".md", ".sh", ".ps1"}
corpus = {}

for sdir in search_dirs:
    for root, _, fs in os.walk(sdir):
        for f in fs:
            p = Path(root) / f
            if p.suffix.lower() in extensions:
                try:
                    rel = str(p.relative_to(WORKSPACE_ROOT)).replace("\\", "/")
                    corpus[rel] = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass

print(f"Loaded {len(corpus)} search corpus files.")

results = []

for item in redundant_files:
    red_path = item["path"]
    canon_path = item["canonical_path"]
    fn = item["filename"]
    sha = item["sha256"]
    sz = item["size_bytes"]
    
    # Check references
    refs = []
    for c_path, c_text in corpus.items():
        if c_path == red_path:
            continue
        if red_path in c_text or (fn in c_text and ("test" in c_path or "manifest" in c_path)):
            refs.append(c_path)
            
    # Manifest reference check
    in_local_manifest = False
    prod_dir = Path(red_path).parent
    while prod_dir != Path("media") and prod_dir != Path("."):
        m1 = WORKSPACE_ROOT / prod_dir / "manifest.json"
        m2 = WORKSPACE_ROOT / prod_dir / "production_manifest.json"
        if m1.exists():
            if fn in m1.read_text(encoding="utf-8", errors="ignore"):
                in_local_manifest = True
                break
        if m2.exists():
            if fn in m2.read_text(encoding="utf-8", errors="ignore"):
                in_local_manifest = True
                break
        prod_dir = prod_dir.parent

    # Test reference check
    in_test = any("test" in r and "backend/tests" in r for r in refs)

    # Classification logic:
    # A. SAFE_TO_REMOVE_AFTER_BACKUP: Unreferenced redundant duplicate, or test fixture duplicate whose canonical copy exists
    # B. PATH_DEPENDENCY_MUST_BE_UPDATED_FIRST: Actively referenced by code or DB by its redundant path
    # C. KEEP_FOR_REPRODUCIBILITY: Required by local directory manifest (e.g. self-contained production bundle like stage 3.16 verification)
    # D. NEEDS_OPERATOR_REVIEW: Ambiguous
    
    if in_local_manifest and "prod_stage_3_16_verification" in red_path:
        dep_class = "C. KEEP_FOR_REPRODUCIBILITY"
        dep_reason = "Referenced in local production_manifest.json of Stage 3.16 verification run"
    elif in_local_manifest:
        dep_class = "C. KEEP_FOR_REPRODUCIBILITY"
        dep_reason = "Referenced in local directory manifest; deleting would break folder self-containment"
    elif in_test:
        dep_class = "B. PATH_DEPENDENCY_MUST_BE_UPDATED_FIRST"
        dep_reason = "Referenced by an active test suite file; test path should be updated to canonical first"
    elif "images/vesna_vulovic" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Identical copy of canonical asset in {canon_path}; safe to remove after zip backup"
    elif "audio/vesna_segments" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Intermediate audio beat duplicated in canonical production narration {canon_path}"
    elif "test_scen_11" in red_path or "test_vesna_integrity" in red_path or "test_auto_job" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Cloned test run artifact; canonical production asset preserved at {canon_path}"
    elif "video/showcase_stage_3_4_tsutomu_yamaguchi.mp4" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Standalone duplicate of canonical showcase video at {canon_path}"
    elif "thumbnails/showcase_stage_" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Standalone thumbnail duplicate of canonical candidate {canon_path}"
    elif "media/audit/latest/workspace_inventory.json" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Identical byte snapshot duplicate of {canon_path}"
    elif "media/audit/integrity_report_" in red_path:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = "Duplicate 2.2 KB crash test integrity report; template report preserved"
    else:
        dep_class = "A. SAFE_TO_REMOVE_AFTER_BACKUP"
        dep_reason = f"Redundant byte duplicate of canonical {canon_path}"

    results.append({
        "path": red_path,
        "canonical_path": canon_path,
        "sha256": sha,
        "size_bytes": sz,
        "dependency_class": dep_class,
        "dependency_reason": dep_reason,
        "referenced_by_manifest": in_local_manifest,
        "referenced_by_test": in_test,
        "reference_files_count": len(refs),
        "reference_files": refs[:5]
    })

print(f"\nAnalyzed {len(results)} redundant duplicate files.")
from collections import Counter
counts = Counter(r["dependency_class"] for r in results)
print("\n=== B. PATH_DEPENDENCY_MUST_BE_UPDATED_FIRST ===")
for r in results:
    if "B. PATH" in r["dependency_class"]:
        p = r["path"]
        sz = round(r["size_bytes"] / 1024, 1)
        refs = r["reference_files"]
        print(f"  {p} ({sz} KB) -> refs: {refs}")

print("\n=== C. KEEP_FOR_REPRODUCIBILITY ===")
for r in results:
    if "C. KEEP" in r["dependency_class"]:
        p = r["path"]
        sz = round(r["size_bytes"] / 1024, 1)
        reason = r["dependency_reason"]
        print(f"  {p} ({sz} KB) -> {reason}")
