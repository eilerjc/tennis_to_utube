from tennis_to_utube.matchfile import default_match
from tennis_to_utube.matchfile import MatchFile
from tennis_to_utube.names import (
    partner_of, players, rename_player, short_names, short_side_names, side_name, side_of,
)
from tennis_to_utube.shortcuts import ACTIONS_BY_ID


def match(a=(), b=(), kind="singles"):
    m = default_match()
    m["kind"] = kind
    m["sides"]["A"]["players"] = list(a)
    m["sides"]["B"]["players"] = list(b)
    return m


def test_default_player_names():
    m = match()
    assert players(m, "A") == ["Player 1"] and players(m, "B") == ["Player 2"]
    assert short_side_names(m) == {"A": "Play 1", "B": "Play 2"}
    d = match(kind="doubles")
    assert side_name(d, "A") == "Player 1 & Player 2" and side_name(d, "B") == "Player 3 & Player 4"
    assert short_side_names(d) == {"A": "Play 1/Play 2", "B": "Play 3/Play 4"}
    # a partly filled doubles side keeps the typed name and fills the rest
    assert players(match(["Emma Smith"], kind="doubles"), "A") == ["Emma Smith", "Player 2"]
    assert players(match(["", "Ana"]), "A") == ["Player 1", "Ana"]


def test_full_names_exactly_as_typed():
    m = match(["Zoë Ó'Brien-Lee"], ["Sara"])
    assert side_name(m, "A") == "Zoë Ó'Brien-Lee" and side_name(m, "B") == "Sara"


def test_short_names():
    m = match(["Alexandra Jones"], ["Emma"])
    assert short_side_names(m) == {"A": "Alex J", "B": "Emma"}
    m = match(["Mary Ann Smith", "Bo Li"], ["Sara Perez", "Ana"], kind="doubles")
    assert short_names(m) == {"A": ["Mary S", "Bo L"], "B": ["Sara P", "Ana"]}
    assert short_side_names(m)["A"] == "Mary S/Bo L"


def test_clashing_short_names_are_lengthened():
    m = match(["Alexandra Smith"], ["Alexander Scott"])
    assert short_side_names(m) == {"A": "Alex Sm", "B": "Alex Sc"}
    siblings = match(["Alexandra Smith"], ["Alexander Smith"])
    assert short_side_names(siblings) == {"A": "Alexandr S", "B": "Alexande S"}
    same = match(["Emma"], ["Emma"])  # identical names cannot be told apart
    assert short_side_names(same) == {"A": "Emma", "B": "Emma"}
    longer = match(["Chris Evans"], ["Christina Evert"])
    assert short_side_names(longer, first_letters=5) == {"A": "Chris Eva", "B": "Chris Eve"}


def test_buttons_use_short_names():
    m = match(["Alexandra Jones"], ["Sara Perez", "Ana Lopez"])
    labels = short_side_names(m)
    assert ACTIONS_BY_ID["point_a"].button_text(labels) == "Point Alex J"
    assert ACTIONS_BY_ID["game_end_b"].button_text(labels) == "Game Sara P/Ana L"



def test_new_matches_store_default_names():
    assert players(MatchFile().match, "A") == ["Player 1"]
    assert MatchFile().match["sides"]["B"]["players"] == ["Player 2"]


def test_side_and_partner_lookup():
    m = match(["Emma Smith", "Ana Perez"], ["Sara"], kind="doubles")
    assert side_of(m, "Ana Perez") == "A" and side_of(m, "Player 4") == "B"
    assert side_of(m, "B") == "B" and side_of(m, "Nobody") is None and side_of(None, "Emma") is None
    assert partner_of(m, "Emma Smith") == "Ana Perez" and partner_of(m, "Sara") == "Player 4"
    assert side_of(match(["Emma"], ["Emma"]), "Emma") is None  # ambiguous


def test_rename_is_find_replace():
    mf = MatchFile()
    mf.add_event(1, "game_start", player="Player 1")
    mf.add_event(2, "score_state", details={"server": "Player 1", "games": [1, 0]})
    mf.add_event(3, "game_start", player="Player 2")
    assert rename_player(mf, "Player 1", "Emma Smith") == 3
    assert mf.match["sides"]["A"]["players"] == ["Emma Smith"]
    assert [e.player for e in mf.events] == ["Emma Smith", None, "Player 2"]
    assert mf.events[1].details["server"] == "Emma Smith"
    for bad in (("Nobody", "X"), ("Emma Smith", "Player 2"), ("Emma Smith", "  ")):
        try:
            rename_player(mf, *bad)
        except ValueError:
            continue
        raise AssertionError(bad)
