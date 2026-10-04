"""Source files: GoPro naming/ordering, join compatibility and creation-time checks.

GoPro names are ``G`` + encoding letter (``X`` HEVC, ``H`` AVC) + 2-digit chapter +
4-digit recording number, e.g. ``GX020008.MP4`` = chapter 2 of recording 8. Plain
alphabetical order is wrong across recordings (``GX020007`` sorts after ``GX010008``),
so files are ordered by recording number, then chapter.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .issues import Issue
from .matchfile import Source, relative_source_path
from .probe import MediaInfo

_GOPRO_RE = re.compile(r"^G([A-Z])(\d{2})(\d{4})\.MP4$", re.IGNORECASE)


@dataclass(frozen=True)
class GoProName:
    encoding: str  # "X" (HEVC) or "H" (AVC)
    chapter: int
    recording: int


def parse_gopro_name(name: str | os.PathLike[str]) -> GoProName | None:
    m = _GOPRO_RE.match(Path(name).name)
    if not m:
        return None
    return GoProName(m.group(1).upper(), int(m.group(2)), int(m.group(3)))


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def sort_key(path: str | os.PathLike[str]) -> tuple:
    """GoPro files by (recording, chapter) first; anything else after, naturally sorted."""
    g = parse_gopro_name(path)
    if g:
        return (0, g.recording, g.chapter, [])
    return (1, 0, 0, _natural_key(Path(path).name))


def order_files(paths: Iterable[str | os.PathLike[str]]) -> list[Path]:
    return sorted((Path(p) for p in paths), key=sort_key)


def group_by_recording(paths: Iterable[str | os.PathLike[str]]) -> "OrderedDict[int | None, list[Path]]":
    """Proposed matches: one group per GoPro recording (non-GoPro files under None)."""
    groups: OrderedDict[int | None, list[Path]] = OrderedDict()
    for p in order_files(paths):
        g = parse_gopro_name(p)
        groups.setdefault(g.recording if g else None, []).append(p)
    return groups


def check_creation_order(infos: Sequence[MediaInfo]) -> list[Issue]:
    """Flag files whose ``creation_time`` goes backwards in the proposed order."""
    issues = []
    for prev, cur in zip(infos, infos[1:]):
        if prev.creation_time and cur.creation_time:
            try:
                backwards = cur.creation_time < prev.creation_time
            except TypeError:  # naive vs aware datetimes
                continue
            if backwards:
                issues.append(Issue(
                    "creation_time_order",
                    f"{cur.path.name} was created before {prev.path.name} "
                    f"({cur.creation_time.isoformat()} < {prev.creation_time.isoformat()}); "
                    "check the file order",
                ))
    return issues


def check_join_compatible(infos: Sequence[MediaInfo]) -> list[Issue]:
    """Lossless joining needs the same codec parameters as the first file."""
    if not infos:
        return []
    first = infos[0].join_params()
    issues = []
    for info in infos[1:]:
        params = info.join_params()
        diffs = [f"{k} {params[k]!r} ≠ {first[k]!r}" for k in first if params.get(k) != first[k]]
        if diffs:
            issues.append(Issue(
                "join_incompatible",
                f"{info.path.name} differs from {infos[0].path.name}: " + ", ".join(diffs),
                severity="error",
            ))
    return issues


def make_source(info: MediaInfo, match_path: str | os.PathLike[str]) -> Source:
    return Source(
        path=relative_source_path(info.path, match_path),
        duration_ms=info.duration_ms,
        codec=info.codec,
        width=info.width,
        height=info.height,
        fps=info.fps,
        pix_fmt=info.pix_fmt,
    )
