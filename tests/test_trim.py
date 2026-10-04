from fractions import Fraction

import pytest

from tennis_to_utube.matchfile import Event
from tennis_to_utube.probe import Keyframe, Tools
from tennis_to_utube.timeline import Interval, Timeline
from tennis_to_utube.trim import (
    Cut, ListKeyframes, Piece, TrimError, concat_script, ffmpeg_command, plan_trim, propose_cuts,
    remap_events,
)

GOP = Fraction(1001)  # keyframe every 60 frames at 59.94 fps = 1001 ms
B_DELAY = Fraction(1001, 30)  # decode times lead display times by 2 frames


def ev(t, type, id=None, **kw):
    return Event(id or f"{type}@{t}", t, type, **kw)


def cut(start, end, rule="test"):
    return Cut(f"{rule}:{start}", rule, start, end, rule)


FRAME = Fraction(1001, 60)


def kfs(*durations):
    return ListKeyframes.regular(durations, GOP, B_DELAY)


def exact(*durations):
    """No B-frames (the owner's camera): every frame is a clean end point."""
    return ListKeyframes.regular(durations, GOP, 0, frame_ms=FRAME)


# -- removal rules ---------------------------------------------------------------

MATCH = [
    ev(1_000, "note"),
    ev(60_000, "match_start"),
    ev(62_000, "set_start"),
    ev(63_000, "game_start"),
    ev(200_000, "game_end", result="A"),       # game 1 -> changeover
    ev(290_000, "game_start"),
    ev(400_000, "game_end", result="B"),      # game 2 -> no changeover
    ev(410_000, "game_start"),
    ev(500_000, "game_end", result="A"),       # game 3 -> changeover
    ev(590_000, "game_start"),
    ev(700_000, "game_end", result="A"),       # game 4
    ev(702_000, "set_end", result="A"),        # set break
    ev(820_000, "game_start"),     # set 2 begins without a Set start event
    ev(900_000, "game_end", result="A"),       # set 2 game 1 -> changeover
    ev(960_000, "game_start"),
    ev(1_000_000, "match_end"),
]


def test_propose_cuts_all_rules():
    cuts = propose_cuts(MATCH, 1_100_000)
    assert [(c.rule, c.start_ms, c.end_ms) for c in cuts] == [
        ("warmup", 0, 60_000),
        ("changeovers", 200_000, 290_000),
        ("changeovers", 500_000, 590_000),
        ("set_breaks", 702_000, 820_000),
        ("changeovers", 900_000, 960_000),
        ("after_match", 1_000_000, 1_100_000),
    ]
    assert cuts[1].label == "Changeover after game 1 (set 1)"
    assert cuts[2].label == "Changeover after game 3 (set 1)"
    assert cuts[3].label == "Set break after set 1"
    assert cuts[4].label == "Changeover after game 1 (set 2)"
    assert all(c.enabled for c in cuts)
    # Keys are stable (rule + anchoring event) so unticked cuts can be remembered.
    assert cuts[1].key == "changeovers:game_end@200000"
    assert propose_cuts(list(reversed(MATCH)), 1_100_000) == cuts


def test_propose_cuts_selected_rules_only():
    cuts = propose_cuts(MATCH, 1_100_000, rules=["warmup", "after_match"])
    assert [c.rule for c in cuts] == ["warmup", "after_match"]
    with pytest.raises(ValueError):
        propose_cuts(MATCH, 1, rules=["lunch"])


def test_warmup_starts_at_first_play_event():
    assert propose_cuts([ev(5000, "game_start")], 9000, ["warmup"])[0].end_ms == 5000
    assert propose_cuts([ev(0, "match_start")], 9000, ["warmup"]) == []
    assert propose_cuts([ev(500, "note")], 9000, ["warmup"]) == []


