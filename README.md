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

Settings (`config.toml`) and keys (`shortcuts.toml`) can be overridden in
`%APPDATA%\tennis_to_utube\`; see `src/tennis_to_utube/config.py` and `shortcuts.py`.

## Using it

1. **Files** — go to the match folder (path bar, Up, siblings, recent, pinned). Tick the
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
4. **Export** — after uploading, paste the YouTube link. Copy the chapters into the video
   description; **Save links** writes a Markdown and a CSV list of links to every mark.

## Development

```
python -m pip install -e .[dev,gui]
python -m pytest
```
Tests marked `ffmpeg` need ffmpeg with libx265; tests marked `gui` need PySide6 and run
offscreen.
