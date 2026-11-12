"""Isolated adapter for the pinned Lemo-Opuscar frame and still renderers."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from backend.app.core.process_utils import hidden_subprocess_kwargs

ROOT = Path(__file__).resolve().parents[3]
PINNED_COMMIT = "9eb7739a0fca4c6990fe966cb8b87b1930f7193a"


class LemoOpuscarAdapter:
    def __init__(
        self,
        library_root: Path | None = None,
        node_executable: Path | None = None,
        ffmpeg_bin: Path | None = None,
        browser_executable: Path | None = None,
        timeout_sec: int = 3600,
    ) -> None:
        self.library_root = (library_root or ROOT / "tools" / "dependencies" / "lemo-opuscar").resolve()
        self.node_executable = (node_executable or ROOT / "tools" / "nodejs" / "node.exe").resolve()
        self.ffmpeg_bin = (ffmpeg_bin or ROOT / "tools" / "ffmpeg" / "bin").resolve()
        default_browser = Path(
            os.environ.get(
                "PLAYWRIGHT_CHROME",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            )
        )
        self.browser_executable = (browser_executable or default_browser).resolve()
        self.timeout_sec = timeout_sec

    def _verify_installation(self) -> None:
        required = (
            self.library_root / "core" / "render" / "video.mjs",
            self.library_root / "core" / "render" / "still.mjs",
            self.node_executable,
            self.ffmpeg_bin / "ffmpeg.exe",
            self.browser_executable,
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError("Lemo-Opuscar runtime files are missing: " + ", ".join(missing))

        result = subprocess.run(
            ["git", "-C", str(self.library_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode:
            raise RuntimeError(f"Unable to identify the Lemo-Opuscar checkout: {result.stderr.strip()}")
        commit = result.stdout.strip()
        if commit != PINNED_COMMIT:
            raise RuntimeError(f"Lemo-Opuscar commit mismatch: expected {PINNED_COMMIT}, found {commit}")

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PLAYWRIGHT_CHROME"] = str(self.browser_executable)
        env["RENDER_SLOTS"] = "1"
        env["PATH"] = os.pathsep.join(
            (str(self.ffmpeg_bin), str(self.node_executable.parent), env.get("PATH", ""))
        )
        return env

    @staticmethod
    def _contained_path(workspace: Path, candidate: Path, label: str) -> Path:
        root = workspace.resolve()
        path = candidate.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"{label} must stay inside the validation workspace: {path}")
        return path

    @staticmethod
    def _validate_page(workspace: Path, demo_dir: Path, output_path: Path) -> tuple[Path, Path]:
        if not workspace.is_dir():
            raise FileNotFoundError(f"Validation workspace does not exist: {workspace}")
        demo = LemoOpuscarAdapter._contained_path(workspace, demo_dir, "Demo")
        output = LemoOpuscarAdapter._contained_path(workspace, output_path, "Output")
        if not (demo / "index.html").is_file():
            raise FileNotFoundError(f"No index.html in Lemo-Opuscar demo directory: {demo}")
        output.parent.mkdir(parents=True, exist_ok=True)
        return demo, output

    def _execute(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            list(command),
            cwd=self.library_root,
            env=self._environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"Lemo-Opuscar command failed ({result.returncode}): {detail[-3000:]}"
            )
        return result

    def render_video(
        self,
        workspace: Path,
        demo_dir: Path,
        output_path: Path,
        *,
        fps: int = 30,
        size: tuple[int, int] = (1080, 1920),
    ) -> Path:
        demo, output = self._validate_page(workspace, demo_dir, output_path)
        width, height = size
        if fps <= 0 or width < 16 or height < 16 or width % 2 or height % 2:
            raise ValueError("FPS must be positive and output dimensions must be even and at least 16 pixels")
        self._verify_installation()
        self._execute(
            (
                str(self.node_executable),
                str(self.library_root / "core" / "render" / "video.mjs"),
                str(demo),
                "--fps",
                str(fps),
                "--workers",
                "1",
                "--size",
                f"{width}x{height}",
                "--out",
                str(output),
            )
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"Lemo-Opuscar renderer completed without producing {output}")
        return output

    def render_still(
        self,
        workspace: Path,
        demo_dir: Path,
        output_path: Path,
        *,
        time_sec: float,
        query: str = "",
        size: tuple[int, int] = (1080, 1920),
    ) -> Path:
        demo, output = self._validate_page(workspace, demo_dir, output_path)
        width, height = size
        if time_sec < 0 or width < 16 or height < 16 or width % 2 or height % 2:
            raise ValueError("Still time must be non-negative and dimensions must be even and at least 16 pixels")
        self._verify_installation()
        with tempfile.TemporaryDirectory(prefix=".lemo_still_", dir=workspace) as temp_dir:
            temp_output = Path(temp_dir)
            prefix = "render_"
            command = [
                str(self.node_executable),
                str(self.library_root / "core" / "render" / "still.mjs"),
                str(demo),
                f"{time_sec:.2f}",
                "--out",
                str(temp_output),
                "--prefix",
                prefix,
                "--size",
                f"{width}x{height}",
            ]
            if query:
                command.extend(("--q", query))
            self._execute(command)
            rendered = temp_output / f"{prefix}{time_sec:.2f}.jpg"
            if not rendered.is_file() or rendered.stat().st_size == 0:
                raise RuntimeError(f"Lemo-Opuscar still renderer completed without producing {rendered}")
            shutil.copyfile(rendered, output)
        return output