def test_mid_match_start_without_games_suspends_changeovers_until_next_set():
    events = [
        ev(0, "score_state"),                    # video starts mid-match, games unknown
        ev(10_000, "game_end", result="A"),      # parity unknown
        ev(20_000, "game_start"),
        ev(30_000, "game_end", result="B"),
        ev(40_000, "set_end", result="A"),
        ev(50_000, "set_start"),
        ev(51_000, "game_start"),
        ev(60_000, "game_end", result="A"),      # first game of the next set -> changeover
        ev(70_000, "game_start"),
    ]
    cuts = propose_cuts(events, 80_000, ["changeovers", "set_breaks"])
    assert [(c.rule, c.start_ms, c.label) for c in cuts] == [
        ("set_breaks", 40_000, "Set break"),
        ("changeovers", 60_000, "Changeover after game 1"),
    ]


def test_set_score_gives_changeover_parity_immediately():
    events = [
        ev(0, "score_state", details={"sets": [[6, 4]], "games": [3, 2]}),
        ev(5_000, "game_start"),                 # game 6 of set 2
        ev(10_000, "game_end", result="A"),      # 6 games closed -> no changeover
        ev(20_000, "game_start"),
        ev(30_000, "game_end", result="B"),      # game 7 -> changeover
        ev(40_000, "game_start"),
    ]
    cuts = propose_cuts(events, 50_000, ["changeovers"])
    assert [(c.start_ms, c.label) for c in cuts] == [(30_000, "Changeover after game 7 (set 2)")]


def test_set_score_corrects_the_count_mid_set():
    events = [
        ev(0, "game_start"),
        ev(10_000, "game_end", result="A"),      # game 1 -> changeover
        ev(20_000, "game_start"),
        # a game was missed while marking; the user enters the real score 2-1
        ev(25_000, "score_state", details={"games": [2, 1]}),
        ev(30_000, "game_start"),
        ev(40_000, "game_end", result="A"),      # game 4 -> no changeover (would be 3 without)
        ev(50_000, "game_start"),
    ]
    cuts = propose_cuts(events, 60_000, ["changeovers"])
    assert [c.start_ms for c in cuts] == [10_000]


def test_cut_without_following_game_start_is_not_proposed():
    assert propose_cuts([ev(0, "game_start"), ev(10, "game_end", result="A")], 100, ["changeovers"]) == []


# -- planning ----------------------------------------------------------------------


def test_no_cuts_is_a_plain_join():
    tl = Timeline((20_854, 20_854, 11_678))
    plan = plan_trim(tl, [], None)
    assert plan.is_identity and plan.total_out_ms == tl.total_ms
    assert plan.pieces == (Piece(0, 0, 20_854, 20_854), Piece(1, 0, 20_854, 20_854),
                           Piece(2, 0, 11_678, 11_678))
    assert plan.remap(30_000) == 30_000


def test_starts_snap_back_and_ends_snap_forward():
    tl = Timeline((60_000,))
    plan = plan_trim(tl, [cut(10_000, 20_000)], kfs(60_000))
    # kept [0, 10000) ends at the first keyframe after 10000 -> 10010 (dts 9976)
    # kept [20000, 60000) starts at the keyframe at or before 20000 -> 19019
    assert [(s.start_ms, s.end_ms, s.out_start_ms) for s in plan.segments] == [
        (0, 10_010, 0), (19_019, 60_000, 10_010)]
    assert plan.pieces == (Piece(0, 0, 9_976, 10_010), Piece(0, 19_019, 60_000, 60_000))
    assert plan.removed == [Interval(10_010, 19_019)]
    assert plan.total_out_ms == 10_010 + 40_981


def test_snapping_never_loses_requested_footage():
    tl = Timeline((60_000,))
    for start, end in [(5_005, 8_008), (5_004, 8_009), (5_006, 8_007), (1, 59_999)]:
        plan = plan_trim(tl, [cut(0, start), cut(end, 60_000)], kfs(60_000))
        (seg,) = plan.segments
        assert seg.start_ms <= start and seg.end_ms > end
        assert start - seg.start_ms < 1001 and seg.end_ms - end <= 1001


