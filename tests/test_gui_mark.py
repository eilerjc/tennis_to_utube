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
    assert "[←]" in page.transport.buttons["skip_back"].text()


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


def test_speed_buttons(window):
    page = window.mark
    page.transport.speed_buttons[0.5].click()
    assert page.player.speed() == 0.5 and page.transport.speed_buttons[0.5].isChecked()
    assert not page.transport.speed_buttons[1.0].isChecked()


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

    wheel(-120)  # wheel down = forward one frame
    assert page.position() == 10_010  # frame 600 starts at 10010.0
    page.player.set_paused(False)
    before = page.player._base_ms
    wheel(-120)
    assert page.player._base_ms == before  # ignored while playing
