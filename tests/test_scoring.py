import dataclasses
import random

import pytest

from tennis_to_utube import scoring
from tennis_to_utube.matchfile import Event
from tennis_to_utube.scoring import (
    PRESETS, Format, SetScore, analyze, patch_format, points_text, resolve_format, score_text,
)

STD = PRESETS["standard"]


class Log:
    def __init__(self):
        self.events, self.t = [], 0

    def __call__(self, type, **kw):
        self.t += 1000
        e = Event(f"e{len(self.events) + 1}", self.t, type, **kw)
        self.events.append(e)
        return e

    def points(self, seq):
        return [self("point", result={"a": "A", "b": "B", "?": "unknown"}[c]) for c in seq]


def final(events, fmt=STD):
    an = analyze(events, fmt)
    return an, an.steps[-1].view


# -- formats -------------------------------------------------------------------------


def test_presets_and_patches():
    assert PRESETS["standard_mtb"].final_set == "match_tiebreak"
    assert PRESETS["short_sets"] == Format(games=4, tiebreak_at=4)
    fmt, problems = resolve_format({"preset": "pro_set", "ad": False})
    assert (fmt.best_of, fmt.games, fmt.tiebreak_at, fmt.ad) == (1, 8, 8, False) and problems == []
    fmt, problems = patch_format(STD, {"best_of": 4, "games": "six", "colour": "red", "preset": "x"})
    assert fmt == STD and len(problems) == 4
    assert resolve_format(None)[0] == PRESETS["standard_mtb"]
    assert resolve_format(None, "best_of_5")[0].best_of == 5


# -- games, sets, tiebreaks ------------------------------------------------------------


def test_deuce_and_advantage():
    log = Log()
    log.points("aaabbb")
    an, view = final(log.events)
    assert view.points == (3, 3) and points_text(view) == "40–40"
    log.points("a")
    assert points_text(final(log.events)[1]) == "AD–40"
    log.points("ba")
    assert points_text(final(log.events)[1]) == "AD–40"
    log.points("a")
    assert final(log.events)[1].games == (1, 0)


def test_no_ad_deciding_point():
    log = Log()
    log.points("aaabbbb")
    view = final(log.events, dataclasses.replace(STD, ad=False))[1]
    assert view.games == (0, 1) and view.points == (0, 0)


def test_tiebreak_set_and_match_tiebreak():
    log = Log()
    for _ in range(6):
        log.points("aaaa")
        log.points("bbbb")
    view = final(log.events, PRESETS["standard_mtb"])[1]
    assert view.games == (6, 6) and view.in_tiebreak
    log.points("abababababab")  # 6-6 in the tiebreak
    log.points("bb")
    an, view = final(log.events, PRESETS["standard_mtb"])
    assert view.sets == (SetScore("B", (6, 7), (6, 8)),) and score_text(view) == "6–7(6), 0–0, 0–0"
    for _ in range(6):
        log.points("aaaa")  # A wins set 2 6-0
    view = final(log.events, PRESETS["standard_mtb"])[1]
    assert view.in_tiebreak  # final set is a match tiebreak
    log.points("aaaaaaaa" + "bbbbbbbb" + "aa")  # 10-8
    an, view = final(log.events, PRESETS["standard_mtb"])
    assert view.winner == "A" and score_text(view) == "6–7(6), 6–0, [10–8]"


def test_short_sets_and_pro_set():
    log = Log()
    for _ in range(4):
        log.points("aaaa")
        log.points("bbbb")
    an, view = final(log.events, PRESETS["short_sets"])
    assert view.sets == () and view.games == (4, 4) and view.in_tiebreak
    log.points("aaaaaaa")
    assert final(log.events, PRESETS["short_sets"])[1].sets == (SetScore("A", (5, 4), (7, 0)),)
    pro = Log()
    for _ in range(8):
        pro.points("aaaa")
    assert final(pro.events, PRESETS["pro_set"])[1].winner == "A"


