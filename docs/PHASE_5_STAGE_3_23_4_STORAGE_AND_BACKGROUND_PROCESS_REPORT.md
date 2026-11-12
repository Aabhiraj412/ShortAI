# Phase 5 — Stage 3.23.4: Canonical Storage & Background Process Visibility Report

## 1. Background Window Flashing Elimination
- **`backend/app/core/process_utils.py`**: Implemented `hidden_subprocess_kwargs()`, `run_hidden()`, `popen_hidden()`, and `install_windows_hidden_subprocess_defaults()` (`CREATE_NO_WINDOW` + `STARTF_USESHOWWINDOW` + `SW_HIDE`).
- Installed `install_windows_hidden_subprocess_defaults()` at startup in:
  - `backend/scripts/startup_manager.py`
  - `backend/scripts/run_scheduler_daemon.py`
  - `backend/scripts/run_dashboard.py`
  - `backend/app/main.py`
- Updated explicit subprocess callers in `lemo_opuscar_adapter.py`, `notification_service.py`, `documentary_storyboard_builder.py`, `expressive_tts_engine.py`, `engine.py`, and `crazzydocs_master_orchestrator.py`.
- Verified `manage_windows_startup.py` registers all 4 Windows Scheduled Tasks using `pythonw.exe`.

## 2. Process Monitor & Redacted Logs
- **`GET /api/control-center/processes`**: Enumerates active CrazzyDocs processes (PID, role, memory MB, window mode) and Windows Scheduled Tasks.
- **`GET /api/control-center/logs`** & **`GET /api/control-center/logs/download`**: Supports level/category/search filtering with automatic secret/token redaction (`_redact_log_secrets`).

## 3. Canonical Storage Configuration
- **`backend/app/services/control_center_service.py`** & **`backend/app/main.py`**:
  - Added persisted configuration and validation for `production_output_root`, `final_video_dir`, `thumbnail_dir`, `temp_render_dir`, `archive_dir`, and `quarantine_dir`.
  - Added `/api/control-center/storage-config`, `/api/control-center/storage-config/validate`, `/api/control-center/storage/open-folder`, and `/api/control-center/storage/browse`.
