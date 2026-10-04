"""Export step (offscreen)."""

import pytest

from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui]


@pytest.fixture
def window(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import AppState
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 900_000, fps="60000/1001")])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    for t in (30_000, 200_000, 400_000, 600_000):
        mf.add_event(t, "game_start", side="A", player="Emma")
        mf.add_event(t + 40_000, "point", result="A", tags=["close"])
        mf.add_event(t + 90_000, "game_end", result="A")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    w = MainWindow(load_config(), AppState(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    w.open_match(mf, path)
    w.steps.setCurrentWidget(w.export)
    yield w
    w.close()


def test_chapters_and_links_without_video_id(window):
    page = window.export
    text = page.chapters.toPlainText()
    assert text.startswith("0:00 ") and "Set 1 · Game 2 (1–0) — Emma serving" in text
    assert "original recording" in page.note.text()
    assert "- 1:05 Point — won by Emma [close]" in page.links.toPlainText()  # 70 s - 5 s lead
    assert "times only" in page.video_note.text()


def test_video_id_makes_links_and_is_saved(window, qapp):
    page = window.export
    page.video.setText("https://youtu.be/dQw4w9WgXcQ")
    page.video.editingFinished.emit()
    assert window.match.video_id == "dQw4w9WgXcQ"
    assert "https://youtu.be/dQw4w9WgXcQ?t=65" in page.links.toPlainText()
    md, csv = page.save_links()
    assert md.name == "GX010001 links.md" and "youtu.be" in md.read_text(encoding="utf-8")
    assert csv.read_text(encoding="utf-8-sig").startswith("chapter,time,seconds")
    page.video.setText("not a link")
    page.video.editingFinished.emit()
    assert window.match.video_id == "dQw4w9WgXcQ" and "not a YouTube" in page.video_note.text()


def test_copy_chapters(window):
    from PySide6.QtGui import QGuiApplication

    window.export.copy_chapters()
    assert QGuiApplication.clipboard().text().startswith("0:00 ")


def test_uses_the_made_video(window):
    from tennis_to_utube.trim import Cut, ListKeyframes, plan_to_dict, plan_trim

    s = window.mark.session
    plan = plan_trim(s.timeline(), [Cut("x", "test", 0, 29_000, "x")],
                     ListKeyframes.regular([900_000], 1001))
    s.set_output(plan_to_dict(plan, "GX010001 trimmed.mp4"))
    window.export.refresh()
    assert "GX010001 trimmed.mp4" in window.export.note.text()
    # the cut snaps back to the keyframe at 28.028 s: 70 - 28.028 - 5 = 36.97 s
    assert "- 0:36 Point" in window.export.links.toPlainText()
