"""Match statistics from the event log, written as CSV (no GUI, no video).

    python -m tennis_to_utube.stats GX010008.match.json [-o stats.csv]

Uses the score engine, so servers, break points and inferred point winners are the same
as in the app. What can be counted depends on what was marked:

* points (+ who serves, from Game start): points won on serve/return, holds/breaks,
  break points; in tiebreaks, minibreaks (points won on return / lost on serve);
* serve marks (serve in / fault / ace): 1st-serve %, 1st/2nd-serve points won, aces,
  double faults (2nd-serve stats only count points with serve marks); serves before a
  Let point (the point is replayed) do not count;
* shot marks (on their own or on a Point): winners, forced and unforced errors (by the
  player who hit them).

Points whose winner is unknown are left out and counted on their own row.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import catalog, matchfile, names
from .flow import analyze_match
from .scoring import SHOT_TYPES, Analysis, ScoreView, other, point_server_side, shot_of

SERVE_MARKS = (catalog.SERVE_IN, catalog.FAULT, catalog.LET, catalog.ACE)


@dataclass
class PointRecord:
    t_ms: int
    set_no: int | None
    server: str | None  # side serving the point
    server_player: str | None
    winner: str | None  # None = unknown
    serve_marked: bool  # serve in / fault / let / ace marked in this point
    faults_before: int  # faults before the point-ending event
    ace: bool
    double_fault: bool
    shot: tuple[str, str | None] | None  # (type, hitter side)
    break_point: bool  # the receiver wins the game if they win this point
    tiebreak: bool = False  # played in a tiebreak (or match tiebreak)


@dataclass
class GameRecord:
    set_no: int | None
    server: str
    server_player: str | None
    winner: str


@dataclass
class Records:
    points: list[PointRecord] = field(default_factory=list)
    games: list[GameRecord] = field(default_factory=list)
    replays: list[int | None] = field(default_factory=list)  # set number of each Let point


def _set_no(v: ScoreView) -> int | None:
    return len(v.sets) + 1 if v.sets is not None else None


def _receiver_game_point(v: ScoreView, server: str) -> bool:
    """Would the receiver win the game by winning the next point? (not in tiebreaks)"""
    if v.points is None or v.in_tiebreak:
        return False
    ps, pr = (v.points[0], v.points[1]) if server == "A" else (v.points[1], v.points[0])
    pr += 1
    return pr >= 4 and (pr - ps >= 2 or not v.fmt.ad)


def records(mf: matchfile.MatchFile, analysis: Analysis) -> Records:
    out = Records()
    match = mf.match
    prev = analysis.initial
    serve_marked, faults = False, 0
    game_player: dict[str, str | None] = {}  # last Game start player per side
    for st in analysis.steps:
        e, v = st.event, st.view
        if e.type == catalog.GAME_START:
            side = names.side_of(match, e.player) or names.side_of(match, e.side)
            if side:
                game_player[side] = e.player
        if e.type == catalog.LET_POINT:  # replayed: the attempt's serves do not count
            out.replays.append(_set_no(prev))
            serve_marked, faults = False, 0
        if e.type in SERVE_MARKS:
            serve_marked = True
        ended = (e.type in (catalog.POINT, catalog.ACE) or e.type in SHOT_TYPES
                 or (e.type == catalog.FAULT and faults >= 1))
        if e.type == catalog.FAULT and not ended:
            faults += 1
        if ended:
            server = point_server_side(prev)
            if server is not None and match.get("kind") != "doubles":
                player = names.players(match, server)[0]
            elif server is not None and not prev.in_tiebreak:
                player = game_player.get(server)
            else:
                player = None
            shot = shot_of(e, match)
            out.points.append(PointRecord(
                t_ms=e.t_ms, set_no=_set_no(prev), server=server, server_player=player,
                winner=st.winner, serve_marked=serve_marked, faults_before=faults,
                ace=e.type == catalog.ACE, double_fault=e.type == catalog.FAULT, shot=shot,
                break_point=server is not None and _receiver_game_point(prev, server),
                tiebreak=prev.in_tiebreak is True))
            serve_marked, faults = False, 0
        # games: ended by a point, or recorded at game level (not tiebreaks)
        game_winner = None
        if ended and v.pending_game is not None:
            game_winner = v.pending_game
        elif e.type == catalog.GAME_END and prev.pending_game is None and st.winner:
            game_winner = st.winner
        if game_winner and prev.server and not prev.in_tiebreak:
            player = (names.players(match, prev.server)[0] if match.get("kind") != "doubles"
                      else game_player.get(prev.server))
            out.games.append(GameRecord(_set_no(prev), prev.server, player, game_winner))
        prev = v
    return out


# -- tables ----------------------------------------------------------------------------


def _ratio(made: int, of: int) -> str:
    return f"{made}/{of} ({round(100 * made / of)}%)" if of else "0/0"


def _side_rows(points: Sequence[PointRecord], games: Sequence[GameRecord],
               replays: int = 0) -> list[tuple[str, dict[str, str]]]:
    """(stat, {side: value}) for one section."""
    known = [p for p in points if p.winner is not None]
    rows: list[tuple[str, dict[str, str]]] = []

    def add(stat: str, fn) -> None:
        rows.append((stat, {side: fn(side) for side in "AB"}))

    add("Points won", lambda s: _ratio(sum(p.winner == s for p in known), len(known)))
    serving = lambda s: [p for p in known if p.server == s]  # noqa: E731
    returning = lambda s: [p for p in known if p.server == other(s)]  # noqa: E731
    add("Service points won", lambda s: _ratio(sum(p.winner == s for p in serving(s)), len(serving(s))))
    add("Return points won", lambda s: _ratio(sum(p.winner == s for p in returning(s)), len(returning(s))))
    marked = lambda s: [p for p in serving(s) if p.serve_marked]  # noqa: E731
    first_in = lambda s: [p for p in marked(s) if p.faults_before == 0 and not p.double_fault]  # noqa: E731
    second = lambda s: [p for p in marked(s) if p.faults_before >= 1 or p.double_fault]  # noqa: E731
    add("1st serve in", lambda s: _ratio(len(first_in(s)), len(marked(s))))
    add("1st serve points won", lambda s: _ratio(sum(p.winner == s for p in first_in(s)), len(first_in(s))))
    add("2nd serve points won", lambda s: _ratio(sum(p.winner == s for p in second(s)), len(second(s))))
    add("Aces", lambda s: str(sum(p.ace for p in serving(s))))
    add("Double faults", lambda s: str(sum(p.double_fault for p in serving(s))))
    bp = lambda s: [p for p in returning(s) if p.break_point]  # noqa: E731
    add("Break points won", lambda s: _ratio(sum(p.winner == s for p in bp(s)), len(bp(s))))
    faced = lambda s: [p for p in serving(s) if p.break_point]  # noqa: E731
    add("Break points saved", lambda s: _ratio(sum(p.winner == s for p in faced(s)), len(faced(s))))
    tb = [p for p in known if p.tiebreak]
    add("Tiebreak points won", lambda s: _ratio(sum(p.winner == s for p in tb), len(tb)))
    tb_return = lambda s: [p for p in tb if p.server == other(s)]  # noqa: E731
    tb_serve = lambda s: [p for p in tb if p.server == s]  # noqa: E731
    add("Minibreaks won", lambda s: _ratio(sum(p.winner == s for p in tb_return(s)), len(tb_return(s))))
    add("Minibreaks lost", lambda s: _ratio(sum(p.winner != s for p in tb_serve(s)), len(tb_serve(s))))
    held = lambda s: [g for g in games if g.server == s]  # noqa: E731
    add("Service games won", lambda s: _ratio(sum(g.winner == s for g in held(s)), len(held(s))))
    add("Return games won", lambda s: str(sum(g.winner == s for g in games if g.server == other(s))))
    for shot_type, label in ((catalog.WINNER, "Winners"), (catalog.FORCED_ERROR, "Forced errors"),
                             (catalog.UNFORCED_ERROR, "Unforced errors")):
        add(label, lambda s, t=shot_type: str(sum(p.shot == (t, s) for p in points)))
    unknown = sum(p.winner is None for p in points)
    rows.append(("Points with unknown winner (not counted)", {"A": str(unknown), "B": ""}))
    rows.append(("Points replayed (let point; serves not counted)", {"A": str(replays), "B": ""}))
    return rows


def stats_csv(mf: matchfile.MatchFile, analysis: Analysis | None = None) -> str:
    analysis = analysis or analyze_match(mf)
    rec = records(mf, analysis)
    match = mf.match
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Section", "Stat", names.side_name(match, "A"), names.side_name(match, "B")])
    sets = sorted({p.set_no for p in rec.points if p.set_no} | {g.set_no for g in rec.games if g.set_no})
    sections = [("Match", rec.points, rec.games, len(rec.replays))] + [
        (f"Set {n}", [p for p in rec.points if p.set_no == n], [g for g in rec.games if g.set_no == n],
         rec.replays.count(n))
        for n in sets]
    for title, points, games, replays in sections:
        for stat, values in _side_rows(points, games, replays):
            w.writerow([title, stat, values["A"], values["B"]])
    # per server (useful in doubles; one row per player)
    by_player: dict[str, list[PointRecord]] = defaultdict(list)
    for p in rec.points:
        if p.server_player and p.winner is not None:
            by_player[p.server_player].append(p)
    if match.get("kind") == "doubles" and by_player:
        w.writerow([])
        w.writerow(["By server", "Player", "Service points won", "1st serve in", "Aces", "Double faults"])
        for side in "AB":
            for player in names.players(match, side):
                ps = by_player.get(player, [])
                marked = [p for p in ps if p.serve_marked]
                first = [p for p in marked if p.faults_before == 0 and not p.double_fault]
                w.writerow(["By server", player, _ratio(sum(p.winner == side for p in ps), len(ps)),
                            _ratio(len(first), len(marked)), sum(p.ace for p in ps),
                            sum(p.double_fault for p in ps)])
    return buf.getvalue()


def default_output(match_path: Path) -> Path:
    stem = match_path.name.removesuffix(".match.json")
    return match_path.with_name(f"{stem} stats.csv")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tennis_to_utube.stats",
                                     description="Write match statistics as CSV.")
    parser.add_argument("match", type=Path, help="the .match.json file")
    parser.add_argument("-o", "--output", type=Path, help="CSV file (default: '<match> stats.csv')")
    args = parser.parse_args(argv)
    try:
        mf = matchfile.load(args.match)
    except (OSError, matchfile.MatchFileError) as exc:
        print(f"Cannot read {args.match}: {exc}", file=sys.stderr)
        return 1
    out = args.output or default_output(args.match)
    out.write_text(stats_csv(mf), encoding="utf-8-sig", newline="")  # BOM: Excel reads UTF-8
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
