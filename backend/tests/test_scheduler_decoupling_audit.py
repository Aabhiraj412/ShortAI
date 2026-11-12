"""
Phase D audit: production scheduling depends only on start time, recovery window,
enabled state, weekdays, timezone and the missed-cycle (STRICT_WINDOW) policy.
"""
import ast
from pathlib import Path

from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[1] / "app"
SLOT_NAMES = {
    "calculate_next_available_publishing_slot",
    "compute_next_publication_slot",
    "get_publication_schedule",
    "get_publication_recovery_timeline",
    "_map_cycles_to_publication_slots",
    "is_deadline_exceeded",
}


def _called_names(path: Path, functions: set[str]) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            names = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            out[node.name] = names
    return out


def test_production_dispatch_and_recovery_never_read_publication_slots():
    checks = {
        APP / "scheduler" / "scheduler.py": {"run_cycle_generation", "dispatch_eligible_recoveries", "configure_scheduler_jobs"},
        APP / "scheduler" / "reconciliation_service.py": {"reconcile_on_startup"},
        APP / "orchestration" / "crazzydocs_master_orchestrator.py": {"execute_production_pipeline", "complete_queue_handoff"},
    }
    for path, fns in checks.items():
        calls = _called_names(path, fns)
        assert set(calls) == fns, (path, fns - set(calls))
        for fn, names in calls.items():
            assert not (names & SLOT_NAMES), (path.name, fn, names & SLOT_NAMES)


def test_legacy_publication_schedule_routes_are_local_only(monkeypatch):
    from backend.app import main as main_module
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    routes = [
        ("get", "/api/control-center/publication-schedule", None),
        ("get", "/api/control-center/publication-recovery", None),
        ("post", "/api/control-center/publication-schedule/preset", {"preset_name": "FOUR_DAILY"}),
        ("post", "/api/control-center/publication-schedule/draft", {}),
        ("post", "/api/control-center/publication-schedule/validate", None),
        ("post", "/api/control-center/publication-schedule/activate", {"confirm": True}),
        ("post", "/api/control-center/publication-schedule/cancel", None),
    ]
    with TestClient(main_module.app, base_url="http://192.168.1.50:8000", client=("192.168.1.77", 50000)) as remote:
        for method, url, body in routes:
            r = getattr(remote, method)(url, json=body) if body is not None else getattr(remote, method)(url)
            assert r.status_code == 403, (url, r.status_code)
