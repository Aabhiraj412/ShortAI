"""
Stage 3.16: First Independent Production Verification Execution Script.
Executes exactly one complete production cycle using CrazzyDocsMasterOrchestrator
from the canonical location at D:\\Agents\\CrazzyDocs\\.
"""

import json
import logging
import os
import subprocess
import sys
import time

DEST_ROOT = r"D:\Agents\CrazzyDocs"
os.chdir(DEST_ROOT)
if DEST_ROOT not in sys.path:
    sys.path.insert(0, DEST_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("stage_3_16_execution")

def main():
    print("=" * 80)
    print("STAGE 3.16: EXECUTING FIRST INDEPENDENT CANONICAL PRODUCTION")
    print("=" * 80)
    t0 = time.time()

    from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
    orchestrator = CrazzyDocsMasterOrchestrator()

    job_id = "prod_stage_3_16_verification"
    logger.info("Starting master production pipeline with job_id='%s'...", job_id)

    res = orchestrator.execute_production_pipeline(
        job_id=job_id,
        force_topic_id="cand_03_vesna_fall",
    )

    elapsed = time.time() - t0
    print("\n" + "=" * 80)
    print("PRODUCTION PIPELINE FINISHED IN %.2f SECONDS" % elapsed)
    print("=" * 80)
    print("Result summary:")
    print(json.dumps(res, indent=2))

    # Save summary report in destination
    summary_path = os.path.join(DEST_ROOT, "media", "productions", job_id, "reports", "stage_3_16_production_summary.json")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(f"\nSaved production summary to {summary_path}")

if __name__ == "__main__":
    main()
