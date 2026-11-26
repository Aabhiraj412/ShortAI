"""Stage 3.37: zero-cost host resource probes for the production resource coordinator.

Everything is measured in-process through Windows APIs (no psutil, no paid service):

* CPU load: two ``GetSystemTimes`` samples (the same method as ``control_center_service.evaluate_resource_safety``);
* RAM: ``GlobalMemoryStatusEx`` (load %, available and total GB);
* disk: ``shutil.disk_usage`` of the media volume;
* GPU: the ``\\GPU Engine(*)\\Utilization Percentage`` performance counter through PDH, summed per engine type
  (3D, VideoEncode, ...) and reported as the busiest engine type. On hardware or drivers that do not publish the
  counter it is ``None`` with a note, never a guessed zero;
* renderer processes: the number of running ``ffmpeg.exe`` OS processes (``tasklist``, hidden console).

Each probe fails soft (``None`` plus a note). Results are cached briefly so dashboard polling never adds load.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import threading
import time
from typing import Any, Callable, Dict, Optional

CACHE_SECONDS = 15.0
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _memory() -> Dict[str, Optional[float]]:
    if os.name != "nt":
        return {"memory_used_percent": None, "memory_available_gb": None, "memory_total_gb": None}
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    stat = MemoryStatus()
    stat.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return {"memory_used_percent": None, "memory_available_gb": None, "memory_total_gb": None}
    return {"memory_used_percent": float(stat.dwMemoryLoad),
            "memory_available_gb": round(stat.ullAvailPhys / 1024 ** 3, 2),
            "memory_total_gb": round(stat.ullTotalPhys / 1024 ** 3, 2)}


def _cpu(sample_seconds: float = 0.25) -> Optional[float]:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    def _i(ft: wintypes.FILETIME) -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    a = [wintypes.FILETIME() for _ in range(3)]
    b = [wintypes.FILETIME() for _ in range(3)]
    k32 = ctypes.windll.kernel32
    if not k32.GetSystemTimes(*(ctypes.byref(x) for x in a)):
        return None
    time.sleep(sample_seconds)
    if not k32.GetSystemTimes(*(ctypes.byref(x) for x in b)):
        return None
    idle = _i(b[0]) - _i(a[0])
    total = (_i(b[1]) - _i(a[1])) + (_i(b[2]) - _i(a[2]))   # kernel time includes idle time
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle / total) * 100.0)), 1)


def _gpu(sample_seconds: float = 0.5) -> Dict[str, Any]:
    """Busiest GPU engine type utilisation (percent) from the PDH GPU Engine counters."""
    if os.name != "nt":
        return {"gpu_used_percent": None, "gpu_note": "GPU counters are only read on Windows"}
    import ctypes
    from ctypes import wintypes

    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    class _Value(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

    class _Item(ctypes.Structure):
        _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]

    try:
        pdh = ctypes.windll.pdh
    except OSError:
        return {"gpu_used_percent": None, "gpu_note": "PDH is not available"}
    query = wintypes.HANDLE()
    counter = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {"gpu_used_percent": None, "gpu_note": "PDH query could not be opened"}
    try:
        if pdh.PdhAddEnglishCounterW(query, "\\GPU Engine(*)\\Utilization Percentage", None,
                                     ctypes.byref(counter)) != 0:
            return {"gpu_used_percent": None, "gpu_note": "GPU Engine counter is not published by this driver"}
        pdh.PdhCollectQueryData(query)
        time.sleep(sample_seconds)
        pdh.PdhCollectQueryData(query)
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if (rc & 0xFFFFFFFF) != PDH_MORE_DATA or size.value == 0:
            return {"gpu_used_percent": None, "gpu_note": "GPU Engine counter returned no instances"}
        buf = (ctypes.c_byte * size.value)()
        rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf)
        if rc != 0:
            return {"gpu_used_percent": None, "gpu_note": f"GPU Engine counter read failed (0x{rc & 0xFFFFFFFF:08X})"}
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        per_type: Dict[str, float] = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus not in (0, 1):      # PDH_CSTATUS_VALID_DATA / NEW_DATA
                continue
            name = it.szName or ""
            etype = name.rsplit("engtype_", 1)[-1] if "engtype_" in name else "other"
            per_type[etype] = per_type.get(etype, 0.0) + float(it.FmtValue.doubleValue)
        if not per_type:
            return {"gpu_used_percent": None, "gpu_note": "GPU Engine counter had no valid samples"}
        busiest = max(per_type, key=per_type.get)
        return {"gpu_used_percent": round(min(100.0, per_type[busiest]), 1), "gpu_busiest_engine": busiest,
                "gpu_engines": {k: round(min(100.0, v), 1) for k, v in sorted(per_type.items())},
                "gpu_note": "Windows PDH GPU Engine counters"}
    except Exception as exc:  # noqa: BLE001
        return {"gpu_used_percent": None, "gpu_note": f"GPU probe failed ({type(exc).__name__})"}
    finally:
        pdh.PdhCloseQuery(query)


def _disk_free_gb() -> Optional[float]:
    try:
        from backend.app.core.paths import MEDIA_DIR
        return round(shutil.disk_usage(str(MEDIA_DIR)).free / 1024 ** 3, 1)
    except Exception:
        return None


def _renderer_processes() -> Dict[str, Any]:
    """Running ffmpeg.exe OS processes (the renderer's heavy children), with their working-set MB."""
    if os.name != "nt":
        return {"ffmpeg_processes": None, "ffmpeg_pids": []}
    try:
        from backend.app.core.process_utils import run_hidden
        out = run_hidden(["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq ffmpeg.exe"],
                         capture_output=True, text=True, timeout=5).stdout or ""
        pids, mem = [], 0.0
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 5 and row[0].lower() == "ffmpeg.exe":
                pids.append(int(row[1]))
                digits = "".join(ch for ch in row[4] if ch.isdigit())
                mem += int(digits or 0) / 1024.0
        return {"ffmpeg_processes": len(pids), "ffmpeg_pids": pids, "ffmpeg_working_set_mb": round(mem, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ffmpeg_processes": None, "ffmpeg_pids": [], "note": f"process probe failed ({type(exc).__name__})"}


def sample(*, use_cache: bool = True, gpu: bool = True) -> Dict[str, Any]:
    """One resource snapshot. Cached for CACHE_SECONDS so repeated callers share one measurement."""
    with _cache_lock:
        if use_cache and _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return dict(_cache["value"])
    t0 = time.perf_counter()
    snap: Dict[str, Any] = {"cpu_used_percent": None}
    gpu_box: Dict[str, Any] = {}
    gpu_thread = None
    if gpu:   # the GPU and CPU samples both wait; overlap them
        gpu_thread = threading.Thread(target=lambda: gpu_box.update(_gpu()), daemon=True)
        gpu_thread.start()
    try:
        snap["cpu_used_percent"] = _cpu()
    except Exception:
        pass
    try:
        snap.update(_memory())
    except Exception:
        snap.update({"memory_used_percent": None, "memory_available_gb": None, "memory_total_gb": None})
    snap["disk_free_gb"] = _disk_free_gb()
    snap.update(_renderer_processes())
    if gpu_thread is not None:
        gpu_thread.join(timeout=5)
        snap.update(gpu_box or {"gpu_used_percent": None, "gpu_note": "GPU probe timed out"})
    else:
        snap.update({"gpu_used_percent": None, "gpu_note": "not sampled"})
    snap["sampled_at_epoch"] = time.time()
    snap["probe_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    with _cache_lock:
        _cache["value"], _cache["at"] = dict(snap), time.monotonic()
    return snap


ResourceProbe = Callable[[], Dict[str, Any]]
