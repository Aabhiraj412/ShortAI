"""
Cycle 4 (2026-11-13) rendered a valid video and then crashed on
`prod_ws.save_manifest(...)`: ProductionWorkspace never had that method. The code after
the render had never executed before, because every earlier render failed first.

These tests pin the fix and guard the whole orchestrator against the same class of bug:
a call to a method that does not exist on a known object only fails at runtime, on the
first production that reaches it.
"""
import ast
import json
from pathlib import Path

from backend.app.orchestration import crazzydocs_master_orchestrator as om
from backend.app.orchestration.production_workspace import ProductionWorkspace

ORCHESTRATOR_SRC = Path(om.__file__).read_text(encoding="utf-8")


def _workspace(tmp_path):
    ws = ProductionWorkspace(production_id="job_post_render", topic_id="t1", topic_title="Topic",
                             category="Science", base_dir=str(tmp_path / "prod"))
    ws.initialize_workspace()
    return ws


def test_post_render_manifest_sequence_matches_the_orchestrator(tmp_path):
    """Exactly what crazzydocs_master_orchestrator does right after the mux."""
    ws = _workspace(tmp_path)
    manifest = ws.load_manifest() or {}
    manifest["renderer"] = "lemo-opuscar"
    manifest["pinned_commit"] = "abc123"
    manifest["fallback_used"] = False
    ws.save_manifest(manifest)                                    # crashed on 2026-11-13
    report = tmp_path / "render_telemetry.json"
    report.write_text("{}", encoding="utf-8")
    ws.register_artifact("STAGE_09_RENDERING_TELEMETRY", "report", str(report), copy_into_workspace=False)

    saved = json.loads(Path(ws.manifest_path).read_text(encoding="utf-8"))
    assert (saved["renderer"], saved["pinned_commit"], saved["fallback_used"]) == ("lemo-opuscar", "abc123", False)
    assert any("STAGE_09_RENDERING_TELEMETRY" in json.dumps(a) for a in saved["artifacts"])


def test_echoed_generated_fields_are_not_frozen(tmp_path):
    ws = _workspace(tmp_path)
    before = ws.load_manifest()
    ws.save_manifest(dict(before, renderer="lemo-opuscar"))
    report = tmp_path / "r.json"
    report.write_text("{}", encoding="utf-8")
    ws.register_artifact("X", "report", str(report), copy_into_workspace=False)
    after = ws.load_manifest()
    assert after["artifacts_count"] == before.get("artifacts_count", 0) + 1   # still derived
    assert after["renderer"] == "lemo-opuscar"


def _local_types(tree):
    types = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)):
            f = node.value.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            cls = getattr(om, name, None) if name else None
            if isinstance(cls, type):
                types.setdefault(node.targets[0].id, set()).add(cls)
    return types


def test_orchestrator_never_calls_a_missing_method_on_a_known_object():
    """Static guard: X.method(...) where X's class is known must have that method."""
    tree = ast.parse(ORCHESTRATOR_SRC)
    orch = om.master_orchestrator
    local_types = _local_types(tree)
    missing, checked = [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method, target = node.func.attr, node.func.value
        owners = []
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
            if hasattr(orch, target.attr):
                owners = [type(getattr(orch, target.attr))]
        elif isinstance(target, ast.Name) and target.id == "self":
            owners = [type(orch)]
        elif isinstance(target, ast.Name) and target.id in local_types:
            owners = list(local_types[target.id])
        for owner in owners:
            if owner in (dict, list, str, type(None)):
                continue
            checked += 1
            if not hasattr(owner, method):
                missing.append(f"line {node.lineno}: {ast.unparse(node.func)} ({owner.__name__})")
    assert checked >= 40, "scanner resolved too few calls to be meaningful"   # 55 at the time of writing
    assert ProductionWorkspace in local_types.get("prod_ws", set())
    assert missing == []
