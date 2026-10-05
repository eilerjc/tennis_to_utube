"""Offscreen tests of the Mark step with the stand-in player (no video)."""

import pytest

from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui]


@pytest.fixture
def window(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), load_state(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 60_000, fps="60000/1001"),
                                      matchfile.Source("GX020001.MP4", 30_000, fps="60000/1001")])
    for t in (5_000, 20_000, 61_000):
        mf.add_event(t, "point", result="A")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    w.open_match(mf, path)
    yield w
    w.close()


def test_mark_page_loads_joined_timeline(window):
    page = window.mark
    assert window.steps.currentWidget() is page
    assert page.player.duration_ms == 90_000
    assert page.transport.time.text() == "0:00:00.000 / 0:01:30.000"


def test_player_actions_and_keys(window):
    page = window.mark
    keys = {a: [s.toString() for s in act.shortcuts()] for a, act in page.actions.items()}
    assert keys["play_pause"] == ["Space"] and keys["skip_back_short"] == ["Shift+Left"]
    assert keys["frame_forward"] == ["Ctrl+Right"] and keys["next_event"] == ["Down"]
    assert page.actions["frame_back"].autoRepeat() and not page.actions["play_pause"].autoRepeat()
    page.actions["skip_forward"].trigger()
    assert page.position() == 5_000
    page.actions["skip_back_short"].trigger()
    assert page.position() == 4_000
    page.actions["frame_forward"].trigger()
    assert page.position() == 4_004  # next frame starts at 4004.0 (frame 240)
    page.actions["skip_back"].trigger()
    assert page.position() == 0
    assert page.transport.buttons["skip_back"].text() == "5 s\n[←]"


def test_jump_between_events(window):
    page = window.mark
    page.actions["next_event"].trigger()
    assert page.position() == 5_000
    page.actions["next_event"].trigger()
    assert page.position() == 20_000
    page.actions["prev_event"].trigger()
    assert page.position() == 5_000
    page.actions["prev_event"].trigger()
    assert page.position() == 5_000  # nothing earlier


def test_speed_drop_down(window):
    combo = window.mark.transport.speed
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "0.25×", "0.5×", "1×", "1.5×", "2×", "4×", "8×"]
    assert combo.currentText() == "1×"
    combo.activated.emit(6)
    assert window.mark.player.speed() == 8.0
    window.mark.player.set_speed(0.5)  # changed elsewhere: the drop-down follows
    assert combo.currentText() == "0.5×"


def test_wheel_steps_frames_only_when_paused(window, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    page = window.mark
    page.player.seek(10_000)

    def wheel(dy):
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, dy),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        page.player.wheelEvent(ev)

    wheel(120)  # wheel forward (up) = forward one frame
    assert page.position() == 10_010  # frame 600 starts at 10010.0
    wheel(-120)  # wheel back (down) = back one frame
    assert page.position() == 9_994  # frame 599 starts at 9993.3
    page.player.set_paused(False)
    before = page.player._base_ms
    wheel(-120)
    assert page.player._base_ms == before  # ignored while playing


def test_marking_with_keys_updates_score_and_suggestions(window):
    page = window.mark
    page.player.seek(1_000)
    page.actions["game_start"].trigger()
    assert page.buttons.buttons["point_a"].styleSheet()  # in a game: points suggested
    assert not page.buttons.buttons["game_start"].styleSheet()
    for t in (2_000, 3_000, 4_000, 5_500):
        page.player.seek(t)
        page.actions["point_a"].trigger()
    assert "Between games" in page.score_panel.text() or "1" in page.score_panel.text()
    events = [(e.t_ms, e.type, e.result) for e in page.match.sorted_events()]
    assert (1_000, "game_start", None) in events and (5_500, "point", "A") in events
    flow = page.session.flow_at(page.position())
    assert flow.score.games == (1, 0)


