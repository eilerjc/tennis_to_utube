import pytest

from tennis_to_utube.playback import (
    clock_text, edl_url, frame_ms, mark_time, neighbor_event, position_ms, seek_seconds,
)


def test_frame_ms():
    assert frame_ms("60000/1001") == pytest.approx(16.6833, abs=1e-4)
    assert frame_ms("30/1") == pytest.approx(33.333, abs=1e-3)
    assert frame_ms(None) == frame_ms("0/0") == frame_ms("junk") == frame_ms("60000/1001")


def test_edl_url_lengths_and_odd_names():
    url = edl_url(["C:/v/GX010008.MP4", "W:/a,b;c/Zoë.MP4"], [2271000, 5005])
    assert url == ("edl://%17%C:/v/GX010008.MP4,0,2271.000;"
                   "%17%W:/a,b;c/Zoë.MP4,0,5.005")  # ë is 2 bytes
    with pytest.raises(ValueError):
        edl_url(["a"], [1, 2])


def test_seek_and_position_round_trip():
    f = frame_ms("60000/1001")
    # mpv shows the first frame starting at/after target - 5 ms: aim so that point is just
    # inside the previous frame, then the frame on screen at t is shown
    assert seek_seconds(1000, f) == pytest.approx((1000 - f + 5.5) / 1000)
    assert seek_seconds(5, f) == 0.0
    assert position_ms(1.0010000000001) == 1001  # frame starts at 1001.0 ms
    assert position_ms(1.0186833) == 1019  # rounded up: stays inside the frame
    assert position_ms(None) == 0


def test_mark_time():
    assert mark_time(10_000, playing=False, speed=1.0, reaction_ms=200) == 10_000
    assert mark_time(10_000, playing=True, speed=1.0, reaction_ms=200) == 9_800
    assert mark_time(10_000, playing=True, speed=0.25, reaction_ms=200) == 9_950
    assert mark_time(100, playing=True, speed=2.0, reaction_ms=200) == 0


def test_neighbor_event():
    f = frame_ms("60000/1001")
    times = [1000, 1019, 2000, 3000]
    assert neighbor_event(times, 1019, f, forward=True) == 2000  # 1019 is in this frame
    assert neighbor_event(times, 1019, f, forward=False) == 1000
    assert neighbor_event(times, 3000, f, forward=True) is None
    assert neighbor_event(times, 1000, f, forward=False) is None


def test_clock_text():
    assert clock_text(3_733_467) == "1:02:13.467" and clock_text(-5) == "0:00:00.000"


@pytest.mark.parametrize("text, ms", [
    ("1:02:13.467", 3_733_467), ("2:13.5", 133_500), ("13", 13_000), ("0:00:00.000", 0),
    ("1:61", None), ("x", None), ("", None), ("1::2", None), ("-1", None),
])
def test_parse_clock(text, ms):
    from tennis_to_utube.playback import parse_clock

    assert parse_clock(text) == ms
