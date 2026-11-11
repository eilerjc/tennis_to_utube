# Tennis to YouTube

Mark tennis match video (GoPro), keep score, trim/join losslessly, and export YouTube
chapters and per-event links. See `DESIGN.md` for the agreed design.

## Running on Windows

1. Install **Python 3.11+** from https://www.python.org/downloads/ (tick "Add to PATH").
2. Install **ffmpeg** (with ffprobe) and make sure it is on PATH, or set its location in
   `%APPDATA%\tennis_to_utube\config.toml`:
   ```toml
   [tools]
   ffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"
   ffprobe = "C:/ffmpeg/bin/ffprobe.exe"
   ```
3. For video playback, put **`libmpv-2.dll`** next to `run.bat` (from the mpv Windows
   builds: https://sourceforge.net/projects/mpv-player-windows/files/libmpv/ — the
   `mpv-dev-x86_64-...` archive).
4. Double-click **`run.bat`**. The first start creates `.venv` and installs what is needed.

Settings can be overridden in `%APPDATA%\tennis_to_utube\`: `config.toml` (the app),
`shortcuts.toml` (keys), `trim.toml` (the Trim tool — copy `docs\trim.example.toml`) and
`upload.toml` (YouTube uploads — `docs\upload.example.toml`);
see `src/tennis_to_utube/config.py` and `shortcuts.py`.

## Using it

1. **Files** — find the match folder (folder tree, Quick access, subfolders; Back/Up). Tick the
   videos (GoPro chapters are put in order), type the players, pick the format, **Create
   match**. Or open a match file already in the folder.
2. **Mark** — play the video and press keys (or click buttons) as things happen. Left hand:
   `A S D` point (side A / B / unknown), `Q W E R` serve in / fault / let / ace, `G` game
   start (`Shift+G` other server), `Z X C` game end, `Shift+Z X C` set end, `T` set start,
   `Shift+T` set score, `1`–`6` coaching marks and note. Right hand: `Space` play/pause,
   arrows skip 5 s (`Shift` 1 s), `Ctrl+arrows` frame step, `↑ ↓` previous/next mark.
   Green buttons are what the score expects next. Marks made while playing are moved 0.2 s
   earlier for reaction time. Everything saves automatically; `Ctrl+Z` undoes.
   On the timeline: drag to scrub, click a mark to select it, `Ctrl`+drag to move it,
   wheel to zoom. Double-click a row in the Events list to edit it; the Issues list shows
   anything that doesn't add up.
3. **Trim** — tick what to remove (warm-up, changeovers, set breaks, after the match),
   untick single cuts to keep them, **Make video** (lossless, minutes for a full match).
   This runs the **Trim tool**, which also writes the trimmed video's chapters and links;
   after uploading the trimmed video, paste its YouTube link here to put it in the links.
4. **Export** — the **full recording**: after uploading it, paste its YouTube link. Copy
   the chapters into the video description; **Save links** writes a Markdown and a CSV list
   of links to every mark.

## Trim tool

The Trim step uses it, and it also runs on its own: drop a `.match.json` file on
`trim.bat` (or `python -m tennis_to_utube.trimtool file.match.json`). It makes
`<match> trimmed.mp4` and `<match> trimmed chapters.txt` / `links.md` / `links.csv` next to
the match file. `--cuts` lists the cuts only; `--links-only --video-id <link>` rewrites the
links once the trimmed video is on YouTube. Its settings — what is cut, the serve lead-in,
the trimmed video's link lead-ins — are in `trim.toml` (see `docs\trim.example.toml`).

## Cut only (no tennis)

To just cut parts out of videos and join them — any videos, nothing tennis — drop them on
**`cut.bat`** (or tick them on the Files step and press **Cut only (no tennis)…**). Play
through and press `I` where a part to remove starts and `O` where it ends (`O` on its own
cuts from the beginning); it is shaded on the timeline and listed on the right (`Delete`
removes one, `Ctrl+Z` undoes). Then **Make
video**: named after when the first video was recorded (`2026-10-04_165207.mp4`), lossless, in about the time it takes to copy the files.
With no parts marked it just joins the files. Nothing is saved but the video.
A part can only be cut where the camera stored a full picture (about every 0.5–1 s), so a
very short part cannot be removed: the result tells you which ones were left in.

## Upload to YouTube

The **Upload** step uploads any video straight from the PC — a trimmed match, a Cut only
video, files from the camera — and survives bad internet: it sends the file in pieces,
and after a lost connection, a closed app or a Windows restart it carries on from the last
piece YouTube confirmed, never from the start. One-time setup: `docs\UPLOAD_SETUP.md`
(your own free Google client id), then **Connect to YouTube…**.

- Get videos in with **Upload to YouTube…** on Files (ticked videos), Cut, Trim and Export,
  or **Add videos… / Add a game…** on the Upload step. A game's files are named
  "Team A vs Team B - First Half", "- Second Half", … from the teams you type once.
- Defaults: Unlisted, Sports; title, description, tags and playlist are editable, and you
  must say whether the video is made for kids.
- Two uploads run at once by default; the rest wait. Pause, resume, retry per video.
- The table shows progress, speed (Mbps), time left, the last checkpoint and the link.
  **Uploaded** means YouTube has the whole file; **Complete** means it has finished
  processing (4K versions can appear later still).
- Diagnostics show interruptions, retries and time spent reconnecting, to tell a dropping
  connection from a slow one. Log: `%APPDATA%\tennis_to_utube\uploads\upload.log`.
- A match's trimmed video (or full recording) gets its YouTube link filled in by itself.

Also from the command line: `upload.bat --connect`, then drop videos on `upload.bat`
(`--game "Hawks" "Owls" --kids no`); `upload.bat --run` carries on with the queue.
Settings: `upload.toml` (copy `docs\upload.example.toml`).

## Statistics

Drop a `.match.json` file on `stats.bat` (or run
`python -m tennis_to_utube.stats file.match.json`): it writes `<match> stats.csv` next to it
— points won on serve and return, serve percentages, aces, double faults, break points,
winners and errors, for the match and each set. Mark serves (`Q W E R`) and click the shot
buttons (winner / forced / unforced error) to get the finer stats. A shot clicked within 3 s
of a Point describes that point instead of ending a new one.

## Development

```
python -m pip install -e .[dev,gui]
python -m pytest
```
Tests marked `ffmpeg` need ffmpeg with libx265; tests marked `gui` need PySide6 and run
offscreen.
