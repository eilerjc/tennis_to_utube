"""Score engine (DESIGN.md §7).

The score is computed by replaying the event log; nothing about it is stored. Scoring
may be recorded at point, game or set level and the level may change during a match.

Unknowns are handled by tracking the *set of possible states* through the log: an event
with an unknown result branches, and events with a known result (Game end A, a Set score
checkpoint, ...) keep only the states that fit. A backward pass then keeps only the paths
that reach the end, which gives, for every event, the results consistent with the whole
log: exactly one → inferred; more than one → uncertain (listed for video review); none →
an issue, after which the event is applied anyway (the log is never rejected).

Points are counts (2–1 = 30–15). The engine works with sides; player names (servers) are
looked up in the match to find their side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Any, Iterable, Mapping, Sequence

from . import catalog, names
from .issues import Issue
from .matchfile import Event

SIDES = ("A", "B")


def other(side: str) -> str:
    return "B" if side == "A" else "A"


# -- formats ---------------------------------------------------------------------


@dataclass(frozen=True)
class Format:
    best_of: int = 3  # sets
    games: int = 6  # games to win a set (by 2)
    tiebreak_at: int | None = 6  # tiebreak at this many games all; None = advantage sets
    tiebreak_points: int = 7
    ad: bool = True  # False: no-ad, deciding point at deuce
    final_set: str = "match_tiebreak"  # "set" | "match_tiebreak" (in place of the final set)
    match_tiebreak_points: int = 10
    # Ends are changed in tiebreaks after every 6 points ("regular"), or after the first
    # point and then every 4 ("coman", used in doubles).
    tiebreak_changeovers: str = "regular"


def tiebreak_changeover_after(points_played: int, kind: str) -> bool:
    """True if players change ends after this many tiebreak points."""
    if kind == "coman":
        return points_played == 1 or (points_played >= 5 and (points_played - 1) % 4 == 0)
    return points_played > 0 and points_played % 6 == 0


PRESETS: dict[str, Format] = {
    "standard": Format(final_set="set"),
    "standard_mtb": Format(),  # best of 3, 10-point match tiebreak instead of a third set
    "best_of_5": Format(best_of=5, final_set="set"),
    "pro_set": Format(best_of=1, games=8, tiebreak_at=8, final_set="set"),
    "pro10": Format(best_of=1, games=10, tiebreak_at=10, final_set="set"),
    "short_sets": Format(games=4, tiebreak_at=4),  # to 4, tiebreak at 4-4, match tiebreak
}
DEFAULT_PRESET = "standard_mtb"
PRESET_LABELS = {
    "standard_mtb": "Best of 3, match tiebreak (10) for the 3rd set",
    "standard": "Best of 3 sets",
    "best_of_5": "Best of 5 sets",
    "pro_set": "Pro set (8 games)",
    "pro10": "Pro set (10 games)",
    "short_sets": "Short sets (to 4), match tiebreak (10)",
}


def patch_format(fmt: Format, patch: Mapping[str, Any]) -> tuple[Format, list[str]]:
    """Apply ``{"preset": ..., field: value}``; invalid parts are skipped and reported."""
    problems: list[str] = []
    if not isinstance(patch, Mapping):
        return fmt, [f"format should be an object, got {patch!r}"]
    if "preset" in patch:
        if patch["preset"] in PRESETS:
            fmt = PRESETS[patch["preset"]]
        else:
            problems.append(f"unknown format preset {patch['preset']!r}")
    known = {f.name for f in fields(Format)}
    changes: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "preset":
            continue
        if key not in known:
            problems.append(f"unknown format setting {key!r}")
            continue
        ok = {
            "best_of": _pos_int(value) and value % 2 == 1,
            "games": _pos_int(value),
            "tiebreak_at": value is None or _pos_int(value),
            "tiebreak_points": _pos_int(value),
            "ad": isinstance(value, bool),
            "final_set": value in ("set", "match_tiebreak"),
            "match_tiebreak_points": _pos_int(value),
            "tiebreak_changeovers": value in ("regular", "coman"),
        }[key]
        if ok:
            changes[key] = value
        else:
            problems.append(f"invalid format setting {key} = {value!r}")
    return replace(fmt, **changes), problems


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def resolve_format(spec: Mapping[str, Any] | None, default_preset: str = DEFAULT_PRESET
                   ) -> tuple[Format, list[str]]:
    base = PRESETS.get(default_preset, PRESETS[DEFAULT_PRESET])
    if spec is None:
        return base, []
    return patch_format(base, spec)


# -- state -----------------------------------------------------------------------


@dataclass(frozen=True)
class SetScore:
    winner: str | None
    games: tuple[int, int] | None  # None: set recorded at set level without the score
    tiebreak: tuple[int, int] | None = None  # points of the deciding (match) tiebreak


@dataclass(frozen=True)
class State:
    """One possible score. ``None`` parts are unknown (e.g. the video starts mid-match)."""

    fmt: Format
    sets: tuple[SetScore, ...] | None = ()  # completed sets
    games: tuple[int, int] | None = (0, 0)  # current set
    points: tuple[int, int] | None = (0, 0)  # current game, as counts
    server: str | None = None  # side serving the current game
    pending_game: str | None = None  # a game just ended (by points), not yet marked
    pending_set: str | None = None  # a set just ended, not yet marked
    tiebreak_now: bool = False  # a Tiebreak start was marked (at any level score, e.g. 7-7)
    winner: str | None = None  # match winner

    @property
    def sets_won(self) -> tuple[int, int] | None:
        if self.sets is None or any(s.winner is None for s in self.sets):
            return None
        return (sum(s.winner == "A" for s in self.sets), sum(s.winner == "B" for s in self.sets))

    @property
    def set_no(self) -> int | None:
        return None if self.sets is None else len(self.sets) + 1

    @property
    def in_match_tiebreak(self) -> bool:
        won = self.sets_won
        need = self.fmt.best_of // 2
        return self.fmt.final_set == "match_tiebreak" and won == (need, need)

    @property
    def in_tiebreak(self) -> bool:
        if self.tiebreak_now or self.in_match_tiebreak:
            return True
        t = self.fmt.tiebreak_at
        return t is not None and self.games == (t, t)

    def point_server(self) -> str | None:
        """Side serving the next point (rotates every two points in a tiebreak)."""
        if self.server is None:
            return None
        if self.in_tiebreak:
            if self.points is None:
                return None
            k = sum(self.points)
            return self.server if ((k + 1) // 2) % 2 == 0 else other(self.server)
        return self.server


def _bump(pair: tuple[int, int], side: str) -> tuple[int, int]:
    return (pair[0] + (side == "A"), pair[1] + (side == "B"))


def win_point(s: State, side: str) -> State:
    s = replace(s, pending_game=None, pending_set=None)
    if s.points is None:
        return s  # mid-game with an unknown score: the point cannot be placed
    pa, pb = _bump(s.points, side)
    lead = abs(pa - pb)
    if s.in_tiebreak:
        target = s.fmt.match_tiebreak_points if s.in_match_tiebreak else s.fmt.tiebreak_points
        done = max(pa, pb) >= target and lead >= 2
    elif s.fmt.ad:
        done = max(pa, pb) >= 4 and lead >= 2
    else:
        done = max(pa, pb) >= 4
    if done:
        return win_game(s, side, tiebreak=(pa, pb) if s.in_tiebreak else None)
    return replace(s, points=(pa, pb))


def win_game(s: State, side: str, tiebreak: tuple[int, int] | None = None) -> State:
    was_tiebreak = s.in_tiebreak
    base = replace(s, points=(0, 0), server=other(s.server) if s.server else None,
                   pending_game=side, pending_set=None)
    if s.in_match_tiebreak:
        return win_set(base, SetScore(side, _bump((0, 0), side), tiebreak))
    if s.games is None:
        return base
    ga, gb = _bump(s.games, side)
    if was_tiebreak or (max(ga, gb) >= s.fmt.games and abs(ga - gb) >= 2):
        return win_set(base, SetScore(side, (ga, gb), tiebreak))
    return replace(base, games=(ga, gb))


def win_set(s: State, result: SetScore) -> State:
    sets = s.sets + (result,) if s.sets is not None else None
    ns = replace(s, sets=sets, games=(0, 0), points=(0, 0), pending_set=result.winner,
                 tiebreak_now=False)
    won = ns.sets_won
    if won is not None and result.winner is not None:
        if won[SIDES.index(result.winner)] > s.fmt.best_of // 2:
            ns = replace(ns, winner=result.winner)
    return ns


def _with_sets(s: State, sets: tuple[SetScore, ...] | None) -> State:
    ns = replace(s, sets=sets, winner=None)
    won = ns.sets_won
    if won is not None:
        for i, side in enumerate(SIDES):
            if won[i] > s.fmt.best_of // 2:
                ns = replace(ns, winner=side)
    return ns


# -- reading events ------------------------------------------------------------------


def _pair(value: Any) -> tuple[int, int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)):
        return value[0], value[1]
    return None


@dataclass(frozen=True)
class Checkpoint:
    """Known parts of a Set score (``score_state``) event."""

    sets: tuple[SetScore, ...] | None
    games: tuple[int, int] | None
    points: tuple[int, int] | None
    server: str | None
    problems: tuple[str, ...]


def read_checkpoint(e: Event, match: dict[str, Any] | None = None) -> Checkpoint:
    d = e.details
    problems = []
    sets = None
    if "sets" in d:
        raw = d["sets"] if isinstance(d["sets"], list) else None
        entries = [set_entry(x) for x in raw] if raw is not None else None
        if entries is None or any(s is None for s in entries):
            problems.append(f"invalid sets {d['sets']!r}")
        else:
            sets = tuple(entries)
    games = _pair(d.get("games"))
    if "games" in d and games is None:
        problems.append(f"invalid games {d['games']!r}")
    points = _pair(d.get("points"))
    if "points" in d and points is None:
        problems.append(f"invalid points {d['points']!r}")
    server = names.side_of(match, d.get("server")) if "server" in d else None
    if "server" in d and server is None:
        problems.append(f"invalid server {d['server']!r}")
    return Checkpoint(sets, games, points, server, tuple(problems))


def _sets_match(given: tuple[SetScore, ...], known: tuple[SetScore, ...]) -> bool:
    return len(given) == len(known) and all(
        k.games is None or k.games == g.games for g, k in zip(given, known))


def _fits(s: State, cp: Checkpoint) -> bool:
    if cp.sets is not None and s.sets is not None and not _sets_match(cp.sets, s.sets):
        return False
    if cp.games is not None and s.games is not None and s.games != cp.games:
        return False
    if cp.points is not None and s.points is not None and s.points != cp.points:
        return False
    return not (cp.server is not None and s.server is not None and s.server != cp.server)


def _apply_checkpoint(s: State, cp: Checkpoint, tracking: bool) -> State:
    """Given parts replace the state's; others are kept, or unknown before any scoring."""
    if cp.sets is not None:
        keep_detail = s.sets is not None and _sets_match(cp.sets, s.sets)
        sets = s.sets if keep_detail else cp.sets
    else:
        sets = s.sets if tracking else None
    games = cp.games if cp.games is not None else (s.games if tracking else None)
    if cp.points is not None:
        points = cp.points
    elif tracking and (cp.games is None or cp.games == s.games):
        points = s.points
    else:
        points = (0, 0) if cp.games is not None else None  # games given: between games
    server = cp.server if cp.server is not None else s.server
    return replace(_with_sets(s, sets), games=games, points=points, server=server)


