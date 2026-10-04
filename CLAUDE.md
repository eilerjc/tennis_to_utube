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
  (ffprobe wrapper), `trim`, `structure` (set/game counting until `flow` exists),
  `chapters`, `youtube` (export), `shortcuts` (actions + key layout); planned: `flow`,
  `scoring`. GUI under `gui/`.
- `tests/` — pytest. Synthetic-video tests generate small HEVC files with ffmpeg that
  mimic the real camera profile (59.94 fps, keyframe every 60 frames, `yuvj420p`).

## Commands

- Tests: `python -m pytest` (tests marked `ffmpeg` are skipped if ffmpeg/ffprobe with
  libx265 is not on PATH).
