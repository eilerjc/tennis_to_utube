"""The Upload tool: upload videos to YouTube from the command line (DESIGN.md §12c).

    python -m tennis_to_utube.uploadtool --connect              sign in (opens the browser)
    python -m tennis_to_utube.uploadtool half1.mp4 half2.mp4 --game "Hawks" "Owls" --kids no
    python -m tennis_to_utube.uploadtool video.mp4 --title "…" --kids no
    python -m tennis_to_utube.uploadtool --run                  carry on with the queue
    python -m tennis_to_utube.uploadtool --status               list the queue
    python -m tennis_to_utube.uploadtool --disconnect

The same queue and engine as the app's Upload step (:mod:`uploadqueue`): an upload that
was interrupted — here, in the app, by a crash or a restart — resumes from its last
checkpoint the next time either runs. Settings: ``upload.toml`` (docs/upload.example.toml).
Ctrl+C stops; nothing is lost.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import matchfile, uploadpresets
from .config import Config, load_upload_config
from .uploadqueue import (
    ACTIVE, COMPLETE, FAILED, PAUSED, PRIVACY, QUEUED, TRANSFERRED, Job, Manager, QueueBusy,
    check_details, new_job, setup_log,
)
from .ytapi import ApiError, AuthError, NetworkError


def size_text(n: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} GB"


def speed_text(bytes_per_s: float) -> str:
    """Megabits per second, as internet speeds are quoted."""
    return f"{bytes_per_s * 8 / 1_000_000:.1f} Mbps"


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def kids_choice(text: str | None) -> bool | None:
    """"yes"/"no" → True/False; anything else → None (not answered)."""
    return {"yes": True, "no": False}.get((text or "").strip().lower())


def default_details(config: Config) -> dict[str, Any]:
    """What a new upload starts with (upload.toml ``[defaults]``); ``made_for_kids`` is
    None until the user has answered."""
    d = config.get("defaults")
    privacy = d["privacy"] if d["privacy"] in PRIVACY else "unlisted"
    return {"privacy": privacy, "category": str(d["category"]),
            "tags": [str(t) for t in d["tags"]], "playlist": str(d["playlist"]),
            "made_for_kids": kids_choice(d["made_for_kids"])}


def apply_link(job: Job) -> str | None:
    """Put a finished upload's video id into the match file it belongs to (``job.link``):
    the trimmed video's id (and its links are rewritten) or the full recording's. Returns
    what was done, or None if there is nothing to do (or the match has moved on: another
    trimmed video was made since)."""
    link = job.link or {}
    if not job.video_id or not link.get("match"):
        return None
    match_path = Path(link["match"])
    try:
        mf = matchfile.load(match_path)
    except (OSError, matchfile.MatchFileError):
        return None
    if link.get("target") == "trimmed":
        from .config import load_config, load_trim_config
        from .session import Session
        from .trimtool import relink

        out = mf.output or {}
        made = matchfile.resolve_source_path(out["path"], match_path) if out.get("path") else None
        if made is None or Path(made).resolve() != Path(job.path).resolve():
            return None
        try:
            if Path(made).stat().st_mtime_ns != job.mtime_ns:
                return None  # made again since: this upload is of the older video
        except OSError:
            return None
        mf.output["video_id"] = job.video_id
        matchfile.save(mf, match_path)
        relink(Session(mf, match_path, load_config()), load_trim_config())
        return f"{match_path.name}: the trimmed video's link is set and its links rewritten"
    mf.youtube["video_id"] = job.video_id
    matchfile.save(mf, match_path)
    return f"{match_path.name}: the full recording's link is set"


def _line(row: dict[str, Any]) -> str:
    text = f"  {row['status']:<12} {row['percent']:5.1f}%  {row['name']}"
    if row["status"] in ACTIVE:
        text += f"  {speed_text(row['speed'])}, {duration_text(row['eta_s'])} left"
    if row["url"]:
        text += f"  {row['url']}"
    if row["error"]:
        text += f"\n      {row['error']}"
    elif row["note"]:
        text += f"\n      {row['note']}"
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uploadtool", description="Upload videos to YouTube.")
    parser.add_argument("videos", nargs="*", type=Path)
    parser.add_argument("--connect", action="store_true", help="sign in to YouTube")
    parser.add_argument("--disconnect", action="store_true")
    parser.add_argument("--status", action="store_true", help="list the queue and stop")
    parser.add_argument("--run", action="store_true", help="carry on with the queue")
    parser.add_argument("--title")
    parser.add_argument("--description", default="")
    parser.add_argument("--tags", help="comma separated")
    parser.add_argument("--privacy", choices=PRIVACY)
    parser.add_argument("--playlist", help="playlist id")
    parser.add_argument("--kids", choices=("yes", "no"), help="made for kids? (required once)")
    parser.add_argument("--game", nargs=2, metavar=("TEAM_A", "TEAM_B"),
                        help="name the videos as the parts of a game")
    parser.add_argument("--date", help="game date (default: the first video's date)")
    parser.add_argument("--no-wait", action="store_true",
                        help="stop when the files are transferred; do not wait for processing")
    args = parser.parse_args(argv)

    config = load_upload_config()
    for warning in config.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    setup_log()
    try:
        manager = Manager(config).open()
    except QueueBusy as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        return _main(args, config, manager)
    except KeyboardInterrupt:
        print("\nStopped. Uploads resume from their checkpoint next time.")
        return 130
    finally:
        manager.close()


def _main(args: argparse.Namespace, config: Config, manager: Manager) -> int:
    auth = manager.auth
    if args.disconnect:
        auth.disconnect()
        print("Disconnected from YouTube.")
        return 0
    if args.connect:
        try:
            print("Opening the browser to sign in to YouTube…")
            auth.connect()
            channel = manager.api.channel()
        except (AuthError, ApiError, NetworkError) as exc:
            print(f"Not connected: {exc}", file=sys.stderr)
            return 1
        print(f"Connected to {channel['title'] if channel else 'YouTube (no channel)'}.")
    for warning in manager.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.videos:
        missing = [str(p) for p in args.videos if not p.is_file()]
        if missing:
            print("Not found: " + ", ".join(missing), file=sys.stderr)
            return 2
        details = default_details(config)
        kids = kids_choice(args.kids) if args.kids else details["made_for_kids"]
        if kids is None:
            print("Say whether the videos are made for kids: --kids yes or --kids no "
                  "(or set made_for_kids in upload.toml).", file=sys.stderr)
            return 2
        details["made_for_kids"] = kids
        if args.privacy:
            details["privacy"] = args.privacy
        if args.playlist is not None:
            details["playlist"] = args.playlist
        if args.tags is not None:
            details["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
        if args.game:
            date = args.date or uploadpresets.file_date(args.videos[0])
            texts = uploadpresets.game_details(len(args.videos), *args.game, date,
                                               notes=args.description)
        else:
            texts = [{"title": args.title if args.title and len(args.videos) == 1
                      else uploadpresets.plain_title(p), "description": args.description}
                     for p in args.videos]
        for text in texts:
            problems = check_details(text["title"], text["description"], details["tags"])
            if problems:
                print(f"{text['title']!r}: " + " ".join(problems), file=sys.stderr)
                return 2
        for path, text in zip(args.videos, texts):
            job = manager.add(new_job(path, **text, **details))
            print(f"Queued {job.name} as {job.title!r} ({size_text(job.size)}, {job.privacy})")

    rows = manager.snapshot()
    if args.status or not (args.videos or args.run):
        if not args.connect or args.status:
            print("\n".join(_line(r) for r in rows) if rows else "The queue is empty.")
        return 0
    if not auth.connected:
        print("Not connected to YouTube: run with --connect first.", file=sys.stderr)
        return 1

    manager.start()
    done = TRANSFERRED if args.no_wait else (COMPLETE,)
    last = 0.0
    linked: set[str] = set()
    while True:
        rows = manager.snapshot()
        for row in rows:
            if row["video_id"] and row["link"] and not row["link_done"] and row["id"] not in linked:
                linked.add(row["id"])
                said = apply_link(manager.jobs[row["id"]])
                manager.mark_linked(row["id"])
                if said:
                    print(said)
        pending = [r for r in rows if r["status"] not in (*done, FAILED, PAUSED)]
        if time.monotonic() - last >= 5 or not pending:
            last = time.monotonic()
            print(time.strftime("%H:%M:%S"))
            print("\n".join(_line(r) for r in rows))
        if not pending:
            break
        time.sleep(0.5)
    failed = [r for r in rows if r["status"] == FAILED]
    waiting = [r for r in rows if r["status"] in (PAUSED, QUEUED)]
    if waiting:
        print(f"{len(waiting)} paused: resume them in the app's Upload step.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
