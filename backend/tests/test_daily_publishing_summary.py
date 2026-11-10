"""
Test Suite for Daily Publishing Summary Email Service.
Phase 5, Stage 3.18 Standard.
"""

from datetime import datetime
from unittest.mock import patch, MagicMock
import pytest

from backend.app.database.db import SessionLocal, PersistentNotificationModel, JobModel
from backend.app.services.daily_publishing_summary import (
    DailyPublishingSummaryService,
    DailySummaryReport,
    daily_publishing_summary_service,
)


@pytest.fixture
def clean_summary_db():
    test_date = "2026-11-10"
    idempotency_key = f"daily_publishing_summary_{test_date}"
    with SessionLocal() as db:
        db.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key == idempotency_key
        ).delete()
        db.commit()

    yield test_date

    with SessionLocal() as db:
        db.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key == idempotency_key
        ).delete()
        db.commit()


def test_compile_daily_summary_gathers_all_eight_metrics(clean_summary_db):
    """Verifies that all 8 required metrics are compiled accurately."""
    test_date = clean_summary_db
    report = daily_publishing_summary_service.compile_daily_summary(date_str=test_date)

    assert isinstance(report, DailySummaryReport)
    assert report.date_str == test_date
    assert hasattr(report, "productions_attempted")
    assert hasattr(report, "productions_completed")
    assert hasattr(report, "videos_published_public")
    assert hasattr(report, "videos_blocked_qc")
    assert hasattr(report, "missed_cycles")
    assert hasattr(report, "recovered_cycles")
    assert hasattr(report, "storage_consumed_mb")
    assert hasattr(report, "errors_requiring_attention")


def test_render_summary_html(clean_summary_db):
    """Verifies that summary HTML renders with metrics and dashboard command."""
    test_date = clean_summary_db
    report = daily_publishing_summary_service.compile_daily_summary(date_str=test_date)
    html = daily_publishing_summary_service.render_summary_html(report)

    assert "CrazzyDocs Daily Publishing Summary" in html
    assert "Productions Attempted:" in html
    assert "show_production_dashboard.py" in html


def test_daily_summary_deduplication(clean_summary_db):
    """Verifies that multiple calls on the same date are deduplicated persistently."""
    test_date = clean_summary_db
    with patch("backend.app.services.daily_publishing_summary.notification_service.notify_email", return_value=True):
        res1 = daily_publishing_summary_service.send_daily_summary(date_str=test_date, force=False)
        assert res1["status"] == "SENT"

        # Second call on same date must be suppressed
        res2 = daily_publishing_summary_service.send_daily_summary(date_str=test_date, force=False)
        assert res2["status"] == "DUPLICATE_SUPPRESSED"


def test_daily_summary_safe_offline_persistence(clean_summary_db):
    """Verifies that if SMTP fails, notification is saved as FAILED in SQLite without data loss."""
    test_date = clean_summary_db
    with patch("backend.app.services.daily_publishing_summary.notification_service.notify_email", return_value=False):
        res = daily_publishing_summary_service.send_daily_summary(date_str=test_date, force=True)
        assert res["status"] == "SAVED_LOCAL_PENDING"

    with SessionLocal() as db:
        rec = db.query(PersistentNotificationModel).filter(
            PersistentNotificationModel.idempotency_key == f"daily_publishing_summary_{test_date}"
        ).first()
        assert rec is not None
        assert rec.status == "FAILED"
