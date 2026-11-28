# CrazzyDocs engineering execution policy

Context: `docs/PROJECT_HANDOVER.md` is the single authoritative engineering context (architecture, commits, live state, risks, test baseline, verification commands in §8). Read it instead of re-auditing the repository. Per-stage detail is in `docs/STAGE_*.md`.

## How to execute a milestone
1. **Scope first.** Read the handover, then audit only the modules the milestone touches. No full repository audits.
2. **Reuse.** Extend existing services, the scheduler, the control plane, the retry policy and the dashboard. Never add a second scheduler, queue, worker, permission system or recovery mechanism.
3. **Test.** Implement in one pass, then run the test level the change requires (`backend/scripts/select_tests.py` prints it; table below). The baseline is 4 known failures (handover §4 item 6). Never hide failures or reclassify them casually; skipped tests stay reported as skipped.

   | Change touches | Level before commit |
   |---|---|
   | Documentation only | none |
   | Test files or test helpers only | FAST (iterate with FAST at any time) |
   | Any `backend/app` module that is not shared architecture | RELATED |
   | Shared architecture: `scheduler/`, `orchestration/`, `database/`, `core/`, `templates/` (dashboard), `main.py`, `autonomy/control_plane.py`, `conftest.py`, `pytest.ini`, requirements | FULL (in the background) |
   | Before any deploy that restarts the daemon | at least RELATED; FULL if any shared-architecture file changed |

   Tests always run serially (no parallel execution: shared media/log folders, and production rendering shares this machine).
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
Test levels (Git Bash, from the dev worktree; the script sets PYTHONPATH and always runs serially):
- FAST (about 1–3 min): `/d/Agents/CrazzyDocs/.venv/Scripts/python.exe backend/scripts/select_tests.py --level fast --run`
- RELATED (about 5–10 min): `/d/Agents/CrazzyDocs/.venv/Scripts/python.exe backend/scripts/select_tests.py --level related --run`
- FULL (about 16 min measured; run it in the background): `/d/Agents/CrazzyDocs/.venv/Scripts/python.exe backend/scripts/select_tests.py --level full --run`
- Without `--run` it prints the changed files, the required level and each selected file with the reason. `--files <paths>` overrides the git diff (default: against `main`, plus uncommitted and untracked files).

Single file (Git Bash): `W='D:\Agents\CrazzyDocs\.claude\worktrees\crazzy-docs-handover-b1a088'; PYTHONPATH="$W;$W\\backend;$W\\backend\\tests" /d/Agents/CrazzyDocs/.venv/Scripts/python.exe -m pytest backend/tests/<file> -q -p no:cacheprovider`. Use Windows paths, otherwise imports resolve to the main checkout. `-m "not slow"` skips slow-marked tests. Live checks: handover §8.
