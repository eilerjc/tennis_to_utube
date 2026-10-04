import pytest

from synth import make_clip
from tennis_to_utube.probe import ProbeKeyframes, ToolError, Tools, inspect_gop, list_keyframes, probe

pytestmark = pytest.mark.ffmpeg


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("probe")
    return {
        "closed": make_clip(d / "GX010001.MP4", 400, b_frames=True),
        "camera": make_clip(d / "GX010002.MP4", 400),  # no B-frames, like the real camera
        "open": make_clip(d / "open.MP4", 400, open_gop=True, b_frames=True, data_track=False),
    }


def test_probe_matches_camera_profile(clips):
    info = probe(clips["closed"])
    assert info.codec == "hevc"
    assert info.fps == "60000/1001"
    assert info.pix_fmt == "yuvj420p"
    assert (info.width, info.height) == (320, 180)
    assert info.duration_ms == round(400 * 1001 / 60)  # 6673
    assert info.has_b_frames > 0
    assert info.audio.codec == "aac" and info.audio.sample_rate == 48000
    assert info.stream_types == ("video", "audio", "subtitle")
    assert info.start_ms == 0


def test_list_keyframes(clips):
    kfs = list_keyframes(clips["closed"])
    assert [k.pts_ms for k in kfs] == [0, 1001, 2002, 3003, 4004, 5005, 6006]
    # decode times run ahead of display times by the B-frame delay (2 frames here)
    assert all(k.pts_ms - 34 <= k.dts_ms < k.pts_ms for k in kfs)
    assert [k.pts_ms for k in list_keyframes(clips["closed"], start_ms=2500, end_ms=4100)] == [2002, 3003, 4004]


def test_probe_keyframes_lookup(clips):
    lk = ProbeKeyframes([clips["closed"]], durations_ms=[6673], window_ms=500)
    assert lk.at_or_before(0, 2500).pts_ms == 2002
    assert lk.at_or_before(0, 2002).pts_ms == 2002
    assert lk.at_or_before(0, 10).pts_ms == 0
    assert lk.after(0, 2002).pts_ms == 3003
    assert lk.after(0, 2003).pts_ms == 3003
    assert lk.after(0, 6100) is None
    assert lk.end_after(0, 2003).pts_ms == 3003  # B-frames: ends only at keyframes


def test_exact_end_points_without_b_frames(clips):
    info = probe(clips["camera"])
    assert info.has_b_frames == 0
    lk = ProbeKeyframes([clips["camera"]], durations_ms=[info.duration_ms])
    assert lk.exact_ends(0)
    # first frame shown after 2003 ms: frame 121 at 2018.68 ms
    end = lk.end_after(0, 2003)
    assert (end.pts_ms, end.dts_ms) == (2019, 2018)
    assert lk.end_after(0, 2019).pts_ms == 2036  # frame 122 at 2035.37 ms
    assert lk.end_after(0, 6660) is None  # last frame starts at 6656.65 ms
    assert lk.at_or_before(0, 2500).pts_ms == 2002  # starts still need keyframes


def test_gop_inspection(clips):
    closed = inspect_gop(clips["closed"])
    assert closed.closed and closed.b_frames and closed.keyframe_interval_ms == 1001
    camera = inspect_gop(clips["camera"])
    assert camera.closed and not camera.b_frames
    assert not inspect_gop(clips["open"]).closed


def test_missing_tool_reports_clearly(clips):
    with pytest.raises(ToolError, match="not found"):
        probe(clips["closed"], Tools(ffprobe="no-such-ffprobe"))
