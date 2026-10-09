import pytest

from tennis_to_utube import catalog, config
from tennis_to_utube.shortcuts import (
    ACTIONS, ACTIONS_BY_ID, apply_overrides, default_shortcuts, load_shortcuts, normalize_key,
)


@pytest.mark.parametrize("text, canon", [
    ("a", "A"), ("Shift+z", "Shift+Z"), ("shift + Z", "Shift+Z"), ("ctrl+left", "Ctrl+Left"),
    ("Shift+Ctrl+Left", "Ctrl+Shift+Left"), ("control+alt+del", "Ctrl+Alt+Delete"),
    ("space", "Space"), ("`", "`"), ("backtick", "`"), ("6", "6"), ("+", "+"),
    ("Ctrl++", "Ctrl++"), ("esc", "Escape"), ("pgdn", "PageDown"), ("f5", "F5"),
])
def test_normalize_key(text, canon):
    assert normalize_key(text) == canon


@pytest.mark.parametrize("bad", ["", "Shift+", "Hyper+A", "Ctrl+Banana", "AB", "Shift++A"])
def test_invalid_keys(bad):
    with pytest.raises(ValueError):
        normalize_key(bad)


def test_agreed_default_layout():
    sc = default_shortcuts()
    expect = {
        "A": "point_a", "S": "point_b", "D": "point_unknown", "F": "let_point",
        "Q": "serve_in", "W": "fault", "E": "let", "R": "ace",
        "G": "game_start", "Shift+G": "game_start_other_server",
        "Z": "game_end_a", "X": "game_end_b", "C": "game_end_unknown",
        "Shift+Z": "set_end_a", "Shift+X": "set_end_b", "Shift+C": "set_end_unknown",
        "T": "set_start", "Shift+T": "score_state",
        "1": "good_recovery", "2": "footwork", "3": "body_language", "4": "late_contact",
        "5": "strategy", "6": "note",
        "Space": "play_pause", "Left": "skip_back", "Right": "skip_forward",
        "Shift+Left": "skip_back_short", "Shift+Right": "skip_forward_short",
        "Ctrl+Left": "frame_back", "Ctrl+Right": "frame_forward",
        "Up": "prev_event", "Down": "next_event",
        "Ctrl+Z": "undo", "Ctrl+Y": "redo", "Alt+Left": "nudge_back", "Alt+Right": "nudge_forward",
        "Delete": "delete_event", "Ctrl+L": "lock_events",
    }
    assert {k: sc.action_for(k).id for k in expect} == expect
    # every key used once, every default already canonical
    all_keys = [k for a in ACTIONS for k in a.keys]
    assert len(all_keys) == len(set(all_keys)) == len(expect)
    assert all(normalize_key(k) == k for k in all_keys)
    # speed has no keys (buttons only); rare events are buttons/menu only
    assert not any("speed" in a.id for a in ACTIONS)
    for rare in ("match_start", "match_end", "tiebreak_start", "rules_change", "ending_state"):
        assert sc.keys_for(rare) == ()


def test_event_actions_use_catalog_types():
    for a in ACTIONS:
        if a.event_type is not None:
            assert a.event_type in catalog.LABELS
    assert ACTIONS_BY_ID["game_end_b"].event_type == "game_end"
    assert ACTIONS_BY_ID["game_end_b"].result == "B"
    assert ACTIONS_BY_ID["set_end_unknown"].result == "unknown"


def test_buttons_show_player_names():
    names = {"A": "Emma", "B": "Sara"}
    assert ACTIONS_BY_ID["point_a"].button_text(names) == "Point Emma"
    assert ACTIONS_BY_ID["game_end_b"].button_text({"A": "Emma", "B": "Sara & Ana"}) == "Game Sara & Ana"
    assert ACTIONS_BY_ID["point_unknown"].button_text(names) == "Point ?"


def test_user_binding_takes_key_from_default():
    sc = apply_overrides({"point_a": "W", "fault": ["Shift+V", "V"], "note": ""})
    assert sc.keys_for("point_a") == ("W",)
    assert sc.keys_for("fault") == ("Shift+V", "V")
    assert sc.keys_for("note") == ()
    assert sc.action_for("A") is None  # point_a moved away from A
    assert sc.warnings == []  # fault was rebound too, so W is free


def test_conflicts_warn_and_resolve_deterministically():
    sc = apply_overrides({"point_a": "W"})
    assert sc.keys_for("point_a") == ("W",) and sc.keys_for("fault") == ()
    assert len(sc.warnings) == 1 and "fault" in sc.warnings[0]
    sc = apply_overrides({"point_a": "Q", "point_b": "q"})
    assert sc.keys_for("point_a") == ("Q",) and sc.keys_for("point_b") == ()
    assert any("both" in w for w in sc.warnings)
    keys = [k for ks in sc.keys.values() for k in ks]
    assert len(keys) == len(set(keys))


def test_bad_entries_warn():
    sc = apply_overrides({"teleport": "P", "ace": 5, "let": "Hyper+L"})
    assert sc.keys_for("ace") == ("R",)
    assert sc.keys_for("let") == ()  # explicit binding with no valid key
    assert len(sc.warnings) == 3


def test_load_file(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(tmp_path))
    assert load_shortcuts().path is None
    (tmp_path / "shortcuts.toml").write_text('[keys]\nace = "B"\n[extra]\nx = 1\n', encoding="utf-8")
    sc = load_shortcuts()
    assert sc.keys_for("ace") == ("B",) and sc.path == tmp_path / "shortcuts.toml"
    assert len(sc.warnings) == 1 and "extra" in sc.warnings[0]
    (tmp_path / "shortcuts.toml").write_text("[keys\n", encoding="utf-8")
    sc = load_shortcuts()
    assert sc.keys_for("ace") == ("R",) and len(sc.warnings) == 1
