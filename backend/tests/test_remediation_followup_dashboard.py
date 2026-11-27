"""Remediation follow-up: interventions are requested in one place; pages no longer repeat approval instructions."""
from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_one_global_attention_badge_opens_the_feed():
    assert 'id="pillAttention"' in HTML and "function openAttention()" in HTML
    assert "function refreshAttentionBadge()" in HTML and 'apiJson("/api/operations/attention")' in HTML
    assert 'id="attnCard"' in HTML


def test_pages_no_longer_repeat_the_autonomy_control_instruction():
    assert HTML.count("are managed in Autonomy Control") == 0
    assert "Approve, reject or execute them in Autonomy Control." not in HTML
    assert "<strong>nothing is executed</strong>" not in HTML      # no longer true: routine follow-ups run


def test_existing_controls_are_kept():
    for needle in ("function healAct", "function apPropose", "/api/autonomy/permissions/levels",
                   "function apCtlStart", "function apCtlStop", "function pqPause", "function intelCreateExperiment"):
        assert needle in HTML, needle
