# CrazzyDocs engineering execution policy

Context: `docs/PROJECT_HANDOVER.md` is the single authoritative engineering context (architecture, commits, live state, risks, test baseline, verification commands in §8). Read it instead of re-auditing the repository. Per-stage detail is in `docs/STAGE_*.md`.

## How to execute a milestone
1. **Scope first.** Read the handover, then audit only the modules the milestone touches. No full repository audits.
2. **Reuse.** Extend existing services, the scheduler, the control plane, the retry policy and the dashboard. Never add a second scheduler, queue, worker, permission system or recovery mechanism.
3. **Test.** Implement in one pass and run focused tests first. Run the full suite (about 23 min, in the background) when shared code changes: scheduler, pipeline, control plane, `main.py` or the dashboard. The baseline is 4 known failures (handover §4 item 6). Never hide failures or reclassify them casually.
4. **Report actual results.** Show test output, the live API state and log lines. Never assume success; say what was not verified.
5. **Close out.** Update handover §2, §3, §4 and §7 (and the stage doc), then send one consolidated report.

## Safety rules (always)
- **Protected data:** never modify or delete production data, `backend/studio.db` records, credentials or tokens, `.env.local`, media, backups, `topic_history.json`, `creative_policy_config.json` or other user modifications. Read production with `?mode=ro`.
- **Production jobs:** never interrupt an active job. Restart a process only through the deploy procedure (handover §8), between :15 and :50 IST with no active job, after a verified backup.
- **Git:** work in the dev worktree; fast-forward `main` only; stage only intended files; scan diffs for secrets. Never `reset --hard`, `clean`, force-push or rewrite history.
- **Never without explicit approval:**
  - public YouTube or Community publishing;
  - external messages;
  - OAuth, scope or credential changes;
  - schedule, production configuration or publishing-policy changes;
  - raising autonomy levels, granting standing approvals or creating `autonomy_startup.json`;
  - activating feature flags;
  - permanent deletion.
- **Cleanup:** dry-run first. Live cleanup is a reversible quarantine (`services/junk_cleaner.py`, `restore(run_id)`), only with an explicit confirmation and no active job.

## Commands
Tests (Git Bash): `W='D:\Agents\CrazzyDocs\.claude\worktrees\crazzy-docs-handover-b1a088'; PYTHONPATH="$W;$W\\backend;$W\\backend\\tests" /d/Agents/CrazzyDocs/.venv/Scripts/python.exe -m pytest backend/tests/<file> -q -p no:cacheprovider`. Use Windows paths, otherwise imports resolve to the main checkout. Live checks: handover §8.