# -- the engine ------------------------------------------------------------------------

# How an event affects the score (decided by a plain pass over the log, see _classify).
POINT_END = "point_end"  # Point, Ace, or the second Fault of a point
OUTCOME_TYPES = (catalog.POINT, catalog.GAME_END, catalog.SET_END)


@dataclass(frozen=True)
class _Info:
    kind: str | None  # POINT_END or None
    tracking: bool  # a scoring event came before this one
    side: str | None  # side of the event's player (or its side field)
    checkpoint: Checkpoint | None  # for Set score events


def _classify(events: Sequence[Event], match: dict[str, Any] | None) -> list[_Info]:
    out, faults, tracking = [], 0, False
    for e in events:
        kind = None
        if e.type in (catalog.POINT, catalog.ACE):
            kind = POINT_END
        elif e.type == catalog.FAULT:
            faults += 1
            kind = POINT_END if faults >= 2 else None
        side = names.side_of(match, e.player) or names.side_of(match, e.side)
        cp = read_checkpoint(e, match) if e.type == catalog.SCORE_STATE else None
        out.append(_Info(kind, tracking, side, cp))
        if kind == POINT_END:
            faults = 0
        if kind == POINT_END or e.type in (catalog.GAME_END, catalog.SET_END):
            tracking = True
    return out


