import pytest

from tennis_to_utube import matchfile
from tennis_to_utube.matchfile import MatchFile
from tennis_to_utube.session import LockedError, Session, point_server_side
from tennis_to_utube.scoring import PRESETS, ScoreView


def session(tmp_path, kind="singles"):
    mf = MatchFile()
    mf.match["kind"] = kind
    if kind == "doubles":
        mf.match["sides"]["A"]["players"] = ["Emma", "Ana"]
        mf.match["sides"]["B"]["players"] = ["Sara", "Mia"]
    else:
        mf.match["sides"]["A"]["players"] = ["Emma"]
        mf.match["sides"]["B"]["players"] = ["Sara"]
    return Session(mf, tmp_path / "m.match.json")


def test_mark_points_games_and_servers(tmp_path):
    s = session(tmp_path)
    g = s.mark("game_start", 1000)
    assert (g.type, g.side, g.player) == ("game_start", "A", "Emma")  # unknown: ours
    ace = s.mark("ace", 2000)
    assert (ace.side, ace.player) == ("A", "Emma")
    for t in (3000, 4000, 5000):
        s.mark("point_a", t)
    assert s.flow_at(5000).score.games == (1, 0)
    end = s.mark("game_end_a", 6000)
    assert (end.type, end.result) == ("game_end", "A")
    g2 = s.mark("game_start", 7000)
    assert (g2.side, g2.player) == ("B", "Sara")  # predicted
    g3 = s.mark("game_start_other_server", 8000)
    assert (g3.side, g3.player) == ("A", "Emma")
    assert s.mark("set_end_unknown", 9000).result == "unknown"
    with pytest.raises(ValueError):
        s.mark("play_pause", 1)


def test_doubles_server_player(tmp_path):
    s = session(tmp_path, "doubles")
    s.mark("game_start", 1000, player="Ana")  # user picked the server
    s.mark("game_end_a", 2000)
    g = s.mark("game_start", 3000)
    assert (g.side, g.player) == ("B", None)  # B's first service game: team chooses
    s.update(g.id, player="Mia")
    s.mark("game_end_b", 4000)
    assert s.mark("game_start", 5000).player == "Emma"


def test_undo_redo_and_ids(tmp_path):
    s = session(tmp_path)
    a = s.mark("point_a", 1000)
    b = s.mark("point_b", 2000)
    assert s.undo() and [e.id for e in s.mf.events] == [a.id]
    assert s.redo() and [e.id for e in s.mf.events] == [a.id, b.id]
    s.undo()
    c = s.mark("point_a", 3000)
    assert c.id not in (a.id, b.id)  # never reused, even after undo
    assert not s.can_redo
    s.move(a.id, 500)
    s.update(a.id, note="close", tags=["close"])
    s.delete(c.id)
    assert [(e.t_ms, e.note) for e in s.mf.events] == [(500, "close")]
    s.undo(), s.undo(), s.undo()
    assert [(e.id, e.t_ms, e.note) for e in s.mf.events] == [(a.id, 1000, ""), (c.id, 3000, "")]
    assert s.undo() and s.undo() and not s.undo()


def test_lock(tmp_path):
    s = session(tmp_path)
    e = s.mark("point_a", 1000)
    s.locked = True
    for edit in (lambda: s.delete(e.id), lambda: s.move(e.id, 5), lambda: s.update(e.id, note="x")):
        with pytest.raises(LockedError):
            edit()
    s.mark("point_b", 2000)  # new marks are still allowed
    assert len(s.mf.events) == 2


def test_analysis_refreshes_and_save(tmp_path):
    s = session(tmp_path)
    for t in (1, 2, 3, 4):
        s.mark("point_a", t)
    assert s.analysis.steps[-1].view.games == (1, 0)
    s.mark("point_b", 5)
    assert s.analysis.steps[-1].view.points == (0, 1)
    assert s.dirty
    s.save()
    assert not s.dirty and len(matchfile.load(tmp_path / "m.match.json").events) == 5


def test_rename_and_set_match(tmp_path):
    s = session(tmp_path)
    s.mark("game_start", 1)
    assert s.rename_player("Emma", "Emma Smith") == 2
    with pytest.raises(ValueError):
        s.rename_player("Sara", "Emma Smith")
    s.undo()
    assert s.mf.events[0].player == "Emma"
    s.set_match(format={"preset": "pro_set"})
    assert s.analysis.initial.fmt.games == 8


def test_point_server_side_in_tiebreak():
    def view(points, tb):
        return ScoreView((), (6, 6), points, "A", tb, None, None, True, PRESETS["standard"])
    assert [point_server_side(view((k, 0), True)) for k in range(5)] == ["A", "B", "B", "A", "A"]
    assert point_server_side(view((3, 0), False)) == "A"
