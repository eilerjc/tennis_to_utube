"""Player names: defaults, full side names, and short names for buttons.

Names are stored (in the match and in events) and exported exactly as typed. New matches
start with "Player 1" and "Player 2" (doubles: 1 & 2 on side A, 3 & 4 on side B), and any
missing name shows as its "Player N" default. Renaming is a find/replace over the match
file (:func:`rename_player`); the app tells the user how many places changed.

Short names (buttons and other tight spots) are the first 4 letters of the first name plus
the last initial: "Alexandra Jones" → "Alex J", "Player 1" → "Play 1", "Emma" → "Emma".
If two players would get the same short name, the part that tells them apart is
lengthened until they differ: "Alex Sm" / "Alex Sc", or "Alexandr S" / "Alexande S".
"""

from __future__ import annotations

from typing import Any

SIDES = ("A", "B")
FIRST_LETTERS = 4


def players(match: dict[str, Any], side: str) -> list[str]:
    """Names on ``side``, with "Player N" filling any that were not entered."""
    per_side = 2 if match.get("kind") == "doubles" else 1
    entered = match.get("sides", {}).get(side, {}).get("players") or []
    base = SIDES.index(side) * per_side
    count = max(per_side, len(entered))
    return [entered[i] if i < len(entered) and str(entered[i]).strip() else f"Player {base + i + 1}"
            for i in range(count)]


def side_of(match: dict[str, Any] | None, who: Any) -> str | None:
    """Side of a player name (or of a bare side "A"/"B"); None if unknown or ambiguous."""
    if who in SIDES:
        return who
    if match is None or not isinstance(who, str):
        return None
    found = [side for side in SIDES if who in players(match, side)]
    return found[0] if len(found) == 1 else None


def partner_of(match: dict[str, Any], name: str) -> str | None:
    """The other player on the same side (doubles)."""
    side = side_of(match, name)
    if side is None:
        return None
    others = [n for n in players(match, side) if n != name]
    return others[0] if len(others) == 1 else None


def rename_player(mf: Any, old: str, new: str) -> int:
    """Find/replace a player's name in the match and every event; returns the count.

    Refuses a name another player already has (they could no longer be told apart).
    """
    new = new.strip()
    if not new:
        raise ValueError("a player needs a name")
    everyone = [n for side in SIDES for n in players(mf.match, side)]
    if old not in everyone:
        raise ValueError(f"no player called {old!r}")
    if new != old and new in everyone:
        raise ValueError(f"another player is already called {new!r}")
    count = 0
    for side in SIDES:
        entry = mf.match.setdefault("sides", {}).setdefault(side, {})
        current = players(mf.match, side)
        if old in current:
            entry["players"] = [new if n == old else n for n in current]
            count += 1
    for e in mf.events:
        if e.player == old:
            e.player = new
            count += 1
        if e.details.get("server") == old:
            e.details["server"] = new
            count += 1
    return count


def side_name(match: dict[str, Any], side: str) -> str:
    """Full names as typed, e.g. "Emma" or "Emma Smith & Ana Perez"."""
    return " & ".join(players(match, side))


def _short(name: str, first: int, last: int) -> str:
    parts = name.split()
    if not parts:
        return name
    text = parts[0][:first]
    if len(parts) > 1:
        text += " " + parts[-1][:last]
    return text


def short_names(match: dict[str, Any], first_letters: int = FIRST_LETTERS) -> dict[str, list[str]]:
    """Short name of every player per side, distinct across the match where possible."""
    full = [(side, n) for side in SIDES for n in players(match, side)]
    lengths = [[first_letters, 1] for _ in full]
    while True:  # lengthen colliding names; ends because lengths only grow up to the name
        shorts = [_short(n, *lengths[i]) for i, (_, n) in enumerate(full)]
        grew = False
        for s in {s for s in shorts if shorts.count(s) > 1}:
            group = [i for i, t in enumerate(shorts) if t == s]
            lasts = {full[i][1].split()[-1] if len(full[i][1].split()) > 1 else "" for i in group}
            # Grow the part that tells them apart: the last name if those differ
            # ("Alex Sm" / "Alex Sc"), otherwise the first name ("Alexandr S" / "Alexande S").
            part = 1 if len(lasts) > 1 else 0
            for i in group:
                words = full[i][1].split()
                if not words or (part == 1 and len(words) < 2):
                    continue
                word = words[-1] if part == 1 else words[0]
                if lengths[i][part] < len(word):
                    lengths[i][part] += 1
                    grew = True
        if not grew:
            break
    out: dict[str, list[str]] = {side: [] for side in SIDES}
    for i, (side, n) in enumerate(full):
        out[side].append(_short(n, *lengths[i]))
    return out


def short_side_names(match: dict[str, Any], first_letters: int = FIRST_LETTERS) -> dict[str, str]:
    """For button labels: {"A": "Emma S", "B": "Sara/Ana P"}."""
    return {side: "/".join(names) for side, names in short_names(match, first_letters).items()}
