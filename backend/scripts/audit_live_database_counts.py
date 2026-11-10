"""
Audit Live Database Counts for Stage 3.21C.
Independently queries studio.db and prints exact counts.
"""

import sqlite3
import os

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
DB_PATH = os.path.join(WORKSPACE_ROOT, "backend", "studio.db")

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Active publishing ledger count
    cur.execute("SELECT count(*) FROM publishing_ledger;")
    active_pub_count = cur.fetchone()[0]

    # 2. Archived publishing ledger count
    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
    arch_pub_count = cur.fetchone()[0]

    # 3. Pending queue count (active ledger)
    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'pending_queue';")
    pending_queue_count = cur.fetchone()[0]

    # 4. Private upload count (active ledger)
    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'uploaded_private';")
    private_upload_count = cur.fetchone()[0]

    # 5. Public upload count (active ledger)
    cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status IN ('uploaded_public', 'published');")
    public_upload_count = cur.fetchone()[0]

    # 6. Persistent jobs breakdown
    cur.execute("SELECT id, status, current_stage, progress_pct, recovery_reason, error_details FROM persistent_jobs;")
    jobs = cur.fetchall()

    test_jobs = [j for j in jobs if (j[0] or "").startswith("test_")]
    genuine_jobs = [j for j in jobs if not (j[0] or "").startswith("test_")]
    genuine_attempted = len(genuine_jobs)
    genuine_completed = len([j for j in genuine_jobs if j[1] == "COMPLETED"])
    aborted_recovery_count = len([j for j in jobs if j[1] == "ABORTED_PRE_RESET"])

    # 7. Historical showcase count (in archive)
    cur.execute("SELECT count(*) FROM publishing_ledger_archive WHERE original_classification LIKE '%SHOWCASE%';")
    historical_showcase_count = cur.fetchone()[0]

    # 8. Archive breakdowns
    cur.execute("SELECT original_classification, count(*) FROM publishing_ledger_archive GROUP BY original_classification;")
    arch_groups = dict(cur.fetchall())

    conn.close()

    print("=== LIVE DATABASE VERIFIED COUNTS ===")
    print(f"Active Publishing Ledger Count:      {active_pub_count}")
    print(f"Archived Publishing Ledger Count:    {arch_pub_count}")
    print(f"Pending Queue Count:                 {pending_queue_count}")
    print(f"Private Upload Count:                {private_upload_count}")
    print(f"Public Upload Count:                 {public_upload_count}")
    print(f"Total Persistent Jobs:               {len(jobs)}")
    print(f"  Test Executions:                   {len(test_jobs)}")
    print(f"  Genuine Production Attempts:       {genuine_attempted}")
    print(f"  Genuine Completed Productions:     {genuine_completed}")
    print(f"  Aborted Recovery Jobs:             {aborted_recovery_count}")
    print(f"Historical Showcase Records:         {historical_showcase_count}")
    print(f"Archived Breakdown:                  {arch_groups}")
    print("\nDetailed Persistent Jobs:")
    for j in jobs:
        print(f"  - ID: {j[0]} | Status: {j[1]} | Stage: {j[2]} | Progress: {j[3]}% | Error: {j[5]}")

if __name__ == "__main__":
    main()
