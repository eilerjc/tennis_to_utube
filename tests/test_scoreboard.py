"""What the burned-in scoreboard shows, and when (pure; no Pillow or ffmpeg)."""

from tennis_to_utube import matchfile
from tennis_to_utube.flow import analyze_match
from tennis_to_utube.scoreboard import (Board, Cell, board_at, board_changes, board_of, boards_on,
                                        points_cells, shows_points, side_names)
from tennis_to_utube.scoring import SetScore, State, resolve_format, view_of
from tennis_to_utube.timeline import Timeline
from tennis_to_utube.trim import Segment, TrimPlan, plan_trim

NAMES = ("Emma", "Sara")


def singles() -> matchfile.MatchFile:
    mf = matchfile.MatchFile(sources=[matchfile.Source("a.MP4", 100_000)])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    return mf


def view(**state):
    fmt, _ = resolve_format(None)
    return view_of([State(fmt, **state)])


def test_side_names_full_and_doubles():
    mf = singles()
    assert side_names(mf.match) == NAMES
    mf.match["kind"] = "doubles"
    mf.match["sides"]["A"]["players"] = ["Emma Jones", "Sara Smith"]
    mf.match["sides"]["B"]["players"] = ["Alexandra Rodriguez"]
    assert side_names(mf.match) == ("Emma Jones / Sara Smith", "Alexandra Rodriguez / Player 4")


def test_points_shown_when_the_match_has_point_marks():
    mf = singles()
    mf.add_event(1000, "game_start", side="A")
    assert not shows_points(mf.events)
    assert shows_points(mf.events, "on")
    mf.add_event(2000, "ace")
    assert shows_points(mf.events) and not shows_points(mf.events, "off")


def test_points_text():
    assert points_cells(view(points=(0, 0))) == ("0", "0")
    assert points_cells(view(points=(2, 1))) == ("30", "15")
    assert points_cells(view(points=(3, 3))) == ("40", "40")
    assert points_cells(view(points=(5, 4))) == ("AD", "40")
    assert points_cells(view(points=(4, 5))) == ("40", "AD")
    assert points_cells(view(games=(6, 6), points=(5, 3))) == ("5", "3")  # tiebreak counts
    assert points_cells(view(points=None)) == ("", "")


def test_before_anything_is_known_the_board_shows_zeros():
    b = board_of(view(), NAMES, True)
    assert b == Board(NAMES, (), ("0", "0"), ("0", "0"), None)
    assert board_of(view(), NAMES, False).points is None


def test_completed_sets_tiebreaks_and_unknown_parts():
    sets = (SetScore("A", (6, 4)), SetScore("B", (6, 7), (5, 7)), SetScore("A", (1, 0), (10, 8)))
    b = board_of(view(sets=sets[:2], games=None, points=None, server="A"), NAMES, True)
    assert b.sets == ((Cell("6", won=True), Cell("4")), (Cell("6", "5"), Cell("7", won=True)))
    assert b.games == ("", "") and b.points == ("", "")  # unknown: blank
    assert b.server is None  # 1-1 in sets: a match tiebreak, and its points are unknown
    assert board_of(view(sets=sets[:1], games=(2, 1), points=None, server="A"), NAMES, True).server == "A"
    done = board_of(view(sets=sets, winner="A"), NAMES, True)
    assert done.sets[2] == (Cell("10", won=True), Cell("8"))  # match tiebreak: its points
    assert (done.games, done.points, done.server, done.winner) == (None, None, None, "A")


def test_board_changes_follow_the_score():
    mf = singles()
    mf.add_event(1000, "match_start")
    mf.add_event(2000, "game_start", side="A", player="Emma")
    mf.add_event(5000, "point", result="A")
    mf.add_event(6000, "note", note="no score change")
    mf.add_event(9000, "point", result="B")
    changes = board_changes(analyze_match(mf), mf.match, True)
    assert [t for t, _ in changes] == [0, 2000, 5000, 9000]  # server known at the Game start
    assert changes[0][1].points == ("0", "0") and changes[0][1].server is None
    assert changes[1][1].server == "A"
    assert changes[2][1].points == ("15", "0")
    assert changes[3][1].points == ("15", "15")
    delayed = board_changes(analyze_match(mf), mf.match, True, delay_ms=500)
    assert [t for t, _ in delayed] == [0, 2500, 5500, 9500]


def test_boards_on_the_full_recording_and_a_trimmed_video():
    b = [Board(NAMES, games=(str(i), "0")) for i in range(4)]
    changes = [(0, b[0]), (10_000, b[1]), (30_000, b[2]), (50_000, b[3])]
    timeline = Timeline((60_000,))
    full = boards_on(plan_trim(timeline, [], None), changes)
    assert [(s.start_ms, s.end_ms, s.board) for s in full] == [
        (0, 10_000, b[0]), (10_000, 30_000, b[1]), (30_000, 50_000, b[2]), (50_000, 60_000, b[3])]
    # 20-40 s cut: b[2]'s start is removed, so it shows from the cut on
    plan = TrimPlan(timeline, (), (Segment(0, 20_000, 0), Segment(40_000, 60_000, 20_000)), ())
    trimmed = boards_on(plan, changes)
    assert [(s.start_ms, s.end_ms, s.board) for s in trimmed] == [
        (0, 10_000, b[0]), (10_000, 20_000, b[1]), (20_000, 30_000, b[2]), (30_000, 40_000, b[3])]
    # a preview window: times relative to its start
    window = boards_on(plan, changes, 15_000, 25_000)
    assert [(s.start_ms, s.end_ms, s.board) for s in window] == [(0, 5_000, b[1]), (5_000, 10_000, b[2])]
    assert board_at(trimmed, 25_000) == b[2] and board_at(trimmed, 99_000) == b[3]


def test_a_cut_that_hides_a_change_merges_equal_boards():
    b = [Board(NAMES, games=(str(i), "0")) for i in range(2)]
    changes = [(0, b[0]), (30_000, b[1]), (35_000, b[0])]
    plan = TrimPlan(Timeline((60_000,)), (), (Segment(0, 25_000, 0), Segment(40_000, 60_000, 25_000)), ())
    assert [(s.start_ms, s.end_ms) for s in boards_on(plan, changes)] == [(0, 45_000)]


def test_a_muxer_offset_is_covered_from_zero():
    import dataclasses
    plan = dataclasses.replace(plan_trim(Timeline((10_000,)), [], None), output_start_ms=21)
    shown = boards_on(plan, [(0, Board(NAMES))])
    assert (shown[0].start_ms, shown[-1].end_ms) == (0, 10_021)