def _entered(e: Event) -> str | None:
    return e.result if e.result in SIDES else None


def _moves(s: State, e: Event, info: _Info, forced: bool) -> list[tuple[State, str | None]]:
    """Next states with the winner chosen for this event (None if it has no winner)."""
    t = e.type
    if s.winner and (info.kind == POINT_END
                     or (t == catalog.GAME_END and s.pending_game is None)
                     or (t == catalog.SET_END and s.pending_set is None)):
        # Play after the match was won does not fit; if nothing else fits, it is ignored.
        return [(s, None)] if forced else []
    if info.kind == POINT_END:
        if t == catalog.POINT:
            known = _entered(e)
        else:
            srv = s.point_server()
            known = (srv if t == catalog.ACE else other(srv)) if srv else None
        sides = [known] if known else list(SIDES)
        return [(win_point(s, x), x) for x in sides]

    if t == catalog.GAME_END:
        out = []
        for x in [_entered(e)] if _entered(e) else SIDES:
            if forced:
                ns = win_game(replace(s, pending_game=None), x)
                out.append((replace(ns, pending_game=None), x))
            elif s.pending_game is not None:
                if s.pending_game == x:
                    out.append((replace(s, pending_game=None), x))
            elif s.points in ((0, 0), None):  # game-level scoring
                out.append((replace(win_game(s, x), pending_game=None), x))
        return out

    if t == catalog.SET_END:
        games = _pair(e.details.get("games"))
        out = []
        for x in [_entered(e)] if _entered(e) else SIDES:
            if forced:
                out.append((replace(win_set(s, SetScore(x, s.games)), pending_set=None,
                                    pending_game=None), x))
            elif s.pending_set is not None:
                if s.pending_set == x:
                    out.append((replace(s, pending_set=None, pending_game=None), x))
            elif (s.pending_game is None and s.games in ((0, 0), None)
                  and s.points in ((0, 0), None)):  # set-level scoring
                server = s.server
                if server is not None:
                    server = (server if games is not None and sum(games) % 2 == 0
                              else other(server) if games is not None else None)
                ns = win_set(replace(s, server=server), SetScore(x, games))
                out.append((replace(ns, pending_set=None), x))
        return out

    if t == catalog.SCORE_STATE:
        cp = info.checkpoint
        if not forced and info.tracking and not _fits(s, cp):
            return []  # (before any scoring there is nothing for it to contradict)
        return [(_apply_checkpoint(s, cp, info.tracking), None)]

    if t == catalog.RULES_CHANGE:
        fmt, _ = patch_format(s.fmt, e.details.get("format", {}))
        return [(replace(s, fmt=fmt), None)]

    if t == catalog.GAME_START:
        return [(replace(s, server=info.side or s.server, pending_game=None, pending_set=None),
                 None)]

    if t == catalog.TIEBREAK_START:
        # The next game is a tiebreak, at whatever score it is marked (e.g. 7-7 in a pro
        # set); it needs level games (not 0-0: a match tiebreak is set by the format) and
        # no game in progress.
        level = s.games is None or (s.games[0] == s.games[1] and s.games[0] > 0)
        if not forced and (s.winner or not level or s.points not in ((0, 0), None)):
            return []
        points = (0, 0) if s.points is not None else None
        return [(replace(s, tiebreak_now=True, points=points), None)]

    if t == catalog.SET_START:
        return [(replace(s, pending_game=None, pending_set=None), None)]

    return [(s, None)]


