#!/usr/bin/env python3
"""
MLB Condensed Game Downloader
----------------------------
Finds and downloads MLB condensed game videos for a specified date, using the
public MLB StatsAPI (no login required).

Usage:
  python MLBDaily.py --date YYYY/MM/DD
  python MLBDaily.py --yesterday
  python MLBDaily.py --today
  python MLBDaily.py --output-dir ~/Videos/MLB
  python MLBDaily.py --yesterday --follow atl,nyy --losing 0.440
  python MLBDaily.py --gui          # tkinter front-end
"""

import os
import sys
import argparse
import subprocess
import re
import time
import threading
import requests
import json
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

__version__ = "1.0.0"

# ANSI colors for terminal output
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

_log_callback = None

# Module-level default so verbose-marked log() calls are import-safe even when a
# helper is called before run()/__main__ sets this global. run() overrides it.
verbose_logging = False

def set_log_callback(cb):
    """Install a callback that receives (line, color) for each log message.
    Pass None to restore stdout printing."""
    global _log_callback
    _log_callback = cb

def log(message, color=None, verbose=False):
    """Print/forward colored log messages, respecting verbose mode.

    Verbose-marked messages are shown only when verbose_logging is True.
    Non-verbose messages are always shown."""
    if verbose and not verbose_logging:
        return
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"[{timestamp}] {message}"
    if _log_callback is not None:
        _log_callback(line, color)
        return
    if color:
        print(f"[{timestamp}] {color}{message}{Colors.ENDC}")
    else:
        print(line)

# MLB StatsAPI team id -> short abbreviation (and full name, for --list-teams).
# Covers all 30 clubs across both leagues.
TEAMS = {
    108: ("laa", "Los Angeles Angels"),
    109: ("az",  "Arizona Diamondbacks"),
    110: ("bal", "Baltimore Orioles"),
    111: ("bos", "Boston Red Sox"),
    112: ("chc", "Chicago Cubs"),
    113: ("cin", "Cincinnati Reds"),
    114: ("cle", "Cleveland Guardians"),
    115: ("col", "Colorado Rockies"),
    116: ("det", "Detroit Tigers"),
    117: ("hou", "Houston Astros"),
    118: ("kc",  "Kansas City Royals"),
    119: ("lad", "Los Angeles Dodgers"),
    120: ("wsh", "Washington Nationals"),
    121: ("nym", "New York Mets"),
    133: ("ath", "Athletics"),
    134: ("pit", "Pittsburgh Pirates"),
    135: ("sd",  "San Diego Padres"),
    136: ("sea", "Seattle Mariners"),
    137: ("sf",  "San Francisco Giants"),
    138: ("stl", "St. Louis Cardinals"),
    139: ("tb",  "Tampa Bay Rays"),
    140: ("tex", "Texas Rangers"),
    141: ("tor", "Toronto Blue Jays"),
    142: ("min", "Minnesota Twins"),
    143: ("phi", "Philadelphia Phillies"),
    144: ("atl", "Atlanta Braves"),
    145: ("cws", "Chicago White Sox"),
    146: ("mia", "Miami Marlins"),
    147: ("nyy", "New York Yankees"),
    158: ("mil", "Milwaukee Brewers"),
}

# Reverse lookup: abbreviation -> team id.
ABBR_TO_ID = {abbr: tid for tid, (abbr, _name) in TEAMS.items()}


def get_team_abbreviation(team_id):
    """Get team abbreviation from team ID, or a 'team<id>' placeholder."""
    entry = TEAMS.get(team_id)
    return entry[0] if entry else f"team{team_id}"


def parse_team_list(spec):
    """Parse a comma/space-separated team spec into a set of team IDs.

    Accepts abbreviations (e.g. 'atl,nyy') or raw numeric IDs (e.g. '144').
    Unknown tokens are logged and skipped. Returns an empty set for a falsy
    spec, so an unset CLI flag means 'no teams'."""
    if not spec:
        return set()
    if isinstance(spec, (set, list, tuple)):
        tokens = [str(t) for t in spec]
    else:
        tokens = re.split(r"[,\s]+", str(spec).strip())
    ids = set()
    for tok in tokens:
        if not tok:
            continue
        key = tok.lower()
        if key in ABBR_TO_ID:
            ids.add(ABBR_TO_ID[key])
        elif tok.isdigit() and int(tok) in TEAMS:
            ids.add(int(tok))
        else:
            log(f"Unknown team '{tok}' ignored (use an abbreviation like 'atl' or a numeric id)", Colors.YELLOW)
    return ids

def get_entering_play_records(formatted_date, headers):
    """Return {team_id: (wins, losses)} as the teams stood ENTERING play on
    formatted_date (YYYY-MM-DD) -- i.e. standings as of the prior day.

    Returns an empty dict on any failure so the caller can fall back to a
    record-less filename rather than crash.
    """
    try:
        game_day = datetime.strptime(formatted_date, "%Y-%m-%d").date()
        prior_day = (game_day - timedelta(days=1)).strftime("%Y-%m-%d")
        season = game_day.year
        url = (f"https://statsapi.mlb.com/api/v1/standings?leagueId=103,104"
               f"&season={season}&date={prior_day}")
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            log(f"Standings fetch failed ({resp.status_code}); filenames will omit records", Colors.YELLOW, verbose=True)
            return {}
        data = resp.json()
        records = {}
        for div in data.get("records", []):
            for tr in div.get("teamRecords", []):
                tid = tr.get("team", {}).get("id")
                if tid is not None:
                    records[tid] = (tr.get("wins", 0), tr.get("losses", 0))
        log(f"Loaded entering-play records for {len(records)} teams (as of {prior_day})", Colors.BLUE, verbose=True)
        return records
    except Exception as e:
        log(f"Error fetching entering-play records: {e}", Colors.YELLOW, verbose=True)
        return {}

@dataclass
class FilterConfig:
    """Tunable rules for which games are pulled and as what.

    follow_teams  : team IDs whose game is ALWAYS pulled as a condensed game,
                    regardless of records.
    never_losers  : team IDs always treated as "winning" for filtering, even
                    when their record is below the threshold.
    win_metric    : 'games' -> compare (wins - losses) to win_threshold.
                    'pct'   -> compare win percentage to win_threshold.
    win_threshold : the cutoff. For 'games' it's a +/- games-vs-.500 number
                    (e.g. -3); for 'pct' it's a fraction (e.g. 0.440).
    """
    follow_teams: set = field(default_factory=set)
    never_losers: set = field(default_factory=set)
    win_metric: str = "games"
    win_threshold: float = -3.0


