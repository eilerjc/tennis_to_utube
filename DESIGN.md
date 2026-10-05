# tennis_to_utube — Design

A Windows desktop tool for reviewing tennis match video recorded on a GoPro Mission 1 Pro:
mark events while watching, optionally keep score, then losslessly trim/join the footage and
export YouTube chapters and per-event links. Primary use is **player/coaching review**
(body language, post-shot recovery, play speed) of **whole matches** that are later uploaded
to YouTube.

This document records decisions agreed with the project owner. Items marked
**[data only]** must exist in the file format now but need no UI yet.

---

## 1. Source footage (measured on real files)

From `ffprobe` on `GX010008.MP4`:

| Property | Value |
|---|---|
| Codec | HEVC (H.265), profile Main (8-bit) |
| Pixel format | `yuvj420p` (full-range) |
| Resolution | 3840×2160 |
| Frame rate | `60000/1001` (59.94 fps) — frame ≈ 16.683 ms |
| Video bitrate | ~41.2 Mbps (camera uses constant-quality, so it varies) |
| Keyframes | every 60 frames = **1.001 s** exactly; **closed GOP** (checked 2026-10-03) |
| B-frames | **none** (`has_b_frames=0`): decode order = display order |

A real match folder:

```
GX010008.MP4  11,698,456,599 bytes   (modified 16:52)
GX020008.MP4  11,701,878,664 bytes   (modified 17:29)
GX030008.MP4   2,161,484,725 bytes   (modified 17:36)
```

- Naming is `GX` + **2-digit chapter** + **4-digit recording number**. Order = sort by
  recording number, then chapter. Plain alphabetical sort is wrong across recordings.
  Cross-check with `creation_time` metadata; flag disagreement.
- ~11.7 GB per full chapter ≈ 37–38 min. This match ≈ 83 min total.
- GoPro files carry extra data tracks (telemetry); joins keep only `0:v:0` and audio.
- Files live on a Windows PC (NTFS). The user also uses Git Bash (MINGW64).

## 2. Platform and stack

- **Windows first.** User PC: i9-14900K, 64 GB RAM, RTX 4060 Ti.
- Python 3.11+, **PySide6** (Qt, LGPL), **mpv** via `python-mpv`/libmpv for playback,
  **ffmpeg/ffprobe** for probing, cutting, joining.
- Trim/join are **stream copy only** — never re-encode. The one exception is the separate
  Overlay tool (§9a): a burned-in scoreboard needs a re-encode (NVENC).
- Core logic (event log, flow, scoring, chapters, trim planning) is pure Python with no GUI
  dependency so it can be fully unit-tested on Linux CI/cloud.

**Separate tools, one repo** (agreed with the owner). They share the core code; the match
file is the only thing passed between them, and each tool has its own config file in the
user config dir:

| Tool | Start | Config | Does |
|---|---|---|---|
| Marker (GUI) | `run.bat` | `config.toml` (playback, keys, names, scoring, full-video links) | Files → Mark → Trim → Export |
| Trim tool | `trim.bat` / `python -m tennis_to_utube.trimtool` | `trim.toml` (what is cut, serve lead-in, the trimmed video's link lead-ins and chapter gap, output name) | makes the trimmed video; writes the trimmed video's chapters and links |
| Stats | `stats.bat` / `python -m tennis_to_utube.stats` | — | CSV statistics (§12a) |
| Overlay tool | `overlay.bat` / `python -m tennis_to_utube.overlay` | `overlay.toml` (what the board shows, its look and place, the encoder) | burned-in scoreboard on the trimmed or full video (re-encode, §9a) |

The Marker's **Trim** screen stays: it lists the cuts (rules and lead-ins from `trim.toml`,
per-match choices in the match file), lets the user untick them and **runs the Trim tool**
as a separate process to make the video (progress, Cancel). The Mark timeline keeps showing
the cuts. The **Export** screen is for the **full recording** (its own YouTube video id);
the trimmed video gets its own chapters and links from the Trim tool (its own video id).

## 3. Core principles

1. **Every event is an instant.** No span events are stored. Intervals (a game, a changeover)
   are *derived* by pairing events (e.g. Game start … Game end).
2. **The event log is the single source of truth.** Score, flow state, chapters and cut
   proposals are all computed from it and recomputed on any edit.