def test_tiebreak_serve_rotation_for_aces_and_double_faults():
    log = Log()
    log("game_start", side="A")
    for i in range(12):  # games alternate servers: A serves 6, B serves 6
        log.points("aaaa" if i % 2 == 0 else "bbbb")
    # tiebreak at 6-6: A served game 1, so A serves first in the tiebreak
    log("ace")              # point 1: A serves -> A
    log("ace")              # point 2: B serves -> B
    log("fault"), log("fault")  # point 3: B serves, double fault -> A
    log("ace")              # point 4: A serves -> A
    an, view = final(log.events)
    assert view.points == (3, 1)
    second_fault = log.events[-2]
    winners = [st.winner for st in an.steps if st.event.type == "ace" or st.event is second_fault]
    assert winners == ["A", "B", "A", "A"]


# -- recording levels ------------------------------------------------------------------


def test_game_level_and_set_level_scoring():
    log = Log()
    for r in "AAAAAA":
        log("game_end", result=r)
    log("set_end", result="A")
    log("set_end", result="B", details={"games": [4, 6]})  # set level, with the score
    log("set_end", result="A")  # set level, score not given
    an, view = final(log.events)
    assert view.sets == (SetScore("A", (6, 0)), SetScore("B", (4, 6)), SetScore("A", None))
    assert view.winner == "A" and an.issues == []


def test_mixed_levels():
    log = Log()
    log.points("aaaa")          # point level game
    log("game_end", result="A")
    log("game_end", result="B")  # game level game
    log.points("bbbb")
    an, view = final(log.events)
    assert view.games == (1, 2) and an.issues == []


# -- unknowns and inference ------------------------------------------------------------


def test_single_unknown_point_is_inferred():
    log = Log()
    log("game_start", side="A")
    pts = log.points("a?ba")
    log.points("a")
    log("game_end", result="A")
    an, _ = final(log.events)
    st = an.step_for(pts[1].id)
    assert st.winner == "A" and st.inferred and not st.uncertain
    assert an.issues == []


def test_order_ambiguous_points_are_uncertain_but_score_certain():
    log = Log()
    pts = log.points("??aa")
    log("game_end", result="A")
    an, view = final(log.events)
    # Only a,a ends the game exactly at the 4th point (a,b or b,a would leave 40-15).
    assert view.games == (1, 0)
    assert all(an.step_for(p.id).winner == "A" for p in pts[:2])
    log2 = Log()
    pts2 = log2.points("??aaa")
    log2("game_end", result="A")
    an2, view2 = final(log2.events)
    assert view2.games == (1, 0)  # score certain: 4-1
    assert all(an2.step_for(p.id).uncertain for p in pts2[:2])  # but not which was B's
    assert [i.code for i in an2.issues] == ["uncertain_result", "uncertain_result"]
    assert all(i.severity == "info" for i in an2.issues)


def test_unknown_game_inferred_from_set_end():
    log = Log()
    for r in "AAAAA":
        log("game_end", result=r)
    g = log("game_end", result="unknown")
    log("set_end", result="A")
    an, view = final(log.events)
    assert an.step_for(g.id).winner == "A" and view.sets == (SetScore("A", (6, 0)),)


def test_set_score_constrains_earlier_unknowns():
    log = Log()
    log("game_end", result="A")
    log("game_end", result="B")
    unknown = [log("game_end", result="unknown"), log("game_end", result="unknown")]
    log("score_state", details={"games": [3, 1]})
    an, view = final(log.events)
    assert [an.step_for(e.id).winner for e in unknown] == ["A", "A"]
    assert an.issues == []


