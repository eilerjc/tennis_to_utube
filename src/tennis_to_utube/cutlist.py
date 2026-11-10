"""Cut only (DESIGN.md §12b): remove parts of one video, or several joined in order, with
nothing tennis about it.

One shot: the removals live in memory only (:class:`CutList`, with undo), and
:func:`make_video` writes the result with the same lossless planner and ffmpeg run as the
Trim tool (keyframe-snapped starts, exact ends on footage without B-frames).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .playback import clock_text
from .probe import MediaInfo, ProbeKeyframes, Tools
from .timeline import Timeline
from .trim import Cut, TrimPlan, TrimResult, plan_trim, run_trim

Report = Callable[[float, str], None]  # fraction 0..1 (or -1: unknown), text


@dataclass
class CutList:
    """Parts of the joined timeline to remove, ``[start_ms, end_ms)``, sorted and never
    overlapping. ``I`` (:meth:`start`) and ``O`` (:meth:`end`) mark one at the playhead."""

    total_ms: int
    removals: list[tuple[int, int]] = field(default_factory=list)
    pending_start: int | None = None  # I pressed, waiting for O
    _undo: list[list[tuple[int, int]]] = field(default_factory=list, repr=False)
    _redo: list[list[tuple[int, int]]] = field(default_factory=list, repr=False)

    def start(self, t_ms: int) -> None:
        self.pending_start = self._clamp(t_ms)

    def end(self, t_ms: int) -> tuple[int, int] | None:
        """Close the removal begun with :meth:`start`; None if there is none or it is empty.
        Marks given the wrong way round are swapped; overlapping removals merge."""
        if self.pending_start is None:
            return None
        a, b = sorted((self.pending_start, self._clamp(t_ms)))
        self.pending_start = None
        if b <= a:
            return None
        return self.add(a, b)

    def add(self, start_ms: int, end_ms: int) -> tuple[int, int]:
        a, b = self._clamp(min(start_ms, end_ms)), self._clamp(max(start_ms, end_ms))
        self._remember()
        keep = []
        for s, e in self.removals:
            if e < a or s > b:  # touching removals merge too
                keep.append((s, e))
            else:
                a, b = min(a, s), max(b, e)
        self.removals = sorted(keep + [(a, b)])
        return a, b

    def delete(self, index: int) -> tuple[int, int]:
        self._remember()
        return self.removals.pop(index)

    def index_at(self, t_ms: int) -> int | None:
        """The removal containing ``t_ms`` (its end included), if any."""
        return next((i for i, (s, e) in enumerate(self.removals) if s <= t_ms <= e), None)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.removals)
        self.removals = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.removals)
        self.removals = self._redo.pop()
        return True

    @property
    def removed_ms(self) -> int:
        return sum(e - s for s, e in self.removals)

    @property
    def kept_ms(self) -> int:
        return self.total_ms - self.removed_ms

    def cuts(self) -> list[Cut]:
        return [Cut(f"cut{i + 1}", "manual", s, e, f"Removal {i + 1}")
                for i, (s, e) in enumerate(self.removals)]

    def _clamp(self, t_ms: int) -> int:
        return min(max(0, int(t_ms)), self.total_ms)

    def _remember(self) -> None:
        self._undo.append(list(self.removals))
        self._redo.clear()


def default_output(first_source: Path) -> Path:
    return first_source.with_name(f"{first_source.stem} cut.mp4")


@dataclass
class Made:
    result: TrimResult

    @property
    def plan(self) -> TrimPlan:
        return self.result.plan

    def summary(self) -> str:
        plan = self.plan
        text = (f"Made {self.result.output.name}: {clock_text(plan.total_out_ms)} kept of "
                f"{clock_text(plan.timeline.total_ms)}")
        if plan.removed:
            text += (" — each kept part resumes at a keyframe, so a removal can end up to "
                     "~1 s earlier than marked")
        return text


def make_video(infos: Sequence[MediaInfo], cut_list: CutList, output: Path, tools: Tools,
               report: Report = lambda f, t: None,
               cancel: threading.Event | None = None) -> Made:
    """Write the joined videos without the removals (stream copy, no re-encode)."""
    paths = [i.path for i in infos]
    durations = [i.duration_ms for i in infos]
    cuts = cut_list.cuts()
    keyframes = None
    if cuts:
        report(-1, "Finding keyframes…")
        keyframes = ProbeKeyframes(paths, tools, durations_ms=durations,
                                   exact_ends=[i.has_b_frames == 0 for i in infos])
    plan = plan_trim(Timeline(durations), cuts, keyframes)
    report(0.0, "Writing video…")
    result = run_trim(plan, paths, output, tools, cancel=cancel,
                      progress=lambda f: report(f, f"Writing video… {f:.0%}"))
    return Made(result)
