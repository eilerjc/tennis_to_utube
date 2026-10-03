"""Set/game position from structure events alone (no scoring).

Used for changeover parity and chapter titles. The flow/score engine (build step 5)
will supersede this with positions derived from the full state, including Starting
state checkpoints; until then a Starting state makes the position unknown until the
next set begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import catalog
from .matchfile import Event


@dataclass(frozen=True)
class Position:
    set_no: int | None  # current set, 1-based; None = unknown; 0 = before the first set
    games_closed: int | None  # Game won/lost events in the current set; None = unknown
    game_no: int | None = None  # Game start events in the current set (current game number)


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
        elif e.type == catalog.STARTING_STATE:
            set_no = games = started = None
            in_set = True
        elif e.type in catalog.GAME_END:
            games = None if games is None else games + 1
        elif e.type in catalog.SET_END:
            in_set = False
        if e.type == catalog.GAME_START and started is not None:
            started += 1
        out.append((e, Position(set_no, games, started)))
    return out