def parse_losing(value):
    """Interpret a 'losing bar' value as (metric, threshold).

    A value containing a decimal point is read as a win-percentage cutoff
    ('pct'); a whole number is read as a games-vs-.500 cutoff ('games').
        '-3'    -> ('games', -3.0)   # keep teams within 3 games of .500
        '0.440' -> ('pct',   0.440)  # keep teams at .440 or better
    """
    s = str(value).strip()
    metric = "pct" if "." in s else "games"
    return metric, float(s)


def build_config(args):
    """Assemble a FilterConfig from a parsed args namespace (CLI or GUI)."""
    metric, threshold = parse_losing(getattr(args, "losing", None) or "-3")
    return FilterConfig(
        follow_teams=parse_team_list(getattr(args, "follow", None)),
        never_losers=parse_team_list(getattr(args, "never_losers", None)),
        win_metric=metric,
        win_threshold=threshold,
    )


# A JSON config file lets repeat/cron CLI users set their preferences once
# instead of passing flags every run. CLI flags always override the file, which
# overrides the built-in defaults. Keys mirror the long flag names.
DEFAULT_CONFIG_NAME = "mlbdaily.config.json"
CONFIG_KEYS = ("follow", "never_losers", "losing",
               "output_dir", "max_workers", "retries")


def _find_config_path(explicit):
    """Resolve which config file to use: an explicit --config path, else the
    first DEFAULT_CONFIG_NAME found in the cwd or next to this script."""
    if explicit:
        return explicit
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        candidate = os.path.join(base, DEFAULT_CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_config_file(path):
    """Read a JSON config into a dict. Returns {} on any problem (so a missing
    or malformed file degrades to built-in defaults rather than crashing)."""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log(f"Config {path} is not a JSON object; ignoring", Colors.YELLOW)
            return {}
        log(f"Loaded config from {path}", Colors.BLUE, verbose=True)
        return data
    except FileNotFoundError:
        log(f"Config file not found: {path}", Colors.YELLOW)
        return {}
    except Exception as e:
        log(f"Could not read config {path}: {e}", Colors.YELLOW)
        return {}


def apply_config_defaults(args):
    """Fill any unset args (value is None) from a JSON config file.

    CLI flags that were actually supplied are left untouched, so the precedence
    is: CLI flag > config file > built-in default. Returns the loaded dict."""
    cfg = load_config_file(_find_config_path(getattr(args, "config", None)))
    for key in CONFIG_KEYS:
        if getattr(args, key, None) is None and cfg.get(key) is not None:
            setattr(args, key, cfg[key])
    return cfg


def describe_config(config):
    """One-line human summary of the active filter rules, for logging."""
    def names(ids):
        return ", ".join(sorted(get_team_abbreviation(t) for t in ids)) or "(none)"
    if config.win_metric == "pct":
        bar = f"win% >= {config.win_threshold:.3f}"
    else:
        bar = f"(W-L) >= {config.win_threshold:+g}"
    return (f"winning bar: {bar} | always-condensed: {names(config.follow_teams)} "
            f"| never-losers: {names(config.never_losers)}")

# VLC rewrites the ASCII hyphen-minus to a space when it derives a title from the
# filename. Use a non-breaking hyphen (U+2011) in filenames instead -- it looks
# identical but isn't in VLC's substitution set, so the date/records survive.
FILENAME_DASH = "‑"


# Highlight items are tagged by keyword, not always by title (a recap's title is
# an editorial headline like "Fried dominates Red Sox", with no "recap" in it).
# Match on these keyword markers instead.
KIND_KEYWORDS = {
    "condensed": {"condensed_game", "mlbcom_condensed_game", "condensed-game"},
    "recap": {"mlb_recap", "mlbcom_game_recap", "game-recap"},
}


def _item_keywords(item):
    """Lowercased set of an item's keyword values."""
    return {k.get("value", "").lower() for k in item.get("keywordsAll", [])}


def item_is_kind(item, kind):
    """True if a highlight item is of the requested kind (condensed/recap),
    by keyword tag, falling back to a title-substring match."""
    if _item_keywords(item) & KIND_KEYWORDS.get(kind, set()):
        return True
    return kind in item.get("title", "").lower()


def _rec_str(record):
    """Format a (wins, losses) tuple as '(W-L)', or '' when unknown."""
    return f"({record[0]}-{record[1]})" if record else ""


def is_winning(record, config):
    """True if a team clears the configured winning bar.

    Uses config.win_metric ('games' or 'pct') against config.win_threshold.
    Returns None when the record is unknown (so callers can fail safe)."""
    if not record:
        return None
    wins, losses = record
    if config.win_metric == "pct":
        games = wins + losses
        pct = (wins / games) if games else 0.0
        return pct >= config.win_threshold
    return (wins - losses) >= config.win_threshold


def team_is_winning(team_id, record, config):
    """is_winning(), but a 'never losers' team always counts as winning."""
    if team_id in config.never_losers:
        return True
    return is_winning(record, config)


def classify_game(away_id, home_id, away_record, home_record, config):
    """Pick which media to pull for a game from entering-play records.

    Returns 'condensed', 'recap', or None (log entry only):
      - Follow-list team involved -> 'condensed' (always).
      - Both teams winning        -> 'condensed'.
      - Exactly one winning       -> 'recap'.
      - Both teams losing         -> None.
      - Unknown record(s)         -> 'condensed' (fail safe; don't miss content).

    'never losers' teams are treated as winning regardless of their record.
    """
    if away_id in config.follow_teams or home_id in config.follow_teams:
        return "condensed"
    aw = team_is_winning(away_id, away_record, config)
    hw = team_is_winning(home_id, home_record, config)
    if aw is None or hw is None:
        return "condensed"
    if aw and hw:
        return "condensed"
    if aw or hw:
        return "recap"
    return None


def get_condensed_games(date_str, config):
    """
    Fetch condensed game links for a specific date.

    Args:
        date_str (str): Date in format YYYY/MM/DD.
        config (FilterConfig): rules for follow/never-losers/winning bar.

    Returns:
        list: List of condensed game objects
    """
    try:
        year, month, day = date_str.split('/')
        formatted_date = f"{year}-{month}-{day}"
        schedule_date = formatted_date
    except ValueError:
        log("Error: Date must be in format YYYY/MM/DD", Colors.RED)
        return [], []

    log(f"Finding condensed games for {date_str}", Colors.BLUE, verbose=True)

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/100.0", "Accept": "application/json, text/plain, */*", "Referer": f"https://www.mlb.com/live-stream-games/{date_str}"}
    schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={formatted_date}"

    try:
        log(f"Fetching games schedule...", Colors.BLUE, verbose=True)
        schedule_response = requests.get(schedule_url, headers=headers, timeout=30)

        if schedule_response.status_code != 200:
            log(f"Failed to access schedule API: {schedule_response.status_code}", Colors.RED)
            return [], []

        schedule_data = schedule_response.json()
        games_data = []
        postponed_games = []
        other_status_games = []

        if "dates" in schedule_data and len(schedule_data["dates"]) > 0:
            for date_info in schedule_data["dates"]:
                if "games" in date_info:
                    for game in date_info["games"]:
                        abstract_status = game.get("status", {}).get("abstractGameState", "")
                        detailed_status = game.get("status", {}).get("detailedState", "")
                        status = detailed_status if detailed_status else abstract_status

                        away_team = game.get("teams", {}).get("away", {}).get("team", {})
                        home_team = game.get("teams", {}).get("home", {}).get("team", {})
                        away_name = away_team.get("name", "Unknown")
                        home_name = home_team.get("name", "Unknown")

                        if "postponed" in status.lower():
                            postponed_games.append(f"{away_name} @ {home_name}")
                            continue

                        if "completed early" in status.lower():
                            log(f"Including game with status '{status}': {away_name} @ {home_name}", Colors.GREEN, verbose=True)

                        doubleheader_flag = game.get("doubleHeader", "N")

                        if status != "Final" and "completed early" not in status.lower() and doubleheader_flag == "N":
                            other_status_games.append(f"{away_name} @ {home_name} ({status})")
                            continue

                        games_data.append(game)

        if postponed_games and verbose_logging:
            log(f"Skipping {len(postponed_games)} postponed games:", Colors.YELLOW)
            for game in postponed_games:
                log(f"  - {game}", Colors.YELLOW)

        if other_status_games and verbose_logging:
            log(f"Skipping {len(other_status_games)} games with non-final status:", Colors.YELLOW)
            for game in other_status_games:
                log(f"  - {game}", Colors.YELLOW)

        log(f"Found {len(games_data)} games for {date_str}", Colors.GREEN, verbose=True)

        if not games_data:
            log("No completed games found for this date", Colors.YELLOW)
            return [], []

        selected_games = []
        logonly_games = []

        # Entering-play records (standings as of the day before) drive both the
        # filename labels and the winning/losing filtering.
        entering_records = get_entering_play_records(formatted_date, headers)

        for game in games_data:
            game_pk = game.get("gamePk")
            if not game_pk:
                continue

            home_team = game.get("teams", {}).get("home", {}).get("team", {})
            away_team = game.get("teams", {}).get("away", {}).get("team", {})

            home_team_name = home_team.get("name", "Unknown")
            away_team_name = away_team.get("name", "Unknown")
            home_team_id = home_team.get("id")
            away_team_id = away_team.get("id")

            home_abbrev = get_team_abbreviation(home_team_id) if home_team_id else "UNK"
            away_abbrev = get_team_abbreviation(away_team_id) if away_team_id else "UNK"

            away_record = entering_records.get(away_team_id)
            home_record = entering_records.get(home_team_id)

            # Decide what to pull (condensed / recap / nothing) from the records.
            kind = classify_game(away_team_id, home_team_id, away_record, home_record, config)
            matchup = f"{away_abbrev}{_rec_str(away_record)} @ {home_abbrev}{_rec_str(home_record)}"

            if kind is None:
                # Both teams losing and neither is a follow team: log entry only.
                log(f"Log only (both losing): {matchup}", Colors.YELLOW)
                logonly_games.append({
                    "matchup": matchup,
                    "away_abbrev": away_abbrev,
                    "home_abbrev": home_abbrev,
                    "away_record": away_record,
                    "home_record": home_record,
                    "kind": "logonly",
                })
                continue

            content_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/content"

            try:
                log(f"Checking game {game_pk} [{kind}]: {matchup}", Colors.BLUE, verbose=True)
                content_response = requests.get(content_url, headers=headers, timeout=30)

                if content_response.status_code != 200:
                    continue

                content_data = content_response.json()

                if "highlights" in content_data and "highlights" in content_data["highlights"]:
                    highlights = content_data["highlights"]["highlights"].get("items", [])

                    for item in highlights:
                        item_title = item.get("title", "")

                        if item_is_kind(item, kind):
                            playbacks = item.get("playbacks", [])

                            url = ""
                            for playback in playbacks:
                                if playback.get("name") == "mp4Avc":
                                    url = playback.get("url", "")
                                    break

                            if not url and playbacks:
                                url = playbacks[0].get("url", "")

                            if url:
                                doubleheader = game.get("doubleHeader", "N")
                                game_number = game.get("gameNumber", 1) if doubleheader != "N" else None

                                game_data = {
                                    "game_pk": game_pk,
                                    "teams": f"{away_team_name} @ {home_team_name}",
                                    "away_abbrev": away_abbrev,
                                    "home_abbrev": home_abbrev,
                                    "away_record": away_record,
                                    "home_record": home_record,
                                    "kind": kind,
                                    "date": schedule_date,
                                    "title": item_title,
                                    "url": url,
                                    "doubleheader": doubleheader,
                                    "game_number": game_number
                                }

                                selected_games.append(game_data)
                                log(f"Found {kind}: {item_title}", Colors.GREEN, verbose=True)
                                break
                    else:
                        log(f"No {kind} video found for {matchup}", Colors.YELLOW, verbose=True)

            except Exception as e:
                log(f"Error processing game {game_pk}: {e}", Colors.RED)

        return selected_games, logonly_games

    except Exception as e:
        log(f"Error: {e}", Colors.RED)
        return [], []

# -----------------------------------------------------------------------------
# External tool (ffmpeg/ffprobe) resolution
# -----------------------------------------------------------------------------

def _app_dirs():
    """Candidate directories that may hold helper binaries bundled with the app.

    Covers both PyInstaller layouts (onefile extracts to sys._MEIPASS; onedir
    keeps them next to the executable) and a plain source run (next to the .py)."""
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    return dirs


def resolve_tool(name):
    """Locate ffmpeg/ffprobe: prefer a copy bundled with the app, else rely on
    PATH (returns the bare name so the OS resolves it)."""
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _app_dirs():
        bundled = os.path.join(d, exe)
        if os.path.isfile(bundled):
            return bundled
    return name


# Resolved once at import. A bundled build finds its own ffmpeg; a source run
# falls back to PATH exactly as before.
FFMPEG = resolve_tool("ffmpeg")
FFPROBE = resolve_tool("ffprobe")


def doctor():
    """Report where ffmpeg/ffprobe resolve to and whether they run. Returns 0 if
    both work, else 1 -- so the exit code is a valid self-test even in a windowed
    build where there's no console to print to."""
    def emit(s):
        try:
            print(s)
        except Exception:
            pass  # windowed exe: no stdout. Exit code still carries the result.

    ok = True
    emit(f"MLBDaily {__version__}")
    emit(f"frozen: {bool(getattr(sys, 'frozen', False))}")
    for label, tool in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        bundled = os.path.isabs(tool)
        try:
            out = subprocess.run([tool, "-version"], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, universal_newlines=True,
                                 timeout=30)
            ver = out.stdout.splitlines()[0] if out.stdout else "(no output)"
            status = "OK" if out.returncode == 0 else f"FAILED (exit {out.returncode})"
            if out.returncode != 0:
                ok = False
        except Exception as e:
            ver, status = f"{type(e).__name__}: {e}", "NOT RUNNABLE"
            ok = False
        emit(f"  {label:8s} [{'bundled' if bundled else 'PATH'}] {status}")
        emit(f"           path: {tool}")
        emit(f"           {ver}")
    emit("result: " + ("all good" if ok else "PROBLEM — see above"))
    return 0 if ok else 1


# -----------------------------------------------------------------------------
# Live CLI progress display
# -----------------------------------------------------------------------------

def _enable_ansi():
    """Enable ANSI/VT escape processing on Windows consoles (no-op elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _probe_duration(url):
    """Return the media duration in seconds via ffprobe, or None if unknown."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30)
        val = out.stdout.strip()
        return float(val) if val and val != "N/A" else None
    except Exception:
        return None


def _human_speed(bps):
    """Format bytes/sec as a human-readable speed string, or None."""
    if not bps or bps <= 0:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if bps < 1024:
            return f"{bps:.1f}{unit}/s"
        bps /= 1024
    return f"{bps:.1f}TB/s"


class LiveProgress:
    """A live, in-place terminal display: one line per game plus a footer.

    A background thread repaints at a fixed rate so spinners animate; worker
    threads only push state via update() (cheap, lock-guarded). Designed for an
    interactive TTY only -- the caller decides whether to use it.
    """

    _SPIN = "|/-\\"

    def __init__(self, labels):
        self.labels = labels
        self.n = len(labels)
        self.width = max((len(s) for s in labels), default=10)
        self.state = [{"status": "queued", "pct": 0.0, "speed": None, "msg": None}
                      for _ in labels]
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._painted = False
        self._tick = 0

    def update(self, idx, status=None, pct=None, speed=None, msg=None):
        if idx is None:
            return
        with self.lock:
            s = self.state[idx]
            if status is not None:
                s["status"] = status
            if pct is not None:
                s["pct"] = pct
            if speed is not None:
                s["speed"] = speed
            if msg is not None:
                s["msg"] = msg

    def _bar(self, pct, width=10):
        filled = int(round(pct / 100.0 * width))
        filled = max(0, min(width, filled))
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    def _spin_bar(self, width=10):
        return "[" + (" " * 4) + self._SPIN[self._tick % len(self._SPIN)] + (" " * (width - 5)) + "]"

    def _render_line(self, i):
        st = self.state[i]
        status = st["status"]
        label = self.labels[i].ljust(self.width)
        if status == "queued":
            return f"{Colors.YELLOW}{label}  {self._bar(0)}  queued{Colors.ENDC}"
        if status == "probing":
            return f"{Colors.BLUE}{label}  {self._spin_bar()}  probing...{Colors.ENDC}"
        if status == "downloading":
            if st["pct"] is None:
                tail = st["speed"] or "working..."
                return f"{Colors.BLUE}{label}  {self._spin_bar()}  {tail}{Colors.ENDC}"
            tail = f"{st['pct']:5.1f}%"
            if st["speed"]:
                tail += f"  {st['speed']}"
            return f"{Colors.BLUE}{label}  {self._bar(st['pct'])}  {tail}{Colors.ENDC}"
        if status == "done":
            return f"{Colors.GREEN}{label}  {self._bar(100)}  done{Colors.ENDC}"
        if status == "skipped":
            # Red so it stands out, but an empty bar + "skipped" make clear no
            # download happened (vs. the red "ERROR" state, which is a failure).
            return f"{Colors.RED}{label}  {self._bar(0)}  skipped (exists){Colors.ENDC}"
        if status == "error":
            extra = f" {st['msg']}" if st["msg"] else ""
            return f"{Colors.RED}{label}  {self._bar(st['pct'] or 0)}  ERROR{extra}{Colors.ENDC}"
        return f"{label}  {self._bar(0)}  {status}"

    def _render_footer(self):
        c = {}
        for st in self.state:
            c[st["status"]] = c.get(st["status"], 0) + 1
        done = c.get("done", 0) + c.get("skipped", 0)
        active = c.get("downloading", 0) + c.get("probing", 0)
        queued = c.get("queued", 0)
        footer = f"{self.n} games | {done} done | {active} downloading | {queued} queued"
        if c.get("error"):
            footer += f" | {c['error']} errors"
        return f"{Colors.BOLD}{footer}{Colors.ENDC}"

    def _paint(self):
        with self.lock:
            lines = [self._render_line(i) for i in range(self.n)]
            lines.append(self._render_footer())
        out = []
        if self._painted:
            out.append(f"\033[{self.n + 1}A")  # move cursor back to top of block
        for ln in lines:
            out.append("\r\033[2K" + ln + "\n")  # clear line, then write
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._painted = True

    def start(self):
        self._paint()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(0.12):
            self._tick += 1
            self._paint()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._paint()  # final frame


def download_video(video_data, output_dir, max_retries=2, progress_cb=None, game_id=None):
    """
    Download a video from the provided data

    Args:
        video_data (dict): Data for the video to download
        output_dir (str): Directory to save the video
        max_retries (int): Number of retries for download failures

    Returns:
        dict: Result of the download operation
    """
    if not video_data or "url" not in video_data:
        return {"status": "error", "reason": "Invalid video data"}

    url = video_data["url"]
    teams_info = f"{video_data['away_abbrev']}@{video_data['home_abbrev']}"

    file_date = video_data.get("date", datetime.now().strftime("%Y-%m-%d"))

    # FIX: Always check title for game numbers (handles DH, suspended games, etc.)
    game_number_suffix = ""
    title = video_data.get("title", "")
    if "Game 1" in title:
        game_number_suffix = " Game 1"
    elif "Game 2" in title:
        game_number_suffix = " Game 2"
    elif "Game 3" in title:
        game_number_suffix = " Game 3"
    elif video_data.get("doubleheader") != "N" and video_data.get("game_number"):
        game_number_suffix = f" Game {video_data['game_number']}"

    # Build "abbrev(W-L)" labels from entering-play records; fall back to a
    # bare abbreviation if the record wasn't available.
    away_rec = video_data.get("away_record")
    home_rec = video_data.get("home_record")
    away_label = f"{video_data['away_abbrev']}({away_rec[0]}{FILENAME_DASH}{away_rec[1]})" if away_rec else video_data['away_abbrev']
    home_label = f"{video_data['home_abbrev']}({home_rec[0]}{FILENAME_DASH}{home_rec[1]})" if home_rec else video_data['home_abbrev']

    kind = video_data.get("kind", "condensed")
    # Use the VLC-safe dash in the date too (e.g. 2026-04-22 -> 2026‑04‑22).
    file_date = file_date.replace("-", FILENAME_DASH)
    filename = f"{file_date} {away_label} @ {home_label}{game_number_suffix} {kind}.mp4"
    output_file = os.path.join(output_dir, filename)

    # When a progress callback is supplied we run in "live" mode: suppress the
    # per-file log lines and stream ffmpeg progress to the callback instead.
    live = progress_cb is not None
    def report(status, pct=None, speed=None, msg=None):
        if live:
            progress_cb(game_id, status, pct, speed, msg)

    if os.path.exists(output_file):
        if live:
            report("skipped")
        else:
            log(f"[{teams_info}] File already exists: {filename}", Colors.YELLOW)
        return {"status": "skipped", "filename": filename, "kind": kind, "reason": "file exists"}

    os.makedirs(output_dir, exist_ok=True)

    if live:
        report("probing")
        duration = _probe_duration(url)
        report("downloading", 0.0)
    else:
        log(f"[{teams_info}] Downloading: {filename}", Colors.BLUE)

    retries = 0
    while retries <= max_retries:
        try:
            if live:
                cmd = [FFMPEG, '-i', url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
                       '-movflags', 'faststart', '-loglevel', 'error',
                       '-progress', 'pipe:1', '-nostats', output_file]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           universal_newlines=True)
                last_size, last_t, speed = 0, time.time(), None
                for line in process.stdout:
                    line = line.strip()
                    if line.startswith("out_time_ms="):
                        try:
                            secs = int(line.split("=", 1)[1]) / 1_000_000  # field is microseconds
                        except ValueError:
                            secs = 0
                        if duration and duration > 0:
                            report("downloading", min(99.5, secs / duration * 100), speed)
                        else:
                            report("downloading", None, speed)
                    elif line.startswith("total_size="):
                        try:
                            size = int(line.split("=", 1)[1])
                        except ValueError:
                            size = last_size
                        now = time.time()
                        if now - last_t >= 0.4 and size >= last_size:
                            speed = _human_speed((size - last_size) / (now - last_t))
                            last_size, last_t = size, now
                process.wait()
                stderr = process.stderr.read()
            else:
                process = subprocess.Popen([FFMPEG, '-i', url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', '-movflags', 'faststart', '-loglevel', 'warning', output_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                stdout, stderr = process.communicate()

            if process.returncode == 0:
                if live:
                    report("done", 100.0)
                else:
                    log(f"[{teams_info}] Download completed: {filename}", Colors.GREEN)
                return {"status": "success", "filename": filename, "kind": kind}
            else:
                retries += 1
                if not live:
                    log(f"[{teams_info}] FFmpeg error (attempt {retries}/{max_retries+1}): {stderr}", Colors.RED)
        except Exception as e:
            retries += 1
            if not live:
                log(f"[{teams_info}] Download error (attempt {retries}/{max_retries+1}): {e}", Colors.RED)

    if live:
        report("error", None, None, "failed")
    return {"status": "error", "filename": filename, "kind": kind, "reason": "download failed"}

def convert_date_format(date_str):
    """Convert between YYYY/MM/DD and YYYY-MM-DD date formats as needed."""
    if "/" in date_str:
        year, month, day = date_str.split("/")
        return f"{year}-{month}-{day}"
    elif "-" in date_str:
        year, month, day = date_str.split("-")
        return f"{year}/{month}/{day}"
    return date_str

def run(args):
    """Execute the download workflow with the given args namespace.

    Returns the stats dict (see end of function). Logs go through log(),
    which routes to the active callback (set via set_log_callback) or stdout.
    """
    global verbose_logging
    verbose_logging = bool(getattr(args, 'verbose', False))

    # Merge in a JSON config file (CLI flags win), then resolve runtime defaults
    # for anything still unset. CLI flags default to None so "not passed" is
    # distinguishable from an explicit value.
    apply_config_defaults(args)
    args.output_dir = getattr(args, 'output_dir', None) or './mlb_videos'
    args.max_workers = int(getattr(args, 'max_workers', None) or 3)
    args.retries = int(args.retries) if getattr(args, 'retries', None) is not None else 2

    try:
        subprocess.run([FFMPEG, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        log("Error: ffmpeg is not installed or not in PATH. Please install ffmpeg first.", Colors.RED)
        return {"success": 0, "skipped": 0, "error": 0, "total": 0, "results": [], "date": None, "output_dir": None}

    if args.date:
        date_str = args.date
    elif getattr(args, 'today', False):
        date_str = datetime.now().strftime("%Y/%m/%d")
    elif getattr(args, 'tomorrow', False):
        date_str = (datetime.now() + timedelta(days=1)).strftime("%Y/%m/%d")
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d")

    if date_str and "-" in date_str:
        date_str = convert_date_format(date_str)

    config = build_config(args)
    log(f"Filter rules — {describe_config(config)}", Colors.BLUE, verbose=True)

    condensed_games, logonly_games = get_condensed_games(date_str, config)

    if not condensed_games and not logonly_games:
        log(f"No games found for {date_str}", Colors.YELLOW)
        return {"success": 0, "skipped": 0, "error": 0, "logonly": 0, "total": 0, "results": [], "date": date_str, "output_dir": None}

    output_dir_abs = os.path.abspath(args.output_dir)
    log(f"Downloading {len(condensed_games)} videos to {output_dir_abs}", Colors.BOLD)

    if verbose_logging:
        log(f"Found {len(condensed_games)} videos to download:", Colors.GREEN)
        for i, game in enumerate(condensed_games, 1):
            log(f"{game.get('kind', 'condensed').capitalize()} {i}:", Colors.BOLD)
            log(f"  Teams: {game['teams']}", Colors.BLUE)
            log(f"  Title: {game['title']}", Colors.BLUE)
            log(f"  URL: {game['url']}", Colors.BLUE)

    if getattr(args, 'save_json', None):
        with open(args.save_json, "w") as f:
            json.dump(condensed_games, f, indent=2)
        log(f"Saved {len(condensed_games)} condensed games to {args.save_json}", Colors.GREEN)

    if getattr(args, 'dry_run', False):
        log("Dry run - videos would be downloaded to: " + output_dir_abs, Colors.YELLOW)
        return {"success": 0, "skipped": 0, "error": 0, "logonly": len(logonly_games), "total": len(condensed_games), "results": [], "date": date_str, "output_dir": output_dir_abs}

    stats = {"success": 0, "skipped": 0, "error": 0, "logonly": len(logonly_games), "total": len(condensed_games), "results": [], "date": date_str, "output_dir": output_dir_abs}

    # Live in-place progress display only on an interactive terminal with no log
    # redirection (the GUI installs a log callback -> falls back to plain logs).
    live = (_log_callback is None) and sys.stdout.isatty()
    lp = None
    if live:
        _enable_ansi()
        labels = [f"{g['away_abbrev']}{_rec_str(g['away_record'])} @ "
                  f"{g['home_abbrev']}{_rec_str(g['home_record'])} {g['kind']}"
                  for g in condensed_games]
        lp = LiveProgress(labels)
        lp.start()
    progress_cb = lp.update if lp else None

    try:
        if args.max_workers > 1 and len(condensed_games) > 1:
            def _dl(pair):
                idx, game = pair
                return download_video(game, args.output_dir, args.retries, progress_cb, idx)
            with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
                stats["results"] = list(executor.map(_dl, list(enumerate(condensed_games))))
        else:
            for idx, game in enumerate(condensed_games):
                stats["results"].append(download_video(game, args.output_dir, args.retries, progress_cb, idx))
    finally:
        if lp:
            lp.stop()

    for result in stats["results"]:
        if result["status"] == "success":
            stats["success"] += 1
        elif result["status"] == "skipped":
            stats["skipped"] += 1
        elif result["status"] == "error":
            stats["error"] += 1

    log("=" * 50)
    log("DOWNLOAD SUMMARY", Colors.BOLD)
    log("=" * 50)
    log(f"Date: {date_str}")
    log(f"Total Videos: {stats['total']}")
    log(f"Successful downloads: {stats['success']}", Colors.GREEN)
    log(f"Skipped (already exists): {stats['skipped']}", Colors.YELLOW)
    log(f"Errors: {stats['error']}", Colors.RED)

    # Per-type splits, always shown (even at 0): condensed, recap, then the
    # "Losers" line counting both-losing games that got a log entry only.
    by_type = {"condensed": {"success": 0, "skipped": 0, "error": 0},
               "recap": {"success": 0, "skipped": 0, "error": 0}}
    for game, result in zip(condensed_games, stats["results"]):
        k = result.get("kind", game.get("kind", "condensed"))
        d = by_type.setdefault(k, {"success": 0, "skipped": 0, "error": 0})
        st = result.get("status", "error")
        d[st] = d.get(st, 0) + 1
    log("-" * 50)
    log("By type:", Colors.BOLD)
    for k in ("condensed", "recap"):
        d = by_type[k]
        log(f"  {k.capitalize():10s} {d['success']} downloaded, "
            f"{d['skipped']} skipped, {d['error']} failed")
    log(f"  {'Losers':10s} {len(logonly_games)} skipped (both losing, log only)", Colors.YELLOW)

    if stats["error"] > 0:
        log("-" * 50)
        log("Failed Downloads:", Colors.RED)
        for result in stats["results"]:
            if result["status"] == "error":
                log(f"  - {result.get('filename', 'Unknown')} ({result.get('reason', 'unknown error')})", Colors.RED)

    log("-" * 50)
    log(f"Videos saved to: {output_dir_abs}")
    log("=" * 50)

    return stats


# -----------------------------------------------------------------------------
# GUI state persistence
# -----------------------------------------------------------------------------

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mlbdaily_state.json")


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def compute_default_date():
    """Default date rule: previous-used + 1 day, capped at today.
    If no prior state, return today."""
    today = datetime.now().date()
    state = _load_state()
    last = state.get("last_date")
    if last:
        try:
            last_d = datetime.strptime(last, "%Y-%m-%d").date()
            candidate = last_d + timedelta(days=1)
            return min(candidate, today)
        except Exception:
            pass
    return today


# -----------------------------------------------------------------------------
# Tkinter GUI
# -----------------------------------------------------------------------------

CRASH_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mlbdaily_crash.log")


def _launch_gui_impl():
    """Run the tkinter-based front-end. Blocks until window closes."""
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox
    import threading
    import queue as queue_mod

    state = _load_state()
    initial_date = compute_default_date().strftime("%Y-%m-%d")
    initial_outdir = state.get("last_output_dir", "./mlb_videos")
    initial_workers = int(state.get("last_workers", 3))
    initial_follow = state.get("last_follow", "")
    initial_never = state.get("last_never_losers", "")
    initial_losing = state.get("last_losing", "-3")

    root = tk.Tk()
    root.title("MLB Daily")
    root.geometry("780x600")
    try:
        root.configure(bg="#1e1e2e")
    except Exception:
        pass

    log_queue = queue_mod.Queue()

    # ---- Row 1: date ----
    date_frame = tk.Frame(root, bg="#1e1e2e")
    date_frame.pack(fill="x", padx=10, pady=(10, 4))
    tk.Label(date_frame, text="Date (YYYY-MM-DD):", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    date_var = tk.StringVar(value=initial_date)
    date_entry = tk.Entry(date_frame, textvariable=date_var, width=14)
    date_entry.pack(side="left", padx=(6, 6))

    def set_offset(days):
        date_var.set((datetime.now().date() + timedelta(days=days)).strftime("%Y-%m-%d"))

    tk.Button(date_frame, text="Yesterday", command=lambda: set_offset(-1)).pack(side="left", padx=2)
    tk.Button(date_frame, text="Today",     command=lambda: set_offset(0)).pack(side="left", padx=2)
    tk.Button(date_frame, text="Tomorrow",  command=lambda: set_offset(1)).pack(side="left", padx=2)

    # ---- Row 2: output dir ----
    out_frame = tk.Frame(root, bg="#1e1e2e")
    out_frame.pack(fill="x", padx=10, pady=4)
    tk.Label(out_frame, text="Output dir:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    out_var = tk.StringVar(value=initial_outdir)
    out_entry = tk.Entry(out_frame, textvariable=out_var)
    out_entry.pack(side="left", fill="x", expand=True, padx=6)
    def browse():
        d = filedialog.askdirectory(initialdir=out_var.get() if os.path.isdir(out_var.get()) else os.path.expanduser("~"))
        if d:
            out_var.set(d)
    tk.Button(out_frame, text="Browse...", command=browse).pack(side="left")

    # ---- Row 3: options ----
    opt_frame = tk.Frame(root, bg="#1e1e2e")
    opt_frame.pack(fill="x", padx=10, pady=4)
    tk.Label(opt_frame, text="Workers:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    workers_var = tk.IntVar(value=initial_workers)
    tk.Spinbox(opt_frame, from_=1, to=8, textvariable=workers_var, width=4).pack(side="left", padx=(6, 12))
    dry_var = tk.BooleanVar(value=False)
    verbose_var = tk.BooleanVar(value=False)
    tk.Checkbutton(opt_frame, text="Dry run", variable=dry_var, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                   activebackground="#1e1e2e", activeforeground="#cdd6f4").pack(side="left", padx=4)
    tk.Checkbutton(opt_frame, text="Verbose", variable=verbose_var, bg="#1e1e2e", fg="#cdd6f4", selectcolor="#313244",
                   activebackground="#1e1e2e", activeforeground="#cdd6f4").pack(side="left", padx=4)
    run_btn = tk.Button(opt_frame, text="Run", width=12)
    run_btn.pack(side="right", padx=4)

    # ---- Row 4: filtering ----
    filt_frame = tk.Frame(root, bg="#1e1e2e")
    filt_frame.pack(fill="x", padx=10, pady=4)
    tk.Label(filt_frame, text="Always condensed:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    follow_var = tk.StringVar(value=initial_follow)
    tk.Entry(filt_frame, textvariable=follow_var, width=16).pack(side="left", padx=(6, 12))
    tk.Label(filt_frame, text="Never losers:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    never_var = tk.StringVar(value=initial_never)
    tk.Entry(filt_frame, textvariable=never_var, width=16).pack(side="left", padx=(6, 12))
    tk.Label(filt_frame, text="Losing bar:", bg="#1e1e2e", fg="#cdd6f4").pack(side="left")
    losing_var = tk.StringVar(value=initial_losing)
    tk.Entry(filt_frame, textvariable=losing_var, width=8).pack(side="left", padx=(6, 0))
    tk.Label(filt_frame, text="(e.g. atl,nyy   |   -3 games or 0.440 pct)",
             bg="#1e1e2e", fg="#6c7086").pack(side="left", padx=8)

    # ---- Log area ----
    log_text = scrolledtext.ScrolledText(root, wrap="word", state="disabled",
                                          font=("Consolas", 9), bg="#181825", fg="#cdd6f4",
                                          insertbackground="#cdd6f4")
    log_text.pack(fill="both", expand=True, padx=10, pady=6)
    log_text.tag_configure("green",  foreground="#a6e3a1")
    log_text.tag_configure("yellow", foreground="#f9e2af")
    log_text.tag_configure("red",    foreground="#f38ba8")
    log_text.tag_configure("blue",   foreground="#89b4fa")
    log_text.tag_configure("bold",   font=("Consolas", 9, "bold"))

    color_to_tag = {
        Colors.GREEN:  "green",
        Colors.YELLOW: "yellow",
        Colors.RED:    "red",
        Colors.BLUE:   "blue",
        Colors.BOLD:   "bold",
    }

    # ---- Status bar ----
    status_var = tk.StringVar(value="Ready.")
    status_label = tk.Label(root, textvariable=status_var, anchor="w", bg="#1e1e2e", fg="#cdd6f4")
    status_label.pack(fill="x", padx=10, pady=(0, 8))

    def append_log(line, color):
        log_text.configure(state="normal")
        tag = color_to_tag.get(color)
        if tag:
            log_text.insert("end", line + "\n", tag)
        else:
            log_text.insert("end", line + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def on_log(line, color):
        log_queue.put(("log", line, color))

    progress = {"done": 0, "total": 0}

    def update_status():
        if progress["total"] > 0:
            status_var.set(f"Progress: {progress['done']} / {progress['total']}")

    def poll_queue():
        try:
            while True:
                item = log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, line, color = item
                    append_log(line, color)
                    if "Downloading " in line and progress["total"] == 0:
                        # parse "Downloading N videos to ..." once
                        m = re.search(r"Downloading (\d+) videos", line)
                        if m:
                            progress["total"] = int(m.group(1))
                            update_status()
                    elif "Download completed" in line or "File already exists" in line:
                        progress["done"] += 1
                        update_status()
                elif kind == "done":
                    _, stats = item
                    run_btn.configure(state="normal")
                    msg = (f"Done. {stats.get('success', 0)} ok / {stats.get('skipped', 0)} skipped / "
                           f"{stats.get('error', 0)} errors  ({stats.get('total', 0)} total)")
                    status_var.set(msg)
        except queue_mod.Empty:
            pass
        root.after(100, poll_queue)

    def validate_date(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def start_run():
        chosen = date_var.get().strip()
        if not validate_date(chosen):
            messagebox.showerror("Bad date", "Date must be YYYY-MM-DD.")
            return
        run_btn.configure(state="disabled")
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")
        progress["done"] = 0
        progress["total"] = 0
        status_var.set("Running...")

        # Persist state immediately on Run (so even crashes update the +1 rule)
        _save_state({
            "last_date": chosen,
            "last_output_dir": out_var.get(),
            "last_workers": int(workers_var.get()),
            "last_follow": follow_var.get().strip(),
            "last_never_losers": never_var.get().strip(),
            "last_losing": losing_var.get().strip() or "-3",
        })

        args = argparse.Namespace(
            date=chosen.replace("-", "/"),
            today=False, yesterday=False, tomorrow=False,
            output_dir=out_var.get(),
            max_workers=int(workers_var.get()),
            retries=2,
            dry_run=bool(dry_var.get()),
            save_json=None,
            verbose=bool(verbose_var.get()),
            follow=follow_var.get().strip(),
            never_losers=never_var.get().strip(),
            losing=losing_var.get().strip() or "-3",
            gui=False,
        )

        def worker():
            set_log_callback(on_log)
            try:
                stats = run(args)
                log_queue.put(("done", stats))
            except Exception as e:
                log_queue.put(("log", f"Unhandled error: {e}", Colors.RED))
                log_queue.put(("done", {"success": 0, "skipped": 0, "error": 1, "total": 0}))
            finally:
                set_log_callback(None)

        threading.Thread(target=worker, daemon=True).start()

    run_btn.configure(command=start_run)
    root.bind("<Return>", lambda e: start_run())
    root.bind("<Escape>", lambda e: root.destroy())

    # Force the window to the front on launch so it isn't hidden behind other apps
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()

    poll_queue()
    root.mainloop()


def _log_crash(prefix, exc):
    """Write a timestamped traceback to CRASH_LOG. Survives pythonw silent failures."""
    import traceback
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat()} {prefix} ===\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


def launch_gui():
    """Public entry point. Wraps _launch_gui_impl so any startup error gets
    written to a crash log (since pythonw.exe swallows stderr)."""
    try:
        _launch_gui_impl()
    except Exception as e:
        _log_crash("launch_gui", e)
        # Best-effort dialog so the user knows something went wrong
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("MLB Daily — startup error",
                                  f"{type(e).__name__}: {e}\n\nFull traceback written to:\n{CRASH_LOG}")
            r.destroy()
        except Exception:
            pass
        raise


def main():
    # Filenames/logs contain a non-breaking hyphen (U+2011); make sure stdout can
    # print it even when redirected to a file/pipe on a legacy Windows code page.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description='''MLB Condensed Game Downloader

Finds and downloads MLB condensed game videos for a specified date, using the
public MLB StatsAPI. Games are filtered by the teams' entering-play records:
both teams winning -> condensed game; one winning -> recap; both losing -> a
log entry only. Tune that behavior with --follow, --never-losers, and --losing.

Examples:
  python MLBDaily.py --yesterday
  python MLBDaily.py --date 2025/09/29 --output-dir ~/Videos/MLB
  python MLBDaily.py --yesterday --follow atl,nyy --losing 0.440
''', formatter_class=argparse.RawDescriptionHelpFormatter)

    date_group = parser.add_argument_group('Date Selection')
    date_options = date_group.add_mutually_exclusive_group()
    date_options.add_argument('--date', help='Date in YYYY/MM/DD format')
    date_options.add_argument('--today', action='store_true', help='Use today\'s date')
    date_options.add_argument('--tomorrow', action='store_true', help='Use tomorrow\'s date')
    date_options.add_argument('--yesterday', action='store_true', help='Use yesterday\'s date (default)')

    filter_group = parser.add_argument_group('Filtering')
    filter_group.add_argument('--follow', metavar='TEAMS',
        help='Comma-separated teams whose game is ALWAYS pulled as a condensed '
             'game, regardless of record (e.g. "atl,nyy"). Default: none.')
    filter_group.add_argument('--never-losers', metavar='TEAMS',
        help='Comma-separated teams always treated as "winning" for filtering, '
             'even when below the bar (e.g. "lad,hou"). Default: none.')
    filter_group.add_argument('--losing', metavar='BAR', default=None,
        help='The winning bar. A whole number is games vs .500 — e.g. -3 keeps '
             'teams within 3 games of .500; a decimal is a win%% — e.g. 0.440 '
             'keeps teams at .440 or better. Default: -3.')
    filter_group.add_argument('--list-teams', action='store_true',
        help='Print all team abbreviations and exit.')

    parser.add_argument('--version', action='version', version=f'MLBDaily {__version__}')
    parser.add_argument('--doctor', action='store_true',
        help='Report ffmpeg/ffprobe resolution and exit (diagnostic).')

    parser.add_argument('--config', metavar='PATH',
        help='JSON config file with default options (CLI flags override it). '
             'If omitted, mlbdaily.config.json in the current or script dir is '
             'used when present.')
    parser.add_argument('--output-dir', default=None, help='Directory to save downloaded videos (default: ./mlb_videos)')
    parser.add_argument('--max-workers', type=int, default=None, help='Maximum number of concurrent downloads (default: 3)')
    parser.add_argument('--retries', type=int, default=None, help='Number of retries for failed downloads (default: 2)')
    parser.add_argument('--dry-run', action='store_true', help='Find videos but don\'t download')
    parser.add_argument('--save-json', help='Save video info to JSON file')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('--gui', action='store_true', help='Launch the tkinter GUI instead of CLI')

    args = parser.parse_args()

    if args.doctor:
        return doctor()

    if args.list_teams:
        for tid in sorted(TEAMS, key=lambda t: TEAMS[t][0]):
            abbr, name = TEAMS[tid]
            print(f"  {abbr:5s} {name}  (id {tid})")
        return 0

    if args.gui:
        launch_gui()
        return 0

    stats = run(args)
    return 0 if stats.get('error', 0) == 0 else 1


if __name__ == "__main__":
    verbose_logging = False
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        _log_crash("__main__", _e)
        sys.exit(2)
