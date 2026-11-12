"""
Global pytest configuration for CrazzyDocs Test Suite.
Enforces ExecutionContext.TEST across all tests, preventing test pollution
of the production publishing ledger, production notification channels, or live media workspace.

Database isolation (see docs/CLAUDE_EXECUTION_PROGRESS.md, "Test database isolation"):
1. This checkout's root is put first on sys.path, so `backend.*` never resolves to
   another checkout (the venv's crazzydocs.pth adds D:\\Agents\\CrazzyDocs).
2. CRAZZYDOCS_DATABASE_PATH points every app module at a throwaway SQLite file,
   set before any app module is imported.
3. sqlite3.connect is guarded for the whole session: opening any production
   studio.db raises immediately instead of reading or writing it.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# --- 1. Import this checkout, not whatever crazzydocs.pth points at -----------
CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
if str(CHECKOUT_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(CHECKOUT_ROOT))

# Enforce TEST execution context and TEST_MODE for all pytest runs
os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "TEST"
os.environ["CRAZZYDOCS_TEST_MODE"] = "1"

# A worktree has no gitignored portable binaries (tools/ffmpeg); borrow them read-only.
_MAIN_TOOLS = Path("D:/Agents/CrazzyDocs/tools")
if not (CHECKOUT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file() and (_MAIN_TOOLS / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
    os.environ.setdefault("CRAZZYDOCS_TOOLS_DIR", str(_MAIN_TOOLS))

# --- 2. Throwaway database for the whole session ------------------------------
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="crazzydocs-test-db-"))
TEST_DATABASE_PATH = _TEST_DB_DIR / "studio_test.db"
os.environ["CRAZZYDOCS_DATABASE_PATH"] = str(TEST_DATABASE_PATH)


# --- 3. Hard guard against opening a production database ----------------------
def _production_db_candidates() -> set[str]:
    candidates = {CHECKOUT_ROOT / "backend" / "studio.db"}
    for entry in sys.path:
        try:
            p = Path(entry or ".").resolve()
        except (OSError, ValueError):
            continue
        if (p / "backend" / "app").is_dir():
            candidates.add(p / "backend" / "studio.db")
    return {os.path.normcase(str(c.resolve())) for c in candidates}


PRODUCTION_DB_PATHS = _production_db_candidates()


def _target_path(database) -> str | None:
    if isinstance(database, bytes):
        database = database.decode()
    database = os.fspath(database)
    if database in ("", ":memory:"):
        return None
    if database.startswith("file:"):
        database = database[5:].split("?", 1)[0]
        if database.startswith("///"):
            database = database[3:]
        if database in ("", ":memory:"):
            return None
    return os.path.normcase(str(Path(database).resolve()))


_real_connect = sqlite3.dbapi2.connect


def _guarded_connect(database, *args, **kwargs):
    target = _target_path(database)
    if target is not None and target in PRODUCTION_DB_PATHS:
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: refusing to open production database {database!r}. "
            "Use backend.app.core.paths.DATABASE_PATH (the per-session test DB) or tmp_path."
        )
    return _real_connect(database, *args, **kwargs)


sqlite3.connect = _guarded_connect
sqlite3.dbapi2.connect = _guarded_connect


# --- Verify imports and DB path really resolve to the isolated setup ----------
import backend as _backend_pkg  # noqa: E402

# `backend` is a namespace package: its search path follows sys.path order, and
# legacy test modules insert D:\Agents\CrazzyDocs at sys.path[0] at import time,
# which would make later `backend.*` imports load another checkout's code. Pin it.
_backend_pkg.__path__ = [str(CHECKOUT_ROOT / "backend")]

from backend.app.core import paths as _paths  # noqa: E402

if not Path(_paths.__file__).resolve().is_relative_to(CHECKOUT_ROOT):
    pytest.exit(
        f"backend.app resolves to {_paths.__file__}, not this checkout ({CHECKOUT_ROOT}).",
        returncode=3,
    )
if Path(_paths.DATABASE_PATH).resolve() != TEST_DATABASE_PATH.resolve():
    pytest.exit(f"DATABASE_PATH is {_paths.DATABASE_PATH}, expected the test DB.", returncode=3)


# --- 4. Tests that cannot run safely in isolation ------------------------------
# Audits of the live production database (exact row counts, specific YouTube IDs).
# They are not unit tests; run them as read-only scripts against a DB snapshot.
LIVE_DB_AUDIT_TESTS = {
    "test_stage_3_21b_ledger_cleanup.py": None,  # whole module
    "test_stage_3_21c_dashboard_accuracy.py": None,
    "test_stage_3_17_deployment.py": {"test_publishing_ledger_reconciliation", "test_existing_private_upload_preserved"},
}
MAIN_CHECKOUT_MARKERS = ("D:\\Agents\\CrazzyDocs", "D:/Agents/CrazzyDocs")
# Reviewed: references the main install path only to *query* machine state read-only
# (Get-ScheduledTask), so it is safe to run from any checkout.
READ_ONLY_MAIN_PATH_TESTS = {"test_windows_startup_configuration.py"}
_IS_MAIN_CHECKOUT = os.path.normcase(str(CHECKOUT_ROOT)) == os.path.normcase("D:\\Agents\\CrazzyDocs")


def pytest_collection_modifyitems(config, items):
    source_cache: dict[str, bool] = {}
    for item in items:
        fname = Path(str(item.fspath)).name
        if fname in LIVE_DB_AUDIT_TESTS:
            names = LIVE_DB_AUDIT_TESTS[fname]
            if names is None or item.originalname in names:
                item.add_marker(pytest.mark.skip(
                    reason="Live production DB audit: blocked by test isolation (run read-only against a snapshot)."
                ))
                continue
        if not _IS_MAIN_CHECKOUT and fname not in READ_ONLY_MAIN_PATH_TESTS:
            path = str(item.fspath)
            if path not in source_cache:
                try:
                    text = Path(path).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    text = ""
                source_cache[path] = any(m in text for m in MAIN_CHECKOUT_MARKERS)
            if source_cache[path]:
                item.add_marker(pytest.mark.skip(
                    reason=f"Hardcodes the main checkout path; would read/write {MAIN_CHECKOUT_MARKERS[0]} media from {CHECKOUT_ROOT}."
                ))


def _foreign_backend_modules() -> list[str]:
    root = os.path.normcase(str(CHECKOUT_ROOT))
    bad = []
    for name, mod in list(sys.modules.items()):
        # app.main imports `app.*` (backend/ on sys.path) alongside `backend.app.*`;
        # both namespaces must come from this checkout.
        if not (name in ("backend", "app") or name.startswith(("backend.", "app."))):
            continue
        f = getattr(mod, "__file__", None)
        if f and not os.path.normcase(str(Path(f).resolve())).startswith(root):
            bad.append(f"{name} -> {f}")
    return bad


def pytest_collection_finish(session):
    bad = _foreign_backend_modules()
    if bad:
        pytest.exit("Imported backend modules from another checkout:\n" + "\n".join(bad[:20]), returncode=3)


def pytest_sessionfinish(session, exitstatus):
    bad = _foreign_backend_modules()
    if bad:
        session.exitstatus = 3
        print("\nTEST ISOLATION VIOLATION: backend modules loaded from another checkout:\n" + "\n".join(bad[:20]))


@pytest.fixture(scope="session", autouse=True)
def isolated_test_database():
    """Creates the schema in the throwaway DB once per session and deletes it afterwards."""
    from backend.app.database.db import init_db, engine

    init_db()
    yield TEST_DATABASE_PATH
    engine.dispose()
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def ensure_test_context(monkeypatch):
    """Guarantees that CRAZZYDOCS_EXECUTION_CONTEXT is always TEST during every test."""
    monkeypatch.setenv("CRAZZYDOCS_EXECUTION_CONTEXT", "TEST")
    monkeypatch.setenv("CRAZZYDOCS_TEST_MODE", "1")
    yield


@pytest.fixture(autouse=True)
def isolate_test_media_cleanup():
    """Teardown hook that cleans up any stray test files or directories in media/."""
    yield
    from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
    media_dir = os.path.join(WORKSPACE, "media")
    # Clean stray audit, thumbnails, video, productions test directories if created
    for sub in ["audit", "_test_tmp"]:
        target = os.path.join(media_dir, sub)
        if os.path.exists(target):
            shutil.rmtree(target, ignore_errors=True)

    # Clean test productions in media/productions
    prod_dir = os.path.join(media_dir, "productions")
    if os.path.exists(prod_dir):
        for item in os.listdir(prod_dir):
            if item.startswith("test_") or item.startswith("mock_"):
                shutil.rmtree(os.path.join(prod_dir, item), ignore_errors=True)
