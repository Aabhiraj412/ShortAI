"""
Windows Startup & Scheduled Task Management Utility for CrazzyDocs.
Phase 5 Production Standard (Stage 3.20B).

Inspects, registers, and manages user-level Windows Scheduled Tasks for:
  - CrazzyDocs-Production-Scheduler (Autonomous 4-Cycle Scheduler Engine)
  - CrazzyDocs-Health-Dashboard     (FastAPI Server & Browser Dashboard Auto-Launch)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.database.db import SessionLocal, StudioSettingModel

logger = logging.getLogger(__name__)

SCHEDULER_TASK_NAME = "CrazzyDocs-Production-Scheduler"
DASHBOARD_TASK_NAME = "CrazzyDocs-Health-Dashboard"
STARTUP_MANAGER_TASK_NAME = "CrazzyDocs-Startup-Manager"
MONITORING_TASK_NAME = "CrazzyDocs-Health-Monitoring"
LEGACY_TASK_NAME = "CrazzyDocsStudioScheduler"
TASK_NAME = SCHEDULER_TASK_NAME
DEFAULT_LAUNCHER_BAT = os.path.join(WORKSPACE_ROOT, "start_studio.bat")
PYTHONW_EXE = os.path.join(WORKSPACE_ROOT, ".venv", "Scripts", "pythonw.exe")


def get_opt_in_setting() -> bool:
    """Reads opt-in setting from database."""
    try:
        with SessionLocal() as db_session:
            setting = db_session.query(StudioSettingModel).filter(
                StudioSettingModel.key == "AUTO_START_ON_WINDOWS_LOGIN"
            ).first()
            if setting:
                return setting.value.lower() in ("true", "1", "yes")
    except Exception:
        pass
    return False


def set_opt_in_setting(enabled: bool):
    """Saves opt-in preference to studio_settings."""
    try:
        with SessionLocal() as db_session:
            setting = db_session.query(StudioSettingModel).filter(
                StudioSettingModel.key == "AUTO_START_ON_WINDOWS_LOGIN"
            ).first()
            if not setting:
                setting = StudioSettingModel(
                    key="AUTO_START_ON_WINDOWS_LOGIN",
                    value="true" if enabled else "false"
                )
                db_session.add(setting)
            else:
                setting.value = "true" if enabled else "false"
            db_session.commit()
    except Exception as e:
        logger.warning("Could not persist startup setting: %s", e)


def _get_subprocess_flags() -> Dict[str, Any]:
    """Returns kwargs for subprocess calls on Windows to suppress console window creation."""
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) prevents creating a console window for CUI processes
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs


def query_task_details(task_name: str) -> Dict[str, Any]:
    """Queries schtasks /Query /TN <task_name> /FO LIST /V for granular task details."""
    cmd = ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8, **_get_subprocess_flags())
        if res.returncode != 0:
            return {
                "task_name": task_name,
                "is_registered": False,
                "status": "NOT_REGISTERED",
                "task_state": "Not Registered",
                "last_run_time": None,
                "last_result": None,
                "details": "Not registered in Windows Task Scheduler",
            }
        data = {}
        for line in res.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()

        status = data.get("Status", "Unknown")
        task_state = data.get("Scheduled Task State", "Unknown")
        last_run = data.get("Last Run Time")
        last_res = data.get("Last Result")
        has_never_run = "1999" in (last_run or "") or last_res == "267011"

        return {
            "task_name": task_name,
            "is_registered": True,
            "status": status,
            "task_state": task_state,
            "has_never_run": has_never_run,
            "last_run_time": "Never" if has_never_run else last_run,
            "last_result": last_res,
            "task_to_run": data.get("Task To Run"),
            "schedule_type": data.get("Schedule Type"),
            "details": res.stdout.strip(),
        }
    except Exception as e:
        return {
            "task_name": task_name,
            "is_registered": False,
            "status": "ERROR",
            "error": str(e),
            "details": f"Error querying task: {e}",
        }


def get_task_status(task_name: str = TASK_NAME) -> Dict:
    """Queries task status with backward compatibility for Stage 3.10 interface."""
    info = query_task_details(task_name)
    info["opt_in_enabled"] = get_opt_in_setting()
    return info


_tasks_cache: Dict[str, Any] = {}
_tasks_cache_time: float = 0.0


def get_all_tasks_status(ttl_seconds: float = 30.0) -> Dict[str, Any]:
    """Queries all 4 CrazzyDocs production scheduled tasks with in-memory TTL caching."""
    global _tasks_cache, _tasks_cache_time
    now = time.time()
    if _tasks_cache and (now - _tasks_cache_time < ttl_seconds):
        return _tasks_cache

    sm_info = query_task_details(STARTUP_MANAGER_TASK_NAME)
    s_info = query_task_details(SCHEDULER_TASK_NAME)
    d_info = query_task_details(DASHBOARD_TASK_NAME)
    m_info = query_task_details(MONITORING_TASK_NAME)

    any_registered = any(
        t.get("is_registered", False) for t in (sm_info, s_info, d_info, m_info)
    )

    res = {
        "task_name": SCHEDULER_TASK_NAME,
        "is_registered": any_registered,
        "opt_in_enabled": get_opt_in_setting(),
        "startup_manager_task": sm_info,
        "scheduler_task": s_info,
        "dashboard_task": d_info,
        "monitoring_task": m_info,
        "details": (
            f"StartupManager: {sm_info.get('status')}; "
            f"Dashboard: {d_info.get('status')}; "
            f"Scheduler: {s_info.get('status')}; "
            f"Monitoring: {m_info.get('status')}"
        ),
    }
    _tasks_cache = res
    _tasks_cache_time = now
    return res


def _register_user_logon_task(
    task_name: str,
    executable: str,
    arguments: str,
    enabled: bool = True,
) -> Tuple[bool, str]:
    """Registers a user-scoped ONLOGON task via ScheduledTasks PowerShell module without requiring admin."""
    ps_script = (
        f"$user = \"$env:USERDOMAIN\\$env:USERNAME\"; "
        f"$action = New-ScheduledTaskAction -Execute '{executable}' -Argument '{arguments}' -WorkingDirectory '{WORKSPACE_ROOT}'; "
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; "
        f"$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited; "
        f"$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0); "
        f"$task = Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force; "
        + ("" if enabled else f"Disable-ScheduledTask -TaskName '{task_name}' | Out-Null; ")
        + "Write-Output 'REGISTERED'"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=20,
            **_get_subprocess_flags(),
        )
        ok = res.returncode == 0 and "REGISTERED" in (res.stdout or "")
        return ok, (res.stdout.strip() or res.stderr.strip())
    except Exception as exc:
        return False, str(exc)


def install_all_studio_tasks(enable_scheduler: bool = False) -> Dict[str, Any]:
    """Registers all 4 user-level Windows Scheduled Tasks under current Windows user (/RL LIMITED)."""
    global _tasks_cache, _tasks_cache_time
    pyw = PYTHONW_EXE if os.path.exists(PYTHONW_EXE) else sys.executable
    tasks_spec = [
        (
            STARTUP_MANAGER_TASK_NAME,
            pyw,
            f'"{os.path.join(WORKSPACE_ROOT, "backend", "scripts", "startup_manager.py")}" --boot',
            True,
        ),
        (
            DASHBOARD_TASK_NAME,
            pyw,
            f'"{os.path.join(WORKSPACE_ROOT, "backend", "scripts", "run_dashboard.py")}" --open-browser',
            True,
        ),
        (
            MONITORING_TASK_NAME,
            pyw,
            f'"{os.path.join(WORKSPACE_ROOT, "backend", "scripts", "startup_manager.py")}" --monitor',
            True,
        ),
        (
            SCHEDULER_TASK_NAME,
            pyw,
            f'"{os.path.join(WORKSPACE_ROOT, "backend", "scripts", "run_scheduler_daemon.py")}"',
            enable_scheduler,
        ),
    ]
    results: Dict[str, Any] = {}
    all_ok = True
    for tname, exe_path, exe_args, should_enable in tasks_spec:
        ok, msg = _register_user_logon_task(tname, exe_path, exe_args, enabled=should_enable)
        results[tname] = {
            "ok": ok,
            "enabled": should_enable if ok else False,
            "message": msg,
        }
        if not ok:
            all_ok = False
    _tasks_cache = {}
    _tasks_cache_time = 0.0
    if all_ok:
        set_opt_in_setting(True)
    return {"all_ok": all_ok, "tasks": results}


def invalidate_tasks_cache() -> None:
    """Clears cached Windows Task Scheduler query results after enable/disable mutations."""
    global _tasks_cache, _tasks_cache_time
    _tasks_cache = {}
    _tasks_cache_time = 0.0


def install_startup_task(
    task_name: str = TASK_NAME,
    launcher_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Registers a user-level Scheduled Task that triggers when the operator logs into Windows.
    /SC ONLOGON /RL LIMITED does not require administrative elevation.
    """
    bat_path = launcher_path or DEFAULT_LAUNCHER_BAT
    if not os.path.exists(bat_path):
        return False, f"Launcher file missing at {bat_path}"

    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'"{bat_path}"',
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/F"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **_get_subprocess_flags())
        if res.returncode == 0:
            set_opt_in_setting(True)
            return True, f"Successfully registered Windows Scheduled Task '{task_name}'."
        else:
            return False, f"schtasks failed: {res.stderr.strip() or res.stdout.strip()}"
    except Exception as e:
        return False, f"Exception during task creation: {e}"


