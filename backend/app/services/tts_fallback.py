"""Stage 3.35: local voiceover fallback (Windows SAPI), OFF by default.

Evidence (production logs): Edge neural TTS made 691 segment syntheses with 5 retries and 1 final failure (a full
network outage on 2026-11-17, when every other online step failed too): 99.3 % first-try, 99.86 % after retries.

Evaluated local options:
* Windows SAPI desktop voices (Microsoft David / Zira): installed, free, offline, about 0.66 s for 6.5 s of speech,
  low resource use - but clearly robotic, 22 kHz mono, and a different voice from the channel's Edge voice.
* Piper / other local neural TTS: better quality on CPU, but needs a separate model download (not installed; a new
  dependency needing approval).

Because uploads are published automatically at their scheduled time, a robotic voice would reach viewers. The
fallback is therefore implemented but disabled: set ``CRAZZYDOCS_TTS_FALLBACK=sapi`` to allow it. When it is used, the
segment is marked ``voice_fallback`` so the production can be reviewed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from backend.app.core.fs_utils import remove_with_retry

VOICE = "Microsoft David Desktop"


def enabled() -> bool:
    return os.environ.get("CRAZZYDOCS_TTS_FALLBACK", "off").strip().lower() == "sapi"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def synthesize(text: str, out_wav: str, *, rate: int = 1, voice: str = VOICE, timeout: float = 60.0) -> Optional[str]:
    """Writes a WAV with Windows SAPI; returns the path, or None on failure. Text is passed as a quoted PowerShell
    literal (no interpolation, no command injection)."""
    if os.name != "nt" or not text.strip():
        return None
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"try {{ $s.SelectVoice({_ps_quote(voice)}) }} catch {{ }}; $s.Rate = {int(max(-10, min(10, rate)))}; "
              f"$s.SetOutputToWaveFile({_ps_quote(out_wav)}); $s.Speak({_ps_quote(text[:4000])}); $s.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        remove_with_retry(out_wav)
        return None
    if r.returncode != 0 or not Path(out_wav).is_file() or Path(out_wav).stat().st_size < 1000:
        remove_with_retry(out_wav)
        return None
    return out_wav
