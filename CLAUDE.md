# CLAUDE.md

Read `DESIGN.md` first — it is the agreed design and the source of truth for decisions.
If a request conflicts with it, point that out and ask before changing direction, then
update `DESIGN.md` in the same change.

## Working with the owner

- The owner likes to discuss design before code. For new areas (especially the event list
  and keyboard layout), propose and wait for agreement before building.
- Target is the owner's **Windows** PC (Git Bash available). Development/testing here is on
  Linux, so keep core logic GUI-free and testable; the owner verifies the GUI on Windows.
- Never re-encode video for trim/join. Stream copy only.
- Commit in small steps on feature branches and push often; open PRs into `main`.

## Layout

- `src/tennis_to_utube/` — package. Core modules (no Qt imports): `config`, `matchfile`,
  `catalog` (event type ids), `issues`, `timeline`, `sources` (GoPro ordering), `probe`
  (ffprobe wrapper), `trim`, `structure` (simple set/game counting for trim/chapters),
  `chapters`, `youtube` (export), `shortcuts` (actions + key layout), `names` (player
  names, rename, short names), `scoring` (score engine), `flow` (state at the playhead),
  `session` (open match: edits, undo, cuts, output), `playback` (time/seek rules shared
  with mpv), `timeline_view` (bands, zoom), `appstate` (remembered folders). Separate tools (own
  config, no GUI): `trimtool` (makes the trimmed video + its chapters/links; `trim.toml`;
  the GUI's Trim step runs it as a process), `stats` (CSV statistics).
- `src/tennis_to_utube/gui/` — PySide6: `app` (entry), `main_window` (four steps),
  `files_page`, `mark_page` (+ `player`, `event_panel`, `timeline_bar`, `lists`,
  `actions`), `trim_page`, `export_page`, `worker` (background jobs). Keep logic in the
  core modules; GUI code only wires it up.
- `tests/` — pytest. Synthetic-video tests generate small HEVC files with ffmpeg that
  mimic the real camera profile (59.94 fps, keyframe every 60 frames, `yuvj420p`).

## Commands

- Tests: `python -m pytest` (tests marked `ffmpeg` are skipped if ffmpeg/ffprobe with
  libx265 is not on PATH; `gui` tests need PySide6 and run offscreen; the mpv player test
  needs libmpv).
- Run the app: `python -m tennis_to_utube [folder or match file]` (Windows: `run.bat`).