def _irregularity(s: State, e: Event, info: _Info) -> int:
    """How unusual this event is in state ``s`` (paths with the lowest total are kept).

    A game or set that ended on points but whose end was not marked before play went on,
    and a Game start in the middle of a game, each count 1. This keeps unknown results
    from being read in improbable ways, e.g. one long deuce game split into two games.
    """
    unmarked_end = s.pending_game is not None or s.pending_set is not None
    if info.kind == POINT_END or e.type == catalog.SET_START:
        return int(unmarked_end)
    if e.type == catalog.GAME_START:
        return int(unmarked_end or s.points not in ((0, 0), None))
    return 0


def _step(s: State, e: Event, info: _Info, forced: bool) -> list[tuple[State, str | None, int]]:
    cost = _irregularity(s, e, info)
    return [(ns, choice, cost) for ns, choice in _moves(s, e, info, forced)]


@dataclass(frozen=True)
class ScoreView:
    """What is certain about the score: parts that differ between possible states are None."""

    sets: tuple[SetScore, ...] | None
    games: tuple[int, int] | None
    points: tuple[int, int] | None
    server: str | None
    in_tiebreak: bool | None
    pending_set: str | None
    winner: str | None
    certain: bool  # exactly one possible state
    fmt: Format
    pending_game: str | None = None  # a game just ended on points (Game end not marked yet)


