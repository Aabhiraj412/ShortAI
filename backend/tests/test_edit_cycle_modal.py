"""Edit Cycle window (2026-11-28): every schedule field, independent Hindi time, Same as English, days, enabled.
The modal's own JavaScript runs in node against a minimal DOM stub; no browser, no server."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"


def _html():
    return HTML.read_text(encoding="utf-8")


def test_modal_exposes_every_field():
    h = _html()
    modal = h[h.index('id="cycleEditorModal"'):h.index("<script>", h.index('id="cycleEditorModal"'))]
    for label in ("Cycle ID", "Cycle name", "Production start (IST)", "Recovery duration",
                  "English target publish time (IST)", "Hindi target publish time (IST)", "Same as English", "Enabled",
                  "Days"):
        assert label in modal, label
    assert re.search(r'id="ceId"[^>]*readonly', modal)
    for el in ("ceRecH", "ceRecM", "ceHindi", "ceSameEn", "ceEnabled", "ceDays"):
        assert f'id="{el}"' in modal


def _extract(h):
    a = h.index("function openScheduleCycleEditor(")
    b = h.index("function toggleScheduleCycle(")
    return h[a:b]


HARNESS = r"""
const els = {};
function el(id) { return els[id] || (els[id] = { id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "", style: {} }); }
const document = {
  getElementById: el,
  querySelectorAll: sel => sel === ".ceDay:checked" ? (globalThis.__days || []).map(v => ({ value: String(v) })) : [],
};
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const posts = []; const toasts = []; let dirtyCalls = 0, renders = 0;
async function dashWrite(url, body) { posts.push({ url, body }); return { ok: true }; }
function showToast(m, e) { toasts.push([m, !!e]); }
function markScheduleDirty() { dirtyCalls++; }
function renderScheduleTable() { renders++; }
function hhmmMinutes(v) { const [h, m] = String(v || "").split(":").map(Number); return h * 60 + m; }
const schedState = { cycles: [
  { cycle_id: "cycle_24", name: "Cycle 24", start_time: "23:00", recovery_duration_minutes: 120, target_publish_time: "00:00", enabled: true, active_weekdays: [0,1,2,3,4,5,6] },
], dirty: false, editIndex: -1 };
const hiTimes = { cycles: {}, dirty: false, loaded: true };
function hiSetting(cid) { return hiTimes.cycles[cid] || { same_as_english: true, time: null }; }
__CODE__
(async () => {
  const scen = JSON.parse(process.argv[process.argv.length - 1]);
  if (scen.hindi_saved) hiTimes.cycles.cycle_24 = scen.hindi_saved;
  openScheduleCycleEditor(0);
  const loaded = { id: el("ceId").value, name: el("ceName").value, start: el("ceStart").value, recH: el("ceRecH").value,
                   recM: el("ceRecM").value, target: el("ceTarget").value, hindi: el("ceHindi").value,
                   same: el("ceSameEn").checked, hindiDisabled: el("ceHindi").disabled, enabled: el("ceEnabled").checked };
  globalThis.__days = scen.days || [0,1,2,3,4,5,6];
  for (const [k, v] of Object.entries(scen.set || {})) { if (typeof v === "boolean") el(k).checked = v; else el(k).value = v; }
  if ("ceSameEn" in (scen.set || {})) ceSyncHindi();
  await applyScheduleCycleEditor();
  console.log(JSON.stringify({ loaded, posts, toasts, dirtyCalls, renders, cycle: schedState.cycles[0], hindi: hiTimes.cycles.cycle_24 || null,
                               hindiDisabled: el("ceHindi").disabled }));
})();
"""


def _run(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    code = HARNESS.replace("__CODE__", _extract(_html()))
    proc = subprocess.run([node, "-e", code, "--", json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_existing_cycle_loads_every_persisted_value():
    r = _run({"hindi_saved": {"same_as_english": False, "time": "07:30"}})
    l = r["loaded"]
    assert l == {"id": "cycle_24", "name": "Cycle 24", "start": "23:00", "recH": 2, "recM": 0, "target": "00:00",
                 "hindi": "07:30", "same": False, "hindiDisabled": False, "enabled": True}


def test_same_as_english_is_default_and_disables_the_hindi_picker():
    r = _run({})
    assert r["loaded"]["same"] is True and r["loaded"]["hindiDisabled"] is True and r["loaded"]["hindi"] == "00:00"
    assert r["posts"] == [] and r["dirtyCalls"] == 0          # nothing changed: no save, no English revision


def test_hindi_only_change_saves_hindi_config_without_an_english_draft():
    r = _run({"set": {"ceSameEn": False, "ceHindi": "08:15"}})
    assert r["posts"] == [{"url": "/api/hindi-publishing/cycle-times",
                           "body": {"cycles": {"cycle_24": {"same_as_english": False, "time": "08:15"}}, "confirm": True}}]
    assert r["dirtyCalls"] == 0 and r["hindi"] == {"same_as_english": False, "time": "08:15"}


def test_english_only_change_keeps_the_hindi_setting_and_marks_the_draft():
    r = _run({"hindi_saved": {"same_as_english": False, "time": "07:30"},
              "set": {"ceName": "Late night", "ceTarget": "00:30", "ceRecH": "3", "ceRecM": "15", "ceEnabled": False},
              "days": [0, 2, 4]})
    assert r["posts"] == [] and r["dirtyCalls"] == 1 and r["hindi"] == {"same_as_english": False, "time": "07:30"}
    c = r["cycle"]
    assert c["name"] == "Late night" and c["target_publish_time"] == "00:30" and c["recovery_duration_minutes"] == 195
    assert c["enabled"] is False and c["active_weekdays"] == [0, 2, 4] and c["cycle_id"] == "cycle_24"
    assert "hindi" not in json.dumps(c).lower()              # the English model is unchanged in shape


def test_both_changed_saves_hindi_and_drafts_english():
    r = _run({"set": {"ceTarget": "23:45", "ceSameEn": False, "ceHindi": "00:00"}})
    assert len(r["posts"]) == 1 and r["dirtyCalls"] == 1 and r["cycle"]["target_publish_time"] == "23:45"


def test_back_to_same_as_english_clears_the_independent_time():
    r = _run({"hindi_saved": {"same_as_english": False, "time": "07:30"}, "set": {"ceSameEn": True}})
    assert r["posts"][0]["body"]["cycles"]["cycle_24"] == {"same_as_english": True, "time": None}


@pytest.mark.parametrize("scen,msg", [
    ({"set": {"ceSameEn": False, "ceHindi": ""}}, "Choose a Hindi target"),
    ({"days": []}, "at least one day"),
])
def test_invalid_input_is_refused_without_saving(scen, msg):
    r = _run(scen)
    assert r["posts"] == [] and r["dirtyCalls"] == 0 and any(msg in t[0] and t[1] for t in r["toasts"])
