# MLBDaily

> Find and download MLB **condensed game** videos for any date, using MLB's free public StatsAPI. No login, no subscription.

MLBDaily looks up a day's games, decides what's worth keeping based on each
team's record, and downloads it with ffmpeg — either from the command line or a
small built-in GUI. It's built for a daily habit: point it at "yesterday" and
get a tidy folder of condensed games named with the date and standings.

```
2025‑09‑24 bos(86‑71) @ tor(90‑67) condensed.mp4
2025‑09‑24 mil(95‑63) @ sd(87‑71) condensed.mp4
```

## Download (no Python or ffmpeg needed)

Not a developer? Grab the ready-to-run Windows build:

1. Go to the [**Releases**](https://github.com/SlimJimPoisson/MLBDaily/releases/latest) page and download `MLBDaily-vX.Y-win64.zip`.
2. Unzip it anywhere.
3. Double-click **`MLBDaily.exe`**.

ffmpeg is bundled, so there's nothing else to install. On first launch Windows
may show a SmartScreen warning for the unsigned app — click **More info → Run
anyway**. Prefer the command line or another OS? Use the Python script (below).

## Features
- **Record-aware filtering** — picks what to grab for each game from the teams' standings *entering* that day (see [How it decides](#how-it-decides)).
- **Everything is a parameter** — tune the rules with `--follow`, `--never-losers`, and `--losing`, on the command line, in a JSON config file, or via the GUI.
- **Condensed *or* recap** — pulls the short condensed game for good matchups and falls back to a recap when only one team is interesting.
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

python MLBDaily.py --yesterday                       # the daily habit
python MLBDaily.py --date 2025/09/29 --output-dir ~/Videos/MLB
python MLBDaily.py --gui                             # graphical front-end
python MLBDaily.py --list-teams                      # show team abbreviations
```

## How it decides

For each completed game, MLBDaily compares both teams' records (as they stood the
day before) against a **winning bar**, then chooses what to download:

| Situation | Result |
|---|---|
| A `--follow` team is playing | **condensed** game (always) |
| Both teams winning | **condensed** game |
| Exactly one team winning | **recap** (shorter highlight) |
| Both teams losing | **log entry only** (nothing downloaded) |
| A team's record is unknown | **condensed** (fail safe — never miss content) |

### Tuning the rules
| Option | Meaning | Example |
|---|---|---|
| `--follow TEAMS` | Teams whose game is **always** pulled as a condensed game, regardless of record. | `--follow atl,nyy` |
| `--never-losers TEAMS` | Teams **always treated as winning**, so they never fall into the "both losing" bucket. | `--never-losers lad,hou` |
| `--losing BAR` | The winning bar. A **whole number** is games vs .500 (`-3` keeps teams within 3 games of .500); a **decimal** is a win percentage (`0.440` keeps teams at .440 or better). Default `-3`. | `--losing 0.440` |

Teams are given as abbreviations (`atl`, `nyy`, …) or numeric StatsAPI IDs.

```bash
# Always grab the Braves and Yankees; treat the Dodgers as never-losers;
# count anyone at .440 or better as "winning".
python MLBDaily.py --yesterday --follow atl,nyy --never-losers lad --losing 0.440
```

## Config file (set it once)

Repeat/cron users can put their preferences in a JSON file instead of passing
flags every run. Copy the example and edit it:

```bash
cp mlbdaily.config.example.json mlbdaily.config.json
```

```json
{
  "follow": ["atl"],
  "never_losers": [],
  "losing": -3,
  "output_dir": "./mlb_videos",
  "max_workers": 3
}
```

MLBDaily auto-loads `mlbdaily.config.json` from the current directory (or next to
the script), or use `--config path/to/file.json`. **Precedence:** CLI flag >
config file > built-in default — so a flag always wins, and the file just changes
your defaults. Your personal `mlbdaily.config.json` is gitignored.

## All options
Run `python MLBDaily.py --help` for the full list. Highlights:

| Flag | Description |
|---|---|
| `--date YYYY/MM/DD` · `--today` · `--yesterday` · `--tomorrow` | Which day (default: yesterday). |
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