def view_of(states: Iterable[State]) -> ScoreView:
    states = list(states)

    def common(get):
        values = {get(s) for s in states}
        return values.pop() if len(values) == 1 else None

    sets = common(lambda s: s.sets)
    if sets is None and states and all(s.sets is not None for s in states):
        # Same set scores but different tiebreak details are still the same sets.
        sets = common(lambda s: tuple((x.winner, x.games) for x in s.sets))
        sets = tuple(SetScore(w, g) for w, g in sets) if sets is not None else None
    return ScoreView(
        sets=sets, games=common(lambda s: s.games), points=common(lambda s: s.points),
        server=common(lambda s: s.server), in_tiebreak=common(lambda s: s.in_tiebreak),
        pending_set=common(lambda s: s.pending_set), winner=common(lambda s: s.winner),
        certain=len(states) == 1, fmt=states[0].fmt if states else Format(),
        pending_game=common(lambda s: s.pending_game))


@dataclass(frozen=True)
class Step:
    event: Event
    view: ScoreView  # score after the event (using the whole log)
    states: frozenset[State]
    winner: str | None  # point/game/set winner, if the event has one and it is certain
    entered: bool  # the winner was entered by the user
    uncertain: bool  # more than one winner fits the log

    @property
    def inferred(self) -> bool:
        return self.winner is not None and not self.entered


@dataclass
class Analysis:
    initial: ScoreView
    steps: list[Step]
    issues: list[Issue]

    def step_for(self, event_id: str) -> Step:
        return next(s for s in self.steps if s.event.id == event_id)

    def view_at(self, t_ms: int) -> ScoreView:
        """Score after every event at or before ``t_ms`` (the playhead)."""
        view = self.initial
        for st in self.steps:
            if st.event.t_ms > t_ms:
                break
            view = st.view
        return view


MAX_STATES = 5000  # more possible scores than this: give up tracking (score becomes unknown)


def analyze(events: Iterable[Event], fmt: Format, duplicate_window_ms: int = 5000,
            match: dict[str, Any] | None = None) -> Analysis:
    """Score analysis of the log. ``match`` maps player names to sides."""
    evs = sorted(events, key=lambda e: e.t_ms)
    infos = _classify(evs, match)
    issues: list[Issue] = []
    initial = State(fmt)
    states: set[State] = {initial}
    cost: dict[State, int] = {initial: 0}
    layers: list[dict[State, set[tuple[State, str | None]]]] = []

    for e, info in zip(evs, infos):
        issues += _pre_issues(e, info, states, match)
        trans, best = _advance(states, cost, e, info, forced=False)
        if not trans:
            if all(s.winner for s in states):
                issues.append(Issue("after_match_end", f"{catalog.label(e.type)} after the match "
                                    "was won; ignored", e.t_ms, e.id))
            else:
                issues.append(_conflict_issue(e))
            trans, best = _advance(states, cost, e, info, forced=True)
        if len(trans) > MAX_STATES:
            issues.append(Issue("too_many_unknowns", "Too many unknown results to track the score; "
                                "add a Set score to continue", e.t_ms, e.id))
            unknown = State(next(iter(trans)).fmt, sets=None, games=None, points=None)
            trans, best = {unknown: {(p, None) for p in states}}, {unknown: min(cost.values())}
        layers.append(trans)
        states, cost = set(trans), best

    # Backward pass: keep only the most regular paths that reach the end of the log.
    lowest = min(cost.values())
    alive = {st for st in states if cost[st] == lowest}
    steps_rev = []
    for e, trans in zip(reversed(evs), reversed(layers)):
        choices = {c for ns in alive for _, c in trans[ns]}
        winners = {c for c in choices if c is not None}
        entered = e.type in OUTCOME_TYPES and _entered(e) is not None
        steps_rev.append(Step(e, view_of(alive), frozenset(alive),
                              winners.pop() if len(winners) == 1 else None, entered,
                              len(winners) > 1))
        alive = {p for ns in alive for p, _ in trans[ns]}
    steps = list(reversed(steps_rev))

    for st, info in zip(steps, infos):
        if st.uncertain:
            what = "point" if info.kind == POINT_END else catalog.label(st.event.type).lower()
            issues.append(Issue("uncertain_result", f"Winner of this {what} cannot be worked out; "
                                "check the video", st.event.t_ms, st.event.id, "info"))
    issues += _duplicate_points(evs, infos, duplicate_window_ms)
    issues.sort(key=lambda i: (i.t_ms if i.t_ms is not None else -1))
    return Analysis(view_of([initial]), steps, issues)


