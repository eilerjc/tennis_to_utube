"""The Trim tool (command line) on synthetic footage."""

import json
import subprocess

import pytest

from synth import FRAME_MS, make_clip
from tennis_to_utube import matchfile
from tennis_to_utube.trimtool import command, default_output, export_paths, main

pytestmark = pytest.mark.ffmpeg


@pytest.fixture
def match(tmp_path, config_dir):
    make_clip(tmp_path / "GX010001.MP4", 1200, data_track=False)  # ~20 s
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", round(1200 * FRAME_MS),
                                                       fps="60000/1001")])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    mf.add_event(2_500, "match_start")
    mf.add_event(3_000, "game_start", side="A", player="Emma")
    mf.add_event(6_000, "game_end", result="A")
    mf.add_event(7_000, "note", note="in the changeover")
    mf.add_event(9_000, "game_start", side="B", player="Sara")
    mf.add_event(15_000, "match_end")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    return path


def test_list_cuts(match, capsys):
    assert main([str(match), "--cuts"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("[x] 0:00:00–0:00:02") and len(out) == 3


def test_make_then_links(match, config_dir, capsys):
    (config_dir / "trim.toml").write_text('rules = ["warmup", "after_match"]\n'
                                          '[lead_in_ms]\ndefault = 1000\n', encoding="utf-8")
    assert main([str(match)]) == 0
    out = default_output(match)
    assert out.exists() and out.name == "GX010001 trimmed.mp4"
    stored = matchfile.load(match).output
    assert stored["path"] == out.name and stored["video_id"] is None
    assert len(stored["segments"]) == 1  # warm-up and after-match removed, changeover kept
    chapters, md, csv_path = export_paths(match)
    assert chapters.exists() and csv_path.exists()
    assert "youtu.be" not in md.read_text(encoding="utf-8")  # no video id yet: times only
    # after uploading: the trimmed video's own id
    assert main([str(match), "--links-only", "--video-id", "https://youtu.be/abcdefghijk"]) == 0
    assert matchfile.load(match).output["video_id"] == "abcdefghijk"
    text = md.read_text(encoding="utf-8")
    assert "https://youtu.be/abcdefghijk?t=" in text
    assert matchfile.load(match).youtube["video_id"] is None  # the full recording's is separate
    capsys.readouterr()


def test_links_only_needs_a_made_video(match, capsys):
    assert main([str(match), "--links-only"]) == 1
    assert "no trimmed video" in capsys.readouterr().err


def test_from_gui_protocol_and_cancel(match):
    cmd, env = command(match, "--from-gui")
    proc = subprocess.run(cmd, env=env, input="", capture_output=True, text=True, timeout=120)
    # end of input (GUI gone) means cancel
    assert proc.returncode == 2 and "CANCELLED" in proc.stdout
    assert not default_output(match).exists() and matchfile.load(match).output is None


def test_from_gui_result(match):
    cmd, env = command(match, "--from-gui")
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    lines = []
    for line in proc.stdout:  # stdin stays open: no cancel
        lines.append(line.rstrip("\n"))
    proc.stdin.close()
    assert proc.wait(timeout=120) == 0
    assert any(l.startswith("PROGRESS ") for l in lines)
    result = json.loads(next(l for l in lines if l.startswith("RESULT "))[7:])
    assert result["output"]["path"] == "GX010001 trimmed.mp4"
    assert matchfile.load(match).output is None  # the GUI stores it, not the tool
