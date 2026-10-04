from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from tennis_to_utube.probe import AudioInfo, MediaInfo
from tennis_to_utube.sources import (
    GoProName, check_creation_order, check_join_compatible, group_by_recording, make_source,
    order_files, parse_gopro_name,
)


def info(name, created=None, **over):
    base = dict(path=Path(name), duration_ms=1000, codec="hevc", profile="Main", width=3840,
                height=2160, fps="60000/1001", pix_fmt="yuvj420p", has_b_frames=2,
                time_base=Fraction(1, 60000), start_ms=0, creation_time=created, size_bytes=1,
                audio=AudioInfo("aac", 48000, 2))
    base.update(over)
    return MediaInfo(**base)


def test_parse_gopro_name():
    assert parse_gopro_name("GX010008.MP4") == GoProName("X", 1, 8)
    assert parse_gopro_name("C:/videos/gx020123.mp4") == GoProName("X", 2, 123)
    assert parse_gopro_name("GH031234.MP4") == GoProName("H", 3, 1234)
    for name in ("GX01008.MP4", "GX010008.LRV", "video.mp4", "GX010008.MP4.bak"):
        assert parse_gopro_name(name) is None


def test_order_by_recording_then_chapter():
    names = ["GX020008.MP4", "GX010009.MP4", "GX030008.MP4", "GX010008.MP4", "GX020007.MP4",
             "GX010007.MP4", "clip10.mp4", "clip9.mp4"]
    # Alphabetical would put GX010009 before GX020007; that is wrong.
    assert [p.name for p in order_files(names)] == [
        "GX010007.MP4", "GX020007.MP4", "GX010008.MP4", "GX020008.MP4", "GX030008.MP4",
        "GX010009.MP4", "clip9.mp4", "clip10.mp4"]


def test_group_by_recording():
    groups = group_by_recording(["GX020008.MP4", "GX010009.MP4", "GX010008.MP4", "notes.mp4"])
    assert {k: [p.name for p in v] for k, v in groups.items()} == {
        8: ["GX010008.MP4", "GX020008.MP4"], 9: ["GX010009.MP4"], None: ["notes.mp4"]}
    assert list(groups) == [8, 9, None]


def test_creation_order_disagreement_flagged():
    t = lambda m: datetime(2026, 5, 1, 16, m, tzinfo=timezone.utc)  # noqa: E731
    ok = [info("GX010008.MP4", t(15)), info("GX020008.MP4", t(52)), info("GX030008.MP4", t(52))]
    assert check_creation_order(ok) == []
    bad = [info("GX010008.MP4", t(52)), info("GX020008.MP4", t(15)), info("GX030008.MP4", None)]
    issues = check_creation_order(bad)
    assert len(issues) == 1 and "GX020008.MP4" in issues[0].message


def test_join_compatibility():
    files = [info("a.MP4"), info("b.MP4"), info("c.MP4", width=1920, pix_fmt="yuv420p"),
             info("d.MP4", audio=None)]
    issues = check_join_compatible(files)
    assert [i.message.split()[0] for i in issues] == ["c.MP4", "d.MP4"]
    assert "width" in issues[0].message and "pix_fmt" in issues[0].message
    assert all(i.severity == "error" for i in issues)


def test_make_source(tmp_path):
    src = make_source(info(str(tmp_path / "GX010008.MP4")), tmp_path / "GX010008.match.json")
    assert src.path == "GX010008.MP4" and src.duration_ms == 1000 and src.fps == "60000/1001"


def test_folder_listing(tmp_path):
    from tennis_to_utube.sources import list_matches, list_videos, sibling_folders

    for name in ("GX020008.MP4", "GX010008.MP4", "clip.mov", "notes.txt", ".hidden.mp4",
                 "GX010008.match.json"):
        (tmp_path / name).write_text("x")
    (tmp_path / "sub.mp4").mkdir()
    assert [p.name for p in list_videos(tmp_path)] == ["GX010008.MP4", "GX020008.MP4", "clip.mov"]
    assert [p.name for p in list_matches(tmp_path)] == ["GX010008.match.json"]
    for name in ("2026-09-28", "2026-10-02", "match 10", "match 9", ".git"):
        (tmp_path / "parent" / name).mkdir(parents=True)
    sib = sibling_folders(tmp_path / "parent" / "match 9")
    assert [p.name for p in sib] == ["2026-09-28", "2026-10-02", "match 9", "match 10"]
    assert list_videos(tmp_path / "nope") == [] and sibling_folders(tmp_path / "x" / "y") == []


def test_new_match_file(tmp_path):
    from tennis_to_utube.sources import new_match_file

    infos = [info(str(tmp_path / "GX010008.MP4")), info(str(tmp_path / "GX020008.MP4"))]
    mf = new_match_file(infos, tmp_path / "GX010008.match.json", kind="doubles",
                        side_a=["Emma ", ""], side_b=["Sara"], format_spec={"preset": "pro10"})
    assert [s.path for s in mf.sources] == ["GX010008.MP4", "GX020008.MP4"]
    assert mf.match["sides"]["A"]["players"] == ["Emma", "Player 2"]
    assert mf.match["sides"]["B"]["players"] == ["Sara", "Player 4"]
    assert mf.match["kind"] == "doubles" and mf.match["format"] == {"preset": "pro10"}
    single = new_match_file(infos[:1], tmp_path / "m.match.json")
    assert single.match["sides"]["B"]["players"] == ["Player 2"]


def test_subfolders_and_summaries(tmp_path):
    from tennis_to_utube.sources import folder_summary, list_subfolders

    for name in ("match 10", "match 2", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "match 2" / "GX010001.MP4").write_bytes(b"")
    (tmp_path / "match 2" / "GX020001.mp4").write_bytes(b"")
    (tmp_path / "match 2" / "GX010001.match.json").write_text("{}")
    (tmp_path / "match 2" / "notes.txt").write_text("")
    (tmp_path / "match 2" / "sub").mkdir()
    assert [p.name for p in list_subfolders(tmp_path)] == ["match 2", "match 10"]
    s = folder_summary(tmp_path / "match 2")
    assert (s.videos, s.matches, s.folders) == (2, 1, 1)
    assert s.text() == "2 videos · 1 match · 1 folder"
    assert folder_summary(tmp_path / "match 10").text() == ""
    assert folder_summary(tmp_path / "nope") is None and list_subfolders(tmp_path / "nope") == []