3. **Marking and processing are separate.** Marking never cuts or exports. A later pass
   decides what to remove and what to export, and can be redone any number of times.
4. **Time is integer milliseconds on the joined (concatenated source) timeline.** Full
   precision is kept in the log (needed for future per-shot tracking); precision is only
   reduced at export (YouTube uses whole seconds).
5. **Never lose user data.** Versioned format, unknown fields preserved on load/save,
   autosave with backup of the previous version, full undo.

## 4. Match file (data format)

One JSON file saved next to the video(s), e.g. `GX010008.match.json`.

```jsonc
{
  "format_version": 1,
  "sources": [            // ordered; paths relative to the match file when possible
    {"path": "GX010008.MP4", "duration_ms": 2271000, "codec": "hevc", "width": 3840,
     "height": 2160, "fps": "60000/1001", "pix_fmt": "yuvj420p"}
  ],
  "match": {
    "kind": "singles",     // or "doubles"
    "format": {"preset": "standard_mtb"},  // §7; plus any overrides, e.g. "ad": false
    "sides": {
      "A": {"players": ["Emma"], "role": "ours"},
      "B": {"players": ["Sara"], "role": "opponent"}
    }
  },
  "settings": {            // per-match overrides of app defaults
    "lead_in_ms": {"default": 5000},
    "chapter_gap_ms": 600000
  },
  "events": [
    {
      "id": "e_0001",      // stable id, never reused
      "t_ms": 734512,
      "type": "game_start",
      "side": "A", "player": "Emma",   // who (server here); optional; the name as typed
      "result": null,      // e.g. "A" | "B" | "unknown" for outcome events
      "observed": null,    // [data only] what the video shows: "in"|"out"|"net"|"unclear"...
      "called": null,      // [data only] what was ruled: "in"|"out"|"let"|"replay"|"no_call"
      "source": "human",   // "human" | "ai" | "import"
      "confidence": null,  // [data only] 0..1, for AI
      "inferred": false,   // reserved; inferred results are derived on the fly, not stored
      "tags": [],          // free labels, e.g. "close", "bad miss"
      "details": {},       // structured qualifiers, e.g. {"direction":"long","margin_cm":6}
      "note": ""
    }
  ],
  "youtube": {"video_id": null},
  "next_event_seq": 2      // next id number; ids are never reused, even after deletes
}
```

- Names are stored and exported **exactly as typed** (usually first names).
- New matches start with **Player 1** and **Player 2** written into the file (doubles: 1 & 2
  on side A, 3 & 4 on side B; a missing name shows as its default). Events store names.
  **Renaming** a player is a find/replace over the match file (sides, events, Set score
  servers) and the app tells the user how many places changed; a name another player
  already has is refused (agreed with the owner, instead of position references).
- **Short names** for buttons and tight spots: first 4 letters of the first name + last
  initial ("Alexandra Jones" → "Alex J", "Player 1" → "Play 1"; letter count is a setting).
  Clashes lengthen the part that differs ("Alex Sm" / "Alex Sc"). Exports and the future
  scoreboard use **full names**.
- Unknown top-level and per-event fields must round-trip unchanged.
- `settings.trim` holds the trim pass choices (`rules`, `unticked` cut keys); top-level
  `output` describes the last video made (`path`, kept `segments` [start, end, output start],
  `output_start_ms`, the `cuts` used) so export can remap event times without re-planning.
- A file with a newer `format_version` than the app knows is refused (never downgraded).
  Saving is atomic; the previous file is kept as `<name>.bak`.
- Default when only one of `observed`/`called` is given: the other equals it.

## 5. Event catalog

