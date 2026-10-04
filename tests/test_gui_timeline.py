"""Timeline bars driven with simulated mouse input (offscreen)."""

import pytest

from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui]


@pytest.fixture
def page(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import AppState
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), AppState(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    w.resize(1600, 1000)
    w.show()
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 600_000, fps="60000/1001")])
    mf.add_event(10_000, "match_start")
    mf.add_event(20_000, "game_start", side="A")
    mf.add_event(60_000, "point", result="A")
    mf.add_event(100_000, "game_end", result="A")
    mf.add_event(190_000, "game_start", side="B")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    w.open_match(mf, path)
    qapp.processEvents()
    yield w.mark
    w.close()


def x_of(bar, t):
    from PySide6.QtCore import QPoint

    return QPoint(round(bar.view.x_of(t, bar.width())), bar.height() // 2)


def test_bars_show_events_bands_and_cuts(page):
    assert len(page.detail.events) == 5
    assert [(b.kind, b.start_ms, b.end_ms) for b in page.overview.bands] == [
        ("game", 20_000, 100_000), ("game", 190_000, 600_000), ("set", 20_000, 600_000)]
    assert (0, 10_000) in page.detail.cuts and (100_000, 190_000) in page.detail.cuts
    assert page.overview.view.span_ms == 600_000 and page.detail.view.span_ms == 120_000


def test_click_tick_selects_and_jumps(page, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    bar = page.detail
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, x_of(bar, 60_000))
    point = next(e for e in page.match.events if e.type == "point")
    assert page.selected_id == point.id and bar.selected == point.id
    assert abs(page.position() - 60_000) < 20
    assert page.match.event(point.id).t_ms == 60_000  # a click never moves


def test_drag_scrubs(page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    bar = page.overview
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, x_of(bar, 300_000))
    QTest.mouseMove(bar, x_of(bar, 400_000))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, x_of(bar, 450_000))
    assert abs(page.position() - 450_000) < 1500  # one pixel is ~400 ms here


def test_ctrl_drag_moves_and_escape_cancels(page, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    bar = page.detail
    point = next(e for e in page.match.events if e.type == "point")
    ctrl = Qt.KeyboardModifier.ControlModifier
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 60_000))
    QTest.mouseMove(bar, x_of(bar, 70_000))
    QTest.keyClick(bar, Qt.Key.Key_Escape)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 70_000))
    assert page.match.event(point.id).t_ms == 60_000
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 60_000))
    QTest.mouseMove(bar, x_of(bar, 70_000))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 70_000))
    assert abs(page.match.event(point.id).t_ms - 70_000) < 200
    page.actions["undo"].trigger()
    assert page.match.event(point.id).t_ms == 60_000
    page.actions["lock_events"].trigger()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 60_000))
    QTest.mouseMove(bar, x_of(bar, 80_000))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, ctrl, x_of(bar, 80_000))
    assert page.match.event(point.id).t_ms == 60_000  # locked


def test_wheel_zooms_detail_and_follow(page):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    bar = page.detail
    pos = QPointF(bar.view.x_of(60_000, bar.width()), 10)
    ev = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 240), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    bar.wheelEvent(ev)
    assert bar.view.span_ms == pytest.approx(120_000 / 1.25 ** 2)
    assert page.overview.window_marker[1] - page.overview.window_marker[0] == pytest.approx(bar.view.span_ms)
    page.player.seek(400_000)
    assert bar.view.start_ms <= 400_000 <= bar.view.start_ms + bar.view.span_ms
