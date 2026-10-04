from tennis_to_utube.matchfile import Event
from tennis_to_utube.timeline_view import Band, View, bands, category, nearest_event


def ev(t, type, **kw):
    return Event(f"{type}@{t}", t, type, **kw)


def test_bands():
    events = [ev(10, "set_start"), ev(20, "game_start"), ev(50, "game_end", result="A"),
              ev(60, "game_start"), ev(90, "game_end", result="B"), ev(95, "set_end", result="A"),
              ev(100, "game_start"), ev(130, "game_start")]  # last game has no end
    assert bands(events, 200) == [
        Band(20, 50, "game", "G1", 0), Band(60, 90, "game", "G2", 1),
        Band(10, 95, "set", "Set 1", 0), Band(100, 130, "game", "G1", 2),
        Band(130, 200, "game", "G2", 3), Band(100, 200, "set", "Set 2", 1)]


def test_categories():
    assert category(ev(0, "point")) == "point" and category(ev(0, "ace")) == "serve"
    assert category(ev(0, "note")) == "note" and category(ev(0, "custom_x")) == "other"


def test_view_geometry_zoom_and_follow():
    v = View(0, 600_000, total_ms=3_600_000)
    assert v.x_of(300_000, 1200) == 600 and v.t_of(600, 1200) == 300_000
    v.zoom(2, anchor_ms=300_000)  # zoom in around the middle
    assert (v.start_ms, v.span_ms) == (150_000, 300_000)
    v.zoom(1000, anchor_ms=200_000)
    assert v.span_ms == v.min_span_ms
    v = View(0, 60_000, total_ms=3_600_000)
    assert not v.follow(30_000)
    assert v.follow(59_000) and v.start_ms == 59_000 - 6_000  # paged so it is near the left
    assert v.follow(10_000) and v.start_ms == 0
    v = View(3_590_000, 60_000, total_ms=3_600_000)
    v.clamp()
    assert v.start_ms == 3_540_000


def test_nearest_event():
    events = [ev(1000, "point"), ev(1500, "ace"), ev(5000, "note")]
    assert nearest_event(events, 1400, 200).type == "ace"
    assert nearest_event(events, 3000, 200) is None
