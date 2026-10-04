"""Player names: defaults, full side names, and short names for buttons.

Events refer to players by position, not name: ``"A1"``, ``"A2"`` (side A's players in
the order entered), ``"B1"``, ``"B2"``. Renaming a player therefore updates every event.
Names are stored and exported exactly as typed. When none were entered, players are
"Player 1" and "Player 2" (doubles: 1 & 2 on side A, 3 & 4 on side B); these are display
defaults and are not written to the match file.

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


def parse_ref(ref: Any) -> tuple[str, int] | None:
    """``"B2"`` → ``("B", 1)`` (side, 0-based index); None if not a player reference."""
    if (isinstance(ref, str) and len(ref) >= 2 and ref[0] in SIDES and ref[1:].isdigit()
            and int(ref[1:]) >= 1):
        return ref[0], int(ref[1:]) - 1
    return None


def ref_side(ref: Any) -> str | None:
    """Side of a player reference, or of a bare side ("A"/"B")."""
    if ref in SIDES:
        return ref
    parsed = parse_ref(ref)
    return parsed[0] if parsed else None


def partner(ref: str) -> str:
    """Doubles partner: A1 ↔ A2."""
    side, i = parse_ref(ref)
    return f"{side}{2 - i}" if i in (0, 1) else ref


def player_name(match: dict[str, Any], ref: str) -> str | None:
    """Full name for a reference ("A1" → "Emma Smith"); None if there is no such player."""
    parsed = parse_ref(ref)
    if parsed is None:
        return None
    side, i = parsed
    names = players(match, side)
    return names[i] if i < len(names) else None


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
