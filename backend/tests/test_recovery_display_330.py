"""Stage 3.30: recovery duration shown as 4 hours (never 4 minutes); API, scheduler and dashboard agree."""
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"
IST = ZoneInfo("Asia/Kolkata")


def _script() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S))


def test_no_function_is_declared_twice_in_the_dashboard():
    # two `function fmtDuration` declarations (minutes and seconds) caused "4 min": the later one won
    names = re.findall(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", _script(), flags=re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"functions declared more than once: {dupes}"


def test_recovery_duration_cells_use_the_minutes_formatter():
    js = _script()
    assert js.count("fmtMinutes(c.recovery_duration_minutes)") == 2
    assert "fmtSeconds(rt.remaining_seconds)" in js


def _extract(js: str, name: str) -> str:
    m = re.search(r"function\s+%s\s*\(.*?\n}\n" % name, js, flags=re.S)
    if not m:   # one-line helper
        m = re.search(r"function\s+%s\s*\([^)]*\)\s*{[^\n]*}\n" % name, js)
    assert m, name
    return m.group(0)


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_formatters_render_explicit_units():
    js = _script()
    src = "\n".join(_extract(js, n) for n in ("plural", "fmtMinutes", "fmtSeconds"))
    probe = src + """
const out = {
  m240: fmtMinutes(240), m90: fmtMinutes(90), m45: fmtMinutes(45), m60: fmtMinutes(60),
  s_remaining: fmtSeconds(2 * 3600 + 45 * 60), s_cooldown: fmtSeconds(200), s_short: fmtSeconds(42),
  s_passed: fmtSeconds(-5), s_none: fmtSeconds(null), m_bad: fmtMinutes("x")
};
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["m240"] == "4 hours"                       # never "4 min"
    assert out["m90"] == "1 hour 30 minutes"
    assert out["m45"] == "45 minutes"
    assert out["m60"] == "1 hour"
    assert out["s_remaining"] == "2 hours 45 minutes"
    assert out["s_cooldown"] == "3 min 20 s"
    assert out["s_short"] == "42 s"
    assert out["s_passed"] == "passed" and out["s_none"] == "--" and out["m_bad"] == "--"


def test_backend_four_hour_window_and_ist_deadline():
    from backend.app.scheduler.schedule_profiles import ProductionCycle
    c = ProductionCycle(cycle_id="cycle_3", name="Cycle 3", start_hour_ist=12, start_min_ist=0,
                        target_pub_hour_ist=18, target_pub_min_ist=0, recovery_window_hours=240 / 60)
    start = datetime(2026, 11, 20, 12, 0, tzinfo=IST)
    assert c.recovery_window_hours == 4.0
    assert c.is_within_recovery_window(start, datetime(2026, 11, 20, 15, 59, tzinfo=IST))
    assert c.is_past_recovery_window(start, datetime(2026, 11, 20, 16, 0, tzinfo=IST))   # deadline 16:00 IST
    assert int(round(c.recovery_window_hours * 60)) == 240                                # API minutes field
