"""
Global pytest configuration for CrazzyDocs Test Suite.
Enforces ExecutionContext.TEST across all tests, preventing test pollution
of the production publishing ledger, production notification channels, or live media workspace.
"""

import os
import shutil
import pytest

# Enforce TEST execution context and TEST_MODE for all pytest runs
os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "TEST"
os.environ["CRAZZYDOCS_TEST_MODE"] = "1"


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
