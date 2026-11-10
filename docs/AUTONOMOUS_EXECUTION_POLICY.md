# Autonomous Execution Policy & Development Operating Standard

**Project Root:** `D:\Agents\CrazzyDocs\`  
**Project Scope:** Entire project repository and all subdirectories  
**Project Owner:** Abhiraj Dixit  
**Adopted Date:** November 10, 2026  
**Status:** ACTIVE & ENFORCED

---

## 1. Scope of Authorization

The AI engineering assistant (Antigravity) is granted one-time authorization to execute terminal commands, builds, tests, script operations, and file modifications within `D:\Agents\CrazzyDocs\` autonomously without requesting per-command manual confirmation.

### 1.1 Permitted Autonomous Operations:
1. **File System Operations**: Reading, creating, editing, refactoring, moving, archiving, and organizing project files and directories within `D:\Agents\CrazzyDocs\`.
2. **Runtime & Script Execution**:
   - Python script and module execution via `python-env` or bundled `tools/python/python.exe`.
   - Node.js, npm, and frontend build commands (`npm run build`, `npm test`, etc.).
   - Tooling execution (FFmpeg, FFprobe, Edge-TTS, CDP browser runner).
3. **Dependency Management**: Installing, updating, and auditing project-local packages in virtual environments (`python-env`, `node_modules`).
4. **Testing, Linting & Quality Assurance**:
   - Running test suites (`pytest`, custom verification scripts).
   - Generating audit reports, contact sheets, and telemetry summaries.
   - Validating quality gates (11-point rendering quality gate, boundary audits).
5. **Version Control**: Executing Git commands (`git status`, `git diff`, `git add`, `git commit`, `git log`) scoped to the local repository.
6. **Local Services & Daemons**: Starting, testing, and gracefully stopping project-local development servers and background tasks.
7. **Database Operations**: SQLite schema migrations, test queries, checkpoint saves, and state transitions in `backend/studio.db` with automated pre-modification backups.
8. **Autonomous Error Diagnosis & Self-Healing**:
   - Independently inspecting stack traces and terminal error logs.
   - Formulating and applying safe code corrections.
   - Retrying failed commands and continuing through the assigned development stage to completion without interrupting the user.

---

## 2. The 10 Mandatory Security Boundaries

This authorization applies **strictly and exclusively** to `D:\Agents\CrazzyDocs\`. The agent is strictly prohibited from executing the following actions without explicit, per-operation human approval:

1. **Workspace Boundary**: Never modify, delete, or create files or directories outside `D:\Agents\CrazzyDocs\`.
2. **Operating System Boundary**: Never modify Windows system settings, Windows Registry keys, boot configuration, disk partitions, or system environment variables.
3. **Security Policy Boundary**: Never alter, disable, or attempt to bypass Windows Defender, antivirus software, system firewall rules, or Windows Execution Policies.
4. **Platform Control Boundary**: Never defeat, bypass, or tamper with Antigravity's platform-level access controls, sandbox boundaries, or authentication mechanisms.
5. **Secrets & Credentials Boundary**: Never access, inspect, export, or expose user credentials, personal files, passwords, or raw API tokens outside safe environment variables.
6. **Data Protection Boundary**: Never delete or overwrite:
   - Original master branding assets (`Youtube Channel Src/`).
   - Immutable reference HTML templates (`docs/reference_styles/*.html`).
   - Production database tables without an immediate preceding backup.
7. **Publishing Authorization Boundary**: Keep `settings.PUBLISHING_MODE = "manual_approval"`. Never upload, publish, or publicly distribute videos, thumbnails, or metadata to YouTube or public networks without explicit manual human approval.
8. **Cost & Billing Boundary**: Never invoke paid third-party APIs, billable cloud subscriptions, or unapproved paid infrastructure. Maintain the local-first, zero-marginal-cost architecture.
9. **Privilege Boundary**: Never invoke elevated administrator (`runas`, UAC prompt, or administrator shell) privileges without explicit instructions.
10. **Targeted Escalation Boundary**: If an operation genuinely requires crossing any of the above boundaries or encounters an irrecoverable hardware/system conflict, pause and request specific authorization for that isolated operation only.

---

## 3. Platform Alignment & Command Prefix Matching

To ensure a smooth, uninterrupted workflow within Antigravity's terminal architecture:

### 3.1 Standard Sandbox vs. Bypass Sandbox
- **Standard Sandbox (`BypassSandbox: false`)**: Auto-runs within the sandbox without prompting the user. The agent prioritizes standard sandbox execution for all local file processing, builds, and analysis.
- **Bypass Sandbox (`BypassSandbox: true`)**: Required for network access (e.g. Edge-TTS synthesis, package downloads) and Windows asyncio loopback sockets. When the user approves a command with "Always allow", Antigravity remembers the binary/subcommand prefix.

### 3.2 Auto-Approvable Command Shapes
The agent adheres to prefix-matchable command shapes so user approvals persist:
- Direct binary invocation (`python -m pytest ...`, `ffmpeg -i ...`) rather than shell wrappers (`& "long-path\python.exe" -c "..."`).
- Literal arguments over complex variable expansions and backtick command substitutions.
- Direct file and test targets rather than multi-command shell chaining where individual steps can be executed cleanly.

---

## 4. Execution Philosophy

- **Goal-Oriented Autonomy**: The user specifies the task, requirements, and boundaries; the agent executes the complete implementation end-to-end.
- **Continuous Momentum**: Do not ask "May I proceed with the next step?" between routine operations.
- **Transparent Audit Trail**: Every code modification, test execution, benchmark result, and media artifact is logged and reported upon stage completion.
