"""Cut only: the removal list (pure) and making the video (ffmpeg, synthetic footage)."""

import pytest

from synth import FRAME_MS, make_clip, read_frames
from tennis_to_utube.cutlist import CutList, default_output, make_video
from tennis_to_utube.probe import Tools, probe


def test_start_end_marks_a_removal():
    cl = CutList(60_000)
    assert cl.end(5_000) == (0, 5_000)  # no start: from the beginning of the video
    cl.delete(0)
    assert cl.end(0) is None  # nothing to remove
    cl.start(10_000)
    assert cl.end(20_000) == (10_000, 20_000) and cl.pending_start is None
    cl.start(40_000)
    assert cl.end(30_000) == (30_000, 40_000)  # marked backwards: swapped
    cl.start(50_000)
    assert cl.end(50_000) is None  # empty
    assert cl.removals == [(10_000, 20_000), (30_000, 40_000)]
    assert (cl.removed_ms, cl.kept_ms) == (20_000, 40_000)
    assert [(c.start_ms, c.end_ms, c.enabled) for c in cl.cuts()] == [
        (10_000, 20_000, True), (30_000, 40_000, True)]


def test_overlapping_and_touching_removals_merge_and_clamp():
    cl = CutList(60_000)
    cl.add(10_000, 20_000)
    cl.add(15_000, 25_000)
    assert cl.removals == [(10_000, 25_000)]
    cl.add(25_000, 30_000)  # touching
    assert cl.removals == [(10_000, 30_000)]
    cl.add(50_000, 99_000)  # past the end
    assert cl.removals == [(10_000, 30_000), (50_000, 60_000)]
    cl.add(0, 70_000)
    assert cl.removals == [(0, 60_000)] and cl.kept_ms == 0


def test_delete_undo_redo_and_lookup():
    cl = CutList(60_000)
    cl.add(10_000, 20_000)
    cl.add(30_000, 40_000)
    assert cl.index_at(35_000) == 1 and cl.index_at(25_000) is None
    assert cl.delete(0) == (10_000, 20_000)
    assert cl.undo() and cl.removals == [(10_000, 20_000), (30_000, 40_000)]
    assert cl.redo() and cl.removals == [(30_000, 40_000)]
    assert cl.undo() and cl.undo() and cl.undo()
    assert cl.removals == [] and not cl.undo()
    cl.add(1_000, 2_000)  # a new edit drops the redo history
    assert not cl.redo()


def test_default_output_is_named_after_the_recording_time(tmp_path):
    import dataclasses
    import os
    from datetime import datetime, timezone

    from tennis_to_utube.probe import MediaInfo

    video = tmp_path / "GX010042.MP4"
    video.write_bytes(b"")
    info = MediaInfo(video, 1000, "hevc", None, 320, 180, "60000/1001", "yuvj420p", 0, 1,
                     0, datetime(2026, 10, 4, 16, 52, 7, tzinfo=timezone.utc), None, None)
    # the camera's clock as written (GoPro: local time labelled UTC), not converted
    assert default_output(info) == tmp_path / "2026-10-04_165207.mp4"
    # no recording time: the file's modified time
    stamp = datetime(2026, 11, 9, 8, 5, 3).timestamp()
    os.utime(video, (stamp, stamp))
    no_time = dataclasses.replace(info, creation_time=None)
    assert default_output(no_time) == tmp_path / "2026-11-09_080503.mp4"


@pytest.mark.ffmpeg
def test_make_video_joins_and_removes(tmp_path):
    a = make_clip(tmp_path / "GX010042.MP4", 600, 0, data_track=False)  # ~10 s
    b = make_clip(tmp_path / "GX020042.MP4", 600, 600, data_track=False)
    infos = [probe(a), probe(b)]
    total = sum(i.duration_ms for i in infos)
    # just join
    joined = make_video(infos, CutList(total), tmp_path / "joined.mp4", Tools())
    assert abs(probe(joined.result.output).duration_ms - total) <= 20
    assert [n for _, n in read_frames(joined.result.output)] == list(range(1200))
    # remove 3-6 s of the first file and 12-14 s (in the second)
    cl = CutList(total)
    cl.add(3_000, 6_000)
    cl.add(12_000, 14_000)
    made = make_video(infos, cl, tmp_path / "cut.mp4", Tools())
    frames = [n for _, n in read_frames(made.result.output)]
    # A removal starts exactly at the first frame after its start (no B-frames: frame 180 at
    # 3.003 s); play resumes at the keyframe at or before its end (one every 60 frames:
    # frame 300 at 5.005 s). In the second file: frames 720 (12.012 s) to 780 (13.013 s).
    assert frames == [n for n in range(1200) if not (180 <= n < 300 or 720 <= n < 780)]
    assert round(180 * FRAME_MS) == 3003 and round(300 * FRAME_MS) == 5005
    assert "kept" in made.summary()


@pytest.mark.ffmpeg
def test_too_short_removals_are_reported_and_the_whole_video_is_refused(tmp_path):
    a = make_clip(tmp_path / "GX010042.MP4", 600, 0, data_track=False)  # keyframe every ~1 s
    infos = [probe(a)]
    total = infos[0].duration_ms
    cl = CutList(total)
    cl.add(3_100, 3_600)  # no keyframe inside: cannot be cut without re-encoding
    cl.add(6_000, 8_000)
    made = make_video(infos, cl, tmp_path / "cut.mp4", Tools())
    assert made.not_removed == [(3_100, 3_600)]
    assert "NOT removed" in made.warning() and "0:00:03.100" in made.warning()
    frames = [n for _, n in read_frames(made.result.output)]
    assert 200 in frames and 400 not in frames
    everything = CutList(total)
    everything.add(0, total)
    with pytest.raises(ValueError, match="whole video"):
        make_video(infos, everything, tmp_path / "none.mp4", Tools())
    assert not (tmp_path / "none.mp4").exists()
    assert make_video(infos, CutList(total), tmp_path / "join.mp4", Tools()).warning() == ""
