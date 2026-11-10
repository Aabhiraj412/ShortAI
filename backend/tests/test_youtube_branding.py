"""
Test Suite for Dashboard YouTube Branding Integration.
Phase 5 — YouTube Favicon & Header Branding Verification.
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

from backend.app.main import app
from backend.app.core.paths import PROJECT_ROOT

client = TestClient(app)

def test_source_youtube_icon_exists():
    source_path = os.path.join(PROJECT_ROOT, "media", "source", "branding", "Youtube-Icon.png")
    assert os.path.exists(source_path), f"Source YouTube icon missing: {source_path}"
    assert os.path.getsize(source_path) == 1970267, "Source YouTube icon size mismatch"


def test_static_youtube_icon_exists():
    static_path = os.path.join(PROJECT_ROOT, "backend", "app", "static", "branding", "Youtube-Icon.png")
    assert os.path.exists(static_path), f"Static YouTube icon missing: {static_path}"
    assert os.path.getsize(static_path) == 1970267, "Static YouTube icon size mismatch"


def test_favicon_endpoint_returns_png():
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) == 1970267
    assert "public, max-age=86400" in response.headers.get("cache-control", "")


def test_static_branding_route_returns_png():
    response = client.get("/static/branding/Youtube-Icon.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) == 1970267


def test_dashboard_html_contains_branding_elements():
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text
    # Favicon links
    assert '<link rel="icon" type="image/png" href="/favicon.ico?v=20261111"' in html
    assert '<link rel="shortcut icon" type="image/png" href="/favicon.ico?v=20261111"' in html
    assert '<link rel="apple-touch-icon" href="/static/branding/Youtube-Icon.png?v=20261111"' in html
    # Header branding
    assert 'yt-header-icon' in html
    assert '<img src="/static/branding/Youtube-Icon.png?v=20261111" alt="YouTube" class="yt-header-icon"' in html
    assert 'CrazzyDocs Shorts Studio' in html