def test_exact_ends_without_b_frames():
    tl = Timeline((60_000,))
    plan = plan_trim(tl, [cut(10_100, 20_000)], exact(60_000))
    # ends at the first frame shown after 10100: frame 606 at 10110.1 ms; start still
    # snaps back to the keyframe at 19019
    assert plan.pieces == (Piece(0, 0, 10_110, 10_111), Piece(0, 19_019, 60_000, 60_000))
    for end in (5_004, 5_005, 7_777, 33_333):
        (seg, _) = plan_trim(tl, [cut(end, 50_000)], exact(60_000)).segments
        assert end < seg.end_ms <= end + 17


def test_short_removal_disappears_after_snapping():
    tl = Timeline((60_000,))
    plan = plan_trim(tl, [cut(10_100, 10_500)], kfs(60_000))
    assert plan.is_identity and plan.removed == []


def test_pieces_split_at_file_boundaries():
    tl = Timeline((20_854, 20_854, 11_678))
    # keep [2500, 11000) and [17500, 39000) and [45000, 50000)
    cuts = [cut(0, 2_500), cut(11_000, 17_500), cut(39_000, 45_000), cut(50_000, tl.total_ms)]
    plan = plan_trim(tl, cuts, kfs(*tl.durations_ms))
    assert [(s.start_ms, s.end_ms) for s in plan.segments] == [
        (2_002, 11_011), (17_017, 20_854 + 19_019), (41_708 + 3_003, 41_708 + 9_009)]
    assert [(p.source_index, p.inpoint_ms, p.end_ms) for p in plan.pieces] == [
        (0, 2_002, 11_011), (0, 17_017, 20_854), (1, 0, 19_019), (2, 3_003, 9_009)]
    assert plan.pieces[1].outpoint_ms == 20_854  # to end of file
    assert plan.pieces[2].outpoint_ms == 18_985  # floor(19019 - 33.37)
    assert plan.total_out_ms == sum(p.duration_ms for p in plan.pieces)


def test_cut_exactly_at_file_boundary():
    tl = Timeline((10_000, 10_000))
    plan = plan_trim(tl, [cut(10_000, 15_000)], kfs(10_000, 10_000))
    assert [(p.source_index, p.inpoint_ms, p.end_ms) for p in plan.pieces] == [
        (0, 0, 10_000), (1, 4_004, 10_000)]
    plan = plan_trim(tl, [cut(5_000, 10_000)], kfs(10_000, 10_000))
    assert [(p.source_index, p.inpoint_ms, p.end_ms) for p in plan.pieces] == [
        (0, 0, 5_005), (1, 0, 10_000)]


def test_cut_at_end_and_start_need_no_extra_keyframes():
    tl = Timeline((10_000,))
    plan = plan_trim(tl, [cut(0, 3_000), cut(9_500, 10_000)], kfs(10_000))
    # no keyframe after 9500 within the file -> runs to the end of the file
    assert [(p.inpoint_ms, p.outpoint_ms, p.end_ms) for p in plan.pieces] == [(2_002, 10_000, 10_000)]


def test_removing_everything_is_an_error():
    with pytest.raises(TrimError):
        plan_trim(Timeline((1000,)), [cut(0, 1000)], kfs(1000))


def test_disabled_cuts_are_ignored_but_kept_in_plan():
    tl = Timeline((60_000,))
    off = Cut("x", "test", 10_000, 20_000, "x", enabled=False)
    plan = plan_trim(tl, [off], kfs(60_000))
    assert plan.is_identity and plan.cuts == (off,)


def test_irregular_keyframes():
    tl = Timeline((10_000,))
    lookup = ListKeyframes([[Keyframe(0, 0), Keyframe(3_337, 3_300), Keyframe(7_000, 6_950)]])
    plan = plan_trim(tl, [cut(1_000, 9_000)], lookup)
    assert [(p.inpoint_ms, p.outpoint_ms, p.end_ms) for p in plan.pieces] == [
        (0, 3_300, 3_337), (7_000, 10_000, 10_000)]
    # kept [0, 4000) snaps to end 7000 and [6000, ...) to start 3337: they overlap
    # after snapping, so they merge and nothing is removed
    plan = plan_trim(tl, [cut(4_000, 6_000)], lookup)
    assert plan.is_identity


