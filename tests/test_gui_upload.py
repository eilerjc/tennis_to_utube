"""The Upload step through the GUI (offscreen) against the stand-in YouTube."""

import copy
import os
import threading
import time

import pytest

from fake_youtube import FakeYouTube
from tennis_to_utube import matchfile
from tennis_to_utube.config import UPLOAD_DEFAULTS, Config
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = pytest.mark.gui

CHUNK = 256 * 1024


def pump(qapp, condition, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def yt():
    server = FakeYouTube()
    yield server
    server.close()


@pytest.fixture
def upload_config(yt):
    data = copy.deepcopy(UPLOAD_DEFAULTS)
    data["google"].update(yt.google())
    data.update(chunk_mb=0.25, retry={"first_s": 0.01, "max_s": 0.05, "timeout_s": 5.0},
                processing={"poll_s": 0.05})
    return Config(data)


@pytest.fixture
def window(qapp, config_dir, yt, upload_config):
    from tennis_to_utube.appstate import load_state
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), load_state(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent),
                   upload_config=upload_config)
    w.upload.open_browser = yt.browser
    yield w
    w.cut.confirm_discard = lambda: True
    w.upload.confirm_close = lambda: True
    w.close()


def video(folder, name, size=3 * CHUNK + 999):
    path = folder / name
    path.write_bytes(os.urandom(size))
    return path


def connect(qapp, w):
    w.upload.connect_account()
    pump(qapp, lambda: w.upload.channel is not None)


