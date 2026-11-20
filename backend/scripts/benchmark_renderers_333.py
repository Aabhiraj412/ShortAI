"""Stage 3.33 rendering benchmarks (synthetic, local, reproducible).

Cases (1080x1920, 30 fps, H.264 yuv420p):

* ``ffmpeg_composition``  - FFmpeg-only scenes: gradients, zoompan "Ken Burns", drawtext captions, xfade transitions
* ``pillow_procedural``   - the existing Python/Pillow procedural engine (``ExplainerAnimationEngine``)
* ``browser_canvas``      - the headless-browser frame-capture class (HTML canvas + DOM/CSS text), rendered by the
                            installed Lemo-Opuscar engine (Playwright + Edge + FFmpeg). HyperFrames, Remotion and
                            Motion Canvas render the same way (headless Chromium screenshots -> FFmpeg), so this is
                            the local proxy for that class; workers 1 and 2.
* ``pop_info_production`` - (optional, ``--with-production``) re-renders one real Pop Info film into a scratch
                            directory to measure the production renderer's peak memory. Its source folder is read only.

Measures wall time, CPU time of the process tree, peak working set of the process tree (sampled every 0.2 s), output
size and probe. Usage::

    python backend/scripts/benchmark_renderers_333.py --out docs/benchmarks/stage_3_33_render_benchmarks.json
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
SECONDS = 6
W, H, FPS = 1080, 1920, 30


def _tools_root() -> Path:
    for p in [ROOT, *ROOT.parents]:
        if (p / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
            return p / "tools"
    raise SystemExit("tools/ffmpeg not found")


TOOLS = _tools_root()
FFMPEG = str(TOOLS / "ffmpeg" / "bin" / "ffmpeg.exe")
FFPROBE = str(TOOLS / "ffmpeg" / "bin" / "ffprobe.exe")
NODE = str(TOOLS / "nodejs" / "node.exe")
LEMO = TOOLS / "dependencies" / "lemo-opuscar"


# -- process-tree sampling (Windows, ctypes; no extra dependencies) ------------------------------------------------
class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


def _children_map() -> Dict[int, List[int]]:
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    out: Dict[int, List[int]] = {}
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        out.setdefault(e.th32ParentProcessID, []).append(e.th32ProcessID)
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def _tree(pid: int) -> List[int]:
    cm, seen, stack = _children_map(), [], [pid]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.append(p)
        stack.extend(cm.get(p, []))
    return seen


def _proc_stats(pid: int):
    h = k32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not h:
        return 0, 0.0
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ws = pmc.WorkingSetSize if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb) else 0
        c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        cpu = 0.0
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            cpu = ((kt.dwHighDateTime << 32 | kt.dwLowDateTime) + (ut.dwHighDateTime << 32 | ut.dwLowDateTime)) / 1e7
        return ws, cpu
    finally:
        k32.CloseHandle(h)


def run_measured(cmd: List[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
                 timeout: float = 1800) -> Dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak, cpu_by_pid, stop = [0], {}, threading.Event()

    def sample():
        while not stop.is_set():
            total = 0
            for pid in _tree(proc.pid):
                ws, cpu = _proc_stats(pid)
                total += ws
                cpu_by_pid[pid] = max(cpu_by_pid.get(pid, 0.0), cpu)
            peak[0] = max(peak[0], total)
            stop.wait(0.2)
    th = threading.Thread(target=sample, daemon=True)
    th.start()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    stop.set()
    th.join(2)
    return {"returncode": proc.returncode, "wall_sec": round(time.perf_counter() - t0, 2),
            "cpu_sec_sampled": round(sum(cpu_by_pid.values()), 1), "peak_working_set_mb": round(peak[0] / 2**20, 1),
            "stderr_tail": (err or "")[-600:], "stdout_tail": (out or "")[-300:]}


def probe(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,pix_fmt,nb_frames,r_frame_rate:format=duration,size", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    f = d.get("format") or {}
    return {"exists": True, "width": s.get("width"), "height": s.get("height"), "pix_fmt": s.get("pix_fmt"),
            "frames": s.get("nb_frames"), "duration": f.get("duration"), "size_mb": round(int(f.get("size") or 0) / 2**20, 2)}


# -- cases ---------------------------------------------------------------------------------------------------------
def case_ffmpeg(work: Path) -> Dict[str, Any]:
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    img = work / "plate.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "mandelbrot=s=2160x3840", "-frames:v", "1",
                    str(img)], check=True, timeout=120)
    seg = SECONDS / 3.0
    fc = (
        f"color=c=0x0b101c:s={W}x{H}:r={FPS}:d={seg + 0.5},geq=r='20+X/20':g='30+Y/30':b='60',"
        f"drawtext=fontfile='{font}':text='SCENE ONE':fontsize=96:fontcolor=white:x=(w-tw)/2:y=h*0.4+40*sin(t*2)[a];"
        f"[0:v]scale=2160:3840,zoompan=z='min(zoom+0.0015,1.25)':d={int((seg + 0.5) * FPS)}:s={W}x{H}:fps={FPS},"
        f"drawtext=fontfile='{font}':text='Ken Burns plate':fontsize=72:fontcolor=white:box=1:boxcolor=black@0.5:"
        f"x=(w-tw)/2:y=h*0.8[b];"
        f"color=c=0x1e293b:s={W}x{H}:r={FPS}:d={seg + 0.5},drawbox=x='200+300*sin(t*3)':y=700:w=400:h=400:c=0x38bdf8:t=fill,"
        f"drawtext=fontfile='{font}':text='DATA %{{eif\\:t*100\\:d}}':fontsize=110:fontcolor=0xfacc15:x=(w-tw)/2:y=1300[c];"
        f"[a][b]xfade=transition=slideleft:duration=0.5:offset={seg - 0.25}[ab];"
        f"[ab][c]xfade=transition=fade:duration=0.5:offset={2 * seg - 0.5},format=yuv420p[out]"
    )
    out = work / "ffmpeg_composition.mp4"
    m = run_measured([FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", str(img), "-filter_complex", fc,
                      "-map", "[out]", "-t", str(SECONDS), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                      "-pix_fmt", "yuv420p", str(out)])
    return {**m, "output": probe(out)}


def case_pillow(work: Path) -> Dict[str, Any]:
    out = work / "pillow_procedural.mp4"
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "from backend.app.rendering.explainer_animation_engine import ExplainerAnimationEngine as E;"
        "E(ffmpeg_path=r'%s').render_explainer_animation_mp4(visual_type='motion_graphic', out_mp4_path=r'%s',"
        "duration_sec=%d, beat_idx=1, total_beats=4, stage_label='CONTEXT', topic_title='Benchmark topic',"
        "segment_text='A synthetic benchmark segment with a number 42 and a place.', full_script='Benchmark script.',"
        "accent_color=(56,189,248))" % (ROOT, FFMPEG, out, SECONDS))
    m = run_measured([sys.executable, "-c", code], cwd=str(ROOT))
    return {**m, "output": probe(out)}


BROWSER_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;background:#0b101c;overflow:hidden;font-family:Arial,sans-serif}
#c{position:absolute;inset:0;width:100%;height:100%}
.cap{position:absolute;left:6%;right:6%;bottom:14%;color:#fff;font-weight:800;font-size:5.2vw;text-align:center;
     text-shadow:0 4px 18px #000;will-change:transform,opacity}
.stat{position:absolute;top:18%;width:100%;text-align:center;color:#facc15;font-weight:900;font-size:14vw}
</style></head><body><canvas id="c"></canvas><div class="stat" id="s"></div><div class="cap" id="t"></div><script>
window.DUR=__DUR__;const c=document.getElementById('c'),x=c.getContext('2d');
const caps=['Most people assume this is simple','But the numbers say otherwise','Here is what changed'];
function size(){c.width=innerWidth*devicePixelRatio;c.height=innerHeight*devicePixelRatio;}size();
window.render=t=>{const w=c.width,h=c.height;const g=x.createLinearGradient(0,0,w,h);
g.addColorStop(0,`hsl(${210+20*Math.sin(t)},60%,12%)`);g.addColorStop(1,`hsl(${260+30*Math.cos(t*.7)},55%,20%)`);
x.fillStyle=g;x.fillRect(0,0,w,h);
for(let i=0;i<60;i++){const a=i*0.37+t*0.9,r=w*0.18+w*0.12*Math.sin(t+i);x.beginPath();
x.arc(w/2+Math.cos(a)*r,h*0.45+Math.sin(a)*r,8+6*Math.sin(t*2+i),0,7);x.fillStyle=`hsla(${(i*12+t*60)%360},80%,60%,.8)`;x.fill();}
x.strokeStyle='#38bdf8';x.lineWidth=10;x.beginPath();for(let k=0;k<=100;k++){const px=w*k/100,py=h*0.7-h*0.08*Math.sin(k/9+t*2);
k?x.lineTo(px,py):x.moveTo(px,py);}x.stroke();
const i=Math.min(2,Math.floor(t/(window.DUR/3))),p=(t%(window.DUR/3))/(window.DUR/3);
const el=document.getElementById('t');el.textContent=caps[i];el.style.opacity=Math.min(1,p*4);
el.style.transform=`translateY(${(1-Math.min(1,p*3))*60}px) scale(${0.9+0.1*Math.min(1,p*3)})`;
document.getElementById('s').textContent=Math.round(t*1000/window.DUR)+'%';};
window.render(0);window.READY=true;</script></body></html>"""


