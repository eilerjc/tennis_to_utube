"""End-to-end trim on synthetic footage matching the camera profile.

Three GoPro-style chapter files (HEVC, yuvj420p, 59.94 fps, closed GOP with a keyframe
every 60 frames, AAC, plus an extra track) are trimmed by stream copy, once as the owner's
camera records them (no B-frames: exact ends) and once with B-frames (ends snap to
keyframes). Every frame carries
its global frame number as a barcode, so the test checks exactly which source frames
end up in the output and that every remapped event time shows the same frame as the
original time did.
"""

import math
import subprocess
from fractions import Fraction

import pytest

from synth import FRAME_MS, frame_at, make_clip, read_frames
from tennis_to_utube.config import effective_settings, load_config
from tennis_to_utube.matchfile import MatchFile
from tennis_to_utube.probe import ProbeKeyframes, probe
from tennis_to_utube.sources import check_join_compatible, make_source, order_files
from tennis_to_utube.timeline import Timeline
from tennis_to_utube.trim import Cut, TrimError, plan_trim, propose_cuts, remap_events, run_trim
from tennis_to_utube.youtube import build_export

pytestmark = pytest.mark.ffmpeg

FRAMES = (1250, 1250, 700)  # ~20.85 s, 20.85 s, 11.68 s; not multiples of the GOP
FIRST = (0, 1250, 2500)  # global number of each file's first frame
DURATIONS = tuple(round(n * FRAME_MS) for n in FRAMES)  # (20854, 20854, 11678)

EVENTS = [  # (joined ms, type, fields)
    (1_000, "note", {"note": "warm-up"}),             # removed (warm-up)
    (2_500, "match_start", {}),
    (3_000, "game_start", {"player": "Emma"}),
    (8_000, "point", {"result": "A"}),
    (11_000, "game_end", {"result": "A"}),              # game 1 -> changeover
    (14_000, "good_recovery", {}),                    # removed (changeover)
    (17_508, "game_start", {"player": "Sara"}),
    (19_900, "ace", {}),
    (20_860, "note", {"note": "just after the file boundary"}),
    (26_500, "game_end", {"result": "B"}),             # game 2 -> no cut
    (27_200, "game_start", {"player": "Emma"}),
    (30_123, "point", {"result": "B"}),
    (39_000, "game_end", {"result": "A"}),              # game 3 -> changeover across files 2/3
    (40_500, "note", {"note": "between games"}),      # removed (changeover)
    (45_000, "game_start", {"player": "Sara"}),
    (47_321, "body_language", {}),
    (50_008, "match_end", {}),                        # rest removed
]
REMOVED_TYPES = ["note", "good_recovery", "note"]


def source_frame(t_ms: int) -> int:
    """Global frame number on screen at joined time ``t_ms`` in the source files."""
    tl = Timeline(DURATIONS)
    i, local = tl.locate(t_ms)
    return FIRST[i] + math.floor(Fraction(local) / FRAME_MS)


# Kept segments after snapping (joined ms). Starts always snap back to keyframes.
SEGMENTS = {
    # ends at the first frame after the requested end: 11011 (frame 660),
    # 39006 (file 2 frame 1088 at 18151.5), 50017 (file 3 frame 498 at 8308.3)
    "camera": [(2_002, 11_011), (17_017, 20_854 + 18_152), (41_708 + 3_003, 41_708 + 8_309)],
    # ends at the next keyframe
    "b_frames": [(2_002, 11_011), (17_017, 20_854 + 19_019), (41_708 + 3_003, 41_708 + 9_009)],
}


