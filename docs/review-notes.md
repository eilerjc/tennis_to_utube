# Review notes (work done while the owner was away)

Assumptions and open points to go through together. Newest at the bottom of each section.

## GUI step 1 — Files page
- Folder listing shows `.mp4`/`.mov` files; GoPro files ordered by recording then chapter.
- When a folder holds several recordings, only the **first recording** is ticked; the
  user ticks others. (Design said "propose grouping, user approves" — this is the proposal.)
- Creating a match where `<first file>.match.json` already exists offers to open it instead
  (never overwrites).
- Creation-time order problems are only shown as a tooltip on "Create match" (not blocking).
- Remembered folders (last, recent ×10, pinned) live in `%APPDATA%\tennis_to_utube\state.json`.
- Window minimum size 1280×800; it opens maximized. Qt scales for 4K automatically.
- `run.bat` creates `.venv` on first run and installs `.[gui]` (PySide6 + python-mpv).
