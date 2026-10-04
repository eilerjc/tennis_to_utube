from tennis_to_utube.appstate import MAX_RECENT, AppState, load_state, save_state


def test_recent_and_favorites(tmp_path):
    st = AppState()
    for i in range(MAX_RECENT + 3):
        st.visit(f"/v/{i}")
    st.visit("/v/5")
    assert st.last_folder == "/v/5" and st.recent[0] == "/v/5" and len(st.recent) == MAX_RECENT
    assert st.recent.count("/v/5") == 1
    assert st.toggle_favorite("/v/1") is True and st.toggle_favorite("/v/1") is False


def test_round_trip_and_bad_files(tmp_path):
    path = tmp_path / "state.json"
    st = AppState(extra={"window": {"w": 3}})
    st.visit("W:/video/2026-09-28")
    st.toggle_favorite("W:/video")
    save_state(st, path)
    again = load_state(path)
    assert again == st
    path.write_text("{broken", encoding="utf-8")
    assert load_state(path) == AppState()
    path.write_text('{"recent": "nope", "favorites": [1, "a"]}', encoding="utf-8")
    assert load_state(path) == AppState(favorites=["a"])
    assert load_state(tmp_path / "missing.json") == AppState()


def test_history_back_and_forward():
    from tennis_to_utube.appstate import History

    h = History()
    assert h.go_back() is None
    for f in ("/a", "/b", "/b", "/c"):
        h.visit(f)
    assert h.go_back() == "/b" and h.go_back() == "/a" and h.go_back() is None
    assert h.go_forward() == "/b"
    h.visit("/d")  # a new visit clears Forward
    assert h.go_forward() is None and h.back == ["/a", "/b"]
