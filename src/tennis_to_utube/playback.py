"""Playback helpers shared by the player and marking (no Qt, no mpv).

Time model: the frame "on screen at t" is the last frame whose start (pts) is <= t.
mpv's exact seek instead shows the first frame starting at or after (target - 5 ms) — it
drops decoded frames more than 5 ms before the target — and reports that frame's start
as its position. These helpers translate between the two,
so a seek to an event shows the frame that was on screen when it was marked (measured
with mpv 0.37 on synthetic 59.94 fps footage; see tests/test_player_mpv.py).
"""

from __future__ import annotations

import math
import os
from fractions import Fraction
from typing import Sequence


def frame_ms(fps: str | None) -> float:
    """Frame duration in ms from an ffprobe rate like "60000/1001" (default 59.94 fps)."""
    try:
        rate = Fraction(fps) if fps else Fraction(60000, 1001)
    except (ValueError, ZeroDivisionError):
        rate = Fraction(60000, 1001)
    if rate <= 0:
        rate = Fraction(60000, 1001)
    return float(1000 / rate)


def edl_url(paths: Sequence[str | os.PathLike[str]], durations_ms: Sequence[int]) -> str:
    """mpv EDL that plays the sources back to back as one timeline.

    Each segment's length is given explicitly (our ``duration_ms``), so mpv's time matches
    the joined timeline used for events exactly. Paths are length-prefixed (``%N%path``)
    so commas, semicolons and non-ASCII names need no escaping.
    """
    parts = []
    for p, ms in zip(paths, durations_ms, strict=True):
        s = os.fspath(p)
        parts.append(f"%{len(s.encode('utf-8'))}%{s},0,{ms / 1000:.3f}")
    return "edl://" + ";".join(parts)


MPV_HRSEEK_TOLERANCE_MS = 5.0


def seek_seconds(t_ms: int, frame: float) -> float:
    """Target for an exact mpv seek that shows the frame on screen at ``t_ms``.

    Aims so that (target - 5 ms) falls just after the previous frame's start: then the first
    frame at or after it is the one containing ``t_ms`` (exact unless ``t_ms`` is within
    0.5 ms of the next frame's start).
    """
    return max(0.0, (t_ms - frame + 0.5 + MPV_HRSEEK_TOLERANCE_MS) / 1000)


def position_ms(mpv_seconds: float | None) -> int:
    """mpv's position (a frame start) as integer ms inside that frame (rounded up)."""
    if mpv_seconds is None:
        return 0
    return max(0, math.ceil(mpv_seconds * 1000 - 1e-6))


def mark_time(position: int, playing: bool, speed: float, reaction_ms: int) -> int:
    """Time to store for a mark: earlier by the reaction offset (real time, so scaled by the
    playback speed) while playing; exactly the shown frame while paused."""
    if not playing:
        return position
    return max(0, round(position - reaction_ms * speed))


def neighbor_event(times_ms: Sequence[int], position: int, frame: float, forward: bool) -> int | None:
    """Time of the next/previous event, skipping events in the frame now on screen.

    ``position`` is a frame start (as reported by the player); an event belongs to the frame
    [position, position + frame).
    """
    if forward:
        later = [t for t in times_ms if t >= position + frame]
        return min(later) if later else None
    earlier = [t for t in times_ms if t < position]
    return max(earlier) if earlier else None


def clock_text(t_ms: int) -> str:
    """ "1:02:13.467" (hours always shown, ms precision)."""
    s, ms = divmod(max(0, int(t_ms)), 1000)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


def parse_clock(text: str) -> int | None:
    """ "1:02:13.467", "2:13.5", "13" → ms; None if not understood."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(parts):
        return None
    try:
        *hm, sec = parts
        seconds = float(sec)
        units = [int(p) for p in hm]
    except ValueError:
        return None
    if seconds < 0 or any(u < 0 for u in units) or (hm and seconds >= 60):
        return None
    total = 0
    for u in units:
        total = total * 60 + u
    return round((total * 60 + seconds) * 1000) if hm else round(seconds * 1000)