def test_serve_then_the_call(window):
    page = window.mark
    page.player.seek(1_000)
    page.actions["game_start"].trigger()
    assert page.buttons.buttons["serve_in"].text().splitlines()[0] == "Serve"
    page.player.seek(2_000)
    page.actions["serve_in"].trigger()
    serve_id = page.selected_id
    assert page.buttons.buttons["fault"].styleSheet()  # the call is suggested
    messages = []
    page.message.connect(messages.append)
    page.player.seek(4_000)
    page.actions["fault"].trigger()
    e = page.session.event(serve_id)
    assert (page.selected_id, e.type, e.t_ms) == (serve_id, "fault", 2_000)
    assert messages == ["Serve at 0:00:02.000 is now Fault"]
    assert not page.buttons.buttons["fault"].styleSheet()


def test_reaction_offset_while_playing(window):
    page = window.mark
    page.player.seek(10_000)
    page.player.set_speed(0.5)
    page.player.set_paused(False)
    page.actions["fault"].trigger()
    page.player.set_paused(True)
    e = page.session.event(page.selected_id)
    # 200 ms real time at half speed = 100 ms of video before the playhead
    assert 9_850 <= e.t_ms <= 9_950


def test_undo_delete_nudge_lock(window):
    page = window.mark
    page.player.seek(30_000)
    page.actions["body_language"].trigger()
    e_id = page.selected_id
    page.actions["nudge_forward"].trigger()
    assert page.session.event(e_id).t_ms == 30_017
    page.actions["lock_events"].trigger()
    page.actions["delete_event"].trigger()
    assert any(e.id == e_id for e in page.match.events)  # locked
    assert "Unlock" in page.buttons.buttons["lock_events"].text()
    page.actions["lock_events"].trigger()
    page.actions["delete_event"].trigger()
    assert all(e.id != e_id for e in page.match.events)
    page.actions["undo"].trigger()
    assert any(e.id == e_id for e in page.match.events)
    assert window.match is page.match


def test_dialog_events(window):
    page = window.mark
    page.ask_note = lambda: "watch her feet"
    page.ask_details = lambda dialog: {"games": [3, 2]}
    page.player.seek(40_000)
    page.actions["note"].trigger()
    page.actions["score_state"].trigger()
    types = {e.type: e for e in page.match.events}
    assert types["note"].note == "watch her feet"
    assert types["score_state"].details == {"games": [3, 2]}
    page.ask_note = lambda: None  # cancelled
    n = len(page.match.events)
    page.actions["note"].trigger()
    assert len(page.match.events) == n


def test_autosave(window, qapp):
    import time

    page = window.mark
    page.player.seek(50_000)
    page.actions["good_recovery"].trigger()
    path = page.session.path
    end = time.monotonic() + 5
    while page.session.dirty and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.05)
    assert not page.session.dirty
    assert any(e.type == "good_recovery" for e in matchfile.load(path).events)
    assert path.with_name(path.name + ".bak").exists()


def test_buttons_show_short_names_and_keys(window):
    page = window.mark
    page.session.mf.match["sides"]["A"]["players"] = ["Alexandra Jones"]
    from tennis_to_utube import names

    page.buttons.set_names(names.short_side_names(page.match.match))
    assert page.buttons.buttons["point_a"].text() == "Point Alex J\n[A]"
    assert page.buttons.buttons["set_end_b"].text().endswith("[Shift+X]")
    assert page.buttons.buttons["match_end"].text() == "Match end"  # menu-only, no key


def test_score_state_dialog_parsing(qapp):
    from tennis_to_utube.gui.event_panel import ScoreStateDialog

    d = ScoreStateDialog({"kind": "singles", "sides": {"A": {"players": ["Emma"]},
                                                        "B": {"players": ["Sara"]}}})
    d.sets.setText("6-4")
    d.games.setText("3-2")
    d.points.setText("30-40")
    d.server.setCurrentIndex(2)
    assert d.validate() == ""
    assert d.details() == {"sets": [[6, 4]], "games": [3, 2], "points": [2, 3], "server": "Sara"}
    d.points.setText("31-40")
    assert "Points" in d.validate()


