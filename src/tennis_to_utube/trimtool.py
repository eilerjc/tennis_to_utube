"""The Trim tool: make the trimmed video of a match, and its chapters and links (DESIGN.md §9).

    python -m tennis_to_utube.trimtool GX010008.match.json            make the video
    python -m tennis_to_utube.trimtool GX010008.match.json --cuts     list the cuts only
    python -m tennis_to_utube.trimtool GX010008.match.json --links-only [--video-id ID]

Settings come from ``trim.toml`` (:func:`config.load_trim_config`): the default rules,
the serve lead-in, and the trimmed video's link lead-ins and chapter gap. Per-match
choices (rules, unticked cuts) are read from the match file; the made video's plan, path
and YouTube id are stored in its ``output``. Next to the match file it writes
``<match> trimmed.mp4``, ``<match> trimmed chapters.txt`` and ``<match> trimmed links``
(``.md`` and ``.csv``).

``--from-gui`` is how the Marker's Trim screen runs it: progress as ``PROGRESS <0..1|-1>
<text>`` lines, the result as one ``RESULT <json>`` line, "cancel" (or end of input) on
stdin stops it, and the match file is not written (the GUI stores the result itself).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import matchfile
from .config import Config, effective_settings, load_config, load_trim_config
from .issues import Issue
from .probe import ProbeKeyframes, Tools, ToolError
from .session import Session
from .trim import TrimError, TrimPlan, plan_to_dict, remap_events, run_trim
from .youtube import build_export, links_csv, links_markdown

Report = Callable[[float, str], None]  # fraction 0..1 (or -1: unknown), text


def match_stem(match_path: Path) -> str:
    return match_path.name.removesuffix(".match.json")


def default_output(match_path: Path, trim_config: Config | None = None) -> Path:
    pattern = trim_config.get("output.name") if trim_config else "{stem} trimmed.mp4"
    return match_path.with_name(pattern.format(stem=match_stem(match_path)))


def export_paths(match_path: Path) -> tuple[Path, Path, Path]:
    """Chapters (.txt) and links (.md, .csv) of the trimmed video."""
    base = f"{match_stem(match_path)} trimmed"
    return (match_path.with_name(f"{base} chapters.txt"), match_path.with_name(f"{base} links.md"),
            match_path.with_name(f"{base} links.csv"))


@dataclass
class Made:
    output: Path
    plan: TrimPlan
    output_dict: dict[str, Any]  # what goes into the match file's ``output``
    files: list[Path] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def write_exports(session: Session, plan: TrimPlan, video_id: str | None,
                  trim_config: Config) -> tuple[list[Path], list[Issue]]:
    """Write the trimmed video's chapters and links next to the match file."""
    mf = session.mf
    settings = effective_settings(trim_config, mf.settings)
    export = build_export(mf, plan, settings, video_id=video_id)
    chapters_path, md_path, csv_path = export_paths(session.path)
    title = f"{match_stem(session.path)} (trimmed)"
    chapters_path.write_text(export.description or
                             "(No chapters: YouTube needs at least 3 chapters of 10 s or more.)\n",
                             encoding="utf-8")
    md_path.write_text(links_markdown(export.links, title, export.summary), encoding="utf-8")
    csv_path.write_text(links_csv(export.links), encoding="utf-8-sig", newline="")
    return [chapters_path, md_path, csv_path], list(export.issues)


def _stored_path(output: Path, match_path: Path) -> str:
    try:
        return Path(os.path.relpath(output, match_path.parent)).as_posix()
    except ValueError:  # another drive (Windows)
        return str(output)


def make_video(session: Session, output: Path, trim_config: Config, tools: Tools,
               report: Report = lambda f, t: None, cancel: threading.Event | None = None) -> Made:
    """Plan the cuts exactly, write the video (stream copy), check it, write chapters/links.
    The match file is not changed: the caller stores ``Made.output_dict``."""
    report(-1, "Finding keyframes…")
    keyframes = ProbeKeyframes(session.source_paths(), tools,
                               durations_ms=[s.duration_ms for s in session.mf.sources])
    plan = session.plan(keyframes)
    report(0.0, "Writing video…")
    result = run_trim(plan, session.source_paths(), output, tools, cancel=cancel,
                      progress=lambda f: report(f, f"Writing video… {f:.0%}"))
    report(1.0, "Writing chapters and links…")
    out = plan_to_dict(result.plan, _stored_path(result.output, session.path))
    out["video_id"] = None  # a new file: it needs its own upload
    files, export_issues = write_exports(session, result.plan, None, trim_config)
    _, remap_issues = remap_events(session.mf.events, result.plan)
    return Made(result.output, result.plan, out, files, list(result.issues) + remap_issues + export_issues)


