# Quick start

From GoPro files to a YouTube video with chapters, links to every point, and (optionally)
a scoreboard. Windows only for now.

## 1. Set up (once)

1. **Python 3.11 or newer** — https://www.python.org/downloads/ (tick "Add to PATH").
2. **ffmpeg** — the "full" build from https://www.gyan.dev/ffmpeg/builds/ (it has the
   HEVC and NVIDIA encoders). Put its `bin` folder on PATH, or point to it in
   `%APPDATA%\tennis_to_utube\config.toml`:
   ```toml
   [tools]
   ffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"
   ffprobe = "C:/ffmpeg/bin/ffprobe.exe"
   ```
3. **Video playback** — download `mpv-dev-x86_64-<date>-git-….7z` from
   https://github.com/shinchiro/mpv-winbuild-cmake/releases, open it with 7-Zip and put
   **`libmpv-2.dll`** next to `run.bat`. The app always uses this one, even if another
   program has put an older mpv on PATH.
4. **NVIDIA driver** — keep it up to date if you want the scoreboard overlay (ffmpeg 9
   needs driver 610 or newer for NVENC).
5. Double-click **`run.bat`**. The first start takes a minute to set things up.

## 2. One match, start to finish

The app has four steps along the top.

**1 Files** — go to the match folder, tick the videos (GoPro chapters are put in order for
you), type the players' names, pick the format (default: best of 3, match tiebreak for
the third), **Create match**. Next time, just open the match file in that folder.

**2 Mark** — play the video and press a key when something happens. Everything saves as
you go; `Ctrl+Z` undoes. Buttons lit green are what the score expects next.

| Key | Mark |
|---|---|
| `G` | Game start (`Shift+G`: the other player serves) |
| `Q` | **Serve** — press when the ball is struck |
| `W` `E` `R` | **Fault / Let / Ace** — press when the call comes. Within 6 s of a Serve it changes that serve; with no call the serve counts as in. Double fault: `Q W Q W` |
| `A` `S` `D` | Point to side A / side B / not sure (at the end of the point) |
| `Z` `X` `C` | Game end A / B / ? (optional when you mark every point) |
| `T`, `Shift+T` | Set start, Set score… (type the real score when the video starts mid-match or something is off) |
| `1`–`6` | Coaching marks: good recovery, footwork, body language, late contact, strategy, note |
| `Space` · `←` `→` · `Ctrl+←` `Ctrl+→` · `↑` `↓` | Play/pause · skip 5 s (`Shift`: 1 s) · one frame · previous/next mark |

You only need the level of detail you care about: Game start and the Point keys are
enough for a full score; serves and the shot buttons (winner / error, mouse only) add
stats. Marks made while playing are moved 0.2 s earlier for reaction time.

Tips:
- **Mark Game start where the trimmed video should pick up again** — the changeover
  before it is cut right up to that mark.
- If the score panel looks wrong, check the **Issues** list under the timeline; each issue
  jumps to its place in the video.
- Drag on the timeline to scrub; `Ctrl`+drag moves a mark; double-click a row in the
  Events list to edit it.

**3 Trim** — tick what to cut (warm-up, changeovers, set breaks, after the match), untick
any single cut you want to keep, **Make video**. It is lossless and takes minutes, not
hours. You get `<match> trimmed.mp4` plus its chapters and links next to the match file.

**4 Export** — chapters and links for the **full recording**, if you upload that one.

## 3. Scoreboard on the video (optional)

Drop the match file on **`overlay.bat`**. It burns a US Open style scoreboard (names,
every set, games, points, server) into the trimmed video and writes
`<match> trimmed overlay.mp4`. Add `--full` (from a command prompt) for the full recording.

This one re-encodes the video, so it takes a while: about 1.8× real time on an RTX 4060 Ti,
around **45 minutes for an 83-minute match**. Check the look first:
```
overlay.bat "D:\video\match 8\GX010008.match.json" --png 12:30
overlay.bat "D:\video\match 8\GX010008.match.json" --preview 12:30
```
`--png` writes just the board at that time; `--preview` makes 20 s of video from there.
Times are on the trimmed video's clock. Colours, corner and size are in `overlay.toml`
(see `docs\overlay.example.toml`).

## 4. Put it on YouTube

1. Upload **one** video: the trimmed one, or the overlay version instead (same timing, so
   the same chapters and links work). The full recording is a separate upload.
2. Paste its YouTube link on the **Trim** step (trimmed / overlay video) or the **Export**
   step (full recording). The links files are rewritten with real `youtu.be` links.
3. Copy the chapters (`<match> trimmed chapters.txt`, or the Export step) into the video
   description.
4. `<match> trimmed links.md` / `.csv` list a link to every mark, grouped by chapter.

## 5. Statistics

Drop the match file on **`stats.bat`**: `<match> stats.csv` (opens in Excel) with points
won on serve and return, serve percentages, aces, double faults, break points, winners
and errors, for the match and each set.

## Settings

All optional, in `%APPDATA%\tennis_to_utube\` — keep only the lines you change:
`config.toml` (playback, names, scoring windows such as the 6 s serve call),
`shortcuts.toml` (keys), `trim.toml` (what is cut; copy `docs\trim.example.toml`),
`overlay.toml` (the scoreboard; copy `docs\overlay.example.toml`).

## If something goes wrong

- **No video** — `libmpv-2.dll` is missing next to `run.bat` (see setup step 3).
- **Overlay stops with "NVIDIA driver is too old"** — update the driver, or set
  `encoder = "libx265"` under `[encode]` in `overlay.toml` (works without the GPU, much
  slower).
- **"no trimmed video has been made"** from the overlay — run the Trim step first, or use
  `--full`.
- **A `.bat` window says "Run run.bat once first"** — do that; it sets up Python.