def test_mid_match_start_and_correction():
    log = Log()
    log("score_state", details={"sets": [[6, 4]], "games": [3, 2], "server": "B"})
    log.points("bbbb")
    an, view = final(log.events, PRESETS["standard_mtb"])
    assert view.sets == (SetScore("A", (6, 4)),) and view.games == (3, 3) and view.server == "A"
    # The user notices the real score is 4-2 and enters it: authoritative, flagged
    log("score_state", details={"games": [4, 2]})
    an, view = final(log.events, PRESETS["standard_mtb"])
    assert view.games == (4, 2)
    assert [i.code for i in an.issues] == ["score_conflict"]


def test_unknown_start_without_parts():
    log = Log()
    log("score_state")  # video starts mid-match, score unknown
    log.points("aaaa")
    an, view = final(log.events)
    assert view.sets is None and view.games is None and score_text(view) == "?, ?, ?"
    log("score_state", details={"sets": [], "games": [2, 0]})
    assert final(log.events)[1].games == (2, 0)


def test_rules_change_mid_match():
    log = Log()
    for _ in range(6):
        log("game_end", result="A")
    for _ in range(6):
        log("game_end", result="B")
    log("rules_change", details={"format": {"final_set": "match_tiebreak"}})
    an, view = final(log.events)  # standard (final set normal) changed on the fly
    assert view.in_tiebreak and view.sets == (SetScore("A", (6, 0)), SetScore("B", (0, 6)))


# -- validation ------------------------------------------------------------------------


def test_conflicts_are_applied_and_reported():
    log = Log()
    log.points("aab")
    log("game_end", result="A")  # 30-15 is not a finished game
    an, view = final(log.events)
    assert view.games == (1, 0) and view.points == (0, 0)
    assert [i.code for i in an.issues] == ["score_conflict"]


def test_other_issues():
    log = Log()
    log("game_start", side="A")
    log.points("aaaa")
    log("game_start", side="A")  # B should serve game 2
    log.points("a")
    log("game_start")  # game 2 is unfinished
    log("tiebreak_start")
    log("point", result="A", player="Emma")  # not a player in this match
    log("score_state", details={"games": [1]})
    log("rules_change", details={"format": {"best_of": 2}})
    match = {"kind": "singles", "sides": {"A": {"players": ["Ana"]}, "B": {"players": ["Sara"]}}}
    codes = [i.code for i in analyze(log.events, STD, match=match).issues]
    assert codes == ["server_out_of_turn", "game_start_mid_game", "score_conflict",
                     "unknown_player", "invalid_score_state", "invalid_rules_change"]


def test_events_after_match_end():
    log = Log()
    for _ in range(12):
        log("game_end", result="A")
    log.points("a")
    an, view = final(log.events)
    assert view.winner == "A" and [i.code for i in an.issues] == ["after_match_end"]


def test_possible_duplicate_point():
    log = Log()
    log("game_start", side="A")
    log("ace")
    log("point", result="A")  # 1 s later, no serve in between
    log("serve_in")
    log("point", result="B")
    log("fault"), log("fault")
    log.t += 10_000
    log("point", result="A")  # outside the window
    an = analyze(log.events, STD, duplicate_window_ms=5000)
    assert [(i.code, i.event_id) for i in an.issues] == [("possible_duplicate_point", "e3")]


def test_too_many_unknowns_gives_up_cleanly(monkeypatch):
    monkeypatch.setattr(scoring, "MAX_STATES", 8)
    log = Log()
    log.points("?" * 12)
    an, view = final(log.events)
    assert "too_many_unknowns" in [i.code for i in an.issues]
    assert view.games is None


def test_view_at_playhead():
    log = Log()
    log.points("aa")
    an = analyze(log.events, STD)
    assert an.view_at(0).points == (0, 0)
    assert an.view_at(1500).points == (1, 0)
    assert an.view_at(99_000).points == (2, 0)


