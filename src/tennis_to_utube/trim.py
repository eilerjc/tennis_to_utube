"""Trim pass: removal rules → kept segments snapped to keyframes → ffmpeg concat plan.

Everything is stream copy. Two facts about stream-copy cuts drive the snapping
(measured on HEVC with B-frames, see tests/test_trim_e2e.py):

* A kept segment must **start** on a keyframe, so its start snaps *back* to the
  keyframe at or before the requested time (keeps up to one GOP of extra context).
* A kept segment should also **end** right before a keyframe. The concat demuxer cuts
  on decode timestamps, so with B-frames an arbitrary end drops some frames before the
  cut and keeps some after it. Ending at the next keyframe's decode time keeps exactly
  the frames displayed before that keyframe. Ends therefore snap *forward* to the
  first keyframe after the requested time (again up to one GOP extra, never less).

Requires closed GOPs (checked by :func:`run_trim`).

Event times are then remapped onto the output using the actual snapped segments.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from . import catalog, structure
from .issues import Issue
from .matchfile import Event
from .probe import Keyframe, MediaInfo, Tools, inspect_gop, probe, run
from .timeline import Interval, Timeline, complement, merge_intervals

RULES = ("warmup", "changeovers", "set_breaks", "after_match")
RULE_LABELS = {
    "warmup": "Warm-up (before play starts)",
    "changeovers": "Changeovers",
    "set_breaks": "Set breaks",
    "after_match": "After match end",
}


class TrimError(RuntimeError):
    pass


# -- removal rules ---------------------------------------------------------------


@dataclass(frozen=True)
class Cut:
    """A proposed removal ``[start_ms, end_ms)`` on the joined timeline."""

    key: str  # stable: rule + anchor event id, so an unticked cut stays unticked
    rule: str
    start_ms: int
    end_ms: int
    label: str
    enabled: bool = True


def _next(events: Sequence[Event], i: int, types: frozenset[str]) -> Event | None:
    for e in events[i + 1:]:
        if e.type in types:
            return e
    return None


def propose_cuts(events: Iterable[Event], total_ms: int, rules: Iterable[str] = RULES) -> list[Cut]:
    """Cuts suggested by the chosen rules, sorted by start time.

    * warmup: from 0 to the first Match/Set/Game start or Set score.
    * changeovers: from the Game end that closes an odd game of a set to the next Game
      start (game parity from :mod:`structure`; while it is unknown, e.g. the video
      starts mid-match without a Set score giving the games, none are proposed).
    * set_breaks: from Set end to the next Set or Game start.
    * after_match: from Match end to the end of the video.
    """
    rules = set(rules)
    unknown = rules - set(RULES)
    if unknown:
        raise ValueError(f"unknown trim rules: {', '.join(sorted(unknown))}")
    evs = sorted(events, key=lambda e: e.t_ms)
    cuts: list[Cut] = []

    def add(rule: str, anchor: Event, start: int, end: int, label: str) -> None:
        start, end = max(0, start), min(total_ms, end)
        if end > start:
            cuts.append(Cut(f"{rule}:{anchor.id}", rule, start, end, label))

    if "warmup" in rules:
        first = next((e for e in evs if e.type in catalog.PLAY_BEGINS), None)
        if first is not None:
            add("warmup", first, 0, first.t_ms, RULE_LABELS["warmup"])

    for i, (e, pos) in enumerate(structure.walk(evs)):
        set_txt = f" (set {pos.set_no})" if pos.set_no else ""
        if e.type == catalog.GAME_END and "changeovers" in rules:
            nxt = _next(evs, i, frozenset({catalog.GAME_START}))
            if pos.games_closed is not None and pos.games_closed % 2 == 1 and nxt is not None:
                add("changeovers", e, e.t_ms, nxt.t_ms,
                    f"Changeover after game {pos.games_closed}{set_txt}")
        elif e.type == catalog.SET_END and "set_breaks" in rules:
            nxt = _next(evs, i, frozenset({catalog.SET_START, catalog.GAME_START}))
            if nxt is not None:
                add("set_breaks", e, e.t_ms, nxt.t_ms,
                    f"Set break after set {pos.set_no}" if pos.set_no else "Set break")
        elif e.type == catalog.MATCH_END and "after_match" in rules:
            add("after_match", e, e.t_ms, total_ms, RULE_LABELS["after_match"])
    return sorted(cuts, key=lambda c: (c.start_ms, c.end_ms))


# -- keyframes -------------------------------------------------------------------


class KeyframeLookup(Protocol):
    def at_or_before(self, index: int, local_ms: int) -> Keyframe: ...

    def after(self, index: int, local_ms: int) -> Keyframe | None: ...


class ListKeyframes:
    """In-memory keyframe lookup (one sorted list per source)."""

    def __init__(self, per_source: Sequence[Sequence[Keyframe]]):
        self.per_source = [sorted(kfs) for kfs in per_source]

    @classmethod
    def regular(cls, durations_ms: Sequence[int], interval: Fraction | int, b_delay: Fraction | int = 0
                ) -> ListKeyframes:
        """Keyframes every ``interval`` ms from 0, decode times ``b_delay`` ms earlier."""
        out = []
        for d in durations_ms:
            kfs, n = [], 0
            while n * interval < d:
                t = Fraction(n * interval)
                kfs.append(Keyframe(math.ceil(t), math.floor(t - b_delay)))
                n += 1
            out.append(kfs)
        return cls(out)

    def at_or_before(self, index: int, local_ms: int) -> Keyframe:
        best = None
        for k in self.per_source[index]:
            if k.pts_ms > local_ms:
                break
            best = k
        if best is None:
            raise TrimError(f"no keyframe at or before {local_ms} ms in source {index}")
        return best

    def after(self, index: int, local_ms: int) -> Keyframe | None:
        return next((k for k in self.per_source[index] if k.pts_ms > local_ms), None)


# -- plan ------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """One concat entry: part of one source file, in local ms."""

    source_index: int
    inpoint_ms: int  # keyframe pts (display start)
    outpoint_ms: int  # concat 'outpoint': packets with decode time >= this are dropped
    end_ms: int  # display end (exclusive)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.inpoint_ms


@dataclass(frozen=True)
class Segment:
    """A kept stretch of the joined timeline after snapping, ``[start_ms, end_ms)``."""

    start_ms: int
    end_ms: int
    out_start_ms: int  # where it begins in the output (before TrimPlan.output_start_ms)

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class TrimPlan:
    timeline: Timeline
    cuts: tuple[Cut, ...]  # as proposed, including unticked ones
    segments: tuple[Segment, ...]
    pieces: tuple[Piece, ...]
    # Start time of the video stream in the produced file. The MP4 muxer shifts video
    # slightly when audio starts before the first keyframe; measured after processing.
    output_start_ms: int = 0

    @property
    def total_out_ms(self) -> int:
        return sum(s.length_ms for s in self.segments)

    @property
    def removed(self) -> list[Interval]:
        """Actually removed regions (after snapping) on the joined timeline."""
        return complement([Interval(s.start_ms, s.end_ms) for s in self.segments], 0,
                          self.timeline.total_ms)

    @property
    def is_identity(self) -> bool:
        return len(self.segments) == 1 and self.segments[0].length_ms == self.timeline.total_ms

    def segment_at(self, t_ms: int) -> Segment | None:
        total = self.timeline.total_ms
        for s in self.segments:
            if s.start_ms <= t_ms < s.end_ms or t_ms == s.end_ms == total:
                return s
        return None

    def remap(self, t_ms: int) -> int | None:
        """Output time of a joined-timeline time, or None if it was removed."""
        s = self.segment_at(t_ms)
        if s is None:
            return None
        return self.output_start_ms + s.out_start_ms + (t_ms - s.start_ms)


def plan_trim(timeline: Timeline, cuts: Iterable[Cut], keyframes: KeyframeLookup | None) -> TrimPlan:
    """Kept segments for the enabled cuts, snapped to keyframes, split into pieces.

    ``keyframes`` may be None when no cut is enabled (plain join).
    """
    cuts = tuple(cuts)
    total = timeline.total_ms
    removal = [Interval(max(0, c.start_ms), min(total, c.end_ms)) for c in cuts if c.enabled]
    kept = complement(merge_intervals(removal), 0, total)
    if not kept:
        raise TrimError("the enabled cuts remove the whole video")

    # Each kept interval becomes (start, end) where end carries its cut info:
    # (joined end, source index, local outpoint, local display end).
    spans: list[tuple[int, tuple[int, int, int, int]]] = []
    for iv in kept:
        start = _snap_start(timeline, keyframes, iv.start)
        end = _snap_end(timeline, keyframes, iv.end)
        if spans and start <= spans[-1][1][0]:
            if end[0] > spans[-1][1][0]:
                spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))

    segments, pieces, out = [], [], 0
    for start, (end, j, out_local, end_local) in spans:
        segments.append(Segment(start, end, out))
        out += end - start
        i, in_local = timeline.locate(start)
        for f in range(i, j + 1):
            p_in = in_local if f == i else 0
            if f == j:
                p_out, p_end = out_local, end_local
            else:
                p_out = p_end = timeline.durations_ms[f]
            if p_end > p_in:
                pieces.append(Piece(f, p_in, p_out, p_end))
    return TrimPlan(timeline, cuts, tuple(segments), tuple(pieces))


def _snap_start(timeline: Timeline, keyframes: KeyframeLookup | None, t: int) -> int:
    i, local = timeline.locate(t)
    if local == 0:
        return t  # files start with a keyframe
    if keyframes is None:
        raise TrimError("keyframe lookup needed for cuts")
    kf = keyframes.at_or_before(i, local)
    return timeline.start_of(i) + min(kf.pts_ms, local)


def _snap_end(timeline: Timeline, keyframes: KeyframeLookup | None, t: int) -> tuple[int, int, int, int]:
    j, local = timeline.locate(t, at_end=True)
    dur = timeline.durations_ms[j]
    if local < dur:
        if keyframes is None:
            raise TrimError("keyframe lookup needed for cuts")
        kf = keyframes.after(j, local)
        if kf is not None and kf.pts_ms < dur:
            return timeline.start_of(j) + kf.pts_ms, j, kf.dts_ms, kf.pts_ms
    return timeline.end_of(j), j, dur, dur


# -- events ----------------------------------------------------------------------


@dataclass(frozen=True)
class Remapped:
    event: Event
    out_ms: int  # output time of the event
    segment_out_ms: int  # output time where its kept segment starts (lead-in floor)

    def link_ms(self, lead_in_ms: int) -> int:
        return max(0, self.segment_out_ms, self.out_ms - lead_in_ms)


def remap_events(events: Iterable[Event], plan: TrimPlan) -> tuple[list[Remapped], list[Issue]]:
    """Output times for all events; removed or out-of-range events become issues."""
    kept, issues = [], []
    total = plan.timeline.total_ms
    for e in sorted(events, key=lambda e: e.t_ms):
        if e.t_ms > total:
            issues.append(Issue("event_outside_video",
                                f"{catalog.label(e.type)} at {e.t_ms} ms is after the end of the video",
                                e.t_ms, e.id, "error"))
            continue
        s = plan.segment_at(e.t_ms)
        if s is None:
            issues.append(Issue("event_in_removed_region",
                                f"{catalog.label(e.type)} is inside removed footage and will not be exported",
                                e.t_ms, e.id))
            continue
        seg_out = plan.output_start_ms + s.out_start_ms
        kept.append(Remapped(e, seg_out + (e.t_ms - s.start_ms), seg_out))
    return kept, issues


# -- ffmpeg ----------------------------------------------------------------------


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def _sec(ms: int) -> str:
    return f"{ms // 1000}.{ms % 1000:03d}"


def concat_script(plan: TrimPlan, paths: Sequence[str | os.PathLike[str]]) -> str:
    """ffconcat list: inpoint = keyframe, outpoint = decode-time cut, duration = shown span."""
    if len(paths) != len(plan.timeline):
        raise ValueError("need one path per source")
    lines = ["ffconcat version 1.0"]
    for p in plan.pieces:
        lines += [
            "file " + _quote(Path(paths[p.source_index]).resolve().as_posix()),
            f"inpoint {_sec(p.inpoint_ms)}",
            f"outpoint {_sec(p.outpoint_ms)}",
            f"duration {_sec(p.duration_ms)}",
        ]
    return "\n".join(lines) + "\n"


def ffmpeg_command(tools: Tools, concat_path: str | os.PathLike[str], output: str | os.PathLike[str],
                   video_codec: str = "hevc") -> list[str]:
    cmd = [tools.ffmpeg, "-hide_banner", "-nostdin", "-y",
           "-f", "concat", "-safe", "0", "-i", str(concat_path),
           "-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
    if video_codec == "hevc":
        cmd += ["-tag:v", "hvc1"]
    return cmd + ["-movflags", "+faststart", str(output)]


@dataclass(frozen=True)
class TrimResult:
    output: Path
    plan: TrimPlan  # with output_start_ms measured from the produced file
    info: MediaInfo
    issues: tuple[Issue, ...]


_WARN_RE = re.compile(r"non-monotonic|invalid|error|corrupt", re.IGNORECASE)
_AUDIO_DTS_RE = re.compile(r"Non-monotonic DTS in output stream 0:([1-9]\d*)")


def _ffmpeg_issues(stderr: str) -> list[Issue]:
    """ffmpeg warnings as issues.

    Non-monotonic DTS on an *audio* stream is expected where two files join: the next
    file's first AAC packet (encoder priming) starts a few ms before its first video
    frame, so the muxer nudges that one packet. It does not move video, so it is info.
    """
    issues = []
    for line in stderr.splitlines():
        if _AUDIO_DTS_RE.search(line):
            issues.append(Issue("audio_timestamp_adjusted", line.strip(), severity="info"))
        elif _WARN_RE.search(line):
            issues.append(Issue("ffmpeg_warning", line.strip()))
    return issues


def run_trim(plan: TrimPlan, paths: Sequence[str | os.PathLike[str]], output: str | os.PathLike[str],
             tools: Tools = Tools(), *, allow_open_gop: bool = False) -> TrimResult:
    """Write the trimmed/joined file with ffmpeg, then verify it with ffprobe."""
    output = Path(output)
    sources = [Path(p).resolve() for p in paths]
    if output.resolve() in sources:
        raise TrimError("output would overwrite a source file")
    first = probe(sources[0], tools)
    if not plan.is_identity and not allow_open_gop:
        for src in dict.fromkeys(sources):
            gop = inspect_gop(src, tools)
            if not gop.closed:
                raise TrimError(f"{src.name} uses open GOPs; stream-copy cuts would show "
                                "broken frames after each cut")

    fd, list_path = tempfile.mkstemp(prefix=output.stem + ".", suffix=".ffconcat",
                                     dir=output.parent if output.parent.exists() else None)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(concat_script(plan, paths))
    try:
        proc = run(ffmpeg_command(tools, list_path, output, first.codec))
    finally:
        os.unlink(list_path)

    issues = _ffmpeg_issues(proc.stderr.decode("utf-8", "replace"))
    info = probe(output, tools)
    expected = plan.total_out_ms
    frame_ms = 1000 / float(Fraction(first.fps)) if first.fps not in ("0/0", "") else 40.0
    tolerance = frame_ms + len(plan.pieces)
    if abs(info.duration_ms - expected) > tolerance:
        issues.append(Issue("duration_mismatch",
                            f"output video is {info.duration_ms} ms, expected {expected} ms",
                            severity="error"))
    extra_streams = [t for t in info.stream_types if t not in ("video", "audio")]
    if extra_streams:
        issues.append(Issue("unexpected_streams", f"output has extra streams: {extra_streams}"))
    plan = dataclasses.replace(plan, output_start_ms=info.start_ms)
    return TrimResult(output, plan, info, tuple(issues))
