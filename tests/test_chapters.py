from tennis_to_utube.chapters import (
    Chapter, anchor_titles, chapter_for, derive_chapters, youtube_chapters,
)
from tennis_to_utube.config import Settings
from tennis_to_utube.matchfile import Event
from tennis_to_utube.timeline import Timeline
from tennis_to_utube.trim import Cut, ListKeyframes, plan_trim, remap_events

MIN = 60_000
SETTINGS = Settings(lead_in={"default": 5000, "ace": 3000}, chapter_gap_ms=10 * MIN)


def ev(t, type, id=None, **kw):
    return Event(id or f"{type}@{t}", t, type, **kw)


def chapters_for(events, total, cuts=(), settings=SETTINGS):
    tl = Timeline((total,))
    plan = plan_trim(tl, cuts, ListKeyframes.regular([total], 1001, 33) if cuts else None)
    kept, _ = remap_events(events, plan)
    return derive_chapters(events, kept, settings, plan.total_out_ms), plan


def test_anchor_titles_number_sets_and_games():
    events = [ev(0, "match_start"), ev(1, "set_start"), ev(2, "game_start", player="Emma"),
              ev(3, "game_end", result="A"), ev(4, "game_start", player="Sara"),
              ev(5, "game_end", result="A"), ev(6, "set_end", result="A"),
              ev(7, "game_start"),  # set 2 without Set start
              ev(8, "game_end", result="A"), ev(9, "set_end", result="B"), ev(10, "set_start"),
              ev(11, "game_start"),
              ev(12, "score_state", details={"games": [4, 4]}), ev(13, "game_start"),
              ev(14, "score_state"), ev(15, "game_start")]
    titles = anchor_titles(events)
    # Set scores after play has started are corrections, not chapters.
    assert [titles[e.id] for e in events if e.id in titles] == [
        "Set 1", "Set 1 · Game 1 — Emma serving", "Set 1 · Game 2 — Sara serving",
        "Set 2 · Game 1", "Set 3", "Set 3 · Game 1", "Set 3 · Game 9", "Set 3 · Game 10"]


def test_set_score_before_play_is_a_chapter():
    events = [ev(0, "score_state", details={"sets": [[6, 4]], "games": [3, 2]}),
              ev(1, "game_start", player="Sara")]
    assert list(anchor_titles(events).values()) == [
        "Match in progress (6–4, 3–2)", "Set 2 · Game 6 — Sara serving"]
    unknown = [ev(0, "score_state"), ev(1, "game_start")]
    assert list(anchor_titles(unknown).values()) == ["Match in progress", "Game"]


def test_chapters_use_lead_in_and_output_times():
    events = [ev(60_000, "set_start"), ev(61_000, "game_start"), ev(300_000, "game_start")]
    chs, _ = chapters_for(events, 20 * MIN, [Cut("w", "warmup", 0, 59_000, "w")])
    seg = 58_058  # warm-up end snaps back to the keyframe at 58.058 s
    assert [(c.t_ms, c.base_ms, c.title) for c in chs] == [
        (0, 60_000 - seg, "Set 1"),  # lead-in clamped to the start of the video
        (0, 61_000 - seg, "Set 1 · Game 1"),
        (300_000 - seg - 5000, 300_000 - seg, "Set 1 · Game 2")]


def test_gap_rule_adds_chapter_at_first_event_after_gap():
    events = [ev(0, "game_start", id="g1"), ev(5 * MIN, "point", id="p5"),
              ev(11 * MIN, "point", id="p11"), ev(13 * MIN, "note", id="n13"),
              ev(22 * MIN, "ace", id="a22"), ev(25 * MIN, "game_start", id="g2"),
              ev(26 * MIN, "point", id="p26")]
    chs, _ = chapters_for(events, 40 * MIN)
    assert [(c.kind, c.event_ids, c.title, c.t_ms) for c in chs] == [
        ("anchor", ("g1",), "Set 1 · Game 1", 0),
        ("gap", ("p11",), "Set 1 · Game 1 (cont.)", 11 * MIN - 5000),
        ("gap", ("a22",), "Set 1 · Game 1 (cont.)", 22 * MIN - 3000),  # from 11 + 10 min
        ("anchor", ("g2",), "Set 1 · Game 2", 25 * MIN - 5000),
    ]  # after g2: no event at/after 35 min -> nothing added


def test_gap_rule_never_invents_times():
    chs, _ = chapters_for([ev(0, "game_start"), ev(30 * MIN, "game_start")], 40 * MIN)
    assert [c.kind for c in chs] == ["anchor", "anchor"]


def test_gap_rule_before_first_anchor():
    events = [ev(12 * MIN, "note", id="n"), ev(30 * MIN, "game_start", id="g")]
    chs, _ = chapters_for(events, 40 * MIN)
    assert [(c.kind, c.title) for c in chs] == [("gap", "Note"), ("anchor", "Set 1 · Game 1")]


