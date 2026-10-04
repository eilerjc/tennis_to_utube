"""Chapters, derived from the event log after trim remapping (DESIGN.md §8).

Anchors are Set start, Game start, and a Set score marked before any of them (the
video starts mid-match); later Set score corrections do not start chapters. If more
than ``chapter_gap_ms`` passes without an anchor, a chapter is added at the first
existing event at or after that point (never at an arbitrary time). Chapter time = event output time - lead-in,
clamped to the start of its kept segment.

:func:`youtube_chapters` then applies YouTube's rules (first at 0:00, each at least
10 s, at least 3) by merging/dropping.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Iterable, Sequence

from . import catalog, structure
from .config import Settings
from .issues import Issue
from .matchfile import Event
from .scoring import Analysis, set_text
from .trim import Remapped

YOUTUBE_MIN_CHAPTERS = 3
YOUTUBE_MIN_CHAPTER_MS = 10_000


@dataclass(frozen=True)
class Chapter:
    t_ms: int  # output time the chapter starts at (lead-in applied)
    base_ms: int  # output time of the event that made it (events group by this)
    title: str
    kind: str  # "anchor" | "gap" | "start"
    event_ids: tuple[str, ...] = ()


def _player_text(e: Event) -> str:
    return f" — {e.player} serving" if e.player else ""


def anchor_titles(events: Iterable[Event], analysis: Analysis | None = None) -> dict[str, str]:
    """Titles for every anchor event, numbered from the whole log (removed parts too).

    With a score ``analysis``, titles carry the score where it is certain: completed sets
    at a Set start ("Set 2 (6–4)"), games at a Game start ("Set 1 · Game 5 (3–1)", A–B),
    and "Tiebreak" / "Match tiebreak" in place of the game number.
    """
    views = {st.event.id: st.view for st in analysis.steps} if analysis else {}
    titles = {}
    play_started = False
    for e, pos in structure.walk(events):
        view = views.get(e.id)
        # Numbers from the score where it is certain and results were recorded; otherwise
        # (scoring off, or not certain) from counting Set/Game start events.
        sets = view.sets if view is not None else None
        set_no = len(sets) + 1 if sets else pos.set_no
        games = view.games if view is not None else None
        game_no = sum(games) + 1 if games not in (None, (0, 0)) else pos.game_no
        if e.type == catalog.SET_START:
            titles[e.id] = f"Set {set_no}" if set_no else "Set"
            if view is not None and view.sets:
                titles[e.id] += f" ({', '.join(set_text(s) for s in view.sets)})"
        elif e.type == catalog.GAME_START:
            parts = []
            if set_no:
                parts.append(f"Set {set_no}")
            if view is not None and view.in_tiebreak:
                parts.append("Match tiebreak" if games == (0, 0) else "Tiebreak")
            else:
                parts.append(f"Game {game_no}" if game_no else "Game")
            title = " · ".join(parts)
            if games is not None and games != (0, 0):
                title += f" ({games[0]}–{games[1]})"
            titles[e.id] = title + _player_text(e)
        elif e.type == catalog.SCORE_STATE and not play_started:
            score = structure.score_text(e)
            titles[e.id] = "Match in progress" + (f" ({score})" if score else "")
        play_started = play_started or e.type in (catalog.SET_START, catalog.GAME_START)
    return titles


def derive_chapters(events: Sequence[Event], remapped: Sequence[Remapped], settings: Settings,
                    total_out_ms: int, analysis: Analysis | None = None) -> list[Chapter]:
    """Chapters on the output timeline, before YouTube's rules are applied."""
    titles = anchor_titles(events, analysis)
    kept = sorted(remapped, key=lambda r: r.out_ms)
    anchors = [r for r in kept if r.event.type in catalog.CHAPTER_ANCHORS and r.event.id in titles]
    chapters = [Chapter(r.link_ms(settings.lead_in_for(r.event.type)), r.out_ms,
                        titles[r.event.id], "anchor", (r.event.id,)) for r in anchors]

    # Gap rule, measured on event times (not lead-in times) from the previous chapter.
    gap = settings.chapter_gap_ms
    bounds = [(0, None)] + [(r.out_ms, titles[r.event.id]) for r in anchors]
    for k, (t, current) in enumerate(bounds):
        limit = bounds[k + 1][0] if k + 1 < len(bounds) else total_out_ms + 1
        while limit - t > gap:
            nxt = next((r for r in kept if t + gap <= r.out_ms < limit), None)
            if nxt is None:
                break
            title = f"{current} (cont.)" if current else catalog.label(nxt.event.type)
            chapters.append(Chapter(nxt.link_ms(settings.lead_in_for(nxt.event.type)), nxt.out_ms,
                                    title, "gap", (nxt.event.id,)))
            t = nxt.out_ms
    return sorted(chapters, key=lambda c: (c.t_ms, c.base_ms))


def _merge_titles(a: str, b: str) -> str:
    if b.startswith(a):
        return b
    if a.startswith(b):
        return a
    return f"{a} / {b}"


def _merge(a: Chapter, b: Chapter) -> Chapter:
    """``b`` folded into ``a`` (keeps ``a``'s time)."""
    return dataclasses.replace(a, title=_merge_titles(a.title, b.title),
                               event_ids=a.event_ids + b.event_ids)


def youtube_chapters(chapters: Sequence[Chapter], total_out_ms: int) -> tuple[list[Chapter], list[Issue]]:
    """Apply YouTube's chapter rules on whole seconds.

    First chapter at 0:00 (an early first chapter is moved there, otherwise a "Start"
    chapter is added), chapters closer than 10 s are merged into the earlier one, a
    final chapter shorter than 10 s is merged into the previous one. Fewer than 3
    chapters means no chapters at all.
    """
    min_s = YOUTUBE_MIN_CHAPTER_MS // 1000
    chs = [dataclasses.replace(c, t_ms=c.t_ms // 1000 * 1000)
           for c in sorted(chapters, key=lambda c: (c.t_ms, c.base_ms))]
    if not chs or chs[0].t_ms >= YOUTUBE_MIN_CHAPTER_MS:
        chs.insert(0, Chapter(0, 0, "Start", "start"))
    else:
        chs[0] = dataclasses.replace(chs[0], t_ms=0, base_ms=0)

    out: list[Chapter] = []
    for c in chs:
        if out and (c.t_ms - out[-1].t_ms) // 1000 < min_s:
            out[-1] = _merge(out[-1], c)
        else:
            out.append(c)
    total_s = total_out_ms // 1000
    while len(out) > 1 and total_s - out[-1].t_ms // 1000 < min_s:
        last = out.pop()
        out[-1] = _merge(out[-1], last)

    if len(out) < YOUTUBE_MIN_CHAPTERS:
        return [], [Issue("too_few_chapters",
                          f"only {len(out)} chapter(s) after YouTube's rules; "
                          f"YouTube needs at least {YOUTUBE_MIN_CHAPTERS}, so none are exported",
                          severity="info")]
    return out, []


def chapter_for(chapters: Sequence[Chapter], out_ms: int) -> Chapter | None:
    """The chapter an event (by output time) belongs to."""
    candidates = [c for c in chapters if c.base_ms <= out_ms]
    return max(candidates, key=lambda c: c.base_ms) if candidates else None
