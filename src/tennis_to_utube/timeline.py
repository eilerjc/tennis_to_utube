"""The joined timeline: source files back to back, in integer milliseconds.

Event times are stored on this timeline. Source ``i`` covers
``[offset(i), offset(i) + duration(i))``; the last one also includes its end.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Timeline:
    durations_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "durations_ms", tuple(self.durations_ms))
        if not self.durations_ms:
            raise ValueError("timeline needs at least one source")
        for d in self.durations_ms:
            if not isinstance(d, int) or d <= 0:
                raise ValueError(f"source durations must be positive integers (ms), got {d!r}")

    @classmethod
    def from_sources(cls, sources: Iterable) -> Timeline:
        """From anything with ``duration_ms`` (matchfile.Source, probe.MediaInfo)."""
        return cls(tuple(s.duration_ms for s in sources))

    @cached_property
    def offsets(self) -> tuple[int, ...]:
        out, t = [], 0
        for d in self.durations_ms:
            out.append(t)
            t += d
        return tuple(out)

    @property
    def total_ms(self) -> int:
        return self.offsets[-1] + self.durations_ms[-1]

    def __len__(self) -> int:
        return len(self.durations_ms)

    def start_of(self, index: int) -> int:
        return self.offsets[index]

    def end_of(self, index: int) -> int:
        return self.offsets[index] + self.durations_ms[index]

    def locate(self, t_ms: int, *, at_end: bool = False) -> tuple[int, int]:
        """Map a joined time to ``(source index, local ms)``.

        A time exactly on a boundary between two sources belongs to the start of the
        later one, unless ``at_end`` is set (used for the end of an interval), in which
        case it is the end of the earlier one.
        """
        if not 0 <= t_ms <= self.total_ms:
            raise ValueError(f"{t_ms} ms is outside the timeline (0..{self.total_ms})")
        if at_end:
            i = max(0, bisect.bisect_left(self.offsets, t_ms) - 1)
        else:
            i = min(bisect.bisect_right(self.offsets, t_ms) - 1, len(self) - 1)
        return i, t_ms - self.offsets[i]

    def to_joined(self, index: int, local_ms: int) -> int:
        if not 0 <= local_ms <= self.durations_ms[index]:
            raise ValueError(f"{local_ms} ms is outside source {index}")
        return self.offsets[index] + local_ms


@dataclass(frozen=True, order=True)
class Interval:
    """Half-open ``[start, end)`` in ms."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def contains(self, t: int) -> bool:
        return self.start <= t < self.end


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Union of intervals, sorted; empty ones dropped, touching ones joined."""
    out: list[Interval] = []
    for iv in sorted(i for i in intervals if i.end > i.start):
        if out and iv.start <= out[-1].end:
            if iv.end > out[-1].end:
                out[-1] = Interval(out[-1].start, iv.end)
        else:
            out.append(iv)
    return out


def complement(intervals: Sequence[Interval], start: int, end: int) -> list[Interval]:
    """Parts of ``[start, end)`` not covered by ``intervals``."""
    out: list[Interval] = []
    t = start
    for iv in merge_intervals(intervals):
        if iv.end <= t:
            continue
        if iv.start >= end:
            break
        if iv.start > t:
            out.append(Interval(t, iv.start))
        t = max(t, iv.end)
    if t < end:
        out.append(Interval(t, end))
    return out