Implement now unless marked **[data only]**. Type ids (the event's `type` field) are in
`code`; agreed with the owner. Outcomes use `result` = `"A"` | `"B"` | `"unknown"` — buttons
may say "won"/"lost"/"?" but all store the same type.

**Match structure / flow**
- Match start `match_start` (sets the score to 0-0 sets, 0-0 games, 0-0 points, keeping a
  server already chosen; the score is known from there), Match end `match_end`
- Set start `set_start`, **Set end** `set_end` (result A / B / unknown)
- Game start `game_start` (with server in `side`/`player`), **Game end** `game_end`
  (result A / B / unknown)
- Tiebreak start `tiebreak_start` — normally implied by the format and score (e.g. 6–6). When
  a tiebreak is played at another score (7–7, 9–9 in a pro set), the user marks Game end and
  then Tiebreak start; the next game is then a tiebreak and decides the set. Needs level
  games above 0–0 (a match tiebreak instead of a set is part of the format). Agreed.
- **Rules change** `rules_change` — carries a patch to the format; applies from its position
  onward (e.g. "set 3 is a 10-point match tiebreak", decided on the fly)
- **Set score** `score_state` — score checkpoint, allowed **at any time**, as often as needed
  (video starts mid-match, or the user knows the real score and wants to correct it). Only
  the known parts are entered in `details`; the rest keeps being computed from earlier
  events, or is unknown. Parts: `"sets": [[6, 4], [7, 6, 7, 5], [1, 0, 10, 8]]` (completed
  sets, A–B; optional tiebreak points A–B — typed as "6-4 7-6(5) [10-8]"), `"games":
  [3, 2]` (current set; without points, taken as between games), `"points": [2, 3]` (counts;
  the user types "30-40", "AD-40", "deuce", or "5-3" in a tiebreak), `"server"` (a name or a
  side). From that point the entered score is authoritative; disagreement with what earlier
  events add up to is an issue. A Set score before anything was scored (or Match start)
  cannot conflict, and parts it leaves out are unknown — except a Set score holding only the
  server (Set server…), which changes just the server. The dialog opens with the current
  server selected.
- **Ending state** `ending_state` — final score from another source (scorebook) when video
  ends early; marked as *entered*, not observed. Same `sets` format (tiebreak points kept).

**Points** (one press per point, at the end of the point)
- Point `point`, won by A / B / **unknown** (can't see ball, can't hear call/score)

**Serve** (optional finer level)
- Serve in `serve_in`, fault `fault`, let `let`, ace `ace`. First vs second serve is
  derived (a fault earlier in the same point), so one `serve_in` covers both; a double fault
  is two faults in the same point (no separate type). A logged serve gives the exact point
  start time. The second fault and an ace **end the point by themselves** (see §7); no Point
  press is needed after them.

**Shot** (optional finer level; buttons only, no keys)
- Winner `winner`, forced error `forced_error`, unforced error `unforced_error`, one button
  each per side (the hitter). A shot **ends the point** like an ace: winner ⇒ point to the
  hitter, error ⇒ point to the other side.
- **Modifier:** a shot pressed within `scoring.shot_modifier_window_ms` (default 3 s) after a
  Point — with no serve/fault/ace/shot mark in between, and agreeing with the Point's winner
  if one was entered — describes that Point instead (`details.shot`, `details.shot_side`;
  sets an unknown winner; pressing again replaces it; one undo step). Otherwise it is a new
  point-ending event.
  How a shot missed (out, net, long, wide) is a qualifier, not a type.
- Qualifiers via tags/details: close, bad miss, out, net, long, wide; later shot type
  (forehand/backhand/volley/serve), direction, numeric margin.

**Coaching marks** (always available)
- Good recovery `good_recovery`, footwork/positioning `footwork`, body language
  `body_language`, late contact `late_contact`, strategy/pattern `strategy`, free-text note
  `note`. Event list is configurable and expected to grow.

**Officiating vs reality** [data only]
- "Out but not called", "called out but looked in" are expressed as `observed` ≠ `called`.
  Score always follows `called`. Disagreements form a reviewable list.

## 6. Flow and state

- State at any moment is computed by **replaying the log up to the playhead** — not from
  "the last button pressed". Seeking back and inserting a missed event just works.
- The GUI shows context-sensitive buttons from that state, e.g. after Game start the button
  becomes Game won / Game lost / Game ? (all `game_end`); Set won / lost / ? appears when
  the score says the set can end
  (and is always available via a menu for retirements/odd formats). Free-standing events
  (points, coaching marks, notes) are always available.
- Validation produces an **issues list**: game won with no game start, unclosed game,
  impossible score, conflicting shot/point outcome, event inside a removed region, etc.
  Each issue jumps to its time.

## 7. Scoring (optional)

- Scoring may be off, or recorded at **set**, **game** or **point** level, and the level may
  differ across the match. The engine computes whatever the recorded events allow.
- **Formats** (`scoring.PRESETS`, stored in `match.format` with any overrides; changed
  mid-match via Rules-change events):

  | Preset | Sets | Games | Tiebreak | Final set |
  |---|---|---|---|---|
  | `standard_mtb` (**default**, agreed) | best of 3 | 6 | at 6–6, to 7 | 10-point match tiebreak |
  | `standard` | best of 3 | 6 | at 6–6 | normal set |
  | `best_of_5` | best of 5 | 6 | at 6–6 | normal set |
  | `pro_set` | 1 | 8 | at 8–8 | — |
  | `pro10` | 1 | 10 | at 10–10 | — |
  | `short_sets` | best of 3 | 4 | at 4–4 | 10-point match tiebreak |

  **Ad / no-ad** is a switch on every format (`"ad": false`). **Tiebreak changeovers**:
  `"tiebreak_changeovers": "regular"` (default; change ends every 6 points) or `"coman"`
  (after the 1st point, then every 4) — chosen per match, agreed with the owner. Every field (`games`,
  `tiebreak_at`, `tiebreak_points`, `final_set`, `match_tiebreak_points`, …) can be
  overridden, e.g. for other short-set variants — or the user just marks set won/lost.
- **Points ended by a serve** (agreed with the owner): the second `fault` in a point is a
  double fault and wins the point for the receiver; an `ace` wins it for the server. The
  engine awards the point itself (no Point event needed; the GUI shows it awarded at once).
  If the server is unknown, the point counts as won by unknown. A Point press within a
  short window after such a point (setting, default ~5 s) with no serve in between is not
  merged silently: it is an issue, "possible duplicate point", for the user to keep or delete.
  Points are delimited by point-ending events (Point, second fault, ace); a `let` is not a
  fault. Logging serves stays optional — without them, Point is pressed as usual.
- **Server tracking:** singles and **doubles from the start**. App predicts next server
  (including tiebreak rotation and fixed doubles partner order per set); user confirms with
  one press. While the server is **not known yet** (first game), `G` pops a quick "Who
  serves?" picker (keys 1/2/…; Esc leaves it open); in doubles it also asks when the team's
  server can't be predicted. **Set server…** (button in the Serve group) uses the same
  picker any time: in a game it changes that game's Game start, otherwise it records a Set
  score holding only the server (for the next game). The score panel marks the server
  with ● and says "Server not set" until it is known.
- **Back-annotation of unknowns:** when a game (or set) closes, search all valid assignments
  of the unknown points (or games) consistent with rules, known outcomes, event count and
  the closing result (DP over score states):
  - exactly one assignment → fill in, mark `inferred: true`;
  - totals fixed but order ambiguous → score certain, individual points flagged; app can
    jump to each for video review;
  - no valid assignment → issue (missing/extra/wrong event).
  Same mechanism one level up (games within a set from a known set score). A **Set score**
  checkpoint is a known state too: unknown points/games before it must lead to it (e.g.
  unknown at 2–2, then Set score 4–2 ⇒ both games went to A).
- Display distinguishes confirmed / inferred / uncertain. Exports only state scores the
  engine is certain of.
- **How it works:** the engine tracks every score consistent with the log so far (unknown
  results branch, known results and Set scores prune), then keeps only paths that reach the
  end. Among those it prefers the most regular reading — a game that ended on points but
  was not marked before play went on, or a Game start mid-game, counts against a path —
  so unknowns are not read in improbable ways (e.g. one long deuce game split into two).
  Inferred results are recomputed on every edit and never overwrite what the user entered.
  If nothing fits, the event is applied as entered and reported. More than 5000 possible
  scores at once: tracking stops (issue) until a Set score.

## 8. Chapters and YouTube export

- **Chapters are derived, not marked.** Anchors: Game start and Set start, plus a Set score
  marked before any of them (video starts mid-match: "Match in progress (6–4, 3–2)"). Later
  Set score corrections do not start chapters. Other events attach to the chapter they
  fall in. Titles carry the score where the engine is certain of it: "Set 2 (6–4)",
  "Set 1 · Game 5 (3–1) — Emma serving" (games A–B), "Set 1 · Tiebreak (6–6)",
  "Set 3 · Match tiebreak". Numbers come from the score once results are recorded, else
  from counting Set/Game start events (scoring off).
- **Gap rule:** if more than **10 minutes** pass with no anchor, add a chapter at the **first
  existing event at or after** the 10-minute point; if no event exists there, add nothing.
  Never at an arbitrary time.
- Two sets of chapters and links: the **full recording** (Export screen, `config.toml`
  lead-ins, `youtube.video_id`) and the **trimmed video** (Trim tool, `trim.toml` lead-ins,
  `output.video_id`). A match-file `settings.lead_in_ms` override applies to both.
- Chapters are computed **after** trim remapping. YouTube chapter rules (as understood —
  verify): first timestamp `0:00`, at least 3 chapters, each ≥ 10 s long. Violations are
  merged/dropped.
- **Per-event links:** `https://youtu.be/<VIDEO_ID>?t=<seconds>` for every event, grouped by
  chapter, exported to a separate file (Markdown and CSV). Description gets only chapters.
- **Lead-in:** link/chapter time = event time − lead-in (default **5 s**, configurable per
  event type, e.g. ace 3–4 s, rally winner 8–10 s). Clamped to ≥ 0 and to the start of the
  kept segment (never reaches into removed footage). Rounded **down** to whole seconds.
- Video ID is pasted after upload; links regenerate from stored offsets.
- Upload is done by the user in the browser for now (YouTube API upload is future work).

## 9. Trim pass (after marking) — the Trim tool

The Trim tool (§2) does everything in this section. Its `trim.toml` holds the default rules,
`serve_lead_in_ms`, and `lead_in_ms` / `chapter_gap_ms` for the trimmed video's links; the
match file holds per-match choices (`settings.trim`: rules, unticked cuts) and the made
video (`output`: plan, path, the trimmed video's YouTube id). Outputs next to the match
file: `<match> trimmed.mp4`, `<match> trimmed chapters.txt`, `<match> trimmed links.md`
and `.csv`. `--links-only` rewrites chapters and links for the made video (after pasting
its YouTube id). Run from the GUI it reports progress on stdout and stops on "cancel" on
stdin (the partial video is deleted).

- User picks removal rules; app lists the resulting cuts; user can untick any:
  - everything before the first Game start / Match start (warmup),
  - **changeovers**: from the end of an odd game of the set to the next Game start. The end
    comes from the score engine, so games ended by points count too (anchored on the
    game-ending point, or on its Game end if marked); uncertain game ends are not cut.
    **Tiebreak changeovers** (every 6 points, or Coman: after point 1 then every 4) are cut
    from that point to `serve_lead_in_ms` (trim.toml, default 3 s) before the next serve mark;
    without a serve mark there is no sign of when play resumes, so none is proposed.
  - set breaks, everything after Match end.
- Removals run exactly up to the next Game start / Set start mark; no extra pre-roll is kept
  before it. So **mark Game start where the kept footage should begin**: a lead-in before
  Game start would land in removed footage, so it is clamped to the start of the kept
  segment (the link starts at the cut). Agreed with the owner.
- Removed regions show shaded on the timeline before processing.
- **Keyframe snapping:** kept-segment **starts snap back** to the keyframe at or before the
  requested time (≤ 1.001 s earlier with these files — keeps a little extra context, never
  loses any). Kept-segment **ends are exact** on footage without B-frames (the owner's
  camera): the piece ends at the first frame shown after the requested time. On footage
  with B-frames, ends **snap forward** to the next keyframe (≤ 1.001 s later). Either way the
  piece is cut at that frame's *decode* time. Decided per source from `has_b_frames`.
  *Why (measured on synthetic HEVC):* the concat demuxer's `outpoint` compares decode
  timestamps. With B-frames, an end at an arbitrary frame drops some frames shown before the
  cut, keeps some shown after it, and collides with the next piece's timestamps; only a
  keyframe is a clean boundary there. Without B-frames decode order is display order, so
  every frame is.
- **Closed GOPs required.** With open GOPs the frames decoded after a keyframe but shown
  before it reference the previous GOP and come out broken after every cut. The GOP structure
  is checked before cutting and open-GOP footage is refused.
- Process: ffmpeg concat demuxer with `inpoint` (keyframe), `outpoint` (keyframe decode
  time) and `duration` (shown span) per kept piece, `-c copy`, `-map 0:v:0 -map 0:a?`,
  `-tag:v hvc1`, `-movflags +faststart`. Then **ffprobe the output** and verify durations.
  The MP4 muxer may start the video a few ms after 0 (audio begins slightly before the
  first keyframe); that offset is measured from the output and added to remapped times.
- Where two files join, the next file's first AAC packet (encoder priming) overlaps the
  previous file's tail by a few ms and ffmpeg nudges that one audio packet; video is not
  affected. Reported as info.
- **Remap:** every event time is shifted by the cumulative removed duration before it, using
  the *actual snapped* boundaries. Events inside removed regions are excluded from export
  and listed as issues (never silently lost).

## 9a. Scoreboard overlay — the Overlay tool

Agreed with the owner (2026-10-04). A separate tool (§2), command line only for now:
`python -m tennis_to_utube.overlay <match file>` (Windows: drop the match file on
`overlay.bat`). Settings in `overlay.toml`. The only part of the project that re-encodes.

- **Input:** the **trimmed video** (default; it must have been made by the Trim tool) or,
  with `--full`, the **full recording** (the sources joined in order). Both are supported.
  Board times are the event times remapped through the made video's stored plan
  (`output.segments`), so nothing is re-planned.
- **Output:** `<match> trimmed overlay.mp4` / `<match> overlay.mp4` (names in
  `overlay.toml`). The overlay video is uploaded **instead of** the plain one, never both:
  timing is identical, so it uses that video's chapters and links, and its YouTube id is
  pasted where the plain one's would go (Trim step / Export step). Nothing new is stored
  in the match file.
- **What the board shows** (US Open style, owner's choice): top-left; one row per side
  with full names (doubles: "Emma Jones / Sara Smith" on one line); **every completed
  set's games** (tiebreak points of the set's loser as a small superscript; a match
  tiebreak shows its points), the current set's games, and the **points** (0/15/30/40/AD;
  counts in a tiebreak); ● marks the side serving the next point. Points are shown only
  when the match has point-level marks (Point, ace, fault, shot), or always/never by
  setting. After the match only the sets are shown, the winner highlighted.
- **Score source:** the score engine (§7) after each event, changing at the event that
  changed it (a Point mark, at the end of the point) plus `update_delay_ms`. Only what the
  engine is certain of is shown; unknown parts are left blank. Before any score is known
  (before the first set) the board shows **0s**, not blank.
- **Drawing:** each distinct board is drawn once with **Pillow** to a PNG of one fixed
  canvas size (text is measured, so the box fits full names; libass/ASS cannot size a box
  to text). A concat list of the PNGs with their durations is the overlay stream, so
  changes land exactly at their times. Board building and timing are pure Python
  (tested on any OS); drawing needs Pillow.
- **Encoding:** `ffmpeg … -filter_complex overlay`, `hevc_nvenc` (RTX 4060 Ti) with
  constant-quality VBR (`encode.args` in `overlay.toml`), `-tag:v hvc1`, 8-bit
  `yuv420p`, audio copied, `+faststart`. Decoding may use the GPU (`encode.hwaccel`),
  frames come back to the CPU for the overlay. Output duration checked with ffprobe.
- **Preview:** `--preview <time> [--seconds N]` encodes a short stretch (default 20 s) to
  `<match> overlay preview.mp4`; `--png <time>` writes just the board at that time.

## 10. Playback and timeline UI

**Window** (agreed): works on a 1920×1200 screen or larger (the owner's is 3840×2160; Qt
scales). Opens as a normal window, **not maximized** (owner's choice): last size and place
(kept in state.json), or 1920×1200 centred the first time. Steps along the top: **1 Files → 2 Mark → 3 Trim → 4 Export**. Mark layout:

```
┌──────────────────────────────────────────┬────────────────────┐
│                                          │ score / server     │
│               Video (mpv)                │ (always visible)   │
│                                          ├────────────────────┤
├──────────────────────────────────────────┤ event buttons      │
│ transport · speed drop-down · time       │ (scroll; lit = next)│
├──────────────────────────────────────────┴────────────────────┤
│ overview bar (whole match)                                     │
│ detail strip (zoomable)                                        │
├────────────────────────────────────────────────────────────────┤
│ [Events] [Issues] lists                                        │
└────────────────────────────────────────────────────────────────┘
```

Runs from source for now: `run.bat` sets up `.venv` on first use (one-click .exe later).

**Playback** (all keys remappable)
- Speeds 0.25×, 0.5×, 1×, 1.5×, 2×, 4×, 8× (4× and 8× for scanning); list is a setting
  (`playback.speeds`); chosen from a **drop-down** next to the transport buttons (no keys —
  agreed with the owner).
- Skip back/forward 5 s and 1 s (distances configurable); jump to previous/next event.
- Frame step forward and back with hold-to-repeat; also the **mouse wheel over the video,
  only while paused** (wheel forward/up = next frame, owner's choice) (does nothing while playing). (Back-stepping long-GOP HEVC is slower;
  verify feel on real footage.)
- **Reaction offset:** marks are shifted earlier by a configurable real-time delay scaled by
  playback speed (default **200 ms** at 1×, agreed). Marks can be nudged by single frames afterward.

**Timeline**
- **Overview bar** (whole match) + **zoomable detail strip** (seconds to minutes; scroll to
  zoom; follow-playhead or fixed). An 83-min match across ~1800 px is ~3 s/px, hence the
  zoom strip.
- Shows: color-coded event ticks; derived set/game/server bands; shaded cut proposals;
  issue markers; hover tooltip (time + nearest event).
- **Drag = scrub only**, even when starting on an event. While dragging, fast keyframe seeks
  (1 s granularity with these files); exact seek on release. Fallback if scrubbing is
  choppy: a once-per-match low-res proxy for scrubbing.
- **Moving an event requires Ctrl+drag** (modifier configurable). Original position shown as
  a ghost; Esc cancels. Click on a tick selects + jumps, never moves.
- Delete is a separate explicit action. **Lock** toggle freezes all events.
- Undo for every edit; autosave with backup of previous version.

## 11. Shortcuts and buttons

Every button with a key shows it on a second line, e.g. `Play / Pause` over `[Space]`.

- Every action has both a **keyboard shortcut and a clickable button**, generated from one
  definition so they can't drift. Buttons display their current key.
- Defaults shipped in the app; **user override file** (e.g.
  `%APPDATA%\tennis_to_utube\shortcuts.toml`, `[keys]` table: `action = "Key"`, a list of
  keys, or `""` for none). Conflicts (duplicate keys, clashes with player controls) produce
  warnings, app still starts: a key the user binds wins over a default binding, and each key
  ends up on exactly one action.
- **Default layout (agreed with the owner):** US QWERTY, mouse in the right hand, so events
  are all on the left hand in rows; the player uses Space and the arrow keys. Buttons and
  labels show the players' names; keys are tied to sides (A = "ours"). Defined in
  `shortcuts.py`.

  | Keys | Action |
  |---|---|
  | `A` `S` `D` | Point: A / B / unknown |
  | `Q` `W` `E` `R` | Serve in, Fault, Let, Ace |
  | `G` / `Shift+G` | Game start with the predicted / the other server |
  | `Z` `X` `C` | Game end: A / B / unknown |
  | `Shift+Z` `Shift+X` `Shift+C` | Set end: A / B / unknown |
  | `T` / `Shift+T` | Set start / Set score… |
  | `1`–`6` | Good recovery, Footwork, Body language, Late contact, Strategy, Note… |
  | — (buttons/menu) | Match start/end, Tiebreak start, Rules change, Ending state, speeds |
  | `Space` | Play/pause |
  | `←` `→` (`Shift`: short) | Skip back/forward 5 s (1 s) |
  | `Ctrl+←` `Ctrl+→` | Frame back/forward (hold to repeat) |
  | `↑` `↓` | Previous / next event |
  | `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
  | `Alt+←` `Alt+→` | Move selected event one frame |
  | `Delete` (or right-click) | Delete selected event |
  | `Ctrl+L` | Lock events |

  Mouse: wheel on the timeline zooms; click jumps/selects; drag scrubs; Ctrl+drag moves an
  event; right-click an event to edit/delete.

## 12. File selection and navigation

- Remember the last folder; open there next time.
- Explorer-like browser built into the Files step: **Back / Forward / Up / Refresh** (also
  Alt+←/→/↑, F5, mouse back/forward buttons, Backspace), an address bar with folder
  completion (Ctrl+L), and **Pin folder**. Left: **Quick access** (pinned, then recent; same
  names get the parent folder added) and a **folder tree** (drives and network drives; a
  click opens a folder, arrow keys open it after a short pause; follows the current folder).
  Right: the current folder's **subfolders and match files**, with what each subfolder holds
  ("2 videos · 1 match", read in the background; folders with a match in bold) and the
  modified date. Double-click or Enter goes into a folder or opens a match; Up selects the
  folder you came from.
- Tick files for a match; show name, duration, size, and whether codec params match the
  first file (lossless join requires a match). App proposes order (recording number, then
  chapter; cross-checked with `creation_time`); user reorders by drag or up/down buttons.
- If a folder holds several recordings, propose grouping by recording number — user approves.
- Match file stores the final order; paths relative to the match file where possible.

## 12a. Statistics (separate tool, no GUI)

`python -m tennis_to_utube.stats <match file> [-o out.csv]` (Windows: drop the match file
on `stats.bat`) writes `<match> stats.csv` (UTF-8 with BOM for Excel) from the event log
only, using the score engine for servers, break points and inferred winners. Columns:
Section, Stat, side A, side B; sections Match and Set N; doubles adds a By server table.
Stats: points won, service/return points won, 1st serve in, 1st/2nd serve points won, aces,
double faults, break points won/saved (not in tiebreaks), tiebreak points won, minibreaks
won (tiebreak points won on return) and lost (tiebreak points lost on serve),
service/return games won, winners, forced/unforced errors, points with unknown winner
(left out). Serve stats only count points with serve
marks; shot stats only count marked shots.

## 13. Future (keep the format ready)

- AI-generated events (`source: "ai"`, confidence) into the same log, with a review queue;
  human corrections override but keep the AI's original. Human logs double as labeled data.
- Overlay tool in the GUI (a button on the Trim / Export step); more board styles.
- YouTube API upload.
- Coaching filters across a match or season ("all close misses long on the backhand").
- Rally/dead-time auto-detection (deferred; not needed for whole-match review).

## 14. Build order

1. Project skeleton, config/shortcut loading, match-file load/save (versioned, round-trip).
2. Timeline (ms, multi-source), GoPro ordering, ffprobe wrapper.
3. Trim planner: rules → removal intervals → keyframe-snapped kept segments → ffmpeg plan;
   event remap; lead-in clamping.
4. Chapters + YouTube link/description export.
5. Flow state machine + score engine (rules, formats, singles/doubles serve rotation,
   set score/ending state, back-annotation).
6. GUI: file browser panel, mpv player, controls, timeline bars, event buttons, issues list,
   trim pass screen, export screen.

Status (2026-10-04): steps 1–5 merged; step 6 built (all screens) and tested offscreen —
awaiting the owner's check on Windows.
Overlay tool (§9a) built 2026-10-04 (command line), tested with libx265 on synthetic video;
NVENC checked on the owner's PC (driver 617, ffmpeg 9.0.2): 4K59.94 at ~1.8× real time
(an 83-min match ≈ 45 min). ffmpeg 9's NVENC needs driver 610+.

**Testing:** pure-Python unit tests for everything in 1–5. End-to-end trim tests on
**synthetic video matching the real profile** (`libx265`, `60000/1001`, keyint 60,
`yuvj420p`, 3840×2160 or scaled-down equivalent, split into GoPro-style chapter files), with
ffprobe verifying that remapped event times land on the right frames. Score engine gets
property-style tests: simulate matches, hide random point results, check back-annotation.
GUI is verified by the owner on Windows.

## 15. To verify / open

- ~~End-cut accuracy with stream copy~~ — resolved: ends snap to keyframes (§9).
- ~~Whether the camera's GOPs are closed~~ — checked on `GX010008.MP4`: closed GOP, no
  B-frames (§1). Lossless cuts work.
- Back frame-step and scrubbing smoothness in mpv on 4K60 HEVC (owner's machine).
- YouTube chapter rules and `t=` behavior (whole seconds) against current YouTube help.
- ~~Event key layout~~ — agreed (§11).
