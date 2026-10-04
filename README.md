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

## Development

```
python -m pip install -e .[dev,gui]
python -m pytest
```
Tests marked `ffmpeg` need ffmpeg with libx265; tests marked `gui` need PySide6 and run
offscreen.
