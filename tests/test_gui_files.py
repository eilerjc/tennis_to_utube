"""Offscreen smoke tests of the Files step (the owner checks the real GUI on Windows)."""

import time

import pytest

from synth import make_clip
from tennis_to_utube import matchfile

pytestmark = [pytest.mark.gui]


def wait_for(qapp, condition, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def window(qapp, config_dir):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow

    w = MainWindow(load_config(), load_state())
    yield w
    w.close()


@pytest.mark.ffmpeg
def test_create_match_from_folder(qapp, window, tmp_path, config_dir):
    folder = tmp_path / "2026-09-28"
    folder.mkdir()
    make_clip(folder / "GX020008.MP4", 60, data_track=False)
    make_clip(folder / "GX010008.MP4", 60, data_track=False)
    make_clip(folder / "GX010009.MP4", 60, data_track=False)  # another recording
    page = window.files
    page.go_to(folder)
    assert [p.name for p in page.order] == ["GX010008.MP4", "GX020008.MP4", "GX010009.MP4"]
    assert {p.name for p in page.ticked} == {"GX010008.MP4", "GX020008.MP4"}
    assert "2 recordings" in page.notes.text()
    wait_for(qapp, lambda: len(page.infos) == 3)
    assert page.table.item(0, 2).text() == "0:00:01"
    assert page.table.item(0, 5).text() == "OK" and page.table.item(2, 5).text() == ""
    page.names["A"][0].setText("Emma")
    page.no_ad.setChecked(True)
    opened = []
    page.matchReady.connect(lambda mf, path: opened.append(path))
    path = page.create_match()
    assert path == folder / "GX010008.match.json" and opened == [path]
    mf = matchfile.load(path)
    assert [s.path for s in mf.sources] == ["GX010008.MP4", "GX020008.MP4"]
    assert mf.match["sides"]["A"]["players"] == ["Emma"]
    assert mf.match["format"] == {"preset": "standard_mtb", "ad": False}
    assert window.steps.currentIndex() == 1 and window.steps.isTabEnabled(3)
    assert "GX010008.match.json" in window.windowTitle()
    # remembered for next time
    assert (config_dir / "state.json").exists() and window.state.last_folder == str(folder)
    # the existing match is offered when the folder is opened again
    page.go_to(folder)
    assert page.matches.count() == 1


def test_navigation_and_pins(qapp, window, tmp_path):
    root = tmp_path / "videos"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
    page = window.files
    page.go_to(root / "b")
    assert [page.siblings.itemText(i) for i in range(page.siblings.count())] == ["a", "b", "c"]
    assert page.siblings.currentText() == "b"
    page._toggle_favorite()
    assert page.favorites.count() == 1 and "Unpin" in page.favorite_button.text()
    page.up_button.click()
    assert page.folder == root.resolve()
    assert page.recent.itemText(0) == "videos"
    page.go_to(root / "missing")
    assert "Not a folder" in page.notes.text()


def test_open_match_reports_missing_videos(qapp, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 1000)])
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    window.files.open_match(path)
    assert "GX010001.MP4" in warnings[0] and window.match is not None
