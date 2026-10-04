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
from .matchfile import MatchFile, Source, default_match, relative_source_path
from .probe import MediaInfo

VIDEO_EXTENSIONS = (".mp4", ".mov")
MATCH_SUFFIX = ".match.json"

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


def list_videos(folder: str | os.PathLike[str]) -> list[Path]:
    """Video files in ``folder``, in the proposed order."""
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return []
    return order_files(p for p in entries
                       if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
                       and not p.name.startswith("."))


def list_matches(folder: str | os.PathLike[str]) -> list[Path]:
    """Match files already in ``folder``."""
    try:
        return sorted(p for p in Path(folder).iterdir() if p.name.endswith(MATCH_SUFFIX))
    except OSError:
        return []


def sibling_folders(folder: str | os.PathLike[str]) -> list[Path]:
    """Folders next to ``folder`` (including itself), naturally sorted; hidden ones skipped."""
    parent = Path(folder).parent
    try:
        dirs = [p for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return []
    return sorted(dirs, key=lambda p: _natural_key(p.name))


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


def new_match_file(infos: Sequence[MediaInfo], match_path: str | os.PathLike[str], *,
                   kind: str = "singles", side_a: Sequence[str] = (), side_b: Sequence[str] = (),
                   format_spec: dict | None = None) -> MatchFile:
    """A new match over ``infos`` (in order). Blank names get their "Player N" defaults."""
    per_side = 2 if kind == "doubles" else 1
    match = default_match()
    match["kind"] = kind
    for side, given, base in (("A", side_a, 0), ("B", side_b, per_side)):
        names = [n.strip() for n in given][:per_side]
        names += [""] * (per_side - len(names))
        match["sides"][side]["players"] = [n or f"Player {base + i + 1}"
                                           for i, n in enumerate(names)]
    if format_spec is not None:
        match["format"] = dict(format_spec)
    return MatchFile(sources=[make_source(i, match_path) for i in infos], match=match)
