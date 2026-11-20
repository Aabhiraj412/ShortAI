"""Stage 3.32H: safe FFmpeg scale-and-pad geometry.

Root cause of the "Padded dimensions cannot be smaller than input dimensions" defect: the Priority B adapter padded
into an 860x855 viewport. With a chroma-subsampled pixel format (yuv420p, the format negotiated for libx264) the
``pad`` filter silently rounds its target DOWN to the chroma grid (855 -> 854), while ``scale`` could produce an input
855 rows tall for any portrait or square source. 854 < 855, so FFmpeg refused the graph - deterministically, for every
such source. Fix: every target is even, and ``scale`` is told to produce even dimensions (``force_divisible_by=2``,
which rounds down for ``force_original_aspect_ratio=decrease``), so the scaled frame always fits the pad.
"""

from __future__ import annotations

from typing import Optional, Tuple

MAX_DIMENSION = 8192


def even_floor(value: int) -> int:
    """The largest even integer <= value (minimum 2)."""
    v = int(value)
    return max(2, v - (v % 2))


def validate_dimensions(width: int, height: int) -> Tuple[int, int]:
    """Rejects invalid output geometry before FFmpeg sees it; returns even dimensions."""
    if isinstance(width, bool) or isinstance(height, bool):
        raise ValueError("invalid dimensions: booleans are not dimensions")
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid dimensions: {width!r}x{height!r}") from exc
    if w != width or h != height:
        raise ValueError(f"invalid dimensions: {width!r}x{height!r} are not whole pixels")
    if w < 2 or h < 2 or w > MAX_DIMENSION or h > MAX_DIMENSION:
        raise ValueError(f"invalid dimensions: {w}x{h} (allowed 2..{MAX_DIMENSION})")
    return even_floor(w), even_floor(h)


def fit_and_pad_filter(width: int, height: int, color: Optional[str] = None) -> str:
    """FFmpeg filter chain that letterboxes any input into exactly ``width`` x ``height`` (rounded down to even).

    The scaled frame is always <= the pad target in both dimensions, for every input aspect ratio and parity.
    """
    w, h = validate_dimensions(width, height)
    pad = f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    if color:
        pad += f":color={color}"
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,{pad}"


def stderr_tail(text: Optional[str], limit: int = 1500) -> str:
    """The last ``limit`` characters of FFmpeg stderr (errors are at the end; whole logs can be megabytes)."""
    s = (text or "").strip()
    return s if len(s) <= limit else "..." + s[-limit:]


def self_check() -> dict:
    """Read-only check that the 3.32H fix is active: odd targets become even, scale is forced even, and FFmpeg
    geometry errors classify as DETERMINISTIC (not TRANSIENT) in the retry policy."""
    from backend.app.scheduler.retry_policy import DETERMINISTIC, classify_error
    f = fit_and_pad_filter(860, 855)
    det = classify_error(RuntimeError("Padded dimensions cannot be smaller than input dimensions."))[0]
    ok = "860:854" in f and "force_divisible_by=2" in f and det == DETERMINISTIC
    return {"ok": ok, "filter": f, "padding_error_class": det}
