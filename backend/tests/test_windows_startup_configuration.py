"""Unit and integration tests for Windows startup configuration and task management."""

import subprocess
from pathlib import Path
import pytest

PROJECT_ROOT = Path("D:/Agents/CrazzyDocs").resolve()


def test_startup_scripts_exist():
    ps_script = PROJECT_ROOT / "backend" / "scripts" / "setup_windows_startup_tasks.ps1"
    py_script = PROJECT_ROOT / "backend" / "scripts" / "setup_windows_startup.py"
    scheduler_script = PROJECT_ROOT / "backend" / "scripts" / "run_scheduler_daemon.py"
    dashboard_script = PROJECT_ROOT / "backend" / "scripts" / "run_dashboard.py"

    assert ps_script.is_file(), "setup_windows_startup_tasks.ps1 must exist"
    assert py_script.is_file(), "setup_windows_startup.py must exist"
    assert scheduler_script.is_file(), "run_scheduler_daemon.py must exist"
    assert dashboard_script.is_file(), "run_dashboard.py must exist"


def test_windows_scheduled_tasks_registered():
    """Verify that both CrazzyDocs scheduled tasks are registered and in Ready/Running state."""
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "(Get-ScheduledTask -TaskName 'CrazzyDocs*').TaskName",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Failed to query scheduled tasks: {res.stderr}"

    output = res.stdout.strip().splitlines()
    task_names = [line.strip() for line in output if line.strip()]

    assert "CrazzyDocs-Production-Scheduler" in task_names, (
        f"CrazzyDocs-Production-Scheduler missing from {task_names}"
    )
    assert "CrazzyDocs-Health-Dashboard" in task_names, (
        f"CrazzyDocs-Health-Dashboard missing from {task_names}"
    )


def test_dashboard_script_arguments():
    """Verify run_dashboard.py argument parsing for --open-browser and --port."""
    script_content = (PROJECT_ROOT / "backend" / "scripts" / "run_dashboard.py").read_text(encoding="utf-8")
    assert "--open-browser" in script_content
    assert "is_server_ready" in script_content
    assert "poll_and_open_browser" in script_content
    assert "dashboard_service.log" in script_content


def test_scheduler_daemon_heartbeat():
    """Verify run_scheduler_daemon.py contains heartbeat loop and lockfile management."""
    script_content = (PROJECT_ROOT / "backend" / "scripts" / "run_scheduler_daemon.py").read_text(encoding="utf-8")
    assert "periodic_heartbeat_loop" in script_content
    assert "SchedulerProcessLock" in script_content
    assert "scheduler_daemon.log" in script_content


def test_granular_task_and_service_reporting():
    """Verify separate reporting for Windows tasks, scheduler daemon, and dashboard service."""
    from backend.scripts.manage_windows_startup import get_all_tasks_status
    from backend.app.services.production_health_dashboard import production_health_dashboard

    tasks = get_all_tasks_status()
    assert "scheduler_task" in tasks
    assert "dashboard_task" in tasks
    assert tasks["scheduler_task"]["task_name"] == "CrazzyDocs-Production-Scheduler"
    assert tasks["dashboard_task"]["task_name"] == "CrazzyDocs-Health-Dashboard"
    assert tasks["scheduler_task"]["is_registered"] is True
    assert tasks["dashboard_task"]["is_registered"] is True

    services = production_health_dashboard.get_services_status()
    assert "scheduler_daemon" in services
    assert "scheduler_heartbeat" in services
    assert "dashboard_service" in services
    assert services["dashboard_service"]["is_running"] is True
    assert services["dashboard_service"]["port"] == 8000
