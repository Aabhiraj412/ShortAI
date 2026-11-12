"""Regression tests for the production lifecycle event logger."""
import os
import sqlite3

from backend.app.core.paths import DATABASE_PATH
from backend.app.services import lifecycle_events


def test_module_resolves_production_db_path():
    # Regression: module used settings.DATABASE_URL, which does not exist, and
    # broke importing scheduler.py / main.py.
    assert lifecycle_events.DB_PATH == str(DATABASE_PATH)


def test_explicit_db_path_persists_and_lists(tmp_path):
    db = str(tmp_path / "events.db")
    event = lifecycle_events.record_lifecycle_event(
        "CYCLE_JOB_CREATED", "QUEUED", cycle_id="cycle_1", job_id="job_1", db_path=db
    )
    assert event["event_type"] == "CYCLE_JOB_CREATED"

    rows = lifecycle_events.list_lifecycle_events(cycle_id="cycle_1", db_path=db)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "job_1"
    assert rows[0]["execution_state"] == "QUEUED"


def test_test_context_never_writes_production_db(monkeypatch):
    # conftest sets CRAZZYDOCS_EXECUTION_CONTEXT=TEST; without an explicit
    # db_path the logger must not open the production database at all.
    opened = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        lifecycle_events.sqlite3,
        "connect",
        lambda path, *a, **k: opened.append(path) or real_connect(":memory:"),
    )
    event = lifecycle_events.record_lifecycle_event("CYCLE_DUE", "DUE", cycle_id="c")
    assert event["event_type"] == "CYCLE_DUE"
    assert lifecycle_events.list_lifecycle_events() == []
    assert opened == []


def test_production_context_targets_production_db(monkeypatch):
    monkeypatch.setenv("CRAZZYDOCS_EXECUTION_CONTEXT", "PRODUCTION")
    monkeypatch.delenv("CRAZZYDOCS_TEST_MODE", raising=False)
    assert lifecycle_events._resolve_db(None) == str(DATABASE_PATH)
    assert os.environ.get("CRAZZYDOCS_TEST_MODE") is None