def relink(session: Session, trim_config: Config) -> tuple[list[Path], list[Issue]]:
    """Rewrite the chapters and links of the video made earlier (e.g. after its YouTube id
    was entered)."""
    plan = session.output_plan()
    if plan is None:
        raise TrimError("no trimmed video has been made for this match yet")
    return write_exports(session, plan, (session.mf.output or {}).get("video_id"), trim_config)


# -- command line ------------------------------------------------------------------------


def command(match_path: Path, *args: str) -> tuple[list[str], dict[str, str]]:
    """argv and environment to run the tool as its own process (as the GUI does), using
    this Python and this copy of the package even when it is not installed."""
    package_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, env.get("PYTHONPATH")) if p)
    env["PYTHONIOENCODING"] = "utf-8"  # names and paths in RESULT/PROGRESS lines
    return [sys.executable, "-m", "tennis_to_utube.trimtool", str(match_path), *args], env


def _clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _watch_stdin(cancel: threading.Event) -> None:
    """--from-gui: "cancel", or the GUI going away (end of input), stops the work."""
    def run() -> None:
        for line in sys.stdin:
            if line.strip() == "cancel":
                break
        cancel.set()

    threading.Thread(target=run, daemon=True).start()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tennis_to_utube.trimtool",
                                     description="Make the trimmed video of a match (lossless), "
                                                 "and its YouTube chapters and links.")
    parser.add_argument("match", type=Path, help="the .match.json file")
    parser.add_argument("-o", "--output", type=Path, help="video file (default from trim.toml)")
    parser.add_argument("--cuts", action="store_true", help="only list the cuts")
    parser.add_argument("--links-only", action="store_true",
                        help="only rewrite chapters and links of the video made earlier")
    parser.add_argument("--video-id", help="YouTube link or id of the trimmed video (stored)")
    parser.add_argument("--trim-config", type=Path, help="trim.toml to use instead of the user's")
    parser.add_argument("--from-gui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    gui = args.from_gui

    def say(text: str) -> None:
        print(text, flush=True)

    def report(fraction: float, text: str) -> None:
        if gui:
            say(f"PROGRESS {fraction:.4f} {text}")
        else:
            print(f"\r{text:<40}", end="", flush=True)

    try:
        mf = matchfile.load(args.match)
    except (OSError, matchfile.MatchFileError) as exc:
        print(f"Cannot read {args.match}: {exc}", file=sys.stderr)
        return 1
    trim_config = load_trim_config(args.trim_config)
    config = load_config()
    for w in trim_config.warnings + config.warnings:
        print(f"warning: {w}", file=sys.stderr)
    session = Session(mf, args.match, config, trim_config)

    if args.cuts:
        for c in session.cuts():
            mark = "x" if c.enabled else " "
            say(f"[{mark}] {_clock(c.start_ms)}–{_clock(c.end_ms)}  {c.label}")
        return 0

    try:
        if args.video_id is not None:
            from .youtube import parse_video_id
            vid = parse_video_id(args.video_id) if args.video_id else None
            if args.video_id and vid is None:
                print(f"Not a YouTube link or id: {args.video_id}", file=sys.stderr)
                return 1
            if not mf.output:
                raise TrimError("no trimmed video has been made for this match yet")
            mf.output["video_id"] = vid
            if not gui:
                matchfile.save(mf, args.match)
        if args.links_only:
            files, issues = relink(session, trim_config)
            if gui:
                say("RESULT " + json.dumps({"files": [str(p) for p in files]}))
            else:
                say("Wrote " + ", ".join(p.name for p in files))
            return 0
        cancel = threading.Event()
        if gui:
            _watch_stdin(cancel)
        output = args.output or default_output(args.match, trim_config)
        made = make_video(session, output, trim_config, Tools.from_config(config), report, cancel)
    except TrimError as exc:
        if str(exc) == "cancelled":
            say("CANCELLED" if gui else "\nCancelled.")
            return 2
        print(f"\nCannot trim: {exc}", file=sys.stderr)
        return 1
    except (ToolError, OSError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    if gui:
        say("RESULT " + json.dumps({
            "video": str(made.output), "output": made.output_dict,
            "files": [str(p) for p in made.files],
            "issues": [{"code": i.code, "message": i.message, "t_ms": i.t_ms,
                        "severity": i.severity} for i in made.issues]}))
        return 0
    mf.output = made.output_dict
    matchfile.save(mf, args.match)
    say(f"\nMade {made.output} ({_clock(made.plan.total_out_ms)})")
    say("Wrote " + ", ".join(p.name for p in made.files))
    for i in made.issues:
        if i.severity != "info":
            say(f"{i.severity}: {i.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
