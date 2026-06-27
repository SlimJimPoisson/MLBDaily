# MLBDaily

> Find and download MLB **game videos** — condensed games or recaps — for any date, using MLB's free public StatsAPI. No login, no subscription.

MLBDaily looks up a day's games and downloads them with ffmpeg, from the command
line or a small built-in GUI. Each run answers two questions: **which games**
(all, or just your teams) and **what to pull** (condensed, recaps, or a *curated*
set). It's built for a daily habit: point it at "yesterday" and get a tidy folder
named with the date and standings.

```
2025‑09‑24 bos(86‑71) @ tor(90‑67) condensed.mp4
2025‑09‑24 mil(95‑63) @ sd(87‑71) condensed.mp4
```

> **No spoilers.** The W‑L records baked into each filename are the standings
> *entering* that game — i.e. from **before first pitch** — so scanning your
> folder never gives away who won.

## Download (no Python or ffmpeg needed)

Not a developer? Grab the ready-to-run Windows build:

1. Go to the [**Releases**](https://github.com/SlimJimPoisson/MLBDaily/releases/latest) page and download `MLBDaily-vX.Y-win64.zip`.
2. Unzip it anywhere.
3. Double-click **`MLBDaily.exe`**.

ffmpeg is bundled, so there's nothing else to install. On first launch Windows
may show a SmartScreen warning for the unsigned app — click **More info → Run
anyway**. Prefer the command line or another OS? Use the Python script (below).

## Features
- **Three ways to pull** — `condensed` (default), `recap`, or **`curated`**: the record-aware mode that weights toward the better games (see [✨ Curated mode](#-curated-mode)).
- **Team scope** — `--teams atl,nyy` to limit any mode to your clubs; leave it off for all 30.
- **No-spoiler filenames** — records shown are *pre-game*, so your folder never reveals results.
- **Everything is a parameter** — every rule is set on the command line, in a JSON config file, or via the GUI.
- **Live terminal progress** — one in-place progress bar per game, with speed and percent.
- **Optional tkinter GUI** (`--gui`) that remembers your settings between runs.
- **Self-documenting** — `--list-teams` prints every team abbreviation.

## Requirements
- Python 3.10+
- `pip install -r requirements.txt` (just `requests`)
- **`ffmpeg` and `ffprobe` on your PATH** ([download](https://ffmpeg.org/download.html))
- The GUI uses `tkinter`, which ships with the standard python.org installer.

## Quick start
```bash
pip install -r requirements.txt

python MLBDaily.py --yesterday                     # all condensed games (default)
python MLBDaily.py --yesterday --pull recap        # all recaps
python MLBDaily.py --yesterday --teams atl,nyy     # condensed, just your teams
python MLBDaily.py --yesterday --pull curated      # ✨ the curated set
python MLBDaily.py --gui                            # graphical front-end
```

## Usage

Every run answers two questions: **which games** and **what to pull**.

- **`--teams LIST`** — limit to games involving these teams (abbreviations or numeric IDs; run `--list-teams`). Leave it off for all 30 clubs.
- **`--pull MODE`** — one of:
  - `condensed` *(default)* — the condensed game for every game
  - `recap` — the recap for every game
  - `curated` — decide per game from records (see below)

| I want… | Command |
|---|---|
| All condensed games | *(nothing — it's the default)* |
| All recaps | `--pull recap` |
| Condensed for just my teams | `--teams atl,nyy` |
| Recaps for just my teams | `--teams atl,nyy --pull recap` |
| The smart, curated set | `--pull curated` *(or just set any curated knob)* |

## ✨ Curated mode

This is the heart of MLBDaily — and the reason it beats grabbing everything.
Instead of a pile of blowouts you'll never watch, **curated mode weights toward
the better games**, deciding *per game* from each team's record entering that day:

| Both teams… | You get |
|---|---|
| winning | the **condensed** game |
| one winning, one not | the **recap** |
| both losing | **nothing** (a red "skipped" line, no download) |
| record unknown | condensed (fail-safe — never miss content) |

Three knobs tune it (they apply **only** in curated mode — pass any one and
curated mode turns on automatically):

| Flag | Effect | Example |
|---|---|---|
| `--follow TEAMS` | Always pull these teams' game as a condensed game, regardless of record | `--follow atl,nyy` |
| `--never-losers TEAMS` | Treat these teams as always "winning" (never in the both-losing bucket) | `--never-losers lad` |
| `--losing BAR` | Where the winning line sits — whole number = games vs .500 (`-3`); decimal = win % (`0.440`). Default `-3`. | `--losing 0.440` |

```bash
# Curated, but always keep the Braves and never count the Dodgers as losers,
# and treat anyone at .440+ as winning:
python MLBDaily.py --yesterday --pull curated --follow atl --never-losers lad --losing 0.440
```

## Config file (set it once)

Repeat/cron users can put their preferences in a JSON file instead of passing
flags every run. Copy the example and edit it:

```bash
cp mlbdaily.config.example.json mlbdaily.config.json
```

```json
{
  "pull": "condensed",
  "teams": [],
  "output_dir": "./mlb_videos",
  "max_workers": 3,

  "follow": ["atl"],
  "never_losers": [],
  "losing": -3
}
```

The `follow`/`never_losers`/`losing` keys are curated-mode only; setting any of
them (and no `pull`) selects curated mode automatically. MLBDaily auto-loads
`mlbdaily.config.json` from the current directory (or next to
the script), or use `--config path/to/file.json`. **Precedence:** CLI flag >
config file > built-in default — so a flag always wins, and the file just changes
your defaults. Your personal `mlbdaily.config.json` is gitignored.

## All options
Run `python MLBDaily.py --help` for the full list. Highlights:

| Flag | Description |
|---|---|
| `--date YYYY/MM/DD` · `--today` · `--yesterday` · `--tomorrow` | Which day (default: yesterday). |
| `--pull {condensed,recap,curated}` | What to download (default: condensed). |
| `--teams LIST` | Limit to games involving these teams (default: all). |
| `--output-dir DIR` | Where to save (default `./mlb_videos`). |
| `--max-workers N` | Concurrent downloads (default 3). |
| `--retries N` | Retries per failed download (default 2). |
| `--dry-run` | Find videos but don't download. |
| `--save-json FILE` | Dump the discovered game list to JSON. |
| `--verbose` | Detailed logging (shows the active filter rules, skipped games, etc.). |
| `--gui` | Launch the tkinter GUI. |

## Notes
- Only the **free public** StatsAPI is used — no credentials anywhere.
- Filenames use a non-breaking hyphen (U+2011) so media players like VLC don't mangle the date/record when deriving a title from the filename.
- Postponed games and non-final games are skipped automatically.

## License
MIT — see [LICENSE](LICENSE).