def uninstall_startup_task(task_name: str = TASK_NAME) -> Tuple[bool, str]:
    """Removes the Windows Scheduled Task and updates opt-in setting to False."""
    cmd = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **_get_subprocess_flags())
        set_opt_in_setting(False)
        if res.returncode == 0:
            return True, f"Successfully unregistered Windows Scheduled Task '{task_name}'."
        else:
            if "cannot find the file" in res.stderr.lower() or "not found" in res.stderr.lower():
                return True, f"Task '{task_name}' was not registered."
            return False, f"schtasks failed: {res.stderr.strip()}"
    except Exception as e:
        set_opt_in_setting(False)
        return False, f"Exception during task deletion: {e}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage CrazzyDocs Windows Startup Scheduled Tasks")
    parser.add_argument("--status", action="store_true", help="Check current task status")
    parser.add_argument("--install", action="store_true", help="Opt-in and register task on Windows user logon")
    parser.add_argument("--install-all", action="store_true", help="Register all 4 user-level studio startup tasks")
    parser.add_argument("--enable-scheduler", action="store_true", help="Also leave Production Scheduler enabled when used with --install-all")
    parser.add_argument("--uninstall", action="store_true", help="Opt-out and unregister task")
    args = parser.parse_args()

    if args.install_all:
        res = install_all_studio_tasks(enable_scheduler=args.enable_scheduler)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["all_ok"] else 1)
    elif args.install:
        ok, msg = install_startup_task()
        print(f"Result: {msg}")
        sys.exit(0 if ok else 1)
    elif args.uninstall:
        ok, msg = uninstall_startup_task()
        print(f"Result: {msg}")
        sys.exit(0 if ok else 1)
    else:
        st = get_all_tasks_status()
        print(json.dumps(st, indent=2))
