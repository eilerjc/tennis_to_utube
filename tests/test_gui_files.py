"""Offscreen smoke tests of the Files step (the owner checks the real GUI on Windows)."""

import time
from pathlib import Path

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

    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), load_state(),
                   player_factory=lambda parent: NullPlayer("test", parent))
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
    assert [p.name for p in page.matches] == ["GX010008.match.json"]


def contents_names(page):
    return [page.contents.topLevelItem(i).text(0) for i in range(page.contents.topLevelItemCount())]


def contents_item(page, name):
    return next(page.contents.topLevelItem(i) for i in range(page.contents.topLevelItemCount())
                if page.contents.topLevelItem(i).text(0) == name)


def test_browsing_subfolders(qapp, window, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    root = tmp_path / "videos"
    for name in ("match 10", "match 2", ".hidden"):
        (root / name).mkdir(parents=True)
    (root / "match 2" / "GX010001.MP4").write_bytes(b"")
    (root / "match 2" / "GX010001.match.json").write_text("{}")
    (root / "loose.match.json").write_text("{}")
    page = window.files
    page.go_to(root)
    # subfolders first (natural order), then match files; what each folder holds, in the background
    assert contents_names(page) == ["match 2", "match 10", "loose.match.json"]
    assert [p.name for p in page.matches] == ["loose.match.json"]
    wait_for(qapp, lambda: contents_item(page, "match 10").text(1) != "…")
    assert contents_item(page, "match 2").text(1) == "1 video · 1 match"
    assert contents_item(page, "match 2").font(0).bold()
    assert contents_item(page, "match 10").text(1) == "empty"
    # double-click (or Enter) goes into a folder
    page.contents.itemActivated.emit(contents_item(page, "match 2"), 0)
    assert page.folder == (root / "match 2").resolve()
    assert page.tree.currentIndex().isValid()
    assert Path(page.fs_model.filePath(page.tree.currentIndex())) == page.folder
    # Up selects the folder we came from; Back/Forward
    page.go_up()
    assert page.folder == root.resolve() and page.contents.currentItem().text(0) == "match 2"
    page.go_back()
    assert page.folder == (root / "match 2").resolve()
    page.go_forward()
    assert page.folder == root.resolve() and not page.forward_button.isEnabled()
    # Enter on the selected folder
    page.contents.setCurrentItem(contents_item(page, "match 10"))
    page.open_button.click()
    assert page.folder == (root / "match 10").resolve()
    # a click in the folder tree opens that folder
    page._on_tree_clicked(page.fs_model.index(str(root / "match 2")))
    assert page.folder == (root / "match 2").resolve()
    # mouse back button
    QTest.mouseClick(page.contents.viewport(), Qt.MouseButton.BackButton)
    assert page.folder == (root / "match 10").resolve()


def test_quick_access_and_pins(qapp, window, tmp_path):
    root = tmp_path / "videos"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
    page = window.files
    page.go_to(root / "a")
    page.go_to(root / "b")
    page._toggle_favorite()
    assert "Unpin" in page.favorite_button.text()
    texts = [page.quick.item(i).text() for i in range(page.quick.count())]
    assert texts[:2] == ["Pinned", "★ b"] and texts[2:5] == ["Recent", "b", "a"]
    page._on_quick_clicked(page.quick.item(4))
    assert page.folder == (root / "a").resolve() and "Pin folder" in page.favorite_button.text()
    page._on_quick_clicked(page.quick.item(0))  # a heading: nothing happens
    assert page.folder == (root / "a").resolve()
    (root / "c" / "b").mkdir(parents=True)  # another "b": the parent tells them apart
    page.go_to(root / "c" / "b")
    assert page.quick.item(3).text() == "b  (c)" and page.quick.item(5).text() == "b  (videos)"
    page.go_to(root / "missing")
    assert "Not a folder" in page.notes.text() and page.folder == (root / "c" / "b").resolve()


def test_open_match_reports_missing_videos(qapp, window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 1000)])
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    window.files.open_match(path)
    assert "GX010001.MP4" in warnings[0] and window.match is not None


def test_window_is_not_maximized_and_remembers_its_place(qapp, config_dir):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    def make():
        return MainWindow(load_config(), load_state(),
                          player_factory=lambda parent: NullPlayer("test", parent))

    w = make()
    w.place_window()
    w.show()
    assert not w.isMaximized() and w.width() >= 1280
    w.setGeometry(40, 50, 1400, 900)
    w.close()
    assert load_state().extra["window"][2:] == [1400, 900]
    w2 = make()
    w2.place_window()
    assert (w2.geometry().width(), w2.geometry().height()) == (1400, 900)
    w2.state.extra["window"] = [99_999, 99_999, 1400, 900]  # off every screen: default place
    w2.place_window()
    assert w2.geometry().x() < 99_999
    w2.close()