def test_selected_event_gone_after_undo(window):
    page = window.mark
    page.player.seek(70_000)
    page.actions["strategy"].trigger()
    page.actions["undo"].trigger()  # the selected event no longer exists
    messages = []
    page.message.connect(messages.append)
    page.actions["delete_event"].trigger()
    page.actions["nudge_forward"].trigger()
    assert messages == ["No event selected", "No event selected"] and page.selected_id is None


def test_doubles_server_picker(window):
    page = window.mark
    page.session.set_match(kind="doubles")
    page.session.mf.match["sides"]["A"]["players"] = ["Emma", "Ana"]
    page.session.mf.match["sides"]["B"]["players"] = ["Sara", "Mia"]
    asked = []

    def pick(players):
        asked.append(players)
        page.player.seek(99_000)  # time passes while choosing
        return players[1]

    page.ask_server = pick
    page.player.seek(80_000)
    page.actions["game_start"].trigger()
    e = page.session.event(page.selected_id)
    # first game: nobody known to serve yet, so all four are offered
    assert asked == [["Emma", "Ana", "Sara", "Mia"]] and (e.side, e.player, e.t_ms) == ("A", "Ana", 80_000)
    page.player.seek(81_000)
    page.actions["game_end_a"].trigger()
    page.player.seek(82_000)
    page.ask_server = lambda players: None  # Esc: still marked, server left open
    page.actions["game_start"].trigger()
    e = page.session.event(page.selected_id)
    assert (e.type, e.side, e.player) == ("game_start", "B", None)


def test_singles_asks_for_server_only_while_unknown(window):
    page = window.mark
    players = [page.session.mf.match["sides"][s]["players"][0] for s in "AB"]
    asked = []
    page.ask_server = lambda options: asked.append(options) or options[1]
    assert "Server not set" in page.score_panel.text()
    page.player.seek(85_000)
    page.actions["game_start"].trigger()
    e = page.session.event(page.selected_id)
    assert asked == [players] and (e.side, e.player) == ("B", players[1])
    assert "Server not set" not in page.score_panel.text()
    page.ask_server = lambda options: pytest.fail("asked again")
    page.player.seek(86_000)
    page.actions["game_end_b"].trigger()
    page.player.seek(87_000)
    page.actions["game_start"].trigger()  # predicted from now on
    assert page.session.event(page.selected_id).side == "A"


def test_set_server_button(window):
    page = window.mark
    players = [page.session.mf.match["sides"][s]["players"][0] for s in "AB"]
    page.ask_server = lambda options: options[0]
    page.player.seek(10_000)
    page.buttons.buttons["set_server"].click()  # between games: a Set score with the server
    e = page.session.event(page.selected_id)
    assert (e.type, e.details) == ("score_state", {"server": players[0]})
    assert page.session.flow_at(11_000).next_server == "A"
    page.player.seek(12_000)
    page.actions["game_start"].trigger()
    start = page.selected_id
    count = len(page.session.mf.events)
    page.ask_server = lambda options: options[1]
    page.player.seek(13_000)
    page.buttons.buttons["set_server"].click()  # in a game: changes its Game start
    assert page.selected_id == start
    assert page.session.event(start).side == "B" and len(page.session.mf.events) == count
    page.ask_server = lambda options: None  # Esc: nothing changes
    page.buttons.buttons["set_server"].click()
    assert page.session.event(start).side == "B"


def test_set_score_dialog_shows_the_current_server(window):
    page = window.mark
    players = [page.session.mf.match["sides"][s]["players"][0] for s in "AB"]
    page.ask_server = lambda options: options[1]
    page.player.seek(10_000)
    page.buttons.buttons["set_server"].click()
    seen = []

    def ask(dialog):
        seen.append(dialog.server.currentData())
        return None

    page.ask_details = ask
    page.player.seek(11_000)
    page.actions["score_state"].trigger()
    assert seen == [players[1]]


def test_match_start_button_starts_at_zero(window):
    page = window.mark
    page.player.seek(500)
    page.buttons.buttons["match_start"].click()
    flow = page.session.flow_at(600)
    assert (flow.score.sets, flow.score.games, flow.score.points) == ((), (0, 0), (0, 0))
