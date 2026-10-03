"""ffprobe/ffmpeg wrapper: media info, keyframe lookup, GOP structure check.

Keyframe lookups read only a few seconds around the requested time (``-read_intervals``)
so they stay fast on 11 GB chapter files.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tools:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @classmethod
    def from_config(cls, config: Any) -> Tools:
        return cls(ffmpeg=config.get("tools.ffmpeg"), ffprobe=config.get("tools.ffprobe"))


def run(cmd: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a tool without a console window (Windows), raising ToolError on failure."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(list(cmd), capture_output=True, **kwargs)
    except FileNotFoundError as exc:
        raise ToolError(f"{cmd[0]} not found; install ffmpeg or set tools in config.toml") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-10:]
        raise ToolError(f"{Path(cmd[0]).name} failed ({proc.returncode}): " + "\n".join(tail))
    return proc


def _probe_json(tools: Tools, args: Sequence[str]) -> dict[str, Any]:
    proc = run([tools.ffprobe, "-v", "error", "-of", "json", *args])
    return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")


def _ms_round(seconds: str | float | None) -> int | None:
    if seconds in (None, "N/A"):
        return None
    return int(round(Fraction(str(seconds)) * 1000))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class AudioInfo:
    codec: str | None
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_ms: int  # of the video stream (falls back to the container)
    codec: str
    profile: str | None
    width: int
    height: int
    fps: str  # exact rational, e.g. "60000/1001"
    pix_fmt: str | None
    has_b_frames: int
    time_base: Fraction
    start_ms: int  # video stream start time (0 for camera files)
    creation_time: datetime | None
    size_bytes: int | None
    audio: AudioInfo | None
    stream_types: tuple[str, ...] = field(default=())

    def join_params(self) -> dict[str, Any]:
        """Parameters that must match across files for a lossless (stream copy) join."""
        params: dict[str, Any] = {
            "codec": self.codec, "profile": self.profile, "width": self.width,
            "height": self.height, "fps": self.fps, "pix_fmt": self.pix_fmt,
        }
        audio = self.audio or AudioInfo(None, None, None)
        params.update(audio_codec=audio.codec, sample_rate=audio.sample_rate, channels=audio.channels)
        return params


def probe(path: str | Path, tools: Tools = Tools()) -> MediaInfo:
    path = Path(path)
    j = _probe_json(tools, ["-show_format", "-show_streams", str(path)])
    streams = j.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ToolError(f"{path.name}: no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = j.get("format", {})
    duration = _ms_round(video.get("duration")) or _ms_round(fmt.get("duration"))
    if not duration:
        raise ToolError(f"{path.name}: unknown duration")
    tags = {**fmt.get("tags", {}), **video.get("tags", {})}
    size = fmt.get("size")
    return MediaInfo(
        path=path,
        duration_ms=duration,
        codec=video.get("codec_name", "?"),
        profile=video.get("profile"),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0",
        pix_fmt=video.get("pix_fmt"),
        has_b_frames=int(video.get("has_b_frames", 0)),
        time_base=Fraction(video.get("time_base", "1/1000")),
        start_ms=_ms_round(video.get("start_time")) or 0,
        creation_time=_parse_time(tags.get("creation_time")),
        size_bytes=int(size) if size and size.isdigit() else None,
        audio=AudioInfo(
            codec=audio.get("codec_name", "?"),
            sample_rate=int(audio["sample_rate"]) if audio.get("sample_rate") else None,
            channels=audio.get("channels"),
        ) if audio else None,
        stream_types=tuple(s.get("codec_type", "?") for s in streams),
    )


# -- keyframes ---------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Keyframe:
    """A video keyframe, in local ms of its file.

    ``pts_ms`` is rounded up and ``dts_ms`` rounded down to whole ms, so that a cut
    *starting* at ``pts_ms`` still begins on this keyframe and a cut *ending* at
    ``dts_ms`` (the concat demuxer compares decode timestamps) stops just before it.
    """

    pts_ms: int
    dts_ms: int


@dataclass(frozen=True)
class _Packet:
    pts: Fraction  # seconds
    dts: Fraction
    key: bool


def _packets(path: Path, tools: Tools, interval: str | None) -> list[_Packet]:
    args = ["-select_streams", "v:0", "-show_entries", "stream=time_base:packet=pts,dts,flags"]
    if interval:
        args += ["-read_intervals", interval]
    j = _probe_json(tools, [*args, str(path)])
    tb = Fraction(j["streams"][0]["time_base"])
    out = []
    for p in j.get("packets", []):
        if p.get("pts") is None:
            continue
        pts = Fraction(int(p["pts"])) * tb
        dts = Fraction(int(p["dts"])) * tb if p.get("dts") is not None else pts
        out.append(_Packet(pts, dts, "K" in p.get("flags", "")))
    return out


def _to_keyframe(p: _Packet) -> Keyframe:
    return Keyframe(pts_ms=math.ceil(p.pts * 1000), dts_ms=math.floor(p.dts * 1000))


def list_keyframes(path: str | Path, tools: Tools = Tools(),
                   start_ms: int | None = None, end_ms: int | None = None) -> list[Keyframe]:
    """Keyframes of the first video stream (whole file, or around a range)."""
    interval = None
    if start_ms is not None or end_ms is not None:
        s = f"{(start_ms or 0) / 1000:.3f}"
        interval = s + "%" + (f"{end_ms / 1000:.3f}" if end_ms is not None else "")
    return sorted({_to_keyframe(p) for p in _packets(Path(path), tools, interval) if p.key})


class ProbeKeyframes:
    """Keyframe lookup backed by ffprobe, reading small windows around each query."""

    def __init__(self, paths: Sequence[str | Path], tools: Tools = Tools(),
                 durations_ms: Sequence[int] | None = None, window_ms: int = 3000):
        self.paths = [Path(p) for p in paths]
        self.tools = tools
        self.durations_ms = list(durations_ms) if durations_ms is not None else None
        self.window_ms = window_ms

    def at_or_before(self, index: int, local_ms: int) -> Keyframe:
        window = self.window_ms
        while True:
            start = max(0, local_ms - window)
            kfs = [k for k in list_keyframes(self.paths[index], self.tools, start, local_ms + 1)
                   if k.pts_ms <= local_ms]
            if kfs:
                return kfs[-1]
            if start == 0:
                raise ToolError(f"{self.paths[index].name}: no keyframe at or before {local_ms} ms")
            window *= 4

    def after(self, index: int, local_ms: int) -> Keyframe | None:
        window = self.window_ms
        limit = self.durations_ms[index] if self.durations_ms else None
        while True:
            kfs = [k for k in list_keyframes(self.paths[index], self.tools, local_ms, local_ms + window)
                   if k.pts_ms > local_ms]
            if kfs:
                return kfs[0]
            if limit is None or local_ms + window >= limit:
                return None
            window *= 4


# -- GOP structure -------------------------------------------------------------


@dataclass(frozen=True)
class GopReport:
    closed: bool  # no frame after a keyframe (decode order) is shown before it
    keyframe_interval_ms: int | None
    b_frames: bool  # decode order differs from display order


def inspect_gop(path: str | Path, tools: Tools = Tools(), seconds: float = 5.0) -> GopReport:
    """Check the GOP structure on the first ``seconds`` of the file.

    Lossless cuts need *closed* GOPs: with open GOPs the frames decoded right after a
    keyframe but displayed before it reference the previous GOP and come out broken.
    """
    packets = _packets(Path(path), tools, f"%+{seconds:.3f}")
    closed, b_frames = True, False
    last_key: _Packet | None = None
    max_pts = None
    key_pts: list[Fraction] = []
    for p in packets:
        if max_pts is not None and p.pts < max_pts:
            b_frames = True
        max_pts = p.pts if max_pts is None else max(max_pts, p.pts)
        if p.key:
            last_key = p
            key_pts.append(p.pts)
        elif last_key is not None and len(key_pts) > 1 and p.pts < last_key.pts:
            closed = False
    interval = None
    if len(key_pts) > 1:
        interval = int(round(min(b - a for a, b in zip(key_pts, key_pts[1:])) * 1000))
    return GopReport(closed=closed, keyframe_interval_ms=interval, b_frames=b_frames)
