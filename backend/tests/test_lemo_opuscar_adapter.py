from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.rendering.lemo_opuscar_adapter import LemoOpuscarAdapter, PINNED_COMMIT


def _adapter(tmp_path: Path) -> tuple[LemoOpuscarAdapter, Path, Path]:
    library = tmp_path / "library"
    (library / "core" / "render").mkdir(parents=True)
    (library / "core" / "render" / "video.mjs").touch()
    (library / "core" / "render" / "still.mjs").touch()
    node = tmp_path / "node.exe"
    ffmpeg_bin = tmp_path / "ffmpeg"
    ffmpeg_bin.mkdir()
    (ffmpeg_bin / "ffmpeg.exe").touch()
    browser = tmp_path / "msedge.exe"
    node.touch()
    browser.touch()
    workspace = tmp_path / "workspace"
    demo = workspace / "film"
    demo.mkdir(parents=True)
    (demo / "index.html").touch()
    adapter = LemoOpuscarAdapter(library, node, ffmpeg_bin, browser)
    return adapter, workspace, demo


def test_video_adapter_invokes_pinned_upstream_renderer_at_requested_format(tmp_path):
    adapter, workspace, demo = _adapter(tmp_path)
    output = workspace / "black_hole.mp4"
    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

    def render_video(command):
        rendered_output = Path(command[command.index("--out") + 1])
        rendered_output.write_bytes(b"mp4")
        return completed

    with patch.object(adapter, "_verify_installation"), patch.object(
        adapter, "_execute", side_effect=render_video
    ) as execute:
        assert adapter.render_video(workspace, demo, output) == output

    command = execute.call_args.args[0]
    assert command[1].endswith("core\\render\\video.mjs")
    assert command[command.index("--fps") + 1] == "30"
    assert command[command.index("--workers") + 1] == "1"
    assert command[command.index("--size") + 1] == "1080x1920"
    assert PINNED_COMMIT == "9eb7739a0fca4c6990fe966cb8b87b1930f7193a"


def test_thumbnail_adapter_uses_separate_upstream_still_render(tmp_path):
    adapter, workspace, demo = _adapter(tmp_path)
    output = workspace / "thumbnail.jpg"

    def render_still(command):
        destination = Path(command[command.index("--out") + 1])
        prefix = command[command.index("--prefix") + 1]
        time_text = command[3]
        (destination / f"{prefix}{time_text}.jpg").write_bytes(b"jpeg")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with patch.object(adapter, "_verify_installation"), patch.object(
        adapter, "_execute", side_effect=render_still
    ) as execute:
        assert adapter.render_still(
            workspace, demo, output, time_sec=18.0, query="thumbnail=1"
        ) == output

    assert output.read_bytes() == b"jpeg"
    command = execute.call_args.args[0]
    assert command[1].endswith("core\\render\\still.mjs")
    assert command[command.index("--q") + 1] == "thumbnail=1"
    assert command[command.index("--size") + 1] == "1080x1920"


def test_adapter_rejects_demo_or_output_outside_validation_workspace(tmp_path):
    adapter, workspace, demo = _adapter(tmp_path)

    with pytest.raises(ValueError, match="Demo must stay inside"):
        adapter.render_video(workspace, tmp_path / "external", workspace / "video.mp4")

    with pytest.raises(ValueError, match="Output must stay inside"):
        adapter.render_video(workspace, demo, tmp_path / "external.mp4")


def test_installation_verification_rejects_wrong_upstream_commit(tmp_path):
    adapter, _, _ = _adapter(tmp_path)
    completed = subprocess.CompletedProcess([], 0, stdout="deadbeef\n", stderr="")

    with patch("backend.app.rendering.lemo_opuscar_adapter.subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="commit mismatch"):
            adapter._verify_installation()
