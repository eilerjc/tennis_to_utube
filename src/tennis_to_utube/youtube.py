"""YouTube export: description chapters and per-event links (Markdown and CSV).

Times are whole seconds, rounded down. Links are ``https://youtu.be/<id>?t=<s>``; with
no video id yet (it is pasted after upload) the times are exported without URLs.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from typing import Any, Sequence

from . import catalog, names, structure
from .chapters import Chapter, chapter_for, derive_chapters, youtube_chapters
from .config import Settings
from .flow import analyze_match
from .issues import Issue
from .matchfile import Event, MatchFile
from .scoring import Analysis, set_entry, set_text
from .trim import TrimPlan, remap_events


def timestamp(seconds: int) -> str:
    """YouTube style: ``M:SS``, or ``H:MM:SS`` from one hour."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(text: str) -> str | None:
    """Video id from a pasted link or id: youtu.be/ID, watch?v=ID, /shorts/ID, /live/ID."""
    text = text.strip()
    if _ID_RE.match(text):
        return text
    url = urlparse(text if "://" in text else "https://" + text)
    host = (url.hostname or "").lower()
    candidate = None
    if host.endswith("youtu.be"):
        candidate = url.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        candidate = parse_qs(url.query).get("v", [None])[0]
        parts = url.path.strip("/").split("/")
        if candidate is None and len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
            candidate = parts[1]
    return candidate if candidate and _ID_RE.match(candidate) else None


def link_url(video_id: str, seconds: int) -> str:
    return f"https://youtu.be/{video_id}?t={int(seconds)}"


def description(chapters: Sequence[Chapter]) -> str:
    return "".join(f"{timestamp(c.t_ms // 1000)} {c.title}\n" for c in chapters)


def describe(e: Event, match: dict[str, Any]) -> str:
    """One-line label, names exactly as typed."""
    text = catalog.label(e.type)
    if e.type == catalog.SCORE_STATE and structure.score_text(e):
        text += f" {structure.score_text(e)}"
    if e.result in ("A", "B"):
        text += f" — won by {names.side_name(match, e.result)}"
        shot = e.details.get("shot") if e.type == catalog.POINT else None
        if shot and e.details.get("shot_side") in ("A", "B"):
            text += f" ({catalog.label(shot).lower()} by {names.side_name(match, e.details['shot_side'])})"
    elif e.result == "unknown":
        text += " — winner unknown"
    who = e.player or (names.side_name(match, e.side) if e.side in ("A", "B") else None)
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
    summary: str = ""  # "Emma vs Sara: 6–4, 3–6, [10–8] — Emma won"


def match_summary(mf: MatchFile, analysis: Analysis) -> str:
    """Players and, when known, the result: the engine's final score if it is certain,
    else the Ending state (final score entered from the scorebook)."""
    head = f"{names.side_name(mf.match, 'A')} vs {names.side_name(mf.match, 'B')}"
    final = analysis.steps[-1].view if analysis.steps else None
    if final is not None and final.winner and final.sets:
        sets = ", ".join(set_text(s) for s in final.sets)
        return f"{head}: {sets} — {names.side_name(mf.match, final.winner)} won"
    ending = [e for e in mf.sorted_events() if e.type == catalog.ENDING_STATE]
    if ending:
        entries = [set_entry(p) for p in ending[-1].details.get("sets", [])]
        if entries and all(entries):
            sets = ", ".join(set_text(s) for s in entries)
            return f"{head}: {sets} (final score from the scorebook)"
    return head


def build_export(mf: MatchFile, plan: TrimPlan, settings: Settings) -> Export:
    remapped, issues = remap_events(mf.events, plan)
    analysis = analyze_match(mf)
    derived = derive_chapters(mf.events, remapped, settings, plan.total_out_ms, analysis)
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
    return Export(chapters, description(chapters), rows, issues, match_summary(mf, analysis))


def links_markdown(rows: Sequence[LinkRow], title: str = "Events", summary: str = "") -> str:
    lines = [f"# {title}", ""] + ([summary, ""] if summary else [])
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
