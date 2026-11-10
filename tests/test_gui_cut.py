"""Cut only through the GUI: Files → Cut → Make video (offscreen, real ffmpeg, stand-in player)."""

import os
import time
from datetime import datetime

import pytest

from synth import make_clip, read_frames
from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui, pytest.mark.ffmpeg]


def pump(qapp, condition, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def window(qapp, config_dir):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), load_state(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    yield w
    w.cut.confirm_discard = lambda: True
    w.close()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "practice"
    d.mkdir()
    first = make_clip(d / "GX010042.MP4", 600, 0, data_track=False)  # ~10 s
    make_clip(d / "GX020042.MP4", 600, 600, data_track=False)
    # (synthetic clips carry no recording time: the output is named after the modified time)
    stamp = datetime(2026, 10, 4, 16, 52, 7).timestamp()
    os.utime(first, (stamp, stamp))
    return d


def test_files_cut_make(qapp, window, folder):
    w = window
    w.files.go_to(folder)
    pump(qapp, lambda: len(w.files._selected_infos()) == 2)
    assert w.files.cut_only() is not None
    cut = w.cut
    assert w.steps.currentWidget() is cut and w.steps.isTabVisible(w.CUT_TAB)
    assert not any(w.steps.isTabVisible(i) for i in w.MATCH_TABS)
    assert cut.cut_list.total_ms == 20_020
    assert cut.output_edit.text() == str(folder / "2026-10-04_165207.mp4")
    assert not cut.has_unmade_work()

    # I … O at the playhead; the timeline shades it
    cut.player.seek(3_000)
    cut.actions["cut_start"].trigger()
    assert cut.has_unmade_work() and "starts at 0:00:03.000" in cut.pending_label.text()
    cut.player.seek(6_000)
    assert cut.detail.cuts == [(3_000, 6_000)]  # shown while marking
    cut.actions["cut_end"].trigger()
    cut.player.seek(12_000)
    cut.start_button.click()
    cut.player.seek(14_000)
    cut.end_button.click()
    assert cut.cut_list.removals == [(3_000, 6_000), (12_000, 14_000)]
    assert cut.table.rowCount() == 2 and cut.table.item(1, 2).text() == "0:00:02.000"
    assert "Keeping 0:00:15.020 of 0:00:20.020" in cut.summary.text()
    # delete the one the playhead is in, undo it
    cut.player.seek(13_000)
    cut.actions["delete_event"].trigger()
    assert cut.cut_list.removals == [(3_000, 6_000)]
    cut.actions["undo"].trigger()
    assert len(cut.cut_list.removals) == 2
    # End removal with no start: from the beginning of the video
    cut.player.seek(1_000)
    cut.actions["cut_end"].trigger()
    assert cut.cut_list.removals[0] == (0, 1_000)
    cut.actions["undo"].trigger()
    # previous/next removal edge
    cut.player.seek(7_000)
    cut.actions["next_event"].trigger()
    assert cut.position() == 12_000

    cut.make()
    pump(qapp, lambda: cut.job is None)
    out = folder / "2026-10-04_165207.mp4"
    assert out.exists() and cut.status.text().startswith("Made 2026-10-04_165207.mp4")
    frames = [n for _, n in read_frames(out)]
    assert frames == [n for n in range(1200) if not (180 <= n < 300 or 720 <= n < 780)]
    assert not cut.has_unmade_work()  # made with these removals
    cut.player.seek(1_000)
    cut.actions["cut_start"].trigger()
    assert cut.has_unmade_work()


def test_making_again_asks_before_replacing(qapp, window, folder):
    w = window
    w.files.go_to(folder)
    pump(qapp, lambda: len(w.files._selected_infos()) == 2)
    w.files.cut_only()
    cut = w.cut
    out = folder / "2026-10-04_165207.mp4"
    out.write_bytes(b"old")
    asked = []
    cut.confirm_overwrite = lambda path: asked.append(path) or False
    cut.make()
    assert asked == [out] and cut.job is None and out.read_bytes() == b"old"
    cut.output_edit.setText(str(folder / "GX010042.MP4"))  # a source: refused
    cut.make()
    assert cut.job is None and "source videos" in cut.status.text()


def test_opening_a_match_asks_about_unmade_removals(qapp, window, folder, tmp_path):
    w = window
    w.files.go_to(folder)
    pump(qapp, lambda: len(w.files._selected_infos()) == 2)
    w.files.cut_only()
    w.cut.cut_list.add(1_000, 2_000)
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010042.MP4", 10_010, fps="60000/1001")])
    path = folder / "GX010042.match.json"
    matchfile.save(mf, path)
    w.cut.confirm_discard = lambda: False  # "No": stay in Cut only
    w.open_match(mf, path)
    assert w.steps.currentWidget() is w.cut and w.match_path is None
    w.cut.confirm_discard = lambda: True
    w.open_match(mf, path)
    assert w.steps.currentWidget() is w.mark
    assert all(w.steps.isTabVisible(i) for i in w.MATCH_TABS) and not w.steps.isTabVisible(w.CUT_TAB)


def test_cut_bat_opens_the_videos_in_order(qapp, window, folder):
    from tennis_to_utube.gui.app import open_cut_videos
    from tennis_to_utube.probe import Tools

    paths = [folder / "GX020042.MP4", folder / "GX010042.MP4"]  # dropped in any order
    assert open_cut_videos(window, paths, Tools())
    assert [i.path.name for i in window.cut.infos] == ["GX010042.MP4", "GX020042.MP4"]
    assert window.steps.currentWidget() is window.cut
