import csv
import io

from tennis_to_utube import matchfile
from tennis_to_utube.flow import analyze_match
from tennis_to_utube.matchfile import MatchFile
from tennis_to_utube.stats import main, records, stats_csv


def hand_match(kind="singles"):
    mf = MatchFile()
    mf.match["format"] = {"preset": "standard"}
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    t = [0]

    def add(type_, **kw):
        t[0] += 1000
        return mf.add_event(t[0], type_, **kw)

    # Game 1, Emma serves: 30-30 with serve marks, then 30-40 (break point), saved, held
    add("game_start", side="A", player="Emma")
    add("serve_in"), add("point", result="A")                    # 1st serve point won
    add("fault"), add("serve_in"), add("point", result="B")      # 2nd serve point lost
    add("ace")                                                   # 1st serve, ace
    add("fault"), add("fault")                                   # double fault
    add("point", result="B")                                     # 30-40: break point next (Emma 2 of 5 so far)
    add("point", result="A")                                     # saved: deuce
    add("point", result="A"), add("point", result="A")           # Emma holds
    add("game_end", result="A")
    # Game 2, Sara serves: Emma breaks with shots
    add("game_start", side="B", player="Sara")
    add("winner", side="A")
    add("unforced_error", side="B")
    add("point", result="A")
    add("forced_error", side="B")                                # 0-40 break point: won
    return mf


def table(mf):
    rows = list(csv.reader(io.StringIO(stats_csv(mf))))
    return rows[0], {(r[0], r[1]): (r[2], r[3]) for r in rows[1:] if r}


def test_point_records(tmp_path):
    mf = hand_match()
    rec = records(mf, analyze_match(mf))
    assert len(rec.points) == 12 and all(p.winner for p in rec.points)
    assert [p.server for p in rec.points] == ["A"] * 8 + ["B"] * 4
    assert [p.break_point for p in rec.points] == [False] * 5 + [True] + [False] * 5 + [True]
    assert [(g.server, g.winner) for g in rec.games] == [("A", "A"), ("B", "A")]


def test_stats_table():
    header, t = table(hand_match())
    assert header == ["Section", "Stat", "Emma", "Sara"]
    m = lambda stat: t[("Match", stat)]  # noqa: E731
    assert m("Points won") == ("9/12 (75%)", "3/12 (25%)")
    assert m("Service points won") == ("5/8 (62%)", "0/4 (0%)")
    assert m("Return points won") == ("4/4 (100%)", "3/8 (38%)")
    assert m("1st serve in") == ("2/4 (50%)", "0/0")  # 4 points had serve marks
    assert m("1st serve points won") == ("2/2 (100%)", "0/0")
    assert m("2nd serve points won") == ("0/2 (0%)", "0/0")
    assert (m("Aces"), m("Double faults")) == (("1", "0"), ("1", "0"))
    assert m("Break points won") == ("1/1 (100%)", "0/1 (0%)")
    assert m("Break points saved") == ("1/1 (100%)", "0/1 (0%)")
    assert m("Service games won") == ("1/1 (100%)", "0/1 (0%)")
    assert m("Winners") == ("1", "0") and m("Unforced errors") == ("0", "1")
    assert m("Forced errors") == ("0", "1")
    assert t[("Set 1", "Points won")] == m("Points won")


def test_unknown_points_counted_separately():
    mf = hand_match()
    mf.add_event(99_000, "point", result="unknown")
    _, t = table(mf)
    assert t[("Match", "Points with unknown winner (not counted)")][0] == "1"


def test_let_point_serves_do_not_count():
    mf = hand_match()
    t = 100_000
    for type_ in ("game_start", "fault", "serve_in", "let_point", "serve_in"):
        t += 1000
        mf.add_event(t, type_, **({"side": "A", "player": "Emma"} if type_ == "game_start" else {}))
    mf.add_event(t + 1000, "point", result="A")  # won on a first serve (the replay's)
    _, table_ = table(mf)
    assert table_[("Match", "1st serve in")][0] == "3/5 (60%)"  # was 2/4: +1 first serve in
    assert table_[("Match", "1st serve points won")][0] == "3/3 (100%)"
    assert table_[("Match", "Points replayed (let point; serves not counted)")][0] == "1"
    assert table_[("Set 1", "Points replayed (let point; serves not counted)")][0] == "1"


def test_doubles_by_server():
    mf = hand_match()
    mf.match["kind"] = "doubles"
    mf.match["sides"]["A"]["players"] = ["Emma", "Ana"]
    mf.match["sides"]["B"]["players"] = ["Sara", "Mia"]
    text = stats_csv(mf)
    assert "By server,Emma,5/8 (62%),2/4 (50%),1,1" in text
    assert "By server,Sara,0/4 (0%),0/0,0,0" in text


def test_command_line(tmp_path, capsys):
    path = tmp_path / "GX010008.match.json"
    matchfile.save(hand_match(), path)
    assert main([str(path)]) == 0
    out = tmp_path / "GX010008 stats.csv"
    assert out.exists() and out.read_text(encoding="utf-8-sig").startswith("Section,Stat,Emma,Sara")
    assert "Wrote" in capsys.readouterr().out
    assert main([str(tmp_path / "missing.match.json")]) == 1


def test_shots_on_points_are_counted():
    mf = hand_match()
    p = mf.add_event(99_000, "point", result="B")
    p.details = {"shot": "winner", "shot_side": "B"}
    _, t = table(mf)
    assert t[("Match", "Winners")] == ("1", "1")


def test_minibreaks_in_tiebreaks():
    mf = MatchFile()
    mf.match["format"] = {"preset": "standard"}
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    t = 0
    for i in range(12):  # 6-6, every game held to love
        t += 1000
        mf.add_event(t, "game_start", side="A" if i % 2 == 0 else "B")
        for _ in range(4):
            t += 1000
            mf.add_event(t, "point", result="A" if i % 2 == 0 else "B")
    # tiebreak: A serves 1, B serves 2-3, A serves 4-5
    for result in "AABBA":
        t += 1000
        mf.add_event(t, "point", result=result)
    _, tab = table(mf)
    m = lambda stat: tab[("Match", stat)]  # noqa: E731
    assert m("Tiebreak points won") == ("3/5 (60%)", "2/5 (40%)")
    assert m("Minibreaks won") == ("1/2 (50%)", "1/3 (33%)")
    assert m("Minibreaks lost") == ("1/3 (33%)", "1/2 (50%)")
    assert m("Break points won") == ("0/0", "0/0")  # not counted in tiebreaks
