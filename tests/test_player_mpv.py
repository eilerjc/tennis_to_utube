"""The real mpv player, headless (vo=null): seeks and frame steps land on the right frames
of a two-file joined timeline. Skipped without libmpv."""

import importlib.util
import math
import time
from fractions import Fraction

import pytest

from synth import BITS, FRAME_MS, make_clip

pytestmark = [pytest.mark.gui, pytest.mark.ffmpeg]
if importlib.util.find_spec("PySide6"):
    from tennis_to_utube.gui.player import _add_dll_dirs

    _add_dll_dirs()  # load libmpv-2.dll next to run.bat, like the app, not another one on PATH
mpv = pytest.importorskip("mpv", exc_type=(ImportError, OSError))
pytest.importorskip("PIL")


@pytest.fixture(scope="module")
def player(qapp, tmp_path_factory):
    from tennis_to_utube.gui.player import MpvPlayer

    d = tmp_path_factory.mktemp("mpv")
    paths = [make_clip(d / "GX010001.MP4", 300, 0, data_track=False),
             make_clip(d / "GX020001.MP4", 300, 300, data_track=False)]
    p = MpvPlayer(vo="null", ao="null")
    p.load(paths, [round(300 * FRAME_MS)] * 2, "60000/1001")
    end = time.monotonic() + 10
    while not p.mpv.duration and time.monotonic() < end:
        time.sleep(0.05)
    yield p
    p.shutdown()


def shown_frame(p) -> int:
    img = p.mpv.screenshot_raw().convert("L")
    w, h = img.size
    return sum(1 << b for b in range(BITS) if img.getpixel((int((b + 0.5) * w / BITS), h // 2)) > 127)


def settle(p, target_ms=None):
    time.sleep(0.4)


def expected(t_ms: int) -> int:
    dur = round(300 * FRAME_MS)
    base, local = (300, t_ms - dur) if t_ms >= dur else (0, t_ms)
    return base + math.floor(Fraction(local) / FRAME_MS)


@pytest.mark.parametrize("t", [0, 1000, 2500, 4990, 5004, 5006, 5020, 7000, 9999])
def test_seek_shows_frame_on_screen_at_t(player, t):
    player.seek(t)
    settle(player)
    assert shown_frame(player) == expected(t)
    # the reported position is inside that frame, so marking here stores the same frame
    assert expected(player.position_ms()) == expected(t)


def test_frame_steps(player):
    player.seek(2000)
    settle(player)
    start = shown_frame(player)
    player.step(1)
    settle(player)
    assert shown_frame(player) == start + 1
    player.step(-1)
    settle(player)
    assert shown_frame(player) == start
