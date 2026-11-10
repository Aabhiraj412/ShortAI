"""
Test Suite for Production Health Dashboard.
Phase 5, Stage 3.18 Standard.
"""

from datetime import datetime
import pytest
from zoneinfo import ZoneInfo

from backend.app.services.production_health_dashboard import (
    ProductionHealthDashboard,
    ProductionHealthMetrics,
    production_health_dashboard,
)

IST_TZ = ZoneInfo("Asia/Kolkata")


def test_dashboard_gathers_all_eleven_metrics():
    """Verifies that all 11 required operational metrics are gathered."""
    metrics = production_health_dashboard.get_health_metrics()
    assert isinstance(metrics, ProductionHealthMetrics)
    assert metrics.current_cycle is not None
    assert "IST" in metrics.next_production_ist
    assert metrics.pipeline_stage is not None
    assert metrics.recovery_status in ("HEALTHY", "RECOVERING", "BLOCKED", "DEGRADED")
    assert metrics.network_status in ("CONNECTED", "DEGRADED", "DISCONNECTED", "UNKNOWN")
    assert "pending" in metrics.publishing_queue
    assert "published" in metrics.publishing_queue
    assert "count" in metrics.failed_qc_jobs
    assert "sent" in metrics.email_notifications
    assert "state" in metrics.scheduler_heartbeat
    assert metrics.overall_health in ("HEALTHY", "DEGRADED", "CRITICAL")


def test_dashboard_renders_ascii_table():
    """Verifies that console dashboard renders a valid ASCII table."""
    table = production_health_dashboard.render_console_dashboard()
    assert "CRAZZYDOCS PRODUCTION HEALTH DASHBOARD" in table
    assert "Overall System Health:" in table
    assert "Current Active Cycle:" in table
    assert "Publishing Queue:" in table
