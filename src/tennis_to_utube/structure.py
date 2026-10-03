"""Set/game position from structure events alone (no scoring).

Used for changeover parity and chapter titles. A Set score (``score_state``) event,
allowed at any time, sets the position from the parts it gives (``details.sets``,
``details.games``); parts it leaves out keep their computed value, or become unknown if
nothing was being tracked (e.g. the video starts mid-match). The score engine (build
step 5) will take over the full state: points, server, format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from . import catalog
from .matchfile import Event


@dataclass(frozen=True)
class Position:
    set_no: int | None  # current set, 1-based; None = unknown; 0 = before the first set
    games_closed: int | None  # games finished in the current set; None = unknown
    game_no: int | None = None  # number of the current (or last started) game in the set


def _pair(value: Any) -> tuple[int, int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)):
        return value[0], value[1]
    return None


def score_parts(e: Event) -> tuple[list[tuple[int, int]] | None, tuple[int, int] | None]:
    """(completed sets, current-set games) from a Set score event; None = not given/invalid."""
    sets = e.details.get("sets")
    sets = [_pair(s) for s in sets] if isinstance(sets, list) else None
    if sets is not None and any(s is None for s in sets):
        sets = None
    return sets, _pair(e.details.get("games"))


def score_text(e: Event) -> str:
    """Known parts of a Set score in tennis notation, e.g. "6–4, 3–2" ("" if none)."""
    sets, current = score_parts(e)
    parts = [f"{a}–{b}" for a, b in (sets or [])]
    if current is not None:
        parts.append(f"{current[0]}–{current[1]}")
    return ", ".join(parts)


def walk(events: Iterable[Event]) -> list[tuple[Event, Position]]:
    """Events in time order, each with the position *after* it is applied.

    A set begins at a Set start, or at the first Game start after a set ended (or at
    the very first Game start).
    """
    out = []
    set_no: int | None = 0
    games: int | None = 0
    started: int | None = 0
    in_set = False
    for e in sorted(events, key=lambda e: e.t_ms):
        if e.type == catalog.SET_START or (e.type == catalog.GAME_START and not in_set):
            set_no = None if set_no is None else set_no + 1
            games, started, in_set = 0, 0, True
        elif e.type == catalog.SCORE_STATE:
            tracking = in_set and set_no not in (None, 0)
            sets, current = score_parts(e)
            if sets is not None:
                set_no = len(sets) + 1
            elif not tracking:
                set_no = None
            if current is not None:
                # Taken as between games: the next Game start is game sum + 1.
                games = started = sum(current)
            elif not tracking:
                games = started = None
            in_set = True
        elif e.type == catalog.GAME_END:
            games = None if games is None else games + 1
        elif e.type == catalog.SET_END:
            in_set = False
        if e.type == catalog.GAME_START and started is not None:
            started += 1
        out.append((e, Position(set_no, games, started)))
    return out
