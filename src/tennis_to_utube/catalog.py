"""Event type ids (stored in the match file's ``type`` field) and display labels.

Only the ids are fixed here; semantics (scoring, flow) come with the score engine.
The list is expected to grow; unknown types in a file are kept and shown by id.
"""

from __future__ import annotations

# Match structure / flow
MATCH_START = "match_start"
MATCH_END = "match_end"
SET_START = "set_start"
SET_END = "set_end"  # result: "A" | "B" | "unknown"
GAME_START = "game_start"
GAME_END = "game_end"  # result: "A" | "B" | "unknown"
TIEBREAK_START = "tiebreak_start"
RULES_CHANGE = "rules_change"
# Score checkpoint, allowed at any time. details holds only the known parts:
#   "sets": [[6, 4], ...]  completed sets (A, B); "games": [3, 2]  games in the current set
# (points, server and format come with the score engine).
SCORE_STATE = "score_state"
ENDING_STATE = "ending_state"  # final score from another source (scorebook), entered

# Points
POINT = "point"  # result: "A" | "B" | "unknown"

# Serve
FIRST_SERVE_IN = "first_serve_in"
FAULT = "fault"
LET = "let"
ACE = "ace"
DOUBLE_FAULT = "double_fault"
SECOND_SERVE_IN = "second_serve_in"

# Shot [data only for v1 UI]
WINNER = "winner"
FORCED_ERROR = "forced_error"
UNFORCED_ERROR = "unforced_error"
OUT = "out"
NET = "net"

# Coaching marks
GOOD_RECOVERY = "good_recovery"
FOOTWORK = "footwork"
BODY_LANGUAGE = "body_language"
LATE_CONTACT = "late_contact"
STRATEGY = "strategy"
NOTE = "note"

LABELS: dict[str, str] = {
    MATCH_START: "Match start",
    MATCH_END: "Match end",
    SET_START: "Set start",
    SET_END: "Set end",
    GAME_START: "Game start",
    GAME_END: "Game end",
    TIEBREAK_START: "Tiebreak start",
    RULES_CHANGE: "Rules change",
    SCORE_STATE: "Set score",
    ENDING_STATE: "Ending state",
    POINT: "Point",
    FIRST_SERVE_IN: "First serve in",
    FAULT: "Fault",
    LET: "Let",
    ACE: "Ace",
    DOUBLE_FAULT: "Double fault",
    SECOND_SERVE_IN: "Second serve in",
    WINNER: "Winner",
    FORCED_ERROR: "Forced error",
    UNFORCED_ERROR: "Unforced error",
    OUT: "Out",
    NET: "Net",
    GOOD_RECOVERY: "Good recovery",
    FOOTWORK: "Footwork/positioning",
    BODY_LANGUAGE: "Body language",
    LATE_CONTACT: "Late contact",
    STRATEGY: "Strategy/pattern",
    NOTE: "Note",
}

# Events after which play has begun (the warm-up rule removes footage before the first).
PLAY_BEGINS = frozenset({MATCH_START, SET_START, GAME_START, SCORE_STATE})
# Chapter anchors (DESIGN.md §8). A Set score is one only before any Set/Game start.
CHAPTER_ANCHORS = frozenset({SET_START, GAME_START, SCORE_STATE})


def label(event_type: str) -> str:
    return LABELS.get(event_type, event_type)
