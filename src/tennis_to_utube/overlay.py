"""The Overlay tool: burn a scoreboard into the trimmed (or full) video (DESIGN.md §9a).

    python -m tennis_to_utube.overlay GX010008.match.json                  trimmed video
    python -m tennis_to_utube.overlay GX010008.match.json --full           full recording
    python -m tennis_to_utube.overlay GX010008.match.json --preview 12:30  20 s to check the look
    python -m tennis_to_utube.overlay GX010008.match.json --png 12:30      just the board

Settings come from ``overlay.toml`` (:func:`config.load_overlay_config`). What the board
shows and when is :mod:`scoreboard`; here each distinct board is drawn once with Pillow
(text is measured, so the box fits full names), the PNGs are listed with their durations
in an ffconcat file, and ffmpeg overlays that stream on the video and re-encodes it (NVENC
by default). The overlay video replaces the plain one on YouTube, so it uses that video's
chapters and links; the match file is not changed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import matchfile
from .config import Config, load_config, load_overlay_config
from .flow import analyze_match
from .issues import Issue
from .playback import clock_text, parse_clock
from .probe import Tools, ToolError, probe
from .scoreboard import (POINTS_SETTINGS, Board, Shown, board_at, board_changes, boards_on,
                         shows_points)
from .session import Session
from .trim import TrimError, TrimPlan, _run_ffmpeg, _sec, concat_script, plan_trim
from .trimtool import match_stem

Report = Callable[[float, str], None]  # fraction 0..1 (or -1: unknown), text
CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


class OverlayError(RuntimeError):
    pass


# -- drawing (Pillow) ----------------------------------------------------------------------


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 (needed only to draw)
    except ImportError as exc:
        raise OverlayError("the Overlay tool needs Pillow: run overlay.bat, or "
                           "pip install Pillow") from exc
    return Image, ImageDraw, ImageFont


def _font(name: str, size: int):
    _, _, ImageFont = _pil()
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size)


def _rgba(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) != 6:
        c = "000000"
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), round(255 * max(0.0, min(1.0, alpha)))


def _mix(color: str, other: str, amount: float) -> str:
    a, b = _rgba(color), _rgba(other)
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a[:3], b[:3]))


class Painter:
    """Draws boards, all on one canvas size (the widest board) so the overlay stream never
    changes size. Sizes scale with the video height."""

    def __init__(self, config: Config, video_height: int, boards: Sequence[Board]):
        self.style = config.get("board")
        self.corner = self.style["corner"] if self.style["corner"] in CORNERS else "top_left"
        self.row = max(8, round(video_height * self.style["row_height"]))
        self.font = _font(self.style["font"], max(6, round(self.row * 0.6)))
        self.small = _font(self.style["font"], max(4, round(self.row * 0.36)))
        self.pad = max(2, round(self.row * 0.3))
        self.dot_w = max(4, round(self.row * 0.8))
        self.cell_w = max(6, round(self.row * 1.05))
        self.points_w = max(8, round(self.row * 1.4))
        self.name_w = max((self.font.getlength(n) for b in boards for n in b.names), default=0)
        self.name_w = int(self.name_w) + 2 * self.pad
        self.width = max((self.board_width(b) for b in boards), default=self.board_width(None))
        self.height = 2 * self.row

    def columns(self, board: Board | None) -> int:
        if board is None:
            return 1
        return len(board.sets) + (board.games is not None)

    def board_width(self, board: Board | None) -> int:
        points = board is not None and board.points is not None
        return self.dot_w + self.name_w + self.columns(board) * self.cell_w + points * self.points_w

    def draw(self, board: Board):
        Image, ImageDraw, _ = _pil()
        s = self.style
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        w = self.board_width(board)
        x0 = self.width - w if self.corner.endswith("right") else 0
        opacity = s["opacity"]
        radius = max(1, self.row // 6)
        d.rounded_rectangle((x0, 0, x0 + w - 1, self.height - 1), radius, fill=_rgba(s["background"], opacity))
        d.line((x0, self.row, x0 + w - 1, self.row), fill=_rgba(s["dim_text"], 0.5),
               width=max(1, self.row // 30))
        x = x0 + self.dot_w + self.name_w
        sets_x = x
        games_x = sets_x + len(board.sets) * self.cell_w
        if board.games is not None:  # the current set stands out a little
            d.rectangle((games_x, 0, games_x + self.cell_w - 1, self.height - 1),
                        fill=_rgba(_mix(s["background"], "#FFFFFF", 0.14), opacity))
        points_x = games_x + (self.cell_w if board.games is not None else 0)
        if board.points is not None:
            d.rounded_rectangle((points_x, 0, points_x + self.points_w - 1, self.height - 1), radius,
                                fill=_rgba(s["accent"]), corners=(False, True, True, False))
        for i, side in enumerate(("A", "B")):
            mid = i * self.row + self.row // 2
            if board.server == side:
                r = max(2, round(self.row * 0.13))
                cx = x0 + self.dot_w // 2
                d.ellipse((cx - r, mid - r, cx + r, mid + r), fill=_rgba(s["accent"]))
            color = s["accent"] if board.winner == side else s["text"]
            d.text((x0 + self.dot_w, mid), board.names[i], font=self.font, fill=_rgba(color), anchor="lm")
            for k, cells in enumerate(board.sets):
                cell = cells[i]
                cx = sets_x + k * self.cell_w + self.cell_w // 2
                fill = _rgba(s["text"] if cell.won else s["dim_text"])
                if cell.sup:
                    cx -= round(self.row * 0.12)
                    right = cx + round(self.font.getlength(cell.text) / 2) + 1
                    d.text((right, mid - round(self.row * 0.06)), cell.sup, font=self.small,
                           fill=fill, anchor="ls")
                d.text((cx, mid), cell.text, font=self.font, fill=fill, anchor="mm")
            if board.games is not None:
                d.text((games_x + self.cell_w // 2, mid), board.games[i], font=self.font,
                       fill=_rgba(s["text"]), anchor="mm")
            if board.points is not None:
                d.text((points_x + self.points_w // 2, mid), board.points[i], font=self.font,
                       fill=_rgba(s["accent_text"]), anchor="mm")
        return img

    def position(self, video_w: int, video_h: int) -> tuple[int, int]:
        margin = round(video_h * self.style["margin"])
        x = video_w - self.width - margin if self.corner.endswith("right") else margin
        y = video_h - self.height - margin if self.corner.startswith("bottom") else margin
        return x, y


def write_board_stream(shown: Sequence[Shown], painter: Painter, folder: Path) -> Path:
    """One PNG per distinct board and an ffconcat list that shows each for its time."""
    files: dict[Board, str] = {}
    lines = ["ffconcat version 1.0"]
    for s in shown:
        name = files.get(s.board)
        if name is None:
            name = files[s.board] = f"board{len(files):05d}.png"
            painter.draw(s.board).save(folder / name)
        lines += [f"file '{name}'", f"duration {_sec(s.end_ms - s.start_ms)}"]
    if shown:  # the last entry's duration only counts when another follows
        lines.append(f"file '{files[shown[-1].board]}'")
    path = folder / "boards.ffconcat"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


# -- what to put the board on --------------------------------------------------------------


@dataclass
class Source:
    """The video the board goes on: ffmpeg input arguments and its timeline."""

    input_args: list[str]
    plan: TrimPlan  # joined timeline -> this video's timeline
    width: int
    height: int
    temp_files: list[Path] = field(default_factory=list)


def open_source(session: Session, full: bool, tools: Tools, folder: Path) -> Source:
    if full:
        plan = plan_trim(session.timeline(), [], None)
        paths = session.source_paths()
        for p in paths:
            if not p.exists():
                raise OverlayError(f"source video not found: {p}")
        listing = folder / "sources.ffconcat"
        listing.write_text(concat_script(plan, paths), encoding="utf-8", newline="\n")
        info = probe(paths[0], tools)
        return Source(["-f", "concat", "-safe", "0", "-i", str(listing)], plan, info.width, info.height)
    plan = session.output_plan()
    video = session.output_path()
    if plan is None or video is None:
        raise OverlayError("no trimmed video has been made for this match yet "
                           "(run the Trim tool, or use --full)")
    if not video.exists():
        raise OverlayError(f"trimmed video not found: {video}")
    info = probe(video, tools)
    return Source(["-i", str(video)], plan, info.width, info.height)


def board_timeline(session: Session, overlay_config: Config, plan: TrimPlan, start_ms: int = 0,
                   end_ms: int | None = None) -> list[Shown]:
    mf = session.mf
    setting = overlay_config.get("points")
    show = shows_points(mf.events, setting if setting in POINTS_SETTINGS else "auto")
    changes = board_changes(analyze_match(mf, session.config), mf.match, show,
                            int(overlay_config.get("update_delay_ms")))
    return boards_on(plan, changes, start_ms, end_ms)


def ffmpeg_command(tools: Tools, overlay_config: Config, source: Source, boards: Path,
                   xy: tuple[int, int], output: Path, start_ms: int = 0,
                   duration_ms: int | None = None) -> list[str]:
    enc = overlay_config.get("encode")
    cmd = [tools.ffmpeg, "-hide_banner", "-nostdin", "-y"]
    if enc["hwaccel"]:
        cmd += ["-hwaccel", enc["hwaccel"]]
    if start_ms:
        cmd += ["-ss", _sec(start_ms)]
    if duration_ms is not None:
        cmd += ["-t", _sec(duration_ms)]
    cmd += source.input_args
    cmd += ["-f", "concat", "-safe", "0", "-i", str(boards)]
    graph = f"[0:v:0][1:v]overlay=x={xy[0]}:y={xy[1]}:format=auto,format={enc['pix_fmt']}[v]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
            "-c:v", enc["encoder"], *[str(a) for a in enc["args"]]]
    if "hevc" in enc["encoder"] or "265" in enc["encoder"]:
        cmd += ["-tag:v", "hvc1"]
    return cmd + ["-c:a", "copy", "-movflags", "+faststart", str(output)]


@dataclass
class Made:
    output: Path
    duration_ms: int
    issues: list[Issue] = field(default_factory=list)


def default_output(match_path: Path, overlay_config: Config, kind: str) -> Path:
    """``kind``: "trimmed", "full", "preview" or "png" (names from overlay.toml)."""
    return match_path.with_name(overlay_config.get(f"output.{kind}").format(stem=match_stem(match_path)))


def make_overlay(session: Session, overlay_config: Config, tools: Tools, output: Path, *,
                 full: bool = False, start_ms: int = 0, duration_ms: int | None = None,
                 report: Report = lambda f, t: None,
                 cancel: threading.Event | None = None) -> Made:
    """Draw the boards and encode the video with them (all of it, or ``duration_ms`` from
    ``start_ms`` for a preview)."""
    _pil()  # fail early without Pillow
    with tempfile.TemporaryDirectory(prefix="overlay.") as tmp:
        folder = Path(tmp)
        report(-1, "Reading the video…")
        source = open_source(session, full, tools, folder)
        if output.resolve() in [p.resolve() for p in session.source_paths()] + \
                [p.resolve() for p in [session.output_path()] if p]:
            raise OverlayError("output would overwrite a source video")
        video_end = source.plan.output_start_ms + source.plan.total_out_ms
        if start_ms >= video_end:
            raise OverlayError(f"{clock_text(start_ms)} is after the end of the video")
        end_ms = video_end if duration_ms is None else min(video_end, start_ms + duration_ms)
        report(-1, "Drawing the scoreboard…")
        shown = board_timeline(session, overlay_config, source.plan, start_ms, end_ms)
        painter = Painter(overlay_config, source.height, [s.board for s in shown])
        boards = write_board_stream(shown, painter, folder)
        cmd = ffmpeg_command(tools, overlay_config, source, boards,
                             painter.position(source.width, source.height), output, start_ms,
                             None if duration_ms is None else end_ms - start_ms)
        report(0.0, "Encoding…")
        try:
            stderr = _run_ffmpeg(cmd, end_ms - start_ms, lambda f: report(f, f"Encoding… {f:.0%}"), cancel)
        except ToolError as exc:
            output.unlink(missing_ok=True)
            raise ToolError(f"{exc}{_encoder_hint(str(exc))}") from exc
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    issues = [Issue("ffmpeg_warning", line.strip()) for line in stderr.splitlines()
              if "error" in line.lower() and "non-monotonic" not in line.lower()]
    info = probe(output, tools)
    expected = end_ms - start_ms
    if abs(info.duration_ms - expected) > 100:
        issues.append(Issue("duration_mismatch",
                            f"overlay video is {info.duration_ms} ms, expected {expected} ms",
                            severity="error"))
    return Made(output, info.duration_ms, issues)


def _encoder_hint(error: str) -> str:
    text = error.lower()
    if "nvenc" in text and ("driver" in text or "api version" in text):
        return ("\nThe NVIDIA driver is too old for this ffmpeg's NVENC: update the driver, or set "
                'encoder = "libx265" (slow) in overlay.toml [encode].')
    if "cuda" in text or "hwaccel" in text:
        return '\nGPU decoding failed: set hwaccel = "" in overlay.toml [encode].'
    return ""


def write_png(session: Session, overlay_config: Config, tools: Tools, output: Path, t_ms: int,
              full: bool = False) -> Path:
    """The board at ``t_ms`` of the (trimmed or full) video, at the video's size."""
    with tempfile.TemporaryDirectory(prefix="overlay.") as tmp:
        source = open_source(session, full, tools, Path(tmp))
    shown = board_timeline(session, overlay_config, source.plan)
    board = board_at(shown, t_ms)
    if board is None:
        raise OverlayError(f"{clock_text(t_ms)} is not in the video")
    Painter(overlay_config, source.height, [s.board for s in shown]).draw(board).save(output)
    return output