@pytest.fixture(scope="module", params=["camera", "b_frames"])
def footage(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(request.param)
    paths = [make_clip(d / f"GX{ch:02d}0042.MP4", n, first, b_frames=request.param == "b_frames")
             for ch, (n, first) in enumerate(zip(FRAMES, FIRST), start=1)]
    return request.param, d, paths


@pytest.fixture(scope="module")
def trimmed(footage):
    profile, d, paths = footage
    ordered = order_files(reversed(paths))
    infos = [probe(p) for p in ordered]
    match_path = d / "GX010042.match.json"
    mf = MatchFile(sources=[make_source(i, match_path) for i in infos])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    for t, type_, fields in EVENTS:
        mf.add_event(t, type_, **fields)
    tl = Timeline.from_sources(mf.sources)
    cuts = propose_cuts(mf.events, tl.total_ms)
    plan = plan_trim(tl, cuts, ProbeKeyframes(ordered, durations_ms=tl.durations_ms))
    result = run_trim(plan, ordered, d / "match.mp4")
    return {"profile": profile, "paths": ordered, "infos": infos, "mf": mf, "cuts": cuts, "result": result,
            "frames": read_frames(result.output)}


def test_event_times_are_clear_of_frame_boundaries():
    # Guards the frame checks below against sub-millisecond rounding.
    tl = Timeline(DURATIONS)
    for t, _, _ in EVENTS:
        _, local = tl.locate(t)
        pos = Fraction(local) / FRAME_MS
        assert (pos - math.floor(pos)) * FRAME_MS >= 2


def test_sources_ordered_probed_and_compatible(trimmed):
    assert [p.name for p in trimmed["paths"]] == ["GX010042.MP4", "GX020042.MP4", "GX030042.MP4"]
    assert tuple(i.duration_ms for i in trimmed["infos"]) == DURATIONS
    assert check_join_compatible(trimmed["infos"]) == []
    assert [s.path for s in trimmed["mf"].sources] == ["GX010042.MP4", "GX020042.MP4", "GX030042.MP4"]


def test_cuts_and_snapped_segments(trimmed):
    assert [(c.rule, c.start_ms, c.end_ms) for c in trimmed["cuts"]] == [
        ("warmup", 0, 2_500), ("changeovers", 11_000, 17_508),
        ("changeovers", 39_000, 45_000), ("after_match", 50_008, sum(DURATIONS))]
    plan = trimmed["result"].plan
    assert [(s.start_ms, s.end_ms) for s in plan.segments] == SEGMENTS[trimmed["profile"]]


def test_output_is_clean_stream_copy(trimmed):
    result = trimmed["result"]
    # Only audio nudges (info), where an AAC packet overlaps the previous piece's tail.
    assert result.issues
    assert all((i.code, i.severity) == ("audio_timestamp_adjusted", "info") for i in result.issues)
    assert all("stream 0:1" in i.message for i in result.issues)
    info = result.info
    assert (info.codec, info.fps, info.pix_fmt) == ("hevc", "60000/1001", "yuvj420p")
    assert info.stream_types == ("video", "audio")  # extra track dropped
    tag = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_tag_string", "-of", "csv=p=0", str(result.output)],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert tag == "hvc1"
    assert abs(info.duration_ms - result.plan.total_out_ms) <= 2


def test_output_has_exactly_the_kept_frames(trimmed):
    plan = trimmed["result"].plan
    expected, expected_pts = [], []
    for piece in plan.pieces:
        seg = plan.segment_at(plan.timeline.to_joined(piece.source_index, piece.inpoint_ms))
        out_base = plan.output_start_ms + seg.out_start_ms + (
            plan.timeline.to_joined(piece.source_index, piece.inpoint_ms) - seg.start_ms)
        k = math.ceil(Fraction(piece.inpoint_ms) / FRAME_MS)
        while math.ceil(k * FRAME_MS) < piece.end_ms:  # end_ms = first cut frame, rounded up
            expected.append(FIRST[piece.source_index] + k)
            expected_pts.append(out_base + k * FRAME_MS - piece.inpoint_ms)
            k += 1
    frames = trimmed["frames"]
    assert [n for _, n in frames] == expected  # no missing, extra, broken or reordered frames
    for (pts, _), want in zip(frames, expected_pts):
        assert abs(pts - want) < 1  # ms


def test_remapped_events_show_the_same_frame(trimmed):
    plan = trimmed["result"].plan
    kept, issues = remap_events(trimmed["mf"].events, plan)
    assert len(kept) == len(EVENTS) - len(REMOVED_TYPES)
    # Cross-check the analytic source frames against the source files themselves.
    src_frames = [read_frames(p) for p in trimmed["paths"]]
    for r in kept:
        t = r.event.t_ms
        i, local = plan.timeline.locate(t)
        assert frame_at(src_frames[i], local) == source_frame(t)
        assert frame_at(trimmed["frames"], r.out_ms) == source_frame(t), r.event


def test_removed_events_are_reported(trimmed):
    _, issues = remap_events(trimmed["mf"].events, trimmed["result"].plan)
    assert [i.code for i in issues] == ["event_in_removed_region"] * 3
    by_id = {e.id: e for e in trimmed["mf"].events}
    assert [by_id[i.event_id].type for i in issues] == REMOVED_TYPES


def test_audio_kept_alongside(trimmed):
    out = trimmed["result"].output
    dur = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert abs(float(dur) * 1000 - trimmed["result"].plan.total_out_ms) < 100


def test_export_from_trimmed_video(trimmed):
    mf, plan = trimmed["mf"], trimmed["result"].plan
    settings = effective_settings(load_config(None), {"lead_in_ms": {"default": 5000, "ace": 3000}})
    export = build_export(mf, plan, settings)
    # The video is ~22.7 s with game starts at ~0.4 s, 8.5 s, 18.3 s (output): too close
    # together for YouTube's 10 s rule, so they merge and fewer than 3 chapters remain.
    assert export.chapters == [] and export.description == ""
    assert [i.code for i in export.issues] == ["event_in_removed_region"] * 3 + ["too_few_chapters"]
    rows = {r.event.id: r for r in export.links}
    ace = next(e for e in mf.events if e.type == "ace")
    # 3 s before the ace is in removed footage: clamped to its segment's start (9.048 s)
    seg_start = plan.output_start_ms + plan.segment_at(ace.t_ms).out_start_ms
    assert plan.remap(ace.t_ms) - 3000 < seg_start
    assert rows[ace.id].seconds == seg_start // 1000 == 9
    first_game = next(e for e in mf.events if e.type == "game_start")
    assert rows[first_game.id].seconds == 0  # lead-in clamped to the start of the video


def test_open_gop_footage_is_refused(tmp_path):
    clip = make_clip(tmp_path / "GX010001.MP4", 300, open_gop=True, b_frames=True, data_track=False)
    tl = Timeline((round(300 * FRAME_MS),))
    run_trim(plan_trim(tl, [], None), [clip], tmp_path / "join_ok.mp4")  # plain copy is fine
    plan = plan_trim(tl, [Cut("x", "test", 1500, 3500, "x")],
                     ProbeKeyframes([clip], durations_ms=tl.durations_ms))
    assert not plan.is_identity
    with pytest.raises(TrimError, match="open GOP"):
        run_trim(plan, [clip], tmp_path / "cut.mp4")


def test_progress_and_cancel(tmp_path):
    import threading

    clip = make_clip(tmp_path / "GX010001.MP4", 600, data_track=False)
    tl = Timeline((round(600 * FRAME_MS),))
    plan = plan_trim(tl, [Cut("x", "test", 2_000, 4_000, "x")],
                     ProbeKeyframes([clip], durations_ms=tl.durations_ms))
    seen = []
    result = run_trim(plan, [clip], tmp_path / "out.mp4", progress=seen.append)
    assert seen and seen == sorted(seen) and seen[-1] > 0.9 and result.output.exists()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TrimError, match="cancelled"):
        run_trim(plan, [clip], tmp_path / "cancelled.mp4", cancel=cancel)
    assert not (tmp_path / "cancelled.mp4").exists()