def _advance(states, cost, e, info, forced):
    """One layer of the search; per next state, keep only its lowest-cost predecessors."""
    trans: dict[State, set[tuple[State, str | None]]] = {}
    best: dict[State, int] = {}
    for s in states:
        for ns, choice, c in _step(s, e, info, forced):
            total = cost[s] + c
            if ns not in best or total < best[ns]:
                best[ns], trans[ns] = total, {(s, choice)}
            elif total == best[ns]:
                trans[ns].add((s, choice))
    return trans, best


def _conflict_issue(e: Event) -> Issue:
    msg = {
        catalog.GAME_END: "Game end does not fit the recorded points (missing or extra point?)",
        catalog.SET_END: "Set end does not fit the recorded games (missing or extra game?)",
        catalog.SCORE_STATE: "Set score differs from what the earlier events add up to",
        catalog.TIEBREAK_START: "Tiebreak start needs level games (e.g. 6-6) and no game in progress",
    }.get(e.type, f"{catalog.label(e.type)} does not fit the score")
    return Issue("score_conflict", msg + "; applied as entered", e.t_ms, e.id, "warning")


def _pre_issues(e: Event, info: _Info, states: set[State], match: dict[str, Any] | None
                ) -> list[Issue]:
    """Validation that needs the possible states before an event."""
    out = []
    t = e.type
    if t == catalog.GAME_START:
        if all(s.points not in ((0, 0), None) and s.pending_game is None for s in states):
            out.append(Issue("game_start_mid_game", "Game start before the previous game finished",
                             e.t_ms, e.id))
        side = info.side
        if side and all(s.server == other(side) for s in states):
            out.append(Issue("server_out_of_turn", "Server differs from the expected rotation",
                             e.t_ms, e.id, "info"))
    if t == catalog.SCORE_STATE:
        for p in info.checkpoint.problems:
            out.append(Issue("invalid_score_state", f"Set score: {p}; ignored", e.t_ms, e.id))
    if t == catalog.RULES_CHANGE:
        for p in patch_format(Format(), e.details.get("format", {}))[1]:
            out.append(Issue("invalid_rules_change", f"Rules change: {p}", e.t_ms, e.id))
    if match is not None and e.player is not None and names.side_of(match, e.player) is None:
        out.append(Issue("unknown_player", f"{e.player!r} is not a player in this match",
                         e.t_ms, e.id))
    return out


def _duplicate_points(evs: Sequence[Event], infos: Sequence[_Info], window_ms: int) -> list[Issue]:
    """A Point shortly after a point ended by a serve (ace or double fault), no serve between."""
    out, serve_end = [], None
    for e, info in zip(evs, infos):
        if e.type in (catalog.SERVE_IN, catalog.LET) or (e.type == catalog.FAULT
                                                          and info.kind is None):
            serve_end = None
        elif info.kind == POINT_END and e.type != catalog.POINT:
            serve_end = e.t_ms
        elif e.type == catalog.POINT:
            if serve_end is not None and e.t_ms - serve_end <= window_ms:
                out.append(Issue("possible_duplicate_point",
                                 "Point marked right after a point ended by the serve "
                                 "(ace or double fault); delete it if it is the same point",
                                 e.t_ms, e.id))
            serve_end = None
    return out


# -- display ---------------------------------------------------------------------------

_POINT_NAMES = ("0", "15", "30", "40")


def points_text(view: ScoreView) -> str | None:
    """"30–15", "40–40", "AD–40", or "5–3" in a tiebreak; None if unknown."""
    if view.points is None:
        return None
    a, b = view.points
    if view.in_tiebreak:
        return f"{a}–{b}"
    if a >= 3 and b >= 3:
        if a == b or not view.fmt.ad:
            return "40–40"
        return "AD–40" if a > b else "40–AD"
    return f"{_POINT_NAMES[min(a, 3)]}–{_POINT_NAMES[min(b, 3)]}"


