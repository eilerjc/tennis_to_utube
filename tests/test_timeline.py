import pytest

from tennis_to_utube.matchfile import Source
from tennis_to_utube.timeline import Interval, Timeline, complement, merge_intervals


def test_offsets_and_total():
    tl = Timeline((1000, 2000, 500))
    assert tl.offsets == (0, 1000, 3000)
    assert tl.total_ms == 3500
    assert (tl.start_of(1), tl.end_of(1)) == (1000, 3000)


def test_from_sources():
    tl = Timeline.from_sources([Source("a", 10), Source("b", 20)])
    assert tl.durations_ms == (10, 20)


def test_locate_boundaries():
    tl = Timeline((1000, 2000, 500))
    assert tl.locate(0) == (0, 0)
    assert tl.locate(999) == (0, 999)
    assert tl.locate(1000) == (1, 0)
    assert tl.locate(1000, at_end=True) == (0, 1000)
    assert tl.locate(3499) == (2, 499)
    assert tl.locate(3500) == (2, 500)
    assert tl.locate(3500, at_end=True) == (2, 500)
    assert tl.locate(0, at_end=True) == (0, 0)
    assert tl.to_joined(2, 100) == 3100
    with pytest.raises(ValueError):
        tl.locate(3501)
    with pytest.raises(ValueError):
        tl.locate(-1)
    with pytest.raises(ValueError):
        tl.to_joined(0, 1001)


@pytest.mark.parametrize("durations", [(), (0,), (10, -1), (1.5,)])
def test_invalid_durations(durations):
    with pytest.raises(ValueError):
        Timeline(durations)


def test_merge_and_complement():
    ivs = [Interval(50, 60), Interval(0, 10), Interval(5, 20), Interval(20, 30), Interval(70, 70)]
    assert merge_intervals(ivs) == [Interval(0, 30), Interval(50, 60)]
    assert complement(ivs, 0, 100) == [Interval(30, 50), Interval(60, 100)]
    assert complement([], 0, 100) == [Interval(0, 100)]
    assert complement([Interval(0, 100)], 0, 100) == []
    assert complement([Interval(-10, 5), Interval(95, 200)], 0, 100) == [Interval(5, 95)]