@pytest.mark.parametrize("points, ad, tb, text", [
    ((0, 0), True, False, "0–0"), ((2, 1), True, False, "30–15"), ((3, 3), True, False, "40–40"),
    ((5, 4), True, False, "AD–40"), ((4, 5), True, False, "40–AD"), ((3, 3), False, False, "40–40"),
    ((5, 3), True, True, "5–3"),
])
def test_points_text(points, ad, tb, text):
    view = scoring.ScoreView((), (6, 6) if tb else (0, 0), points, None, tb, None, None, True,
                             dataclasses.replace(STD, ad=ad))
    assert points_text(view) == text


# -- simulated matches (independent of the engine) ---------------------------------------


def simulate(fmt: Format, seed: int):
    """Play a random match point by point with plain rules; return events and set scores."""
    rng = random.Random(seed)
    log = Log()
    need = fmt.best_of // 2 + 1
    sets, won, server = [], [0, 0], 0

    def play_point(srv: int) -> int:
        log.t += 20_000  # points are ~20 s apart (keeps clear of the duplicate-point window)
        w = 0 if rng.random() < 0.52 else 1
        r = rng.random()
        if w == srv and r < 0.1:
            log("ace")
        elif w != srv and r < 0.08:
            log("fault"), log("fault")
        else:
            if r < 0.3:
                log("fault")
            log("serve_in") if r < 0.5 else None
            log("point", result="AB"[w])
        return w

    def play_game(srv: int, tiebreak_to: int | None) -> tuple[int, tuple[int, int] | None]:
        log("game_start", side="AB"[srv])
        p = [0, 0]
        if tiebreak_to:
            order = [srv]
            while len(order) < 60:
                order += [1 - srv, 1 - srv, srv, srv]
            k = 0
            while not (max(p) >= tiebreak_to and abs(p[0] - p[1]) >= 2):
                p[play_point(order[k])] += 1
                k += 1
        else:
            while not (max(p) >= 4 and (abs(p[0] - p[1]) >= 2 or not fmt.ad)):
                p[play_point(srv)] += 1
        w = 0 if p[0] > p[1] else 1
        log("game_end", result="AB"[w])
        return w, (tuple(p) if tiebreak_to else None)

    while max(won) < need:
        log("set_start")
        if fmt.final_set == "match_tiebreak" and won == [need - 1, need - 1]:
            w, tb = play_game(server, fmt.match_tiebreak_points)
            server = 1 - server
            sets.append(("AB"[w], (1, 0) if w == 0 else (0, 1)))
        else:
            g = [0, 0]
            while True:
                tb_game = fmt.tiebreak_at is not None and g == [fmt.tiebreak_at] * 2
                w, _ = play_game(server, fmt.tiebreak_points if tb_game else None)
                server = 1 - server
                g[w] += 1
                if tb_game or (max(g) >= fmt.games and abs(g[0] - g[1]) >= 2):
                    break
            w = 0 if g[0] > g[1] else 1
            sets.append(("AB"[w], tuple(g)))
        won[w] += 1
        log("set_end", result="AB"[w])
    return log.events, sets


FORMATS = ["standard", "standard_mtb", "pro_set", "short_sets"]


@pytest.mark.parametrize("preset", FORMATS)
@pytest.mark.parametrize("seed", range(4))
def test_simulated_match_fully_known(preset, seed):
    fmt = PRESETS[preset]
    events, sets = simulate(fmt, seed)
    an = analyze(events, fmt)
    view = an.steps[-1].view
    assert [(s.winner, s.games) for s in view.sets] == sets
    sets_a = sum(w == "A" for w, _ in sets)
    assert view.winner == ("A" if sets_a > len(sets) - sets_a else "B")
    assert all(st.view.certain for st in an.steps)
    assert an.issues == []


