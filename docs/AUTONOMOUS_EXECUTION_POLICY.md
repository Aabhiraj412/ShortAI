# Autonomous Execution Policy & Development Operating Standard

**Project Root:** `D:\AI Arts\AI\`  
**Project Owner:** Abhiraj Dixit  
**Adopted Date:** November 10, 2026  
**Status:** ACTIVE & ENFORCED

---

## 1. Scope of Authorization

The AI engineering agent (Antigravity) is formally authorized to execute routine development commands autonomously within `D:\AI Arts\AI\` without requesting per-command manual confirmation.

### Permitted Autonomous Operations:
1. **Shell & Script Execution:** PowerShell and CMD commands scoped to the project workspace.
2. **Runtime & Packages:** Python execution via `tools/python/python.exe`, pip package inspection, Node.js and npm builds.
3. **Media & Rendering:** FFmpeg / FFprobe operations, browser CDP headless canvas rendering, and procedural audio synthesis.
4. **File Lifecycle:** Project-specific file creation, modification, refactoring, and temp cleanup within `D:\AI Arts\AI\`.
5. **Database Operations:** SQLite schema migrations, test queries, checkpoint verification, and state transitions in `studio.db`.
6. **Automation & Testing:** Unit test execution, QualityGate compliance validation, contact sheet extraction, and background job testing.
7. **Diagnostics & Debugging:** Log inspection, performance profiling, error diagnosis, and automated bug fixes.

---

## 2. The 10 Mandatory Boundaries

The following hard boundaries are strictly enforced and require explicit human authorization before any action:

1. **Workspace Boundary:** Never modify or delete any file or directory outside `D:\AI Arts\AI\`.
2. **Operating System Boundary:** Never alter Windows system settings, system registry, security policies, or global system environment variables.
3. **Security Boundary:** Never disable security software, bypass sandbox boundaries, or alter firewall rules.
4. **Data Protection Boundary:** Never delete or alter production databases, historical backups, or ground-truth reference files (`docs/reference_styles/*.html`).
5. **Publishing Authorization Boundary:** Always maintain the strict manual publishing approval gate (`settings.PUBLISHING_MODE = "manual_approval"`).
6. **Public Distribution Boundary:** Never upload, publish, or publicly distribute generated videos, audio, or metadata to YouTube or public networks without explicit user authorization.
7. **Cost & Subscription Boundary:** Never introduce paid APIs, billable cloud subscriptions, or paid infrastructure. Maintain the local-first, zero-marginal-cost architecture.
8. **Backup Prerequisite:** Always create a timestamped backup before initiating destructive bulk file or database modifications.
9. **Privilege Boundary:** Never invoke elevated administrator (`runas` / UAC) privileges unless explicitly commanded and approved.
10. **Integrity & Regression Boundary:** Always maintain project integrity and run relevant regression test suites (`test_job_manager.py`, `test_stick_animation.py`, `QualityGate`) after significant structural changes.

---

## 3. Execution Philosophy & Reporting

- **Autonomous Resolution:** Investigate and resolve ordinary syntax errors, missing dependencies, coordinate overflows, and test failures independently.
- **Minimal Disruption:** Do not interrupt the workflow for routine build, compile, format, or test commands.
- **Targeted Escalation:** Escalate to the project owner only when an architectural design choice requires product alignment or when an operation intersects the 10 Mandatory Boundaries.
- **Transparent Reporting:** Report concise summaries of significant code modifications, test results, benchmarks, and generated media assets.