# -- command line ------------------------------------------------------------------------


def _time(text: str) -> int:
    ms = parse_clock(text)
    if ms is None:
        raise argparse.ArgumentTypeError(f"not a time: {text!r} (e.g. 1:02:30 or 75)")
    return ms


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tennis_to_utube.overlay",
                                     description="Burn a scoreboard into the trimmed (or full) "
                                                 "video of a match (re-encodes).")
    parser.add_argument("match", type=Path, help="the .match.json file")
    parser.add_argument("--full", action="store_true",
                        help="the full recording instead of the trimmed video")
    parser.add_argument("-o", "--output", type=Path, help="video file (default from overlay.toml)")
    parser.add_argument("--preview", type=_time, metavar="TIME",
                        help="only a short stretch from TIME (of the trimmed/full video)")
    parser.add_argument("--seconds", type=float, default=20.0, help="preview length (default 20)")
    parser.add_argument("--png", type=_time, metavar="TIME", help="only write the board at TIME")
    parser.add_argument("--overlay-config", type=Path, help="overlay.toml to use instead of the user's")
    args = parser.parse_args(argv)

    try:
        mf = matchfile.load(args.match)
    except (OSError, matchfile.MatchFileError) as exc:
        print(f"Cannot read {args.match}: {exc}", file=sys.stderr)
        return 1
    overlay_config = load_overlay_config(args.overlay_config)
    config = load_config()
    for w in overlay_config.warnings + config.warnings:
        print(f"warning: {w}", file=sys.stderr)
    session = Session(mf, args.match, config)
    tools = Tools.from_config(config)

    def report(fraction: float, text: str) -> None:
        print(f"\r{text:<40}", end="", flush=True)

    try:
        if args.png is not None:
            out = write_png(session, overlay_config, tools,
                            args.output or default_output(args.match, overlay_config, "png"),
                            args.png, args.full)
            print(f"Wrote {out}")
            return 0
        if args.preview is not None:
            out = args.output or default_output(args.match, overlay_config, "preview")
            made = make_overlay(session, overlay_config, tools, out, full=args.full,
                                start_ms=args.preview, duration_ms=round(args.seconds * 1000),
                                report=report)
        else:
            kind = "full" if args.full else "trimmed"
            out = args.output or default_output(args.match, overlay_config, kind)
            made = make_overlay(session, overlay_config, tools, out, full=args.full, report=report)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 2
    except (OverlayError, TrimError) as exc:
        print(f"\nCannot make the overlay: {exc}", file=sys.stderr)
        return 1
    except (ToolError, OSError) as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1
    print(f"\nMade {made.output} ({clock_text(made.duration_ms)})")
    for i in made.issues:
        print(f"{i.severity}: {i.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
