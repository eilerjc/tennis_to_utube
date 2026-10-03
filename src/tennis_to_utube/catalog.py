"""Event type ids (stored in the match file's ``type`` field) and display labels.

Only the ids are fixed here; semantics (scoring, flow) come with the score engine.
The list is expected to grow; unknown types in a file are kept and shown by id.
"""

from __future__ import annotations

# Match structure / flow
MATCH_START = "match_start"
MATCH_END = "match_end"
SET_START = "set_start"
SET_WON = "set_won"
SET_LOST = "set_lost"
GAME_START = "game_start"
GAME_WON = "game_won"
GAME_LOST = "game_lost"
TIEBREAK_START = "tiebreak_start"
RULES_CHANGE = "rules_change"
STARTING_STATE = "starting_state"
ENDING_STATE = "ending_state"

# Points (result: "A" | "B" | "unknown")
POINT = "point"

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
    SET_WON: "Set won",
    SET_LOST: "Set lost",
    GAME_START: "Game start",
    GAME_WON: "Game won",
    GAME_LOST: "Game lost",
    TIEBREAK_START: "Tiebreak start",
    RULES_CHANGE: "Rules change",
    STARTING_STATE: "Starting state",
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

GAME_END = frozenset({GAME_WON, GAME_LOST})
SET_END = frozenset({SET_WON, SET_LOST})
# Events after which play has begun (the warm-up rule removes footage before the first).
PLAY_BEGINS = frozenset({MATCH_START, SET_START, GAME_START, STARTING_STATE})
# Chapter anchors (DESIGN.md §8).
CHAPTER_ANCHORS = frozenset({SET_START, GAME_START, STARTING_STATE})


def label(event_type: str) -> str:
    return LABELS.get(event_type, event_type)
