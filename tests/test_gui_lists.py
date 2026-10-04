"""Events/Issues lists and the edit dialogs (offscreen)."""

import pytest

from tennis_to_utube import matchfile
from tennis_to_utube.shortcuts import default_shortcuts

pytestmark = [pytest.mark.gui]


@pytest.fixture
def page(qapp, config_dir, tmp_path):
    from tennis_to_utube.appstate import AppState
    from tennis_to_utube.config import load_config
    from tennis_to_utube.gui.main_window import MainWindow
    from tennis_to_utube.gui.player import NullPlayer

    w = MainWindow(load_config(), AppState(), default_shortcuts(),
                   player_factory=lambda parent: NullPlayer("test", parent))
    mf = matchfile.MatchFile(sources=[matchfile.Source("GX010001.MP4", 600_000, fps="60000/1001")])
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    mf.add_event(1_000, "game_start", side="A", player="Emma")
    for t, r in ((2_000, "A"), (3_000, "unknown"), (4_000, "A"), (5_000, "A")):
        mf.add_event(t, "point", result=r)
    mf.add_event(6_000, "game_end", result="A")
    mf.add_event(7_000, "point", result="B")
    mf.add_event(7_500, "point", result="A")  # unusual but fine
    path = tmp_path / "GX010001.match.json"
    matchfile.save(mf, path)
    w.open_match(mf, path)
    yield w.mark
    w.close()


def cell(page, row, col):
    m = page.lists.events_model
    return m.data(m.index(row, col))


def test_events_list_shows_inferred_and_scores(page):
    m = page.lists.events_model
    assert m.rowCount() == 8 and page.lists.tabText(0) == "Events (8)"
    assert [cell(page, 0, c) for c in range(3)] == ["0:00:01.000", "Game start", "Emma"]
    assert cell(page, 2, 3) == "Emma (inferred)"  # the unknown point must have been A's
    assert cell(page, 5, 4) == "1–0, 0–0"  # score after Game end
    assert cell(page, 1, 3) == "Emma"


def test_click_row_selects_and_jumps(page):
    m = page.lists.events_model
    page.lists.events_view.clicked.emit(m.index(6, 0))
    assert page.selected_id == m.events[6].id and page.position() == 7_000


def test_edit_event_dialog(page):
    m = page.lists.events_model
    target = m.events[6]

    def fill(dialog):
        dialog.time.setText("0:00:07.250")
        dialog.note.setText("great return")
        dialog.tags.setText("close, long rally")
        return True

    page.run_dialog = fill
    page.lists.events_view.doubleClicked.emit(m.index(6, 0))
    e = page.match.event(target.id)
    assert (e.t_ms, e.note, e.tags) == (7_250, "great return", ["close", "long rally"])
    page.actions["undo"].trigger()
    assert page.match.event(target.id).t_ms == 7_000


def test_issues_tab(page):
    page.player.seek(7_700)
    page.mark("game_end_b")  # does not fit the points
    lists = page.lists
    codes = [i.code for i in lists.issues_model.issues]
    assert "score_conflict" in codes
    assert "to check" in lists.tabText(1)
    row = codes.index("score_conflict")
    lists.issues_view.clicked.emit(lists.issues_model.index(row, 0))
    assert page.position() == 7_700


def test_rename_and_swap_players(page):
    informed = []
    page.inform = lambda title, text: informed.append(text)

    def swap(dialog):
        dialog.edits["A"][0].setText("Sara")
        dialog.edits["B"][0].setText("Emma")
        dialog.no_ad.setChecked(True)
        return True

    page.run_dialog = swap
    page.edit_match()
    mf = page.match
    assert mf.match["sides"]["A"]["players"] == ["Sara"] and mf.match["format"]["ad"] is False
    assert mf.events[0].player == "Sara"
    assert "Replaced" in informed[0]
    assert page.buttons.buttons["point_a"].text().startswith("Point Sara")
    page.actions["undo"].trigger()  # one step undoes all of it
    assert page.match.match["sides"]["A"]["players"] == ["Emma"]
    assert page.match.match["format"].get("ad") is not False


def test_match_dialog_refuses_duplicates(qapp):
    from tennis_to_utube.gui.lists import MatchDialog

    d = MatchDialog({"kind": "singles", "sides": {"A": {"players": ["Emma"]},
                                                   "B": {"players": ["Sara"]}}})
    d.edits["B"][0].setText("Emma")
    assert d.validate()
