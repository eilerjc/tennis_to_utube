"""The whole workflow through the GUI: Files → Mark → Trim → Export (offscreen, real ffmpeg,
stand-in player)."""

import time

import pytest

from synth import make_clip
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


def test_files_mark_trim_export(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    folder = tmp_path / "2026-10-04"
    folder.mkdir()
    make_clip(folder / "GX010042.MP4", 1800, 0, data_track=False)  # ~30 s
    make_clip(folder / "GX020042.MP4", 1200, 1800, data_track=False)  # ~20 s
    w = MainWindow(load_config(), load_state(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    try:
        # 1 Files
        files = w.files
        files.go_to(folder)
        pump(qapp, lambda: len(files.infos) == 2)
        files.names["A"][0].setText("Emma Smith")
        files.names["B"][0].setText("Sara Jones")
        files.format.setCurrentIndex(files.format.findData("pro_set"))
        assert files.create_match() is not None
        assert w.steps.currentWidget() is w.mark

        # 2 Mark: warm-up, then a few games with keys
        mark = w.mark
        assert mark.player.duration_ms == 50_050

        def at(t, action):
            mark.player.seek(t)
            mark.actions[action].trigger()

        at(3_000, "match_start")
        t = 4_000
        for game in range(3):
            at(t, "game_start")
            for k in range(4):
                at(t + 1_000 * (k + 1), "point_a" if game != 1 else "point_b")
            at(t + 5_500, "game_end_a" if game != 1 else "game_end_b")
            t += 14_000  # changeovers after odd games are ~8 s of nothing
        at(45_000, "match_end")
        flow = mark.session.flow_at(45_000)
        assert flow.score.games == (2, 1)
        assert mark.lists.events_model.rowCount() == 3 * 6 + 2

        # 3 Trim
        w.steps.setCurrentWidget(w.trim)
        trim = w.trim
        assert trim.table.rowCount() == 3  # warm-up, changeover after game 1 (game 3 is last), after match
        trim.start_make()
        pump(qapp, lambda: not trim.busy() and "Made" in trim.result.text(), 120)
        out = w.mark.session.output_path()
        assert out.exists() and out.name == "GX010042 trimmed.mp4"
        assert (folder / "GX010042 trimmed chapters.txt").exists()  # written by the Trim tool

        # 4 Export
        w.steps.setCurrentWidget(w.export)
        export = w.export
        export.video.setText("dQw4w9WgXcQ")
        export.video.editingFinished.emit()
        md, _csv = export.save_links()
        text = md.read_text(encoding="utf-8")
        assert text.startswith("# GX010042\n\nEmma Smith vs Sara Jones\n")
        assert "?t=0) Match start" in text and "Game end — won by Sara Jones" in text
        assert "full recording" in export.note.text()  # the trimmed video's are on Trim

        # everything was saved in the match file
        w.mark.save()
        saved = matchfile.load(folder / "GX010042.match.json")
        assert saved.video_id == "dQw4w9WgXcQ" and saved.output["path"] == "GX010042 trimmed.mp4"
        assert len(saved.events) == 20 and saved.match["format"] == {"preset": "pro_set"}
    finally:
        w.close()
