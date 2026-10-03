from tennis_to_utube.matchfile import Event
from tennis_to_utube.structure import Position, walk


def ev(t, type):
    return Event(f"{type}@{t}", t, type)


def test_positions_after_each_event():
    events = [ev(0, "match_start"), ev(1, "game_start"), ev(2, "game_won"), ev(3, "game_start"),
              ev(4, "game_start"),  # game end not marked: numbering still advances
              ev(5, "game_lost"), ev(6, "set_won"), ev(7, "set_start"), ev(8, "game_start"),
              ev(9, "starting_state"), ev(10, "game_start"), ev(11, "set_lost"), ev(12, "game_start")]
    assert [p for _, p in walk(events)] == [
        Position(0, 0, 0), Position(1, 0, 1), Position(1, 1, 1), Position(1, 1, 2),
        Position(1, 1, 3), Position(1, 2, 3), Position(1, 2, 3), Position(2, 0, 0),
        Position(2, 0, 1), Position(None, None, None), Position(None, None, None),
        Position(None, None, None), Position(None, 0, 1)]


def test_walk_sorts_by_time_stably():
    a, b = ev(5, "game_start"), ev(5, "note")
    assert [e for e, _ in walk([ev(9, "game_won"), a, b])] == [a, b, ev(9, "game_won")]
