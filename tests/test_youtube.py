import csv
import io

import pytest

from tennis_to_utube.config import Settings
from tennis_to_utube.matchfile import MatchFile
from tennis_to_utube.timeline import Timeline
from tennis_to_utube.trim import plan_trim
from tennis_to_utube.youtube import (
    build_export, describe, description, link_url, links_csv, links_markdown, parse_video_id,
    timestamp,
)

SETTINGS = Settings(lead_in={"default": 5000, "ace": 3000}, chapter_gap_ms=600_000)


def test_timestamp_format():
    assert [timestamp(s) for s in (0, 9, 75, 600, 3599, 3600, 5025)] == [
        "0:00", "0:09", "1:15", "10:00", "59:59", "1:00:00", "1:23:45"]


def test_link_url():
    assert link_url("dQw4w9WgXcQ", 754) == "https://youtu.be/dQw4w9WgXcQ?t=754"


def match():
    mf = MatchFile()
    mf.match["sides"]["A"]["players"] = ["Emma"]
    mf.match["sides"]["B"]["players"] = ["Sara"]
    mf.add_event(30_000, "set_start")
    mf.add_event(31_000, "game_start", player="Emma")
    mf.add_event(40_500, "point", result="A", tags=["close"])
    mf.add_event(52_900, "ace", player="Emma")
    mf.add_event(150_000, "game_start", player="Sara")
    mf.add_event(170_000, "point", result="unknown")
    mf.add_event(171_000, "body_language", side="B", note="head down")
    mf.add_event(290_000, "game_start", player="Emma")
    return mf


def test_describe_uses_names_as_typed():
    mf = match()
    texts = [describe(e, mf.match) for e in mf.sorted_events()]
    assert texts == ["Set start", "Game start — Emma", "Point — won by Emma [close]", "Ace — Emma",
                     "Game start — Sara", "Point — winner unknown", "Body language — Sara: head down",
                     "Game start — Emma"]


def test_build_export_without_video_id():
    mf = match()
    plan = plan_trim(Timeline((300_000,)), [], None)
    export = build_export(mf, plan, SETTINGS)
    assert export.issues == []
    assert export.description == ("0:00 Start\n0:25 Set 1 · Game 1 — Emma serving\n"
                                  "2:25 Set 1 · Game 2 — Sara serving\n4:45 Set 1 · Game 3 — Emma serving\n")
    assert [(r.seconds, r.chapter, r.url) for r in export.links][:4] == [
        (25, "Set 1 · Game 1 — Emma serving", None),  # set start merged into game 1
        (26, "Set 1 · Game 1 — Emma serving", None),
        (35, "Set 1 · Game 1 — Emma serving", None),
        (49, "Set 1 · Game 1 — Emma serving", None),  # ace: 3 s lead-in, rounded down
    ]
    md = links_markdown(export.links, "Emma vs Sara")
    assert md.startswith("# Emma vs Sara\n\n## Set 1 · Game 1 — Emma serving\n\n- 0:25 Set start\n")
    assert "\n\n\n" not in md


def test_build_export_with_video_id():
    mf = match()
    mf.youtube["video_id"] = "abc123"
    export = build_export(mf, plan_trim(Timeline((300_000,)), [], None), SETTINGS)
    md = links_markdown(export.links)
    assert "- [0:49](https://youtu.be/abc123?t=49) Ace — Emma" in md
    assert "## Set 1 · Game 2 — Sara serving" in md
    rows = list(csv.reader(io.StringIO(links_csv(export.links))))
    assert rows[0] == ["chapter", "time", "seconds", "event_id", "type", "description", "url"]
    assert rows[4] == ["Set 1 · Game 1 — Emma serving", "0:49", "49", "e_0004", "ace", "Ace — Emma",
                       "https://youtu.be/abc123?t=49"]
    assert len(rows) == 1 + len(mf.events)


def test_description_lines():
    assert description([]) == ""


@pytest.mark.parametrize("text, vid", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://example.com/watch?v=dQw4w9WgXcQ", None), ("nope", None), ("", None),
])
def test_parse_video_id(text, vid):
    assert parse_video_id(text) == vid


def test_match_summary():
    from tennis_to_utube.flow import analyze_match
    from tennis_to_utube.youtube import match_summary

    mf = match()
    assert match_summary(mf, analyze_match(mf)) == "Emma vs Sara"
    mf.add_event(299_000, "ending_state", details={"sets": [[6, 4], [3, 6], [1, 0]], "entered": True})
    assert match_summary(mf, analyze_match(mf)) == (
        "Emma vs Sara: 6–4, 3–6, 1–0 (final score from the scorebook)")
    pro = MatchFile()
    pro.match["format"] = {"preset": "pro_set"}
    for t in range(8):
        pro.add_event(t, "game_end", result="B")
    assert match_summary(pro, analyze_match(pro)) == "Player 1 vs Player 2: 0–8 — Player 2 won"
    md = links_markdown([], "T", "Emma vs Sara")
    assert md.startswith("# T\n\nEmma vs Sara\n")


def test_match_summary_keeps_tiebreaks():
    from tennis_to_utube.flow import analyze_match
    from tennis_to_utube.scoring import parse_sets
    from tennis_to_utube.youtube import match_summary

    mf = match()
    sets = [list(p) for p in parse_sets("6-4 6-7(5) [10-8]")]
    mf.add_event(299_000, "ending_state", details={"sets": sets, "entered": True})
    assert match_summary(mf, analyze_match(mf)) == (
        "Emma vs Sara: 6–4, 6–7(5), [10–8] (final score from the scorebook)")
