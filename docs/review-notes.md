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

## GUI step 3 — Marking, score panel, undo, autosave
- **Reaction offset** (200 ms × speed) applies only while playing; when paused the mark
  goes exactly on the frame shown.
- **G (Game start)** records the predicted server. If nobody is known yet (first game), it
  assumes **side A (ours)** serves; use Shift+G for the other side. In doubles, a side's
  first service game has no player prediction (the team chooses) — the server can be set
  later (event editing comes with the lists).
- Serve events (serve in, fault, let, ace) record the serving side, and the player in singles.
- **Suggested buttons** (green): during a game the point and Game end buttons; between games
  Game start/Set start; when the score says a set ended, the Set end buttons; after the
  match, Match end. Serve buttons are never pushed (optional level).
- **Selected event** (for Delete / move one frame) = the event just marked, or the one jumped
  to with ↑/↓ (clicking in the timeline/lists comes next).
- **Lock** freezes existing events (no delete/move/edit) but still allows new marks.
- **Autosave** 1.5 s after each change (and when leaving/closing), keeping `.bak` of the
  previous save. Undo/redo up to 500 steps per session (not across restarts).
- Set score dialog: type "6-4 3-6", "3-2", "30-40"/"AD-40"/"deuce" and pick the server;
  only filled parts are stored. Rules change dialog: preset + no-ad + match-tiebreak tick.
  Ending state: final sets ("6-4 3-6 [10-8]"), stored with `"entered": true`.
- Buttons show short names ("Point Emma S") and their key; menu-only events show no key.

## GUI step 4 — Timeline bars
- Overview (whole match) + detail strip (opens at 2 minutes wide; wheel zooms around the
  cursor; down to 2 s). "Follow playhead" (on by default) pages the strip when the playhead
  nears an edge. The overview shows the strip's window as a blue box.
- Tick colours: points green (A) / red (B) / grey (unknown); serve blue; games orange; sets
  brown; coaching teal; notes yellow; match-level indigo. Bands: sets (top row), games.
- Cut proposals (from the trim rules) are shaded grey on both bars; red triangles = issues.
- **Unticked cuts are stored in the match file** (`settings.trim.unticked`, by cut key), and
  the chosen rules in `settings.trim.rules` — so the trim choices survive reopening.
- Click picks a tick within 5 px; Ctrl+drag moves (refused while locked); Esc cancels.