EDGE = os.environ.get("PLAYWRIGHT_CHROME", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def _node_env() -> Dict[str, str]:
    """The same environment the production adapter gives the engine: FFmpeg on PATH, the installed Edge browser."""
    env = dict(os.environ)
    env["PATH"] = str(TOOLS / "ffmpeg" / "bin") + os.pathsep + env.get("PATH", "")
    env["PLAYWRIGHT_CHROME"] = EDGE
    env["RENDER_SLOTS"] = "1"           # queue behind any production render instead of competing with it
    return env


def case_browser(work: Path, workers: int) -> Dict[str, Any]:
    demo = work / f"browser_demo_w{workers}"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "index.html").write_text(BROWSER_PAGE.replace("__DUR__", str(SECONDS)), encoding="utf-8")
    out = work / f"browser_canvas_w{workers}.mp4"
    env = _node_env()
    m = run_measured([NODE, str(LEMO / "core" / "render" / "video.mjs"), str(demo), "--fps", str(FPS),
                      "--workers", str(workers), "--size", f"{W}x{H}", "--out", str(out)], cwd=str(LEMO), env=env)
    return {**m, "output": probe(out)}


def case_pop_info(work: Path, film: Path) -> Dict[str, Any]:
    out = work / "pop_info_production.mp4"
    env = _node_env()
    m = run_measured([NODE, str(LEMO / "core" / "render" / "video.mjs"), str(film), "--fps", str(FPS),
                      "--workers", "1", "--size", f"{W}x{H}", "--out", str(out)], cwd=str(LEMO), env=env, timeout=3600)
    return {**m, "output": probe(out), "source_film": str(film)}