# -- remap and lead-in ------------------------------------------------------------


def test_remap_events_and_issues():
    tl = Timeline((60_000,))
    plan = plan_trim(tl, [cut(10_000, 20_000)], kfs(60_000))
    events = [ev(9_000, "point"), ev(10_005, "game_end", result="A"), ev(15_000, "note"),
              ev(19_019, "game_start"), ev(25_000, "ace"), ev(60_000, "match_end"),
              ev(70_000, "note")]
    kept, issues = remap_events(events, plan)
    assert [(r.event.type, r.out_ms, r.segment_out_ms) for r in kept] == [
        ("point", 9_000, 0), ("game_end", 10_005, 0), ("game_start", 10_010, 10_010),
        ("ace", 10_010 + 5_981, 10_010), ("match_end", 50_991, 10_010)]
    assert [(i.code, i.event_id) for i in issues] == [
        ("event_in_removed_region", "note@15000"), ("event_outside_video", "note@70000")]
    assert plan.remap(10_010) is None  # first removed ms


def test_lead_in_clamped_to_segment_start():
    tl = Timeline((60_000,))
    plan = plan_trim(tl, [cut(0, 30_000)], kfs(60_000))
    kept, _ = remap_events([ev(30_500, "game_start"), ev(40_000, "ace")], plan)
    seg_start = plan.segments[0].start_ms  # 29029
    assert kept[0].out_ms == 30_500 - seg_start
    assert kept[0].link_ms(5_000) == 0  # would reach into removed footage
    assert kept[1].link_ms(3_000) == 40_000 - seg_start - 3_000
    # second segment: clamp to its start in the output, not to 0
    plan = plan_trim(tl, [cut(10_000, 30_000)], kfs(60_000))
    kept, _ = remap_events([ev(30_500, "game_start")], plan)
    assert kept[0].link_ms(5_000) == plan.segments[1].out_start_ms == 10_010


def test_output_start_offset_shifts_everything():
    import dataclasses

    tl = Timeline((60_000,))
    plan = dataclasses.replace(plan_trim(tl, [cut(10_000, 20_000)], kfs(60_000)), output_start_ms=39)
    assert plan.remap(25_000) == 39 + 10_010 + 5_981
    kept, _ = remap_events([ev(19_500, "game_start")], plan)
    assert kept[0].link_ms(5_000) == 39 + 10_010


# -- ffmpeg plan --------------------------------------------------------------------


def test_concat_script(tmp_path):
    tl = Timeline((60_000, 5_000))
    plan = plan_trim(tl, [cut(10_000, 20_000)], kfs(60_000, 5_000))
    a, b = tmp_path / "GX010008.MP4", tmp_path / "it's here" / "GX020008.MP4"
    text = concat_script(plan, [a, b])
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1:5] == [f"file '{a.resolve().as_posix()}'", "inpoint 0.000", "outpoint 9.976",
                          "duration 10.010"]
    assert lines[5:9] == [f"file '{a.resolve().as_posix()}'", "inpoint 19.019", "outpoint 60.000",
                          "duration 40.981"]
    assert lines[9].endswith("/it'\\''s here/GX020008.MP4'")
    assert lines[10:13] == ["inpoint 0.000", "outpoint 5.000", "duration 5.000"]
    with pytest.raises(ValueError):
        concat_script(plan, [a])


def test_ffmpeg_command_is_stream_copy():
    cmd = ffmpeg_command(Tools(ffmpeg="ffmpeg.exe"), "list.ffconcat", "out.mp4")
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg.exe"
    for part in ("-f concat -safe 0 -i list.ffconcat", "-map 0:v:0 -map 0:a?", "-c copy",
                 "-tag:v hvc1", "-movflags +faststart"):
        assert part in joined
    assert cmd[-1] == "out.mp4"
    assert "-c:v" not in cmd and "libx265" not in joined
    assert "-tag:v" not in ffmpeg_command(Tools(), "l", "o", video_codec="h264")
