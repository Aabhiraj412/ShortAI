"""
Test Suite for Browser-Accessible Production Health Dashboard.
Phase 5, Stage 3.19 Standard.

Validates:
1. /dashboard returns 200 with HTML content.
2. /api/dashboard/overview returns all required fields.
3. /api/dashboard/stats returns stats with time filtering.
4. /api/dashboard/health returns subsystem statuses.
5. /api/dashboard/history returns publishing ledger without exposing secrets.
6. /api/dashboard/recovery returns crash/checkpoint recovery history.
7. /api/dashboard/activity returns chronological activity feed.
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if os.path.join(WORKSPACE_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "backend"))

os.environ["ENABLE_EMBEDDED_SCHEDULER"] = "false"
from backend.app.main import app

client = TestClient(app)


def test_dashboard_html_page_served():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "CrazzyDocs Shorts Studio" in response.text
    assert "FOUR_CYCLES_DAILY" in response.text


def test_root_redirect_to_dashboard():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/dashboard"


def test_dashboard_overview_endpoint():
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    data = response.json()
    assert "current_cycle" in data
    assert "next_production_ist" in data
    assert "target_publishing_ist" in data
    assert "pipeline_stage" in data
    assert data["active_profile"] == "FOUR_CYCLES_DAILY"
    assert data["overall_health"] in ("HEALTHY", "DEGRADED", "CRITICAL")


def test_dashboard_stats_endpoint_with_filters():
    for f in ["today", "7d", "30d", "all"]:
        response = client.get(f"/api/dashboard/stats?filter={f}")
        assert response.status_code == 200
        data = response.json()
        assert data["time_filter"] == f
        assert "attempted" in data
        assert "completed" in data
        assert "published" in data
        assert "qc_failed" in data
        assert "missed" in data
        assert "recovered" in data


def test_dashboard_health_subsystems_endpoint():
    response = client.get("/api/dashboard/health")
    assert response.status_code == 200
    data = response.json()
    assert "scheduler_heartbeat" in data
    assert "windows_task" in data
    assert "database" in data
    assert "disk" in data
    assert data["database"]["status"] in ("HEALTHY", "DEGRADED")
    assert data["disk"]["free_gb"] > 0


def test_dashboard_publishing_history_security():
    response = client.get("/api/dashboard/history?limit=10")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    for item in items:
        assert "id" in item
        assert "topic_title" in item
        assert "upload_status" in item
        # Ensure credentials/tokens are never present
        assert "token" not in item
        assert "client_secret" not in item
        assert "oauth" not in item


def test_dashboard_recovery_endpoint():
    response = client.get("/api/dashboard/recovery?limit=10")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)


def test_dashboard_recent_activity_endpoint():
    response = client.get("/api/dashboard/activity?limit=15")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    if items:
        assert "timestamp" in items[0]
        assert "type" in items[0]
        assert "title" in items[0]
