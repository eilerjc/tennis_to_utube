"""The Upload tool (command line), the presets and upload.toml."""

import copy
import os

import pytest

from fake_youtube import FakeYouTube
from synth import FRAME_MS, make_clip
from tennis_to_utube import matchfile, uploadpresets, uploadtool
from tennis_to_utube.config import UPLOAD_DEFAULTS, Config, load_upload_config
from tennis_to_utube.uploadqueue import new_job


@pytest.fixture
def yt():
    server = FakeYouTube()
    yield server
    server.close()


@pytest.fixture
def tool(yt, config_dir, monkeypatch):
    """The tool's ``main`` with upload.toml pointing at the stand-in and a stand-in browser."""
    data = copy.deepcopy(UPLOAD_DEFAULTS)
    data["google"].update(yt.google())
    data.update(chunk_mb=0.25, retry={"first_s": 0.01, "max_s": 0.05, "timeout_s": 5.0},
                processing={"poll_s": 0.05})
    monkeypatch.setattr(uploadtool, "load_upload_config", lambda: Config(data))
    monkeypatch.setattr("webbrowser.open", yt.browser)
    return uploadtool.main


def video(folder, name, size=300_000):
    path = folder / name
    path.write_bytes(os.urandom(size))
    return path


def test_connect_upload_a_game_and_status(tool, yt, tmp_path, capsys):
    a, b = video(tmp_path, "h1.mp4"), video(tmp_path, "h2.mp4")
    assert tool([str(a)]) == 2 and "--kids" in capsys.readouterr().err  # never guessed
    assert tool([str(a), "--kids", "no"]) == 1
    assert "--connect" in capsys.readouterr().err  # queued, but nobody is signed in
    assert tool(["--connect"]) == 0 and "Connected to Test Channel" in capsys.readouterr().out
    assert tool([str(a), str(b), "--game", "Hawks", "Owls", "--date", "2026-11-07", "--kids",
                 "no", "--tags", "hoops, u14"]) == 0
    out = capsys.readouterr().out
    assert "Queued h2.mp4 as 'Hawks vs Owls - Second Half'" in out and "Complete" in out
    titles = sorted(v["body"]["snippet"]["title"] for v in yt.videos.values())
    assert titles == ["Hawks vs Owls - First Half", "Hawks vs Owls - Second Half", "h1"]
    game = next(v for v in yt.videos.values() if v["body"]["snippet"]["title"].endswith("Second Half"))
    assert bytes(game["data"]) == b.read_bytes()
    assert game["body"]["snippet"]["tags"] == ["hoops", "u14"]
    assert game["body"]["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    assert tool(["--status"]) == 0
    status = capsys.readouterr().out
    assert status.count("Complete") == 3 and "https://youtu.be/vid" in status
    assert tool([str(tmp_path / "nope.mp4"), "--kids", "no"]) == 2
    assert tool(["--disconnect"]) == 0
    capsys.readouterr()


def test_presets():
    assert uploadpresets.game_parts(1) == ["Full Game"]
    assert uploadpresets.game_parts(2) == ["First Half", "Second Half"]
    assert uploadpresets.game_parts(4) == ["First Half", "Second Half", "Overtime", "Overtime 2"]
    assert uploadpresets.game_title(" Hawks ", "Owls", "First Half") == "Hawks vs Owls - First Half"
    assert uploadpresets.game_title("", "", "") == "Team A vs Team B"
    assert len(uploadpresets.game_title("H" * 80, "O" * 80, "First Half")) == 100
    assert uploadpresets.plain_title("some_dir/2026-10-04_165207.mp4") == "2026-10-04 165207"
    match = uploadpresets.match_details("Ann vs Bea: 6-3, 6-4 — Ann won", "2026-10-04",
                                        "0:00 Warm-up\n1:10 Set 1\n", trimmed=True)
    assert match["title"] == "Ann vs Bea - 2026-10-04"
    assert match["description"] == ("Ann vs Bea: 6-3, 6-4 — Ann won\n2026-10-04\n"
                                    "Trimmed: warm-up and breaks removed.\n\n0:00 Warm-up\n1:10 Set 1")
    assert "<" not in uploadpresets.fit_title("a <b> c") and uploadpresets.clean("x>y") == "x›y"
    assert uploadtool.speed_text(1_250_000) == "10.0 Mbps"
    assert uploadtool.size_text(20 * 2 ** 30) == "20.00 GB" and uploadtool.size_text(900) == "900 bytes"
    assert uploadtool.duration_text(3725) == "1:02:05" and uploadtool.duration_text(None) == "—"


def test_upload_toml(config_dir):
    config = load_upload_config()
    assert uploadtool.default_details(config) == {
        "privacy": "unlisted", "category": "17", "tags": [], "playlist": "", "made_for_kids": None}
    assert config.get("parallel") == 2 and config.get("chunk_mb") == 16
    (config_dir / "upload.toml").write_text(
        'chunk_mb = 8\nparallel = 1\n[google]\nclient_id = "abc.apps.googleusercontent.com"\n'
        '[defaults]\nprivacy = "nonsense"\nmade_for_kids = "no"\ntags = ["tennis"]\n',
        encoding="utf-8")
    config = load_upload_config()
    assert not config.warnings and config.get("chunk_mb") == 8 and config.get("parallel") == 1
    assert config.get("google.token_uri").startswith("https://oauth2.googleapis.com")
    details = uploadtool.default_details(config)
    assert details["privacy"] == "unlisted" and details["made_for_kids"] is False
    assert details["tags"] == ["tennis"]


@pytest.mark.ffmpeg
def test_a_trimmed_videos_link_goes_into_its_match(tmp_path, config_dir):
    from tennis_to_utube import trimtool

    make_clip(tmp_path / "GX010001.MP4", 1200, data_track=False)
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", round(1200 * FRAME_MS),
                                                       fps="60000/1001")])
    mf.add_event(2_500, "match_start")
    mf.add_event(3_000, "game_start", side="A", player="Player 1")
    mf.add_event(6_000, "game_end", result="A")
    mf.add_event(15_000, "match_end")
    match = tmp_path / "GX010001.match.json"
    matchfile.save(mf, match)
    assert trimtool.main([str(match)]) == 0
    made = trimtool.default_output(match)
    job = new_job(made, link={"match": str(match), "target": "trimmed"})
    assert uploadtool.apply_link(job) is None  # no video id yet
    job.video_id = "abcdefghijk"
    assert "links rewritten" in uploadtool.apply_link(job)
    assert matchfile.load(match).output["video_id"] == "abcdefghijk"
    assert matchfile.load(match).youtube["video_id"] is None
    assert "https://youtu.be/abcdefghijk?t=" in trimtool.export_paths(match)[1].read_text(encoding="utf-8")
    # the video was made again since: an upload of the old one must not claim it
    stale = new_job(made, link={"match": str(match), "target": "trimmed"})
    stale.video_id, stale.mtime_ns = "zzzzzzzzzzz", stale.mtime_ns - 1
    assert uploadtool.apply_link(stale) is None
    assert matchfile.load(match).output["video_id"] == "abcdefghijk"
    # the full recording
    full = new_job(tmp_path / "GX010001.MP4", link={"match": str(match), "target": "full"})
    full.video_id = "fullfullful"
    assert "full recording" in uploadtool.apply_link(full)
    assert matchfile.load(match).youtube["video_id"] == "fullfullful"
