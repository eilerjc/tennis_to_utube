from tennis_to_utube.matchfile import Event
from tennis_to_utube.structure import Position, score_text, walk


def ev(t, type, **kw):
    return Event(f"{type}@{t}", t, type, **kw)


def test_positions_after_each_event():
    events = [ev(0, "match_start"), ev(1, "game_start"), ev(2, "game_end", result="A"),
              ev(3, "game_start"),
              ev(4, "game_start"),  # game end not marked: numbering still advances
              ev(5, "game_end", result="B"), ev(6, "set_end", result="A"), ev(7, "set_start"),
              ev(8, "game_start"),
              ev(9, "score_state"),  # nothing entered while tracking: nothing changes
              ev(10, "game_start"), ev(11, "set_end", result="B"), ev(12, "game_start")]
    assert [p for _, p in walk(events)] == [
        Position(0, 0, 0), Position(1, 0, 1), Position(1, 1, 1), Position(1, 1, 2),
        Position(1, 1, 3), Position(1, 2, 3), Position(1, 2, 3), Position(2, 0, 0),
        Position(2, 0, 1), Position(2, 0, 1), Position(2, 0, 2), Position(2, 0, 2),
        Position(3, 0, 1)]


def test_set_score_without_parts_at_start_is_unknown():
    walked = walk([ev(0, "score_state"), ev(1, "game_start"), ev(2, "game_end", result="A"),
                   ev(3, "set_end", result="A"), ev(4, "game_start")])
    assert [p for _, p in walked] == [
        Position(None, None, None), Position(None, None, None), Position(None, None, None),
        Position(None, None, None), Position(None, 0, 1)]


def test_walk_sorts_by_time_stably():
    a, b = ev(5, "game_start"), ev(5, "note")
    assert [e for e, _ in walk([ev(9, "game_end", result="A"), a, b])] == [a, b, ev(9, "game_end", result="A")]


def test_set_score_sets_known_parts_only():
    walked = walk([ev(0, "game_start"), ev(1, "game_end", result="A"),
                   ev(2, "score_state", details={"games": [3, 3]}),       # set stays 1
                   ev(3, "game_start"),
                   ev(4, "score_state", details={"sets": [[6, 4]]}),      # games kept
                   ev(5, "score_state", details={"games": [1, "x"]}),     # invalid -> ignored
                   ev(6, "game_end", result="B")])
    assert [p for _, p in walked][2:] == [
        Position(1, 6, 6), Position(1, 6, 7), Position(2, 6, 7), Position(2, 6, 7),
        Position(2, 7, 7)]


def test_score_text():
    assert score_text(ev(0, "score_state", details={"sets": [[6, 4], [3, 6]], "games": [2, 1]})) == \
        "6–4, 3–6, 2–1"
    assert score_text(ev(0, "score_state", details={"sets": [[6, "a"]]})) == ""
