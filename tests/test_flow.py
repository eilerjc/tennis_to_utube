from tennis_to_utube.config import load_config
from tennis_to_utube.flow import analyze_match, flow_at
from tennis_to_utube.matchfile import MatchFile


def singles():
    mf = MatchFile()
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    return mf


def test_flow_through_a_game():
    mf = singles()
    mf.add_event(1000, "game_start", player="Emma")
    for t in (2000, 3000, 4000, 5000):
        mf.add_event(t, "point", result="A")
    mf.add_event(6000, "game_end", result="A")
    an = analyze_match(mf)
    f = flow_at(an, mf.match, 500)
    assert not f.in_game and f.suggested()[0] == "game_start" and f.next_server_player is None
    f = flow_at(an, mf.match, 3500)
    assert f.in_game and "point_a" in f.suggested() and f.score.points == (2, 0)
    f = flow_at(an, mf.match, 6000)
    assert not f.in_game and f.score.games == (1, 0)
    assert (f.next_server, f.next_server_player) == ("B", "Sara")


def test_set_can_end_and_match_over():
    mf = singles()
    mf.match["format"] = {"preset": "pro_set"}
    for i in range(8):
        mf.add_event(1000 * (i + 1), "game_end", result="A")
    an = analyze_match(mf)
    f = flow_at(an, mf.match, 8000)
    assert f.match_over and f.suggested() == ["match_end"]
    mf.match["format"] = {"preset": "standard"}
    mf.events = mf.events[:6]
    f = flow_at(analyze_match(mf), mf.match, 6000)
    assert f.set_can_end and f.suggested()[0] == "set_end_a"


def test_doubles_server_prediction():
    mf = MatchFile()
    mf.match["kind"] = "doubles"
    mf.match["sides"]["A"]["players"] = ["Emma", "Ana"]
    mf.match["sides"]["B"]["players"] = ["Sara", "Mia"]
    mf.add_event(1000, "game_start", player="Ana")
    mf.add_event(2000, "game_end", result="A")
    an = analyze_match(mf)
    f = flow_at(an, mf.match, 2000)
    assert f.next_server == "B" and f.next_server_player is None  # B has not served yet
    mf.add_event(3000, "game_start", player="Mia")
    mf.add_event(4000, "game_end", result="A")
    an = analyze_match(mf)
    assert flow_at(an, mf.match, 4000).next_server_player == "Emma"  # Ana's partner
    mf.add_event(5000, "game_start", player="Emma")
    mf.add_event(6000, "game_end", result="B")
    assert flow_at(analyze_match(mf), mf.match, 6000).next_server_player == "Sara"


def test_format_from_match_or_config(tmp_path):
    mf = singles()
    del mf.match["format"]
    (tmp_path / "config.toml").write_text('[scoring]\ndefault_format = "pro_set"\n', encoding="utf-8")
    cfg = load_config(tmp_path / "config.toml")
    assert analyze_match(mf, cfg).initial.fmt.games == 8
    mf.match["format"] = {"preset": "short_sets", "ad": False}
    fmt = analyze_match(mf, cfg).initial.fmt
    assert (fmt.games, fmt.ad) == (4, False)
