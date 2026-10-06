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
`overlay.toml` (the Overlay tool — copy `docs\overlay.example.toml`);
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

## Scoreboard overlay

Drop a `.match.json` file on `overlay.bat` (or `python -m tennis_to_utube.overlay
file.match.json`): it burns a US Open style scoreboard (names, every set, games, points,
server) into the trimmed video and writes `<match> trimmed overlay.mp4`. `--full` does the
full recording instead (`<match> overlay.mp4`). This re-encodes the video (NVENC on the
NVIDIA card), so it takes a while; try `--preview 1:02:30` (20 s from that time) or
`--png 1:02:30` (just the board) first. Upload the overlay video **instead of** the plain
one: the timing is the same, so it uses the same chapters and links — paste its YouTube
link where you would paste the plain video's. Settings (look, place, encoder) are in
`overlay.toml` (see `docs\overlay.example.toml`). NVENC needs an NVIDIA driver new enough
for your ffmpeg; the tool says so if it isn't.

## Statistics

Drop a `.match.json` file on `stats.bat` (or run
`python -m tennis_to_utube.stats file.match.json`): it writes `<match> stats.csv` next to it
— points won on serve and return, serve percentages, aces, double faults, break points,
winners and errors, for the match and each set. Mark serves (`Q W E R`) and click the shot
buttons (winner / forced / unforced error) to get the finer stats. A shot clicked within 3 s
of a Point describes that point instead of ending a new one.

## Development

```
python -m pip install -e .[dev,gui,overlay]
python -m pytest
```
Tests marked `ffmpeg` need ffmpeg with libx265; tests marked `gui` need PySide6 and run
offscreen.
