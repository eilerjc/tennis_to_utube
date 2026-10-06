"""The Overlay tool: drawing the board (Pillow) and burning it in (ffmpeg) on synthetic footage."""

import subprocess

import pytest

from synth import FRAME_MS, make_clip
from tennis_to_utube import matchfile
from tennis_to_utube.config import load_overlay_config
from tennis_to_utube.overlay import Painter, board_timeline, default_output, main
from tennis_to_utube.probe import probe
from tennis_to_utube.scoreboard import Board, Cell
from tennis_to_utube.session import Session
from tennis_to_utube.trim import plan_trim
from tennis_to_utube.trimtool import main as trim_main

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

BOARDS = [Board(("Emma Jones", "Sara Smith"), (), ("0", "0"), ("0", "0"), "A"),
          Board(("Emma Jones", "Sara Smith"), ((Cell("6", won=True), Cell("4")),), ("3", "2"),
                ("AD", "40"), "B"),
          Board(("Emma Jones", "Sara Smith"), ((Cell("6", won=True), Cell("4")),
                                               (Cell("6", "5"), Cell("7", won=True)),
                                               (Cell("10", won=True), Cell("8"))), None, None, None, "A")]


def test_one_canvas_for_all_boards_sized_by_the_video(config_dir):
    cfg = load_overlay_config()
    p = Painter(cfg, 2160, BOARDS)
    images = [p.draw(b) for b in BOARDS]
    assert {im.size for im in images} == {(p.width, p.height)}
    assert p.height == 2 * round(2160 * 0.034)
    assert abs(Painter(cfg, 1080, BOARDS).height - p.height // 2) <= 1
    assert p.position(3840, 2160) == (round(2160 * 0.03),) * 2  # top-left
    # the widest board fills the canvas; narrower ones leave it transparent on the right
    widest = max(BOARDS, key=p.board_width)
    assert p.board_width(widest) == p.width
    first = images[0]
    assert first.getpixel((p.board_width(BOARDS[0]) + 2, p.height // 2))[3] == 0


def test_points_column_and_server_ball_use_the_accent(config_dir):
    cfg = load_overlay_config()
    p = Painter(cfg, 2160, BOARDS[:1])
    img = p.draw(BOARDS[0])
    accent = (0xFF, 0xD2, 0x00, 255)
    assert img.getpixel((p.width - 3, p.height - 3 - p.row // 4)) == accent  # points column
    assert img.getpixel((p.dot_w // 2, p.row // 2)) == accent  # A serves
    assert img.getpixel((p.dot_w // 2, p.row + p.row // 2)) != accent


def test_right_corners_align_the_box_right(config_dir):
    (config_dir / "overlay.toml").write_text('[board]\ncorner = "bottom_right"\n', encoding="utf-8")
    cfg = load_overlay_config()
    p = Painter(cfg, 1080, BOARDS)
    narrow = p.draw(BOARDS[0])
    assert narrow.getpixel((1, p.height // 2))[3] == 0 and narrow.getpixel((p.width - 2, p.height // 2))[3] > 0
    margin = round(1080 * 0.03)
    assert p.position(1920, 1080) == (1920 - p.width - margin, 1080 - p.height - margin)


def test_a_missing_font_falls_back_to_the_built_in_one(config_dir):
    (config_dir / "overlay.toml").write_text('[board]\nfont = "no such font.ttf"\n', encoding="utf-8")
    p = Painter(load_overlay_config(), 720, BOARDS)
    assert p.draw(BOARDS[1]).size == (p.width, p.height)


# -- end to end (ffmpeg) -------------------------------------------------------------------

ENCODE_X265 = ('[encode]\nencoder = "libx265"\nargs = ["-preset", "ultrafast", "-crf", "12", "-x265-params", '
               '"log-level=error"]\nhwaccel = ""\n')


@pytest.fixture
def match(tmp_path, config_dir):
    make_clip(tmp_path / "GX010001.MP4", 900, data_track=False)  # ~15 s
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", round(900 * FRAME_MS),
                                                       fps="60000/1001")])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    mf.add_event(2_500, "match_start")
    mf.add_event(3_000, "game_start", side="A", player="Emma")
    mf.add_event(6_000, "point", result="A")
    mf.add_event(12_000, "match_end")
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    (config_dir / "overlay.toml").write_text(
        '[board]\nrow_height = 0.15\nopacity = 1.0\n' + ENCODE_X265, encoding="utf-8")
    return path


def frame(video, t: float):
    png = video.with_name(f"frame {t}.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1",
                    str(png)], check=True)
    return Image.open(png).convert("RGB")


def board_diff(a, b, box) -> float:
    pa, pb = a.crop(box).tobytes(), b.crop(box).tobytes()  # RGB
    return 3 * sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)



@pytest.mark.ffmpeg
def test_full_recording_gets_the_board_changing_at_the_point(match, capsys):
    assert main([str(match), "--full"]) == 0
    out = default_output(match, load_overlay_config(), "full")
    info = probe(out)
    assert info.codec == "hevc" and info.audio is not None
    assert abs(info.duration_ms - round(900 * FRAME_MS)) <= 50
    cfg = load_overlay_config()
    session = Session(matchfile.load(match), match)
    shown = board_timeline(session, cfg, plan_trim(session.timeline(), [], None))
    assert [s.board.points for s in shown] == [("0", "0"), ("0", "0"), ("15", "0")]
    p = Painter(cfg, 180, [s.board for s in shown])
    x, y = p.position(320, 180)
    right = x + p.width
    cell = (right - p.points_w + 3, y + 3, right - 3, y + p.row - 3)  # Emma's points
    before, after, later = frame(out, 4.0), frame(out, 7.0), frame(out, 9.0)
    assert board_diff(before, after, cell) > 4 * board_diff(after, later, cell) + 5  # 0 → 15
    r, g, b = later.getpixel((right - 3, y + p.row // 2))
    assert r > 200 and g > 170 and b < 90  # yellow points column
    capsys.readouterr()


@pytest.mark.ffmpeg
def test_trimmed_video_and_preview_and_png(match, config_dir, capsys):
    (config_dir / "trim.toml").write_text('rules = ["warmup", "after_match"]\n', encoding="utf-8")
    assert main([str(match)]) == 1  # no trimmed video yet
    assert "no trimmed video" in capsys.readouterr().err
    assert trim_main([str(match)]) == 0
    trimmed = probe(match.with_name("GX010001 trimmed.mp4"))
    assert main([str(match)]) == 0
    out = default_output(match, load_overlay_config(), "trimmed")
    assert out.name == "GX010001 trimmed overlay.mp4"
    assert abs(probe(out).duration_ms - trimmed.duration_ms) <= 50
    assert main([str(match), "--preview", "0:02", "--seconds", "3"]) == 0
    preview = default_output(match, load_overlay_config(), "preview")
    assert abs(probe(preview).duration_ms - 3000) <= 50
    assert main([str(match), "--png", "5"]) == 0
    png = Image.open(default_output(match, load_overlay_config(), "png"))
    assert png.mode == "RGBA" and png.height == 2 * round(trimmed.height * 0.15)
    assert matchfile.load(match).output["path"] == "GX010001 trimmed.mp4"  # match file unchanged
    capsys.readouterr()


def _have_nvenc() -> bool:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "hevc_nvenc" in out


@pytest.mark.ffmpeg
@pytest.mark.skipif(not _have_nvenc(), reason="needs ffmpeg with hevc_nvenc (NVIDIA GPU)")
def test_default_encoder_nvenc_preview(match, config_dir, capsys):
    (config_dir / "overlay.toml").write_text('[board]\nrow_height = 0.15\n', encoding="utf-8")
    code = main([str(match), "--full", "--preview", "1", "--seconds", "2"])
    if code != 0 and "nvenc" in capsys.readouterr().err.lower():
        pytest.skip("hevc_nvenc listed but not usable here (no NVIDIA GPU, or driver too old)")
    assert code == 0
    info = probe(default_output(match, load_overlay_config(), "preview"))
    assert info.codec == "hevc" and abs(info.duration_ms - 2000) <= 50
