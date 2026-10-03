"""Synthetic camera footage for end-to-end tests.

Clips mimic the GoPro profile (HEVC Main, yuvj420p, 60000/1001 fps, keyframe every
60 frames, B-frames, AAC audio, plus a non-A/V track like GoPro telemetry) at a small
size. Every frame shows its global frame number as a 12-bit barcode of vertical bars,
so a decoded frame can be identified exactly.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

FPS = Fraction(60000, 1001)
FRAME_MS = 1000 / FPS  # 16.683... ms
BITS = 12
SIZE = "320x180"


@functools.cache
def have_ffmpeg() -> bool:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "libx265" in out


def make_clip(path: Path, frames: int, first_index: int = 0, *, open_gop: bool = False,
              data_track: bool = True) -> Path:
    """Encode ``frames`` frames numbered from ``first_index``."""
    x265 = f"keyint=60:min-keyint=60:scenecut=0:open-gop={int(open_gop)}:log-level=error"
    barcode = (f"geq=lum='if(mod(floor((N+{first_index})/pow(2,floor(X*{BITS}/W))),2),255,0)',"
               "format=yuvj420p")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=black:s={SIZE}:r={FPS.numerator}/{FPS.denominator},format=gray",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    if data_track:
        srt = path.with_suffix(".srt")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\ntelemetry stand-in\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v", "-map", "1:a"] + (["-map", "2:s"] if data_track else [])
    cmd += ["-vf", barcode, "-frames:v", str(frames),
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", x265, "-tag:v", "hvc1",
            "-c:a", "aac", "-shortest"]
    if data_track:
        cmd += ["-c:s", "mov_text"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def read_frames(path: Path) -> list[tuple[Fraction, int]]:
    """Every displayed frame of the first video stream: (pts in ms, barcode number)."""
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "frame=pts:stream=time_base", "-of", "json", str(path)],
        capture_output=True, check=True).stdout)
    tb = Fraction(j["streams"][0]["time_base"])
    pts = [Fraction(int(f["pts"])) * tb * 1000 for f in j["frames"]]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
         "-vf", "crop=iw:2:0:ih/2,format=gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    stride = len(raw) // len(pts)
    width = stride // 2
    out = []
    for k, p in enumerate(pts):
        row = raw[k * stride:k * stride + width]
        n = sum(1 << b for b in range(BITS) if row[int((b + 0.5) * width / BITS)] > 127)
        out.append((p, n))
    return out


def frame_at(frames: list[tuple[Fraction, int]], t_ms: Fraction | int) -> int:
    """Barcode of the frame on screen at ``t_ms`` (last frame with pts <= t)."""
    shown = None
    for pts, n in sorted(frames):
        if pts <= t_ms:
            shown = n
        else:
            break
    if shown is None:
        raise AssertionError(f"no frame on screen at {t_ms} ms")
    return shown
