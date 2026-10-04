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

## GUI step 2 — Player
- Playback joins the files with an mpv **EDL** with explicit segment lengths, so mpv's time
  equals our joined timeline. Tested headless with real mpv 0.37 on barcode clips: seeking
  to any time (including across the file join) shows exactly the frame that was on screen.
- mpv detail found while testing: exact seeks show the first frame starting at or after
  *target − 5 ms*; the app compensates (`playback.seek_seconds`). If your mpv build behaves
  differently, events would show one frame late — worth a quick check on Windows.
- **Wheel over the video** (frame step while paused): mpv draws into its own native window;
  on Windows that window may swallow wheel events. Please check; fallback idea: make the
  wheel work over the transport bar/timeline too.
- Transport buttons show their key in brackets, e.g. `◀ 5s  [←]`. Buttons never take keyboard
  focus, so Space always means play/pause.
- Without `libmpv-2.dll` the app still runs: the video area explains what's missing and a
  stand-in clock lets you mark anyway.
- mpv options: `hwdec=auto-safe`, `hr-seek=yes`, `keep-open=always`. Back-stepping on 4K HEVC
  may be slow (DESIGN §15) — please judge the feel.
