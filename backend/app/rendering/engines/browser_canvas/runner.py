"""
Headless Chromium Browser Canvas Runner.

Executes HTML5 Canvas reference styles and vertical adapters frame-by-frame,
streaming canvas frames directly into FFmpeg and extracting synthesized Web Audio SFX stems.
"""

import asyncio
import base64
import logging
import os
import subprocess
import time
from typing import Optional
import urllib.request
import json

from backend.app.rendering.engines.browser_canvas.client import CDPClient
from backend.app.rendering.types import RenderTelemetry, SegmentResult

logger = logging.getLogger(__name__)

EDGE_DEFAULT_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"
PROFILE_DIR_DEFAULT = r"D:\AI Arts\AI\cache\edge_canvas_profile"


class BrowserCanvasRunner:
    """Manages Headless Chromium lifecycle, page coordination, and video generation."""

    def __init__(
        self,
        edge_path: str = EDGE_DEFAULT_PATH,
        ffmpeg_path: str = FFMPEG_DEFAULT_PATH,
        profile_dir: str = PROFILE_DIR_DEFAULT,
        cdp_port: int = 9222,
    ):
        self.edge_path = edge_path
        self.ffmpeg_path = ffmpeg_path
        self.profile_dir = profile_dir
        self.cdp_port = cdp_port
        self.browser_proc: Optional[subprocess.Popen] = None
        self.browser_client: Optional[CDPClient] = None
        self.page_client: Optional[CDPClient] = None

    def start_browser(self) -> None:
        """Launch Microsoft Edge in headless mode with remote debugging enabled."""
        if self.browser_proc is not None:
            return

        os.makedirs(self.profile_dir, exist_ok=True)
        cmd = [
            self.edge_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={self.cdp_port}",
            "about:blank",
        ]
        logger.info("Spawning Headless Edge on port %d...", self.cdp_port)
        self.browser_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for CDP endpoint to be available
        version_url = f"http://127.0.0.1:{self.cdp_port}/json/version"
        connected = False
        for _ in range(25):
            try:
                with urllib.request.urlopen(version_url, timeout=1.0) as resp:
                    if resp.status == 200:
                        connected = True
                        break
            except Exception:
                time.sleep(0.3)

        if not connected:
            self.stop_browser()
            raise RuntimeError(f"Failed to connect to Edge CDP on port {self.cdp_port}")
        logger.info("Headless Edge active and listening for CDP commands.")

    def stop_browser(self) -> None:
        """Terminate Headless Edge process and clean up resources."""
        if self.browser_proc is not None:
            try:
                self.browser_proc.terminate()
                self.browser_proc.wait(timeout=5)
            except Exception:
                try:
                    self.browser_proc.kill()
                except Exception:
                    pass
            self.browser_proc = None
            logger.info("Headless Edge terminated.")

    async def _setup_page(self, adapter_url: str, width: int = 1080, height: int = 1920) -> CDPClient:
        """Create target, configure viewport, navigate, and wait for window.seek hook."""
        # Get browser debugger URL
        version_url = f"http://127.0.0.1:{self.cdp_port}/json/version"
        with urllib.request.urlopen(version_url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            browser_ws = data["webSocketDebuggerUrl"]

        self.browser_client = CDPClient(browser_ws)
        await self.browser_client.connect()

        # Create target
        create_res = await self.browser_client.send("Target.createTarget", {"url": "about:blank"})
        target_id = create_res["targetId"]

        # Find target WS URL
        list_url = f"http://127.0.0.1:{self.cdp_port}/json"
        with urllib.request.urlopen(list_url, timeout=2.0) as resp:
            targets = json.loads(resp.read().decode())
            page_ws = next(t["webSocketDebuggerUrl"] for t in targets if t["id"] == target_id)

        self.page_client = CDPClient(page_ws)
        await self.page_client.connect()

        # Configure page
        await self.page_client.send("Page.enable")
        await self.page_client.send("Runtime.enable")
        await self.page_client.send("Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": False,
        })

        # Navigate
        await self.page_client.send("Page.navigate", {"url": adapter_url})

        # Wait for window.seek hook and ensure all web fonts are loaded
        ready = False
        for _ in range(30):
            val = await self.page_client.evaluate("typeof window.seek")
            if val == "function":
                ready = True
                break
            await asyncio.sleep(0.2)

        if not ready:
            raise RuntimeError(f"Timed out waiting for window.seek hook in {adapter_url}")

        # Explicitly wait for all fonts to finish downloading and rasterizing
        font_check_script = """
        (async () => {
            await document.fonts.ready;
            // Force evaluate ready state on all loaded fonts
            let loaded = 0;
            for (const f of document.fonts) {
                if (f.status === 'loaded') loaded++;
            }
            return { total: document.fonts.size, loaded: loaded };
        })()
        """
        font_status = await self.page_client.evaluate(font_check_script, await_promise=True, return_by_value=True)
        logger.info("Adapter page ready: %s | Fonts: %s", adapter_url, font_status)
        return self.page_client

    async def extract_sfx_audio(self, client: CDPClient, duration: float, out_wav_path: str) -> Optional[str]:
        """Synthesize Web Audio sound score using window.SFX.render and write 16-bit 48kHz WAV."""
        script = f"""
        (async () => {{
            if (!window.SFX || !window.scoreSound) return null;
            const res = await window.SFX.render(window.scoreSound, {duration}, 48000, {{ voiceover: true }});
            const numChannels = 2;
            const sampleRate = res.sr;
            const numFrames = res.frames;
            const bytesPerSample = 2;
            const blockAlign = numChannels * bytesPerSample;
            const byteRate = sampleRate * blockAlign;
            const dataSize = numFrames * blockAlign;
            const buffer = new ArrayBuffer(44 + dataSize);
            const view = new DataView(buffer);

            function writeString(offset, str) {{
                for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
            }}

            writeString(0, 'RIFF');
            view.setUint32(4, 36 + dataSize, true);
            writeString(8, 'WAVE');
            writeString(12, 'fmt ');
            view.setUint32(16, 16, true);
            view.setUint16(20, 1, true);
            view.setUint16(22, numChannels, true);
            view.setUint32(24, sampleRate, true);
            view.setUint32(28, byteRate, true);
            view.setUint16(32, blockAlign, true);
            view.setUint16(34, bytesPerSample * 8, true);
            writeString(36, 'data');
            view.setUint32(40, dataSize, true);

            const inter = res.inter;
            let offset = 44;
            for (let i = 0; i < inter.length; i++) {{
                let s = Math.max(-1, Math.min(1, inter[i]));
                view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
                offset += 2;
            }}

            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunkSize = 32768;
            for (let i = 0; i < bytes.length; i += chunkSize) {{
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
            }}
            return btoa(binary);
        }})()
        """
        try:
            wav_b64 = await client.evaluate(script, await_promise=True, return_by_value=True)
            if not wav_b64:
                return None
            wav_data = base64.b64decode(wav_b64)
            os.makedirs(os.path.dirname(out_wav_path), exist_ok=True)
            with open(out_wav_path, "wb") as f:
                f.write(wav_data)
            logger.info("Synthesized %d bytes of procedural Web Audio SFX to %s", len(wav_data), out_wav_path)
            return out_wav_path
        except Exception as e:
            logger.warning("Failed to render SFX audio: %s", e)
            return None

    async def render_scene_video(
        self,
        scene_id: str,
        adapter_url: str,
        duration: float,
        out_mp4_path: str,
        fps: float = 30.0,
        quality: float = 0.88,
        sfx_out_wav: Optional[str] = None,
    ) -> SegmentResult:
        """Render a full scene adapter to MP4 at 1080x1920 @ 30 FPS using Headless Chromium."""
        self.start_browser()
        total_frames = int(round(duration * fps))
        os.makedirs(os.path.dirname(out_mp4_path), exist_ok=True)

        page = await self._setup_page(adapter_url, width=1080, height=1920)

        # Configure dynamic duration standard in adapter context
        await page.evaluate(f"if (typeof setDuration === 'function') setDuration({duration}); else if (window.setDuration) window.setDuration({duration}); window.DURATION = {duration};")

        # Synthesize audio stem if requested
        sfx_path = None
        if sfx_out_wav:
            sfx_path = await self.extract_sfx_audio(page, duration, sfx_out_wav)

        # Launch FFmpeg pipe with strict constant framerate PTS
        ffmpeg_cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-video_track_timescale", "30000",
            "-r", str(fps),
            "-movflags", "+faststart",
            out_mp4_path,
        ]

        logger.info("Rendering %d frames (%.1fs) for %s to %s...", total_frames, duration, scene_id, out_mp4_path)
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        t_start = time.time()
        frames_done = 0

        try:
            for i in range(total_frames):
                t = i / fps
                # Seek and extract JPEG in single round-trip for maximum throughput
                eval_expr = f"window.seek({t}); document.getElementById('stage').toDataURL('image/jpeg', {quality})"
                data_url = await page.evaluate(eval_expr)
                if not data_url or not data_url.startswith("data:image/jpeg;base64,"):
                    raise RuntimeError(f"Invalid frame data at t={t:.2f}s")

                b64_str = data_url[len("data:image/jpeg;base64,"):]
                jpeg_bytes = base64.b64decode(b64_str)
                ffmpeg_proc.stdin.write(jpeg_bytes)
                frames_done += 1

                if (i + 1) % 30 == 0 or (i + 1) == total_frames:
                    elapsed = time.time() - t_start
                    cur_fps = frames_done / elapsed if elapsed > 0 else 0
                    pct = (frames_done / total_frames) * 100
                    logger.info("[%s] %d/%d frames (%.1f%%) rendered @ %.1f FPS", scene_id, frames_done, total_frames, pct, cur_fps)

            ffmpeg_proc.stdin.close()
            ffmpeg_proc.wait(timeout=25)
            if ffmpeg_proc.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding failed with code {ffmpeg_proc.returncode}")

            t_end = time.time()
            elapsed_total = t_end - t_start
            final_fps = frames_done / elapsed_total if elapsed_total > 0 else 0.0

            telemetry = RenderTelemetry(
                frames_rendered=frames_done,
                total_duration_sec=duration,
                elapsed_time_sec=elapsed_total,
                fps=round(final_fps, 2),
                engine="Headless Chromium (Edge)",
            )

            logger.info("Successfully rendered %s: %d frames in %.2fs (%.1f FPS)", scene_id, frames_done, elapsed_total, final_fps)

            return SegmentResult(
                scene_id=scene_id,
                video_path=out_mp4_path,
                duration=duration,
                fps=fps,
                sfx_audio_path=sfx_path,
                telemetry=telemetry,
                success=True,
            )

        except Exception as e:
            logger.error("Failed to render scene %s: %s", scene_id, e)
            if ffmpeg_proc.stdin and not ffmpeg_proc.stdin.closed:
                ffmpeg_proc.stdin.close()
            ffmpeg_proc.kill()
            return SegmentResult(
                scene_id=scene_id,
                video_path=out_mp4_path,
                duration=duration,
                fps=fps,
                success=False,
                error_message=str(e),
            )
        finally:
            if self.page_client:
                await self.page_client.close()
                self.page_client = None
            if self.browser_client:
                await self.browser_client.close()
                self.browser_client = None
