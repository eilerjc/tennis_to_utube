"""Titles and descriptions for uploads (DESIGN.md §12c): any video (its file name), a
game in parts (the Game preset: "Team A vs Team B - First Half"), or a tennis match (the
players, the result and the chapters from the match file). Pure; everything it proposes
is editable before the upload starts.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .uploadqueue import DESCRIPTION_MAX, TITLE_MAX

FULL_GAME = "Full Game"
HALVES = ("First Half", "Second Half")
# Offered for each file of a game (free text is allowed too).
GAME_PARTS = (*HALVES, "Overtime", "Overtime 2", FULL_GAME, "First Quarter", "Second Quarter",
              "Third Quarter", "Fourth Quarter")


def clean(text: str) -> str:
    """YouTube refuses < and > in titles and descriptions."""
    return text.replace("<", "‹").replace(">", "›")


def fit_title(title: str) -> str:
    title = clean(" ".join(title.split()))
    return title if len(title) <= TITLE_MAX else title[:TITLE_MAX - 1].rstrip() + "…"


def fit_description(text: str) -> str:
    text = clean(text).strip()
    while len(text.encode("utf-8")) > DESCRIPTION_MAX:
        text = text[:-50]
    return text


def file_date(path: str | os.PathLike[str]) -> str:
    """The file's date, ``YYYY-MM-DD`` (its modified time: for a camera file, when the
    recording ended)."""
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def plain_title(path: str | os.PathLike[str]) -> str:
    return fit_title(Path(path).stem.replace("_", " "))


def game_parts(count: int) -> list[str]:
    """The usual parts for ``count`` files in order: one is the full game, two are the
    halves, more are overtime."""
    if count <= 1:
        return [FULL_GAME][:count]
    extra = ["Overtime" if i == 0 else f"Overtime {i + 1}" for i in range(count - 2)]
    return [*HALVES, *extra]


def game_title(team_a: str, team_b: str, part: str = "") -> str:
    teams = f"{team_a.strip() or 'Team A'} vs {team_b.strip() or 'Team B'}"
    return fit_title(f"{teams} - {part.strip()}" if part.strip() else teams)


def game_description(team_a: str, team_b: str, date: str, part: str = "", notes: str = "") -> str:
    lines = [game_title(team_a, team_b), date.strip(), part.strip(), "", notes.strip()]
    return fit_description("\n".join(lines[:3]).strip() + ("\n\n" + notes.strip() if notes.strip() else ""))


def game_details(count: int, team_a: str, team_b: str, date: str,
                 parts: Sequence[str] | None = None, notes: str = "") -> list[dict[str, str]]:
    """Title and description for each file of a game."""
    parts = list(parts) if parts is not None else game_parts(count)
    return [{"title": game_title(team_a, team_b, part),
             "description": game_description(team_a, team_b, date, part, notes)}
            for part in parts[:count]]


def match_details(summary: str, date: str, chapters: str = "", trimmed: bool = False) -> dict[str, str]:
    """A tennis match: ``summary`` is ``youtube.match_summary`` ("A vs B: 6-3, 6-4 — A
    won"), ``chapters`` the description chapters of the video being uploaded."""
    players = summary.split(":", 1)[0].strip()
    title = fit_title(f"{players} - {date}" if date else players)
    lines = [summary, date]
    if trimmed:
        lines.append("Trimmed: warm-up and breaks removed.")
    description = "\n".join(line for line in lines if line)
    if chapters.strip():
        description += "\n\n" + chapters.strip()
    return {"title": title, "description": fit_description(description)}
