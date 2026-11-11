"""The uploader against a stand-in for YouTube (tests/fake_youtube.py): sign-in, the
resumable protocol under bad connections, and the queue."""

import copy
import json
import os
import sys
import time

import pytest

from fake_youtube import FakeYouTube
from tennis_to_utube.config import UPLOAD_DEFAULTS, Config
from tennis_to_utube.uploadqueue import (
    COMPLETE, FAILED, PAUSED, PROCESSING, QUEUED, UPLOADED, Manager, QueueBusy, Store,
    check_details, new_job,
)
from tennis_to_utube.ytapi import Auth, AuthError, AuthExpired, TokenStore, YouTube
from tennis_to_utube.ytupload import Hooks, Stopped, Upload, UploadFailed, chunk_bytes, parse_range

CHUNK = 256 * 1024


@pytest.fixture
def yt():
    server = FakeYouTube()
    yield server
    server.close()


def make_config(yt, **over):
    data = copy.deepcopy(UPLOAD_DEFAULTS)
    data["google"].update(yt.google())
    data.update(chunk_mb=0.25, parallel=2,
                retry={"first_s": 0.01, "max_s": 0.05, "timeout_s": 5.0},
                processing={"poll_s": 0.05})
    data.update(over)
    return Config(data)


@pytest.fixture
def config(yt):
    return make_config(yt)


@pytest.fixture
def auth(yt, config, tmp_path):
    a = Auth(config, TokenStore(tmp_path / "token.bin"))
    a.connect(open_browser=yt.browser, timeout_s=10)
    return a


def video(tmp_path, name="game.mp4", size=3 * CHUNK + 12_345):
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    return path


def puts(yt, data_only=True):
    """Content-Range of every upload PUT the server saw."""
    ranges = [h.get("Content-Range", "") for m, p, h in yt.requests
              if m == "PUT" and p.startswith("/upload/videos")]
    return [r for r in ranges if not (data_only and r.startswith("bytes */"))]


def sessions_opened(yt):
    return sum(m == "POST" and p.startswith("/upload/videos") for m, p, _ in yt.requests)