def test_dialog_names_a_game_and_insists_on_made_for_kids(qapp, tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox

    from tennis_to_utube.gui.upload_page import UploadDialog

    paths = [video(tmp_path, f"GX01004{i}.MP4", 10) for i in (1, 2, 3)]
    defaults = {"privacy": "unlisted", "category": "17", "tags": [], "playlist": "",
                "made_for_kids": None}
    d = UploadDialog(paths, defaults, [{"id": "PL2", "title": "Basketball"}], UploadDialog.GAME)
    ok = d.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and "made for kids" in d.problems.text()  # never guessed
    d.team_a.setText("Hawks")
    d.team_b.setText("Owls")
    d.date.setText("2026-11-07")
    d.kids_no.setChecked(True)
    assert ok.isEnabled()
    details = d.details()
    assert [x["title"] for x in details] == ["Hawks vs Owls - First Half",
                                             "Hawks vs Owls - Second Half",
                                             "Hawks vs Owls - Overtime"]
    assert details[1]["description"] == "Hawks vs Owls\n2026-11-07\nSecond Half"
    assert {(x["privacy"], x["category"], x["made_for_kids"]) for x in details} == {
        ("unlisted", "17", False)}
    # a title typed by hand stays when the teams change; the others follow
    d.table.item(2, 2).setText("Hawks vs Owls - OT thriller")
    d.team_a.setText("Falcons")
    d.description.setPlainText("League game")
    d.playlist.setCurrentIndex(d.playlist.findData("PL2"))
    details = d.details()
    assert [x["title"] for x in details] == ["Falcons vs Owls - First Half",
                                             "Falcons vs Owls - Second Half",
                                             "Hawks vs Owls - OT thriller"]
    assert details[0]["description"].endswith("First Half\n\nLeague game")
    assert details[0]["playlist"] == "PL2"
    d.table.item(0, 2).setText("x" * 101)
    assert not ok.isEnabled() and "100" in d.problems.text()
    # plain: the file names
    plain = UploadDialog(paths[:1], {**defaults, "made_for_kids": False}, [], UploadDialog.PLAIN)
    assert plain.details()[0]["title"] == "GX010041" and plain.table.isColumnHidden(1)
    assert plain.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_connect_queue_a_game_and_watch_it_finish(qapp, window, yt, tmp_path):
    w, page = window, window.upload
    assert "Not connected" in page.account.text() and not page.disconnect_button.isEnabled()
    connect(qapp, w)
    assert "Connected to <b>Test Channel</b>" in page.account.text()
    assert [p["title"] for p in page.playlists] == ["Basketball", "Matches"]

    paths = [video(tmp_path, "half1.mp4"), video(tmp_path, "half2.mp4")]
    asked = []

    def fill(dialog):
        asked.append(dialog)
        dialog.team_a.setText("Hawks")
        dialog.team_b.setText("Owls")
        dialog.date.setText("2026-11-07")
        dialog.kids_no.setChecked(True)
        return True

    page.ask = fill
    yt.gate = threading.Event()
    yt.processing = [{"status": {"uploadStatus": "uploaded"},
                      "processingDetails": {"processingStatus": "processing"}}] * 2
    ids = page.add_videos(paths, preset="game")
    assert len(ids) == 2 and w.upload.manager.busy()
    assert page.add_videos(paths, preset="game") == [] and len(asked) == 1  # not twice
    pump(qapp, lambda: page.table.rowCount() == 2 and page.table.item(0, 7).text() == "Uploading")
    assert page.table.item(0, 1).text() == "Hawks vs Owls - First Half"
    assert page.pause_button.isEnabled() and not page.resume_button.isEnabled()
    # closing while uploading asks first
    page.confirm_close = lambda: False
    assert not w.close() and page.manager is not None
    yt.gate.set()
    seen = set()

    def finished():
        page.refresh()
        seen.update(page.table.item(r, 7).text() for r in range(page.table.rowCount()))
        return all(r["status"] == "Complete" for r in page.rows)

    pump(qapp, finished)
    assert "Processing" in seen  # the transfer being done is not the same as ready
    for r, path in enumerate(paths):
        video_id = page.rows[r]["video_id"]
        assert yt.received(video_id) == path.read_bytes()
        assert page.table.item(r, 3).text() == "100.0%"
        assert page.table.item(r, 9).text() == f"https://youtu.be/{video_id}"
    body = yt.videos[page.rows[1]["video_id"]]["body"]
    assert body["snippet"]["title"] == "Hawks vs Owls - Second Half"
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    page.table.selectRow(1)
    assert "finished processing" in page.detail.text() and "2160p" in page.detail.text()
    assert "Connection interruptions" in page.diagnostics.text()
    opened = []
    page.open_browser = opened.append
    page.open_selected()
    assert opened == [page.rows[1]["url"]]
    # removed from the list, the record stays on disk
    page.remove_selected()
    assert page.table.rowCount() == 1
    assert len(list((page.manager.store.folder).glob("*.json"))) == 2
    page.disconnect_account()
    assert "Not connected" in page.account.text()


def test_failed_upload_is_explained_and_only_restarts_on_request(qapp, window, yt, tmp_path):
    w, page = window, window.upload
    connect(qapp, w)
    page.ask = lambda dialog: dialog.kids_no.setChecked(True) or True
    yt.faults = [(), ("lose",)]
    page.add_videos([video(tmp_path, "clip.mp4", 5 * CHUNK)])
    pump(qapp, lambda: page.rows and page.rows[0]["status"] == "Failed")
    assert "cannot be resumed" in page.detail.text() and "never done without you" in page.detail.text()
    assert page.restart_button.isEnabled() and not page.retry_button.isEnabled()
    answers = []
    page.confirm = lambda title, text: answers.append(title) or False
    page.restart_selected()
    qapp.processEvents()
    assert answers == ["Restart from the beginning?"] and page.rows[0]["status"] == "Failed"
    page.confirm = lambda title, text: True
    page.restart_selected()
    pump(qapp, lambda: page.rows[0]["status"] == "Complete")
    assert yt.received(page.rows[0]["video_id"]) == (tmp_path / "clip.mp4").read_bytes()


def test_uploading_a_matchs_recording_fills_in_its_link(qapp, window, yt, tmp_path):
    w, page = window, window.upload
    connect(qapp, w)
    folder = tmp_path / "match"
    folder.mkdir()
    video(folder, "GX010042.MP4")
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010042.MP4", 10_010, fps="60000/1001")])
    mf.match["sides"]["A"]["players"] = ["Ann Lee"]
    mf.match["sides"]["B"]["players"] = ["Bea Roy"]
    path = folder / "GX010042.match.json"
    matchfile.save(mf, path)
    w.open_match(mf, path)
    dialogs = []

    def fill(dialog):
        dialogs.append((dialog.mode(), dialog.details()[0]["title"], dialog.description.toPlainText()))
        dialog.kids_no.setChecked(True)
        return True

    page.ask = fill
    assert len(w.upload_recording()) == 1 and w.steps.currentWidget() is page
    mode, title, description = dialogs[0]
    assert mode == "match" and title.startswith("Ann Lee vs Bea Roy - 20")
    assert description.startswith("Ann Lee vs Bea Roy")
    pump(qapp, lambda: page.rows and page.rows[0]["link_done"])
    video_id = page.rows[0]["video_id"]
    assert w.match.video_id == video_id and w.export.video.text() == video_id
    w.mark.save()
    assert matchfile.load(path).video_id == video_id
    # a match that is not open gets the link written into its file
    other = folder / "other.match.json"
    matchfile.save(matchfile.MatchFile(sources=[matchfile.Source("GX010042.MP4", 10_010)]), other)
    video(folder, "second.mp4")
    page.add_videos([folder / "second.mp4"], link={"match": str(other), "target": "full"})
    pump(qapp, lambda: len(page.rows) == 2 and page.rows[1]["link_done"])
    assert matchfile.load(other).video_id == page.rows[1]["video_id"]


def test_files_and_cut_send_videos_to_upload(qapp, window, yt, tmp_path):
    w = window
    folder = tmp_path / "practice"
    folder.mkdir()
    a = video(folder, "GX010042.MP4")
    seen = []
    w.upload.add_videos = lambda paths, **kw: seen.append(list(paths)) or []
    w.files.go_to(folder)
    pump(qapp, lambda: w.files.order == [a])
    w.files.ticked = {a}
    assert w.files.upload_ticked() == [a]
    assert seen == [[a]] and w.steps.currentWidget() is w.upload
    assert not w.cut.upload_button.isEnabled()  # nothing made yet
    w.cut.made_path = a
    w.cut.upload_button.setEnabled(True)
    w.cut.upload_button.click()
    assert seen[-1] == [a]
