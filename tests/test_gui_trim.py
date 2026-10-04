"""Trim step end to end (offscreen GUI + real ffmpeg on synthetic footage)."""

import time

import pytest

from synth import FRAME_MS, make_clip
from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui, pytest.mark.ffmpeg]


def wait_job(qapp, page, timeout=60):
    """Until "Check exact cuts" or the Trim tool has finished."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if not page.busy():
            for _ in range(20):
                qapp.processEvents()
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def window(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import AppState
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    clip = make_clip(tmp_path / "GX010001.MP4", 1200, data_track=False)  # ~20 s
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", round(1200 * FRAME_MS),
                                                       fps="60000/1001")])
    mf.add_event(2_500, "match_start")
    mf.add_event(3_000, "game_start", side="A")
    mf.add_event(6_000, "game_end", result="A")
    mf.add_event(7_000, "note", note="in the changeover")
    mf.add_event(9_000, "game_start", side="B")
    mf.add_event(15_000, "match_end")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    w = MainWindow(load_config(), AppState(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    w.open_match(mf, path)
    w.steps.setCurrentWidget(w.trim)
    yield w, clip
    w.close()


def test_cut_list_and_untick(window):
    w, _ = window
    page = w.trim
    rows = [page.table.item(r, 1).text() for r in range(page.table.rowCount())]
    assert rows == ["Warm-up (before play starts)", "Changeover after game 1 (set 1)",
                    "After match end"]
    assert page.output.text().endswith("GX010001 trimmed.mp4")
    from PySide6.QtCore import Qt

    page.table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)
    assert [c.enabled for c in w.mark.session.cuts()] == [True, True, False]
    assert (15_000, w.mark.session.total_ms) not in w.mark.detail.cuts  # shading follows
    page.rules["warmup"].setChecked(False)
    assert page.table.rowCount() == 2 and w.mark.session.trim_rules()[0] == "changeovers"


def test_make_video_and_store_output(window, qapp):
    w, _ = window
    page = w.trim
    page.start_plan()
    wait_job(qapp, page)
    assert page.plan is not None and "Exact" in page.summary.text()
    page.start_make()
    wait_job(qapp, page, 120)
    out = w.mark.session.output_path()
    assert out is not None and out.exists() and out.name == "GX010001 trimmed.mp4"
    stored = matchfile.load(w.mark.session.path).output
    assert stored["path"] == "GX010001 trimmed.mp4" and len(stored["segments"]) == 2
    assert "Made" in page.result.text() and "inside removed footage" in page.result.text()
    plan = w.mark.session.output_plan()
    assert plan.remap(3_000) is not None and plan.remap(7_000) is None
    # the Trim tool wrote the trimmed video's chapters/links; its YouTube id goes here
    assert page.trimmed_box.isEnabled() and "links will have times only" in page.video_note.text()
    page.video.setText("https://youtu.be/abcdefghijk")
    page._video_entered()
    wait_job(qapp, page, 60)
    links = (w.mark.session.path.parent / "GX010001 trimmed links.md").read_text(encoding="utf-8")
    assert "https://youtu.be/abcdefghijk?t=" in links
    assert matchfile.load(w.mark.session.path).output["video_id"] == "abcdefghijk"
    assert w.mark.session.mf.video_id is None  # the full recording's id is separate


def test_cancel(window, qapp):
    w, _ = window
    page = w.trim
    page.start_make()
    page.cancel()
    wait_job(qapp, page, 120)
    assert page.result.text() == "Cancelled." and w.mark.session.mf.output is None
    assert not (w.mark.session.path.parent / "GX010001 trimmed.mp4").exists()
    assert page.make_button.isEnabled()
