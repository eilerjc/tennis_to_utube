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
    assert s.game_start_server(1000) == (None, None)  # not known yet: the GUI asks
    g = s.mark("game_start", 1000, side="A", player="Emma")
    assert (g.type, g.side, g.player) == ("game_start", "A", "Emma")
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
    s.mark("game_start", 1, side="A", player="Emma")
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


def test_cut_choices_are_stored_in_the_match(tmp_path):
    from tennis_to_utube.matchfile import Source

    s = session(tmp_path)
    s.mf.sources = [Source("GX010001.MP4", 600_000)]
    s.mark("match_start", 60_000)
    s.mark("game_start", 61_000)
    s.mark("game_end_a", 200_000)
    s.mark("game_start", 290_000)
    s.mark("match_end", 500_000)
    assert [c.rule for c in s.cuts()] == ["warmup", "changeovers", "after_match"]
    key = s.cuts()[1].key
    s.set_cut_enabled(key, False)
    assert [c.enabled for c in s.cuts()] == [True, False, True]
    assert s.mf.settings["trim"]["unticked"] == [key]
    s.set_trim_rules(["after_match", "warmup", "bogus"])
    assert s.trim_rules() == ["warmup", "after_match"]
    assert [c.rule for c in s.cuts()] == ["warmup", "after_match"]
    s.undo()
    assert s.trim_rules() == ["warmup", "changeovers", "set_breaks", "after_match"]
    s.undo()
    assert all(c.enabled for c in s.cuts())
    s.redo()
    assert [c.enabled for c in s.cuts()] == [True, False, True]
    s.set_cut_enabled(key, True)
    assert all(c.enabled for c in s.cuts())


def test_export_plan_and_video_id(tmp_path):
    from tennis_to_utube.matchfile import Source
    from tennis_to_utube.trim import plan_to_dict, plan_trim

    s = session(tmp_path)
    s.mf.sources = [Source("a.MP4", 10_000), Source("b.MP4", 5_000)]
    plan, note = s.export_plan()
    assert plan.is_identity and "original recording" in note and "joined" in note
    made = plan_trim(s.timeline(), [], None)
    s.set_output(plan_to_dict(made, "out.mp4"))
    plan, note = s.export_plan()
    assert "out.mp4" in note and plan.total_out_ms == 15_000
    s.set_video_id("dQw4w9WgXcQ")
    assert s.mf.video_id == "dQw4w9WgXcQ"
    s.undo()
    assert s.mf.video_id is None


def test_shot_shortly_after_a_point_describes_it(tmp_path):
    s = session(tmp_path)
    s.mark("game_start", 1000)
    p = s.mark("point_a", 2000)
    e = s.mark("winner_a", 3500)  # within the window: modifies the Point
    assert e.id == p.id and len(s.mf.events) == 2
    assert e.details == {"shot": "winner", "shot_side": "A"} and e.result == "A"
    assert s.flow_at(4000).score.points == (1, 0)  # still one point
    s.mark("forced_error_b", 4000)  # pressed again: replaces the shot
    assert s.event(p.id).details["shot"] == "forced_error"
    assert s.undo() and s.event(p.id).details["shot"] == "winner"  # one undo step
    # disagrees with the Point's winner: a new point
    assert s.mark("winner_b", 4500).type == "winner"
    assert s.flow_at(5000).score.points == (1, 1)
    assert [i.code for i in s.analysis.issues] == ["possible_duplicate_point"]
    # too late, or after a serve mark: a new point
    s.mark("point_a", 10_000)
    assert s.mark("winner_a", 14_000).type == "winner"
    s.mark("point_b", 20_000)
    s.mark("serve_in", 21_000)
    assert s.mark("unforced_error_a", 22_000).type == "unforced_error"


def test_shot_sets_an_unknown_point_winner(tmp_path):
    s = session(tmp_path)
    p = s.mark("point_unknown", 1000)
    s.mark("unforced_error_b", 2000)
    assert s.event(p.id).result == "A"


def test_set_server(tmp_path):
    s = session(tmp_path)
    cp = s.set_server(500, "Sara")  # before anything: a Set score holding the server
    assert (cp.type, cp.details) == ("score_state", {"server": "Sara"})
    assert s.game_start_server(1000) == ("B", "Sara")
    g = s.mark("game_start", 1000, side="B", player="Sara")
    s.mark("point_a", 2000)
    assert s.set_server(2500, "Emma").id == g.id  # in a game: its Game start changes
    assert (g.side, g.player) == ("A", "Emma") and s.flow_at(3000).score.server == "A"
    assert s.undo() and s.event(g.id).side == "B"
    with pytest.raises(ValueError):
        s.set_server(3000, "Nobody")
    s.locked = True
    with pytest.raises(LockedError):
        s.set_server(3000, "Emma")