_POINT_WORDS = {"0": 0, "love": 0, "15": 1, "30": 2, "40": 3}


def parse_pair(text: str) -> tuple[int, int] | None:
    """ "3-2", "3–2", "3:2", "3 2" → (3, 2); None if not two whole numbers."""
    parts = text.replace("–", "-").replace(":", "-").replace(" ", "-").split("-")
    parts = [p for p in parts if p]
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return int(parts[0]), int(parts[1])
    return None


def parse_sets(text: str) -> list[tuple[int, ...]] | None:
    """Set scores as typed, tiebreak points kept: "6-4 3-6" → [(6, 4), (3, 6)];
    "7-6(5)" → (7, 6, 7, 5); a match tiebreak "[10-8]" → (1, 0, 10, 8).

    Entries are (games A, games B) or (games A, games B, tiebreak A, tiebreak B), as stored in
    Set score / Ending state ``details.sets``. "(5)" is the loser's tiebreak points; the
    winner's are 7, or 2 more in an extended tiebreak. Empty → []; not understood → None.
    """
    out: list[tuple[int, ...]] = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        m = re.fullmatch(r"(.+?)\((\d+)\)", token)
        loser_tb = int(m.group(2)) if m else None
        token = m.group(1) if m else token
        bracket = token.startswith("[") and token.endswith("]")
        pair = parse_pair(token.strip("[]"))
        if pair is None or pair[0] == pair[1]:
            return None
        a_won = pair[0] > pair[1]
        if bracket:
            out.append(((1, 0) if a_won else (0, 1)) + pair)
        elif loser_tb is not None:
            winner_tb = max(7, loser_tb + 2)
            out.append(pair + ((winner_tb, loser_tb) if a_won else (loser_tb, winner_tb)))
        else:
            out.append(pair)
    return out


def set_entry(value: Any) -> SetScore | None:
    """A stored set: [games A, games B] or [games A, games B, tiebreak A, tiebreak B]."""
    if not isinstance(value, list) or len(value) not in (2, 4):
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value):
        return None
    a, b = value[0], value[1]
    if a == b:
        return None
    tiebreak = (value[2], value[3]) if len(value) == 4 else None
    return SetScore("A" if a > b else "B", (a, b), tiebreak)


def parse_points(text: str, tiebreak: bool = False) -> tuple[int, int] | None:
    """Score as typed → point counts: "30-40" → (2, 3), "AD-40" → (4, 3), "deuce" → (3, 3),
    "15-love" → (1, 0); in a tiebreak plain numbers: "5-3" → (5, 3). None if not understood.
    """
    t = text.strip().lower()
    if tiebreak:
        return parse_pair(t)
    if t in ("deuce", "40-40", "40–40"):
        return (3, 3)
    parts = [p for p in t.replace("–", "-").replace(":", "-").replace(" ", "-").split("-") if p]
    if len(parts) != 2:
        return None
    if "ad" in parts:
        if parts.count("ad") == 1 and "40" in parts:
            return (4, 3) if parts[0] == "ad" else (3, 4)
        return None
    if all(p in _POINT_WORDS for p in parts):
        return _POINT_WORDS[parts[0]], _POINT_WORDS[parts[1]]
    return None


def set_text(s: SetScore) -> str:
    if s.games is None:
        return f"set {s.winner or '?'}"
    a, b = s.games
    if s.tiebreak is not None and (a, b) in ((1, 0), (0, 1)):  # match tiebreak
        return f"[{s.tiebreak[0]}–{s.tiebreak[1]}]"
    text = f"{a}–{b}"
    if s.tiebreak is not None:
        text += f"({min(s.tiebreak)})"
    return text


def score_text(view: ScoreView) -> str:
    """E.g. "6–4, 7–6(5), 2–1, 30–15"; unknown parts shown as "?"."""
    parts = [set_text(s) for s in view.sets] if view.sets is not None else ["?"]
    if view.winner is None:
        parts.append(f"{view.games[0]}–{view.games[1]}" if view.games is not None else "?")
        parts.append(points_text(view) or "?")
    return ", ".join(parts)