def machine() -> Dict[str, Any]:
    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    m = MEM()
    m.dwLength = ctypes.sizeof(MEM)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    gpu = ""
    try:
        gpu = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        pass
    return {"os": platform.platform(), "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
            "ram_total_gb": round(m.ullTotalPhys / 2**30, 1), "ram_available_gb_at_start": round(m.ullAvailPhys / 2**30, 1),
            "gpu": gpu}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-production", default="", help="path to a real pop_info_film folder to re-render")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="cd_bench_333_"))
    results: Dict[str, Any] = {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "machine": machine(),
                               "clip": {"seconds": SECONDS, "width": W, "height": H, "fps": FPS}, "cases": {}}
    for name, fn in (("ffmpeg_composition", lambda: case_ffmpeg(work)), ("pillow_procedural", lambda: case_pillow(work)),
                     ("browser_canvas_w1", lambda: case_browser(work, 1)), ("browser_canvas_w2", lambda: case_browser(work, 2))):
        print("running", name, flush=True)
        try:
            results["cases"][name] = fn()
        except Exception as exc:
            results["cases"][name] = {"error": f"{type(exc).__name__}: {exc}"}
        r = results["cases"][name]
        if r.get("wall_sec") and (r.get("output") or {}).get("exists"):
            r["realtime_factor"] = round(r["wall_sec"] / SECONDS, 2)          # seconds of rendering per second of video
        print(" ", {k: r.get(k) for k in ("returncode", "wall_sec", "cpu_sec_sampled", "peak_working_set_mb", "realtime_factor")},
              flush=True)
    if args.with_production:
        print("running pop_info_production", flush=True)
        r = case_pop_info(work, Path(args.with_production))
        dur = float((r.get("output") or {}).get("duration") or 0)
        if dur:
            r["realtime_factor"] = round(r["wall_sec"] / dur, 2)
        results["cases"]["pop_info_production"] = r
        print(" ", {k: r.get(k) for k in ("returncode", "wall_sec", "peak_working_set_mb", "realtime_factor")}, flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print("written", args.out)


if __name__ == "__main__":
    main()
