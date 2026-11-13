"""
The Lemo film page is what viewers see. Internal scaffolding (beat counters, the renderer's
name) and the retired History/Archive "DOCUMENTARY" framing must never be drawn on it.
"""
import re
from pathlib import Path

from backend.app.rendering import lemo_opuscar_film_builder as fb

SRC = Path(fb.__file__).read_text(encoding="utf-8")


def test_renderer_name_is_not_drawn_on_screen():
    markup = SRC[SRC.index("<body>"):]
    assert "LEMO-OPUSCAR STUDIO" not in markup


def test_beat_counter_is_never_visible():
    block = re.search(r"\.footer-bar \{\{(.*?)\}\}", SRC, re.S).group(1)
    assert "display: none" in block
    assert re.search(r"BEAT \$\{idx \+ 1\}", SRC) is None     # procedural fallback card


def test_no_documentary_fallback_labels():
    assert '"DOCUMENTARY"' not in SRC
    assert "DOCUMENTARY BRIEF" not in SRC