@pytest.mark.parametrize("preset", FORMATS + ["no_ad"])
@pytest.mark.parametrize("seed", range(6))
def test_simulated_match_with_hidden_points(preset, seed):
    fmt = dataclasses.replace(STD, ad=False) if preset == "no_ad" else PRESETS[preset]
    events, sets = simulate(fmt, seed)
    rng = random.Random(1000 + seed)
    truth = {}
    hidden = []
    for e in events:
        if e.type == "point" and rng.random() < 0.3:
            truth[e.id] = e.result
            hidden.append(dataclasses.replace(e, result="unknown"))
        elif e.type == "game_end" and rng.random() < 0.15:
            truth[e.id] = e.result
            hidden.append(dataclasses.replace(e, result=None))
        else:
            hidden.append(e)
    an = analyze(hidden, fmt)
    view = an.steps[-1].view
    # Set/game results are all still known, so the final score is certain and correct.
    assert [(s.winner, s.games) for s in view.sets] == sets
    uncertain = set()
    for st in an.steps:
        if st.event.id in truth:
            if st.uncertain:
                uncertain.add(st.event.id)
            else:
                assert st.winner == truth[st.event.id], st.event  # never inferred wrong
    assert {i.event_id for i in an.issues} == uncertain
    assert all(i.code == "uncertain_result" for i in an.issues)


@pytest.mark.parametrize("seed", range(6))
def test_one_hidden_point_per_game_is_always_inferred(seed):
    events, _ = simulate(STD, seed)
    rng = random.Random(seed)
    out, game_points = [], []
    for e in events:
        if e.type == "point":
            game_points.append(len(out))
        if e.type == "game_end" and game_points:
            i = rng.choice(game_points)
            out[i] = dataclasses.replace(out[i], result="unknown")
            game_points = []
        out.append(e)
    truth = {e.id: e.result for e in events}
    an = analyze(out, STD)
    for st in an.steps:
        if st.event.type == "point" and st.event.result == "unknown":
            assert st.inferred and st.winner == truth[st.event.id]
    assert an.issues == []


@pytest.mark.parametrize("text, tb, counts", [
    ("30-40", False, (2, 3)), ("30–40", False, (2, 3)), ("15-love", False, (1, 0)),
    ("0 15", False, (0, 1)), ("Deuce", False, (3, 3)), ("40-40", False, (3, 3)),
    ("AD-40", False, (4, 3)), ("40-ad", False, (3, 4)), ("5-3", True, (5, 3)),
    ("30-45", False, None), ("ad-ad", False, None), ("5-3", False, None), ("x", True, None),
])
def test_parse_points(text, tb, counts):
    assert scoring.parse_points(text, tiebreak=tb) == counts


def test_pro10_preset():
    fmt = PRESETS["pro10"]
    log = Log()
    for _ in range(10):
        log("game_end", result="A")
        log("game_end", result="B")
    view = final(log.events, fmt)[1]
    assert view.games == (10, 10) and view.in_tiebreak
    log.points("aaaaaaa")
    assert final(log.events, fmt)[1].winner == "A"


def test_tiebreak_start_at_any_level_score():
    # Pro set to 8 (tiebreak at 8-8 by default), but they play the tiebreak at 7-7.
    log = Log()
    for _ in range(7):
        log("game_end", result="A")
        log("game_end", result="B")
    log("tiebreak_start")
    log.points("aaaaaab" + "a")
    an, view = final(log.events, PRESETS["pro_set"])
    assert view.sets == (SetScore("A", (8, 7), (7, 1)),) and view.winner == "A"
    assert an.issues == []


def test_tiebreak_start_constrains_unknown_game():
    log = Log()
    for _ in range(6):
        log("game_end", result="A")
        log("game_end", result="B")
    log("game_end", result="A")                 # 7-6
    g = log("game_end", result="unknown")       # must be B: tiebreak needs level games
    log("tiebreak_start")
    an, view = final(log.events, PRESETS["pro_set"])
    assert an.step_for(g.id).winner == "B" and view.in_tiebreak and an.issues == []


def test_tiebreak_start_needs_level_games():
    log = Log()
    log("game_end", result="A")
    log("tiebreak_start")
    an, view = final(log.events)
    assert [i.code for i in an.issues] == ["score_conflict"]
    assert view.in_tiebreak  # applied as entered