def test_removed_anchors_make_no_chapters():
    events = [ev(0, "game_start", id="g1"), ev(100_000, "game_start", id="g2"),
              ev(200_000, "game_start", id="g3")]
    chs, _ = chapters_for(events, 300_000, [Cut("c", "x", 90_000, 150_000, "c")])
    assert [c.event_ids for c in chs] == [("g1",), ("g3",)]
    assert chs[1].title == "Set 1 · Game 3"  # numbering counts removed games too


def ch(t, title, base=None):
    return Chapter(t, t if base is None else base, title, "anchor", (title,))


def test_youtube_rules_ok_case():
    out, issues = youtube_chapters([ch(3_400, "A"), ch(60_999, "B"), ch(3_700_000, "C")], 4_000_000)
    assert issues == []
    assert [(c.t_ms, c.title) for c in out] == [(0, "A"), (60_000, "B"), (3_700_000, "C")]


def test_youtube_adds_start_when_first_chapter_is_late():
    out, _ = youtube_chapters([ch(10_000, "A"), ch(60_000, "B")], 120_000)
    assert [(c.t_ms, c.title, c.kind) for c in out] == [
        (0, "Start", "start"), (10_000, "A", "anchor"), (60_000, "B", "anchor")]


def test_youtube_merges_short_chapters():
    out, _ = youtube_chapters([ch(0, "Set 2"), ch(4_000, "Set 2 · Game 1"), ch(9_999, "X"),
                               ch(30_000, "Y"), ch(39_500, "Z"), ch(60_000, "W"),
                               ch(115_000, "Tail")], 120_000)
    assert [(c.t_ms, c.title) for c in out] == [
        (0, "Set 2 · Game 1 / X"), (30_000, "Y / Z"), (60_000, "W / Tail")]
    assert out[0].event_ids == ("Set 2", "Set 2 · Game 1", "X")


def test_youtube_needs_three_chapters():
    out, issues = youtube_chapters([ch(0, "A"), ch(5_000, "B"), ch(60_000, "C")], 120_000)
    assert out == [] and [i.code for i in issues] == ["too_few_chapters"]
    assert youtube_chapters([], 1_000)[0] == []


def test_chapter_for_groups_by_event_time():
    chs = [ch(0, "A", base=0), ch(55_000, "B", base=60_000), ch(118_000, "C", base=121_000)]
    assert chapter_for(chs, 59_999).title == "A"
    assert chapter_for(chs, 60_000).title == "B"
    assert chapter_for(chs, 500_000).title == "C"
    assert chapter_for([ch(10, "late")], 5) is None



def test_titles_carry_certain_scores():
    from tennis_to_utube.scoring import PRESETS, analyze

    events = [ev(0, "set_start"), ev(1, "game_start", player="Emma")]
    events += [ev(2 + i, "game_end", result=r) for i, r in enumerate("AAAAABA")]  # 6-1
    events += [ev(100, "set_end", result="A"), ev(101, "set_start"),
               ev(102, "game_start", player="Sara"), ev(103, "game_end", result="unknown"),
               ev(104, "game_start", player="Emma"), ev(105, "game_end", result="B"),
               ev(106, "game_start")]
    titles = anchor_titles(events, analyze(events, PRESETS["standard"]))
    assert [titles[e.id] for e in events if e.id in titles] == [
        "Set 1", "Set 1 · Game 1 — Emma serving", "Set 2 (6–1)", "Set 2 · Game 1 — Sara serving",
        "Set 2 · Game 2 — Emma serving",  # 1–0 or 0–1: not certain, so no score
        "Set 2 · Game 3"]  # 1–1 or 0–2: not certain either


def test_tiebreak_titles():
    from tennis_to_utube.scoring import PRESETS, analyze

    events, t = [], 0
    for _ in range(6):
        for r in "AB":
            events.append(ev(t, "game_end", result=r))
            t += 1
    events.append(ev(t, "game_start", player="Emma"))
    titles = anchor_titles(events, analyze(events, PRESETS["standard"]))
    assert list(titles.values()) == ["Set 1 · Tiebreak (6–6) — Emma serving"]
    events = [ev(0, "set_end", result="A", details={"games": [6, 2]}),
              ev(1, "set_end", result="B", details={"games": [3, 6]}), ev(2, "set_start"),
              ev(3, "game_start")]
    titles = anchor_titles(events, analyze(events, PRESETS["standard_mtb"]))
    # sets recorded only as Set ends: numbered from the score, not from Set start events
    assert list(titles.values()) == ["Set 3 (6–2, 3–6)", "Set 3 · Match tiebreak"]
