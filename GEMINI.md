# CrazzyDocs Workspace Operational Rules & Autonomous Execution Standard

## Workspace Context
- **Canonical Root Directory:** `D:\Agents\CrazzyDocs\`
- **Topology:** Multi-Agent Foundation (`D:\Agents\{AgentName}\`)
- **Agent Identity:** `crazzydocs` (CrazzyDocs YouTube Channel Agent)
- **Platform:** Windows (PowerShell / CMD)
- **Runtimes:** Python (`.venv`, `tools/python/python.exe`), Node.js / npm, FFmpeg (`tools/ffmpeg/bin/ffmpeg.exe`)
- **Policy Reference:** [`docs/AUTONOMOUS_EXECUTION_POLICY.md`](file:///D:/Agents/CrazzyDocs/docs/AUTONOMOUS_EXECUTION_POLICY.md)
- **Rollback Copy:** `D:\AI Arts\AI\` (Preserved reference copy)

---

## 1. Autonomous Execution Mandate
- Execute assigned tasks end-to-end within `D:\Agents\CrazzyDocs\` without requesting per-command confirmations for routine operations.
- Routine operations include: file editing, directory creation, dependency inspection, test execution, building, linting, git status, FFmpeg rendering, and database verification.
- When an error occurs, diagnose the root cause, formulate a safe fix, and retry independently. Do not pause mid-stage to ask permission to fix a bug or run the next command in a sequence.
- Present final results, test summaries, and audit reports upon completion of the assigned stage.

---

## 2. Command Formatting for Frictionless Execution
- Prefer prefix-matchable command structures (e.g. `python -m pytest ...`, `git status`, `ffmpeg -i ...`) to allow persistent platform-level authorization.
- Avoid unnecessary command chaining, command substitutions, or shell backgrounding (`&`) that break platform prefix-matching.
- Attempt Standard Sandbox mode (`BypassSandbox: false`) first for purely local file processing. When network or Windows loopback sockets (asyncio) require unsandboxed execution, use consistent, clean binary invocations.

---

## 3. Strict Security & Safety Boundaries
1. **Workspace Boundary:** Operate within `D:\Agents\CrazzyDocs\` (and preserve rollback copy at `D:\AI Arts\AI\`).
2. **OS & System Boundary:** Never modify Windows registry, security software, Windows Defender, execution policies, or system environment variables.
3. **Platform Integrity:** Never bypass or tamper with Antigravity platform access controls or permission checks.
4. **Data Protection:**
   - Ground-truth reference style templates in `docs/reference_styles/*.html` are strictly immutable. Never modify them.
   - Master branding assets in `Youtube Channel Src/` are original reference files. Preserve them untouched.
   - Database operations on `backend/studio.db` must preserve data integrity with automated backups before structural changes.
5. **Publishing Gate:** Maintain `settings.PUBLISHING_MODE = "manual_approval"`. Never upload or publish videos to YouTube or public networks without explicit manual authorization.
