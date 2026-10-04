"""Match file: versioned JSON next to the video(s), e.g. ``GX010008.match.json``.

Unknown fields (top level, per source, per event) are kept and written back unchanged,
so files from newer versions or other tools never lose data. Files with a newer
``format_version`` than this code understands are refused rather than silently
downgraded. Saving is atomic and keeps the previous version as ``<name>.bak``.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
BACKUP_SUFFIX = ".bak"


class MatchFileError(ValueError):
    pass


class UnsupportedVersionError(MatchFileError):
    pass


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise MatchFileError(f"{what} must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise MatchFileError(f"{what} must be an integer, got {value!r}")


def _split(d: dict[str, Any], known: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    return ({k: d[k] for k in known if k in d}, {k: v for k, v in d.items() if k not in known})


_SOURCE_FIELDS = ("path", "duration_ms", "codec", "width", "height", "fps", "pix_fmt")


@dataclass
class Source:
    path: str  # relative to the match file when possible, "/" separated
    duration_ms: int
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: str | None = None  # exact rational as ffprobe reports it, e.g. "60000/1001"
    pix_fmt: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Source:
        if not isinstance(d, dict) or not isinstance(d.get("path"), str):
            raise MatchFileError(f"source needs a 'path': {d!r}")
        known, extra = _split(d, _SOURCE_FIELDS)
        known["duration_ms"] = _int(known.get("duration_ms"), f"duration_ms of {d['path']}")
        if known["duration_ms"] <= 0:
            raise MatchFileError(f"duration_ms of {d['path']} must be positive")
        return cls(**known, extra=extra)

    def to_dict(self) -> dict[str, Any]:
        return {**{k: getattr(self, k) for k in _SOURCE_FIELDS}, **self.extra}


_EVENT_FIELDS = (
    "id", "t_ms", "type", "side", "player", "result", "observed", "called",
    "source", "confidence", "inferred", "tags", "details", "note",
)


@dataclass
class Event:
    id: str
    t_ms: int  # joined (concatenated source) timeline, integer ms
    type: str
    side: str | None = None
    player: str | None = None
    result: str | None = None
    observed: str | None = None
    called: str | None = None
    source: str = "human"
    confidence: float | None = None
    inferred: bool = False
    tags: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_observed(self) -> str | None:
        """What the video shows; defaults to the call when only that was given."""
        return self.observed if self.observed is not None else self.called

    @property
    def effective_called(self) -> str | None:
        """What was ruled; defaults to the observation when only that was given."""
        return self.called if self.called is not None else self.observed

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        if not isinstance(d, dict):
            raise MatchFileError(f"event must be an object: {d!r}")
        if not isinstance(d.get("id"), str) or not isinstance(d.get("type"), str):
            raise MatchFileError(f"event needs string 'id' and 'type': {d!r}")
        known, extra = _split(d, _EVENT_FIELDS)
        known["t_ms"] = _int(known.get("t_ms"), f"t_ms of event {d['id']}")
        if known["t_ms"] < 0:
            raise MatchFileError(f"t_ms of event {d['id']} is negative")
        # JSON null for collection/text fields means "empty".
        for name, empty in (("tags", list), ("details", dict), ("note", str)):
            if known.get(name, ...) is None:
                known[name] = empty()
        if known.get("source", ...) is None:
            known["source"] = "human"
        if known.get("inferred", ...) is None:
            known["inferred"] = False
        return cls(**known, extra=extra)

    def to_dict(self) -> dict[str, Any]:
        return {**{k: getattr(self, k) for k in _EVENT_FIELDS}, **self.extra}


def default_match(format_preset: str = "standard_mtb") -> dict[str, Any]:
    return {
        "kind": "singles",
        "format": {"preset": format_preset},  # scoring.PRESETS name plus any overrides
        "sides": {
            "A": {"players": ["Player 1"], "role": "ours"},
            "B": {"players": ["Player 2"], "role": "opponent"},
        },
    }


_TOP_FIELDS = ("format_version", "sources", "match", "settings", "events", "youtube", "next_event_seq")


@dataclass
class MatchFile:
    sources: list[Source] = field(default_factory=list)
    match: dict[str, Any] = field(default_factory=default_match)
    settings: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    youtube: dict[str, Any] = field(default_factory=lambda: {"video_id": None})
    # Sequence for new event ids, so ids are never reused even after deletions.
    next_event_seq: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    # -- events -------------------------------------------------------------

    def new_event_id(self) -> str:
        used = {e.id for e in self.events}
        while True:
            event_id = f"e_{self.next_event_seq:04d}"
            self.next_event_seq += 1
            if event_id not in used:
                return event_id

    def add_event(self, t_ms: int, type: str, **fields: Any) -> Event:
        event = Event(id=self.new_event_id(), t_ms=t_ms, type=type, **fields)
        self.events.append(event)
        return event

    def event(self, event_id: str) -> Event:
        for e in self.events:
            if e.id == event_id:
                return e
        raise KeyError(event_id)

    def remove_event(self, event_id: str) -> Event:
        e = self.event(event_id)
        self.events.remove(e)
        return e

    def sorted_events(self) -> list[Event]:
        """Events by time; ties keep insertion order (the order they were marked)."""
        return sorted(self.events, key=lambda e: e.t_ms)

    @property
    def video_id(self) -> str | None:
        return self.youtube.get("video_id")

    # -- (de)serialization --------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MatchFile:
        if not isinstance(d, dict):
            raise MatchFileError("match file must be a JSON object")
        if "format_version" not in d:
            raise MatchFileError("not a match file (no format_version)")
        version = _int(d["format_version"], "format_version")
        if version > FORMAT_VERSION:
            raise UnsupportedVersionError(
                f"match file format {version} is newer than this app supports "
                f"({FORMAT_VERSION}); update the app"
            )
        d = _migrate(d, version)
        known, extra = _split(d, _TOP_FIELDS)
        for name, typ in (("sources", list), ("events", list), ("match", dict),
                          ("settings", dict), ("youtube", dict)):
            if name in known and not isinstance(known[name], typ):
                raise MatchFileError(f"'{name}' must be a {typ.__name__}")
        events = [Event.from_dict(e) for e in known.get("events", [])]
        ids = [e.id for e in events]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise MatchFileError(f"duplicate event ids: {', '.join(dupes)}")
        mf = cls(
            sources=[Source.from_dict(s) for s in known.get("sources", [])],
            match=known.get("match", default_match()),
            settings=known.get("settings", {}),
            events=events,
            youtube=known.get("youtube", {"video_id": None}),
            extra=extra,
        )
        seq = known.get("next_event_seq")
        mf.next_event_seq = max(_int(seq, "next_event_seq") if seq is not None else 1, _max_seq(ids) + 1)
        return mf

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "sources": [s.to_dict() for s in self.sources],
            "match": self.match,
            "settings": self.settings,
            "events": [e.to_dict() for e in self.events],
            "youtube": self.youtube,
            "next_event_seq": self.next_event_seq,
            **self.extra,
        }


def _max_seq(ids: list[str]) -> int:
    best = 0
    for i in ids:
        if i.startswith("e_") and i[2:].isdigit():
            best = max(best, int(i[2:]))
    return best


def _migrate(d: dict[str, Any], version: int) -> dict[str, Any]:
    """Upgrade older formats to FORMAT_VERSION. Version 1 is the first; nothing to do."""
    return d


def loads(text: str) -> MatchFile:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MatchFileError(f"invalid JSON: {exc}") from exc
    return MatchFile.from_dict(data)


def dumps(mf: MatchFile) -> str:
    return json.dumps(mf.to_dict(), indent=2, ensure_ascii=False) + "\n"


def load(path: str | os.PathLike[str]) -> MatchFile:
    return loads(Path(path).read_text(encoding="utf-8"))


def save(mf: MatchFile, path: str | os.PathLike[str], *, backup: bool = True) -> None:
    """Write atomically; the previous file (if any) is kept as ``<path>.bak``."""
    path = Path(path)
    text = dumps(mf)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    os.replace(tmp, path)


def default_match_path(first_video: str | os.PathLike[str]) -> Path:
    p = Path(first_video)
    return p.with_name(p.stem + ".match.json")


def relative_source_path(media: str | os.PathLike[str], match_path: str | os.PathLike[str]) -> str:
    """Path to store for ``media``: relative to the match file's folder when possible."""
    media = Path(media).resolve()
    base = Path(match_path).resolve().parent
    try:
        return Path(os.path.relpath(media, base)).as_posix()
    except ValueError:  # different drive on Windows
        return media.as_posix()


def resolve_source_path(stored: str, match_path: str | os.PathLike[str]) -> Path:
    p = Path(stored)
    if p.is_absolute():
        return p
    return (Path(match_path).resolve().parent / p).resolve()
