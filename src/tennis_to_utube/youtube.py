"""YouTube export: description chapters and per-event links (Markdown and CSV).

Times are whole seconds, rounded down. Links are ``https://youtu.be/<id>?t=<s>``; with
no video id yet (it is pasted after upload) the times are exported without URLs.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Sequence

from . import catalog, structure
from .chapters import Chapter, chapter_for, derive_chapters, youtube_chapters
from .config import Settings
from .issues import Issue
from .matchfile import Event, MatchFile
from .trim import TrimPlan, remap_events


def timestamp(seconds: int) -> str:
    """YouTube style: ``M:SS``, or ``H:MM:SS`` from one hour."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def link_url(video_id: str, seconds: int) -> str:
    return f"https://youtu.be/{video_id}?t={int(seconds)}"


def description(chapters: Sequence[Chapter]) -> str:
    return "".join(f"{timestamp(c.t_ms // 1000)} {c.title}\n" for c in chapters)


def side_names(match: dict[str, Any], side: str) -> str:
    players = match.get("sides", {}).get(side, {}).get("players") or []
    return " & ".join(players) if players else f"Side {side}"


def describe(e: Event, match: dict[str, Any]) -> str:
    """One-line label, names exactly as typed."""
    text = catalog.label(e.type)
    if e.type == catalog.SCORE_STATE and structure.score_text(e):
        text += f" {structure.score_text(e)}"
    if e.result in ("A", "B"):
        text += f" — won by {side_names(match, e.result)}"
    elif e.result == "unknown":
        text += " — winner unknown"
    who = e.player or (side_names(match, e.side) if e.side in ("A", "B") else None)
    if who and e.result is None:
        text += f" — {who}"
    if e.tags:
        text += f" [{', '.join(e.tags)}]"
    if e.note:
        text += f": {e.note}"
    return text


@dataclass(frozen=True)
class LinkRow:
    chapter: str
    seconds: int
    event: Event
    text: str
    url: str | None


@dataclass(frozen=True)
class Export:
    chapters: list[Chapter]  # as they go into the description (may be empty)
    description: str
    links: list[LinkRow]
    issues: list[Issue]


def build_export(mf: MatchFile, plan: TrimPlan, settings: Settings) -> Export:
    remapped, issues = remap_events(mf.events, plan)
    derived = derive_chapters(mf.events, remapped, settings, plan.total_out_ms)
    chapters, ch_issues = youtube_chapters(derived, plan.total_out_ms)
    issues += ch_issues
    grouping = chapters or derived
    video_id = mf.video_id
    rows = []
    for r in sorted(remapped, key=lambda r: r.out_ms):
        seconds = r.link_ms(settings.lead_in_for(r.event.type)) // 1000
        ch = chapter_for(grouping, r.out_ms)
        rows.append(LinkRow(ch.title if ch else "", seconds, r.event, describe(r.event, mf.match),
                            link_url(video_id, seconds) if video_id else None))
    return Export(chapters, description(chapters), rows, issues)


def links_markdown(rows: Sequence[LinkRow], title: str = "Events") -> str:
    lines = [f"# {title}", ""]
    current = None
    for row in rows:
        if row.chapter != current:
            current = row.chapter
            lines += ["", f"## {current or 'Events'}", ""]
        stamp = timestamp(row.seconds)
        lines.append(f"- [{stamp}]({row.url}) {row.text}" if row.url else f"- {stamp} {row.text}")
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def links_csv(rows: Sequence[LinkRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["chapter", "time", "seconds", "event_id", "type", "description", "url"])
    for row in rows:
        w.writerow([row.chapter, timestamp(row.seconds), row.seconds, row.event.id, row.event.type,
                    row.text, row.url or ""])
    return buf.getvalue()