def wait_for(condition, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


# -- sign-in ---------------------------------------------------------------------------


def test_sign_in_token_storage_refresh_and_disconnect(yt, config, tmp_path):
    store = TokenStore(tmp_path / "token.bin")
    auth = Auth(config, store)
    assert auth.configured and not auth.connected
    with pytest.raises(AuthExpired):
        auth.access_token()
    auth.connect(open_browser=yt.browser, timeout_s=10)
    assert auth.connected
    # the consent request: offline access, PKCE, the configured scopes; never a password
    q = yt.last_auth_query
    assert q["access_type"] == "offline" and q["code_challenge_method"] == "S256"
    assert q["scope"].split() == config.get("google.scopes")
    assert q["redirect_uri"].startswith("http://127.0.0.1:")
    raw = store.path.read_bytes()
    if sys.platform == "win32":  # encrypted for the Windows user
        assert b"rt-" not in raw and b"refresh_token" not in raw
    assert store.load()["refresh_token"].startswith("rt-")
    # a new Auth (the app restarted) is still connected
    again = Auth(config, store)
    api = YouTube(config, again)
    assert api.channel() == {"id": "UC123", "title": "Test Channel"}
    assert [p["title"] for p in api.playlists()] == ["Basketball", "Matches"]
    # an expired access token is renewed without anyone noticing
    yt.expire_access_tokens()
    assert api.channel()["title"] == "Test Channel"
    # a withdrawn sign-in is reported as such
    yt.revoke_everything()
    with pytest.raises(AuthExpired):
        api.channel()
    again.disconnect()
    assert not store.path.exists() and not Auth(config, store).connected


def test_sign_in_refused_or_not_set_up(yt, config, tmp_path):
    auth = Auth(config, TokenStore(tmp_path / "token.bin"))
    yt.deny_sign_in = True
    with pytest.raises(AuthError, match="access_denied"):
        auth.connect(open_browser=yt.browser, timeout_s=10)
    assert not auth.connected
    with pytest.raises(AuthError, match="Timed out"):
        auth.connect(open_browser=lambda url: None, timeout_s=0.3)
    blank = make_config(yt)
    blank.data["google"]["client_id"] = ""
    with pytest.raises(AuthError, match="UPLOAD_SETUP"):
        Auth(blank, TokenStore(tmp_path / "t2.bin")).connect(open_browser=yt.browser)


# -- one upload ------------------------------------------------------------------------


def run_upload(config, auth, job, **hooks):
    retry = config.get("retry")
    return Upload(YouTube(config, auth), job, chunk_mb=config.get("chunk_mb"),
                  first_wait_s=retry["first_s"], max_wait_s=retry["max_s"],
                  timeout_s=retry["timeout_s"], hooks=Hooks(**hooks)).run()


def test_chunk_size_and_range_parsing():
    assert chunk_bytes(16) == 16 * 1024 * 1024 and chunk_bytes(0.3) == CHUNK
    assert chunk_bytes(0.01) == CHUNK and chunk_bytes(10.1) % CHUNK == 0
    assert parse_range("bytes=0-99") == 100 and parse_range(None) == 0


def test_upload_sends_the_file_and_its_details(yt, config, auth, tmp_path):
    path = video(tmp_path)
    job = new_job(path, "Hawks vs Owls - First Half", description="2026-11-07", tags=["hoops"],
                  privacy="unlisted", category="17", made_for_kids=False)
    sent = []
    result = run_upload(config, auth, job, sent=sent.append)
    assert yt.received(result["id"]) == path.read_bytes()
    assert sum(sent) == job.size == job.confirmed
    assert puts(yt) == [f"bytes 0-{CHUNK - 1}/{job.size}",
                        f"bytes {CHUNK}-{2 * CHUNK - 1}/{job.size}",
                        f"bytes {2 * CHUNK}-{3 * CHUNK - 1}/{job.size}",
                        f"bytes {3 * CHUNK}-{job.size - 1}/{job.size}"]
    body = yt.videos[result["id"]]["body"]
    assert body == {"snippet": {"title": "Hawks vs Owls - First Half", "description": "2026-11-07",
                                "categoryId": "17", "tags": ["hoops"]},
                    "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}}


def test_every_kind_of_interruption_resumes_from_what_youtube_has(yt, config, auth, tmp_path):
    path = video(tmp_path, size=6 * CHUNK + 777)
    job = new_job(path)
    yt.faults = [("drop_after", 1000),  # dies mid-chunk, nothing kept
                 ("status", 503),  # server error
                 ("keep_then_drop", 100_000),  # dies mid-chunk, part of it kept
                 ("drop_answer",),  # all kept but the answer is lost
                 ("status", 500), ("status", 502),  # twice in a row
                 ("partial", 5_000)]  # only part of a chunk confirmed
    yt.query_faults = [("status", 503), ("drop",)]  # even asking where it stands fails
    events = {"interrupted": [], "retrying": 0, "waited": 0.0, "status": []}

    def retrying():
        events["retrying"] += 1

    def waited(seconds):
        events["waited"] += seconds

    result = run_upload(config, auth, job, interrupted=events["interrupted"].append,
                        retrying=retrying, waited=waited, status=events["status"].append)
    assert yt.received(result["id"]) == path.read_bytes()  # byte for byte, nothing doubled
    assert sessions_opened(yt) == 1  # never started again
    assert not yt.faults and not yt.query_faults
    # after each failure it asked YouTube where it stood, and continued exactly there
    starts = [int(r.removeprefix("bytes ").split("-")[0]) for r in puts(yt)]
    assert starts.count(0) == 3 and 100_000 in starts  # nothing was kept the first two times
    assert len(events["interrupted"]) >= 5 and events["retrying"] == 8
    assert events["waited"] > 0
    assert {"Uploading", "Reconnecting", "Resuming"} <= set(events["status"])


def test_stop_and_resume_later_from_the_saved_checkpoint(yt, config, auth, tmp_path):
    path = video(tmp_path, size=8 * CHUNK)
    store = Store(tmp_path / "uploads")
    job = new_job(path)
    sent = []

    def sent_some(n):
        sent.append(n)

    with pytest.raises(Stopped) as stop:
        run_upload(config, auth, job, checkpoint=lambda: store.save(job), sent=sent_some,
                   should_stop=lambda: "stop" if sum(sent) >= 3 * CHUNK else None)
    assert stop.value.reason == "stop"
    # the app is closed; later a new one reads the queue from disk
    (saved,), _ = Store(tmp_path / "uploads").load_all()
    assert saved.session_uri and saved.confirmed == 3 * CHUNK and saved.id == job.id
    before = len(puts(yt))
    result = run_upload(config, auth, saved, checkpoint=lambda: store.save(saved))
    assert yt.received(result["id"]) == path.read_bytes()
    assert sessions_opened(yt) == 1
    resumed = puts(yt)[before:]
    assert resumed[0].startswith(f"bytes {3 * CHUNK}-")  # not from zero
    assert puts(yt, data_only=False)[before + 0] == f"bytes */{job.size}"  # asked first


def test_a_lost_session_or_changed_file_is_never_restarted_silently(yt, config, auth, tmp_path):
    path = video(tmp_path, size=4 * CHUNK)
    job = new_job(path)
    yt.faults = [(), ("lose",)]
    with pytest.raises(UploadFailed) as failed:
        run_upload(config, auth, job)
    assert failed.value.needs_restart and "cannot be resumed" in str(failed.value)
    assert sessions_opened(yt) == 1 and job.confirmed == CHUNK
    # the file changed after it was added
    other = new_job(video(tmp_path, "other.mp4"))
    with open(other.path, "ab") as fh:
        fh.write(b"more")
    with pytest.raises(UploadFailed, match="has changed") as failed:
        run_upload(config, auth, other)
    assert failed.value.needs_restart and sessions_opened(yt) == 1


def test_expired_and_withdrawn_sign_in(yt, config, auth, tmp_path):
    path = video(tmp_path, size=4 * CHUNK)
    job = new_job(path)
    sent = []

    def expire_once(n):
        sent.append(n)
        if len(sent) == 2:
            yt.expire_access_tokens()  # the hour is up mid-upload

    result = run_upload(config, auth, job, sent=expire_once)
    assert yt.received(result["id"]) == path.read_bytes() and sessions_opened(yt) == 1
    # withdrawn: the upload stops, keeps its place, and resumes after connecting again
    job2 = new_job(video(tmp_path, "two.mp4", size=4 * CHUNK))
    sent2 = []

    def revoke_once(n):
        sent2.append(n)
        if len(sent2) == 2:
            yt.revoke_everything()

    with pytest.raises(UploadFailed) as failed:
        run_upload(config, auth, job2, sent=revoke_once)
    assert failed.value.auth and not failed.value.needs_restart and "connect again" in str(failed.value)
    assert job2.session_uri and 0 < job2.confirmed < job2.size
    auth.connect(open_browser=yt.browser, timeout_s=10)
    result = run_upload(config, auth, job2)
    assert yt.received(result["id"]) == open(job2.path, "rb").read()
    assert sessions_opened(yt) == 2  # one per video


def test_quota_and_refusals_are_explained(yt, config, auth, tmp_path):
    job = new_job(video(tmp_path))
    yt.create_faults = [("status", 403, "quotaExceeded")]
    with pytest.raises(UploadFailed, match="daily upload limit") as failed:
        run_upload(config, auth, job)
    assert not failed.value.needs_restart
    yt.create_faults = [("status", 400, "invalidTitle")]
    with pytest.raises(UploadFailed, match="refused the upload.*invalidTitle"):
        run_upload(config, auth, job)
    yt.create_faults = [("status", 503), ("status", 403, "rateLimitExceeded")]  # temporary
    assert run_upload(config, auth, job)["id"]


def test_details_are_checked_against_youtubes_limits():
    assert check_details("Fine", "ok", ["a", "b c"]) == []
    assert "empty" in check_details("  ")[0]
    assert "100" in check_details("x" * 101)[0]
    assert "< or >" in check_details("a <b>")[0]
    assert "5000" in check_details("t", "é" * 2501)[0]
    assert "500" in check_details("t", "", ["x" * 250, "y" * 250])[0]


# -- the queue -------------------------------------------------------------------------


def manager(config, auth, tmp_path, **kwargs):
    return Manager(config, auth, Store(tmp_path / "uploads"), **kwargs).open()


def by_name(m):
    return {row["name"]: row for row in m.snapshot()}


def test_queue_uploads_two_at_a_time_then_follows_processing(yt, config, auth, tmp_path):
    awake = []
    yt.processing = [{"status": {"uploadStatus": "uploaded"},
                      "processingDetails": {"processingStatus": "processing",
                                            "processingProgress": {"partsTotal": 10,
                                                                   "partsProcessed": 4,
                                                                   "timeLeftMs": 180_000}}}] * 3
    yt.forced_privacy = "private"  # what YouTube does to uploads from an unaudited app
    yt.gate = __import__("threading").Event()
    m = manager(config, auth, tmp_path, keep_awake=awake.append)
    paths = [video(tmp_path, f"half{i}.mp4") for i in (1, 2, 3)]
    for i, p in enumerate(paths):
        m.add(new_job(p, f"Game - part {i + 1}", playlist="PL2" if i == 0 else ""))
    m.start()
    wait_for(lambda: sum(r["status"] == "Uploading" for r in m.snapshot()) == 2)
    rows = by_name(m)
    assert [rows[p.name]["status"] for p in paths] == ["Uploading", "Uploading", "Queued"]
    assert m.busy() and m.awake
    with pytest.raises(QueueBusy):  # a second window cannot run the same queue
        Manager(config, auth, Store(tmp_path / "uploads")).open()
    seen = set()
    yt.gate.set()

    def all_complete():
        rows = m.snapshot()
        seen.update((r["name"], r["status"], r["note"].split(" processed")[0]) for r in rows)
        return all(r["status"] == COMPLETE for r in rows)

    wait_for(all_complete)
    rows = by_name(m)
    for p in paths:
        row = rows[p.name]
        assert yt.received(row["video_id"]) == p.read_bytes()
        assert row["url"] == f"https://youtu.be/{row['video_id']}" and row["percent"] == 100
        assert row["transferred_at"] and row["completed_at"] >= row["transferred_at"]
        assert "made this video private, not unlisted" in row["note"]
    assert ("half1.mp4", PROCESSING, "40%") in seen  # transfer done is not "ready to watch"
    assert yt.playlist_items == [("PL2", rows["half1.mp4"]["video_id"])]
    wait_for(lambda: not m.awake)
    assert awake == [True, False]
    m.close()
    # the records are still there for the next start
    again = manager(config, auth, tmp_path)
    assert {r["status"] for r in again.snapshot()} == {COMPLETE}
    assert again.remove(next(iter(again.jobs)))  # hidden, not deleted
    assert len(again.snapshot()) == 2 and len(list((tmp_path / "uploads").glob("*.json"))) == 3
    again.close()


def test_queue_recovers_what_was_uploading_when_the_app_died(yt, config, auth, tmp_path):
    path = video(tmp_path, size=6 * CHUNK)
    store = Store(tmp_path / "uploads")
    job = new_job(path)
    sent = []
    with pytest.raises(Stopped):
        run_upload(config, auth, job, checkpoint=lambda: store.save(job), sent=sent.append,
                   should_stop=lambda: "x" if sum(sent) >= 2 * CHUNK else None)
    job.status = "Uploading"  # what the file said when the power went
    store.save(job)
    (tmp_path / "uploads" / "broken.json").write_text("{not json", encoding="utf-8")
    m = manager(config, auth, tmp_path)
    assert m.jobs[job.id].status == QUEUED and m.jobs[job.id].confirmed == 2 * CHUNK
    assert "broken.json" in m.warnings[0] and (tmp_path / "uploads" / "broken.json.bad").exists()
    m.start()
    wait_for(lambda: m.snapshot()[0]["status"] == COMPLETE)
    row = m.snapshot()[0]
    assert yt.received(row["video_id"]) == path.read_bytes() and sessions_opened(yt) == 1
    m.close()


def test_queue_pause_resume_fail_retry_restart(yt, config, auth, tmp_path):
    import threading

    path = video(tmp_path, size=6 * CHUNK)
    yt.gate = threading.Event()
    m = manager(config, auth, tmp_path)
    job = m.add(new_job(path))
    waiting = m.add(new_job(video(tmp_path, "later.mp4")))
    m.parallel = 1
    m.pause(waiting.id)
    assert m.jobs[waiting.id].status == PAUSED
    assert m.edit(waiting.id, title="Later") and m.jobs[waiting.id].title == "Later"
    m.start()
    wait_for(lambda: m.snapshot()[0]["running"])
    m.pause(job.id)
    yt.gate.set()
    wait_for(lambda: m.jobs[job.id].status == PAUSED)
    assert not m.uploading() and not m.edit(job.id, title="too late")  # the session is open
    at = m.jobs[job.id].confirmed
    time.sleep(0.2)
    assert m.jobs[job.id].confirmed == at and m.jobs[job.id].session_uri
    # the internet goes away entirely: it keeps trying, and says so
    yt.down = True
    m.resume(job.id)
    wait_for(lambda: m.jobs[job.id].status == "Reconnecting" and m.jobs[job.id].retries >= 3)
    row = m.snapshot()[0]
    assert row["interruptions"] == 1 and row["reconnect_s"] > 0 and "Connection problem" in row["note"]
    yt.down = False
    wait_for(lambda: m.jobs[job.id].status in (UPLOADED, PROCESSING, COMPLETE))
    assert yt.received(m.jobs[job.id].video_id) == path.read_bytes() and sessions_opened(yt) == 1
    # a lost session fails and waits for the user; Retry will not restart it, Restart does
    yt.faults = [(), ("lose",)]
    m.resume(waiting.id)
    wait_for(lambda: m.jobs[waiting.id].status == FAILED)
    failed = m.jobs[waiting.id]
    assert failed.needs_restart and failed.confirmed == CHUNK
    m.retry(waiting.id)
    assert m.jobs[waiting.id].status == FAILED and sessions_opened(yt) == 2
    m.restart(waiting.id)
    wait_for(lambda: m.jobs[waiting.id].status == COMPLETE)
    assert sessions_opened(yt) == 3
    assert yt.received(m.jobs[waiting.id].video_id) == open(waiting.path, "rb").read()
    m.close()


def test_queue_waits_for_a_sign_in_and_reports_processing_failures(yt, config, tmp_path):
    auth = Auth(config, TokenStore(tmp_path / "token.bin"))
    m = manager(config, auth, tmp_path)
    job = m.add(new_job(video(tmp_path)))
    m.start()
    time.sleep(0.3)
    assert m.jobs[job.id].status == QUEUED and not yt.sessions  # nothing without an account
    yt.processing = [{"status": {"uploadStatus": "rejected", "rejectionReason": "duplicate"}}]
    auth.connect(open_browser=yt.browser, timeout_s=10)
    wait_for(lambda: m.jobs[job.id].status == FAILED)
    assert "could not process the video (duplicate)" in m.jobs[job.id].error
    assert m.jobs[job.id].video_id  # the transfer itself was confirmed
    saved = json.loads((tmp_path / "uploads" / f"{job.id}.json").read_text(encoding="utf-8"))
    assert saved["status"] == FAILED and saved["video_id"] == m.jobs[job.id].video_id
    m.close()
