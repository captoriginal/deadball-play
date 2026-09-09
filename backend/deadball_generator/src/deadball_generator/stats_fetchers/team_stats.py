"""
Fetch season or postseason batting/pitching stats for a given MLB team and season.

Regular season: pulls Fangraphs stats, falling back to MLB season totals.
Postseason: aggregates MLB Stats API boxscore data for team totals.

Outputs:
- TEAM_YEAR_batting.csv / TEAM_YEAR_pitching.csv
- TEAM_YEAR_batting_postseason.csv / TEAM_YEAR_pitching_postseason.csv
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlencode

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment

from deadball_generator import paths
from deadball_generator import rules, career, cache_policy
from deadball_generator.rules import batter_traits, pitcher_traits, pitcher_die, target as fmt_two_digit
from deadball_generator.paths import RETRO_POST_DIR

# Local caches to avoid home-directory writes
PROJECT_ROOT = paths.PROJECT_ROOT
STAT_DIR = paths.STATS_DIR
DEADBALL_DIR = paths.DEADBALL_SEASON_DIR
CACHE_ROOT = PROJECT_ROOT / ".cache"
os.environ.setdefault("PYBASEBALL_CACHE", str(CACHE_ROOT / "pybaseball"))
os.environ.setdefault("MPLCONFIGDIR", str(CACHE_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_ROOT))
for cache_dir in ("PYBASEBALL_CACHE", "MPLCONFIGDIR", "XDG_CACHE_HOME"):
    Path(os.environ[cache_dir]).mkdir(parents=True, exist_ok=True)
HAND_CACHE_FILE = CACHE_ROOT / "hands_cache.json"

from pybaseball import fg_batting_data, fg_pitching_data  # type: ignore
from pybaseball import cache as pybaseball_cache  # type: ignore
from pybaseball.teamid_lookup import team_ids
from pybaseball.datasources.fangraphs import fg_fielding_data  # type: ignore
from pybaseball import playerid_reverse_lookup, playerid_lookup  # type: ignore

# This project owns cache freshness explicitly; avoid pybaseball's separate
# seven-day cache making a requested 24-hour refresh return older source data.
pybaseball_cache.disable()

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; team-stat-fetcher/1.0)"}
HAND_CACHE_FG: dict[int, tuple[Optional[str], Optional[str]]] = {}
HAND_CACHE_NAME: dict[str, tuple[Optional[str], Optional[str]]] = {}
RESOLVED_HANDS: dict[str, tuple[Optional[str], Optional[str]]] = {}
_HAND_CACHE_LOADED = False
MIN_ROWS_REQUIRED = 1
RETRO_CACHE_BY_ID: dict[str, tuple[Optional[str], Optional[str]]] = {}
RETRO_CACHE_BY_NAME: dict[str, tuple[Optional[str], Optional[str]]] = {}
RETRO_NAME_BY_ID: dict[str, str] = {}
RETRO_LOADED_YEARS: set[int] = set()
RETRO_POST_LOADED: set[int] = set()
MLB_TEAM_IDS = {
    "ARI": 109,
    "ATL": 144,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CHW": 145,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "DET": 116,
    "HOU": 117,
    "KCR": 118,
    "LAA": 108,
    "LAD": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "OAK": 133,
    "PHI": 143,
    "PIT": 134,
    "SDP": 135,
    "SEA": 136,
    "SFG": 137,
    "STL": 138,
    "TBR": 139,
    "TEX": 140,
    "TOR": 141,
    "WSN": 120,
}


def csv_has_columns(path: Path, required: list[str]) -> bool:
    try:
        df = pd.read_csv(path, nrows=1)
    except Exception:
        return False
    return all(col in df.columns for col in required)


def dataset_complete(paths: list[Path], required_cols: list[str]) -> bool:
    for path in paths:
        if not path.exists():
            return False
        if path.stat().st_size == 0:
            return False
        if not csv_has_columns(path, required_cols):
            return False
    return True


def stat_paths(team: str, season: int, postseason: bool = False) -> list[Path]:
    prefix = f"{team.lower()}_{season}"
    if postseason:
        return [
            STAT_DIR / f"{prefix}_batting_postseason.csv",
            STAT_DIR / f"{prefix}_pitching_postseason.csv",
        ]
    return [
        STAT_DIR / f"{prefix}_batting.csv",
        STAT_DIR / f"{prefix}_pitching.csv",
    ]


def deadball_paths(team: str, season: int, postseason: bool = False) -> list[Path]:
    prefix = f"{team.lower()}_{season}"
    if postseason:
        return [DEADBALL_DIR / f"{prefix}_deadball_postseason.csv"]
    return [DEADBALL_DIR / f"{prefix}_deadball.csv"]


def _iter_retro_post_paths(season: int) -> list[Path]:
    """
    Return postseason event file paths for a given season if present.
    Looks under data/raw/retrosheet/allpost/ for files like 2025WS.EVE, 2025ALCS.EVE, etc.
    """
    if not RETRO_POST_DIR.exists():
        return []
    patterns = [
        f"{season}*EVE",
        f"{season}*EVN",
    ]
    paths: list[Path] = []
    for pat in patterns:
        paths.extend(RETRO_POST_DIR.glob(pat))
    return sorted(paths)


def _mlb_team_id(team_code: str) -> int:
    code = team_code.upper()
    if code not in MLB_TEAM_IDS:
        raise ValueError(f"No MLB team id known for code '{team_code}'")
    return MLB_TEAM_IDS[code]


def _parse_retro_event_file(path: Path, team_map: dict[str, str]) -> tuple[list[tuple], list[tuple]]:
    """
    Parse a single Retrosheet event file (.EVE/.EVN) and return batting/pitching event records.
    Batting records: (team, player_id, event_text, half)
    Pitching records: (team, pitcher_id, event_text, half)
    """
    batting: list[tuple] = []
    pitching: list[tuple] = []
    home_team = away_team = None
    current_pitchers = {"0": None, "1": None}  # half -> pitcher id

    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            code = parts[0]
            if code == "info":
                if parts[1] == "visteam":
                    away_team = team_map.get(parts[2], parts[2])
                elif parts[1] == "hometeam":
                    home_team = team_map.get(parts[2], parts[2])
            elif code == "start":
                # start,slot,half,player,position
                _, _, half, player_id, pos = parts[:5]
                if pos == "1":  # pitcher
                    current_pitchers[half] = player_id
            elif code == "sub":
                # substitutions; track pitcher changes
                _, _, half, player_id, pos, *_ = parts
                if pos == "1":
                    current_pitchers[half] = player_id
            elif code == "play":
                # play,inning,half,batter,count,pitches,event
                if len(parts) < 7:
                    continue
                _, _, half, batter_id, _, _, event_text = parts[:7]
                team = home_team if half == "1" else away_team
                batting.append((team, batter_id, event_text, half))
                pitcher_id = current_pitchers.get(half)
                if pitcher_id:
                    pit_team = away_team if half == "1" else home_team
                    pitching.append((pit_team, pitcher_id, event_text, half))
    return batting, pitching


def _accumulate_batting(records: list[tuple]) -> pd.DataFrame:
    rows = []
    for team, batter_id, event_text, _ in records:
        et = event_text.upper()
        h = hr = dbl = trp = bb = hbp = sf = sh = 0
        pa = ab = 0
        sb = cs = 0

        # Simple parsing based on leading token
        if et.startswith("HR"):
            hr = 1
            h = 1
            ab = 1
        elif et.startswith("TR"):
            trp = 1
            h = 1
            ab = 1
        elif et.startswith("D"):
            dbl = 1
            h = 1
            ab = 1
        elif et.startswith("S"):
            h = 1
            ab = 1
        elif et.startswith(("W", "IW", "BB")):
            bb = 1
        elif et.startswith("HP"):
            hbp = 1
        elif et.startswith("K"):
            ab = 1
        elif et.startswith(("E", "FC", "DP", "TP")):
            ab = 1
        elif et.startswith(("SF", "SH")):
            if et.startswith("SF"):
                sf = 1
            else:
                sh = 1
        elif et.startswith("CS"):
            cs = 1
        elif et.startswith("SB"):
            sb = 1

        # SB/CS can appear later in string
        if "SB" in et:
            sb = 1
        if "CS" in et:
            cs = 1

        pa = 1

        rows.append(
            {
                "Team": team,
                "Player": batter_id,
                "H": h,
                "HR": hr,
                "2B": dbl,
                "3B": trp,
                "BB": bb,
                "HBP": hbp,
                "SF": sf,
                "SH": sh,
                "SB": sb,
                "CS": cs,
                "PA": pa,
                "AB": ab,
                "G": 1,
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    agg = df.groupby(["Team", "Player"], as_index=False).sum()
    agg["AVG"] = agg.apply(lambda r: r["H"] / r["AB"] if r["AB"] else None, axis=1)
    agg["OBP"] = agg.apply(
        lambda r: (r["H"] + r["BB"] + r["HBP"]) / (r["AB"] + r["BB"] + r["HBP"] + r["SF"])
        if (r["AB"] + r["BB"] + r["HBP"] + r["SF"])
        else None,
        axis=1,
    )
    return agg


def _accumulate_pitching(records: list[tuple]) -> pd.DataFrame:
    rows = []
    for team, pitcher_id, event_text, _ in records:
        et = event_text.upper()
        er = so = bb = 0
        outs = 0
        # Very rough heuristics
        if et.startswith("K"):
            so = 1
            outs = 1
        elif et.startswith("W"):
            bb = 1
        elif et.startswith("IW"):
            bb = 1
        elif et.startswith("BB"):
            bb = 1
        elif et.startswith("HP"):
            bb = 0
        elif et.startswith(("S", "D", "TR", "HR", "E", "FC")):
            outs = 0
        elif et.startswith(("SF", "SH")):
            outs = 1
        else:
            # default assume an out
            outs = 1

        rows.append(
            {
                "Team": team,
                "Player": pitcher_id,
                "ER": er,
                "SO": so,
                "BB": bb,
                "outs_ct": outs,
                "G": 1,
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    agg = df.groupby(["Team", "Player"], as_index=False).sum()
    agg["IP"] = agg["outs_ct"] / 3.0
    agg["ERA"] = agg.apply(lambda r: (r["ER"] * 9) / r["IP"] if r["IP"] else None, axis=1)
    agg["K/9"] = agg.apply(lambda r: (r["SO"] * 9) / r["IP"] if r["IP"] else None, axis=1)
    agg["BB/9"] = agg.apply(lambda r: (r["BB"] * 9) / r["IP"] if r["IP"] else None, axis=1)
    return agg


def _load_retro_rosters(season: int) -> None:
    """
    Load Retrosheet roster files (TEAMYYYY.ROS) from data/raw/retrosheet
    and cache bats/throws by retro id and normalized name.
    """
    if season in RETRO_LOADED_YEARS:
        return
    retro_root = paths.DATA_RAW_DIR / "retrosheet"
    if not retro_root.exists():
        return
    roster_paths = list(retro_root.rglob(f"*{season}*.ROS")) + list(retro_root.rglob(f"*{season}*.ros"))
    if not roster_paths:
        return
    loaded = False
    for path in roster_paths:
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 5:
                    continue
                retro_id, last, first, bats_raw, throws_raw = parts[:5]
                bats = normalize_hand(bats_raw)
                throws = normalize_hand(throws_raw)
                RETRO_CACHE_BY_ID[retro_id] = (bats, throws)
                name_key = normalize_player_name(f"{first} {last}")
                if name_key:
                    RETRO_CACHE_BY_NAME[name_key] = (bats, throws)
                RETRO_NAME_BY_ID[retro_id] = f"{first} {last}".strip()
                loaded = True
        except Exception:
            continue
    if loaded:
        RETRO_LOADED_YEARS.add(season)


def _load_hand_cache() -> None:
    global _HAND_CACHE_LOADED
    if _HAND_CACHE_LOADED or not HAND_CACHE_FILE.exists():
        _HAND_CACHE_LOADED = True
        return
    try:
        raw = json.loads(HAND_CACHE_FILE.read_text())
    except Exception:
        _HAND_CACHE_LOADED = True
        return

    def _norm_tuple(val) -> tuple[Optional[str], Optional[str]]:
        if isinstance(val, (list, tuple)) and len(val) == 2:
            return normalize_hand(val[0]), normalize_hand(val[1])
        return None, None

    for k, v in raw.get("fg", {}).items():
        try:
            HAND_CACHE_FG[int(k)] = _norm_tuple(v)
        except Exception:
            continue
    for k, v in raw.get("name", {}).items():
        HAND_CACHE_NAME[str(k)] = _norm_tuple(v)
    _HAND_CACHE_LOADED = True


def _save_hand_cache() -> None:
    payload = {
        "fg": {str(k): list(v) for k, v in HAND_CACHE_FG.items()},
        "name": {str(k): list(v) for k, v in HAND_CACHE_NAME.items()},
    }
    HAND_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        HAND_CACHE_FILE.write_text(json.dumps(payload))
    except Exception:
        pass

# Only the columns needed for downstream Deadball builds.
REQUIRED_BAT_COLS = [
    "StatsVersion",
    "StatsFetchedAt",
    "IDfg",
    "IDmlb",
    "Name",
    "Age",
    "G",
    "AB",
    "PA",
    "H",
    "1B",
    "2B",
    "3B",
    "HR",
    "R",
    "RBI",
    "BB",
    "SO",
    "SB",
    "CS",
    "AVG",
    "OBP",
    "SLG",
    "OPS",
    "Pos",
    "FP",
    "ISO", "K%", "BsR", "DRS", "GoldGlove",
    "HBP", "SF",
    "Hand",
    "LR",
    "Throws",
]
REQUIRED_PIT_COLS = [
    "StatsVersion",
    "StatsFetchedAt",
    "IDfg",
    "IDmlb",
    "Name",
    "Age",
    "G",
    "GS",
    "IP",
    "ERA",
    "K/9",
    "BB/9",
    "GB%",
    "CG",
    "Role",
    "Pos",
    "Hand",
    "Throws",
]
REQUIRED_POST_BAT_COLS = [
    "StatsVersion",
    "StatsFetchedAt",
    "Player",
    "IDmlb", "PA",
    "Age",
    "Pos",
    "G",
    "AVG",
    "BA",
    "OBP",
    "HR",
    "2B",
    "SB",
    "Hand",
    "LR",
    "Throws",
]
REQUIRED_POST_PIT_COLS = [
    "StatsVersion",
    "StatsFetchedAt",
    "Player",
    "IDmlb", "G",
    "Age",
    "IP",
    "ERA",
    "K/9",
    "BB/9",
    "GB%",
    "GS",
    "Hand",
    "Throws",
]


def _maybe_sleep(rate_limit_seconds: float) -> None:
    if rate_limit_seconds > 0:
        time.sleep(rate_limit_seconds)


def _announce_request(label: str, rate_limit_seconds: float) -> None:
    msg = f"[deadball] Requesting {label}"
    if rate_limit_seconds > 0:
        msg += f" (will wait {rate_limit_seconds:.1f}s after)"
    print(msg)


def _fetch_with_rate_limit(url: str, rate_limit_seconds: float, label: str) -> requests.Response:
    print(f"[deadball] Requesting {label}: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if rate_limit_seconds > 0:
        print(f"[deadball] Waiting {rate_limit_seconds:.1f}s before next request")
        time.sleep(rate_limit_seconds)
    return resp


def _fetch_fg_leaderboard(team_id: int, season: int, stats: str, rate_limit_seconds: float = 0.0) -> pd.DataFrame:
    """
    Fetch a Fangraphs leaderboard CSV for a given team/season.
    Uses month=13 to capture postseason games.
    stats: "bat" or "pit"
    """
    type_param = 8  # standard table
    url = (
        "https://www.fangraphs.com/leaders.aspx"
        f"?pos=all&stats={stats}&lg=all&qual=0&type={type_param}"
        f"&season={season}&month=13&season1={season}&ind=0&team={team_id}"
        "&seasonType=2&rost=0&age=0&filter=&players=0&startdate=&enddate=&page=1_10000&csv=1"
    )
    resp = _fetch_with_rate_limit(url, rate_limit_seconds, f"Fangraphs postseason {stats}")
    resp.raise_for_status()
    from io import StringIO
    # Try CSV first.
    try:
        df = pd.read_csv(StringIO(resp.text))
        if not df.empty:
            return df
    except Exception:
        pass
    # Fall back to parsing the HTML leaderboard table.
    try:
        tables = pd.read_html(resp.text)
        for t in tables:
            if "Name" in t.columns:
                return t
        if tables:
            return tables[0]
    except Exception:
        pass
    # Last resort: let pandas try directly from the URL (may still fail).
    return pd.read_csv(url)


def _mlb_postseason_stats(team_code: str, season: int, rate_limit_seconds: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Aggregate postseason batting/pitching per player for a team using MLB Stats API boxscores.
    """
    team_id = _mlb_team_id(team_code)
    sched_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}&season={season}&gameTypes=F,D,L,W,C"
    sched_resp = _fetch_with_rate_limit(sched_url, rate_limit_seconds, "MLB postseason schedule")
    try:
        sched = sched_resp.json()
    except Exception:
        return pd.DataFrame(), pd.DataFrame()
    game_pks: list[int] = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            game_pks.append(g.get("gamePk"))
    game_pks = [g for g in game_pks if g]
    if not game_pks:
        return pd.DataFrame(), pd.DataFrame()

    bat_totals: dict[str, dict] = {}
    pit_totals: dict[str, dict] = {}

    def _accum_bat(pid: str, name: str, pos: str, bats: Optional[str], throws: Optional[str], stats: dict):
        rec = bat_totals.setdefault(
            pid,
            {"Player": name, "Pos": pos, "Hand": bats, "Throws": throws, "Team": team_code, "G": 0, "H": 0, "HR": 0, "2B": 0, "3B": 0, "SB": 0, "CS": 0, "BB": 0, "HBP": 0, "SF": 0, "SH": 0, "AB": 0, "PA": 0},
        )
        rec["G"] += 1
        rec["IDmlb"] = int(pid)
        rec["H"] += stats.get("hits", 0)
        rec["HR"] += stats.get("homeRuns", 0)
        rec["2B"] += stats.get("doubles", 0)
        rec["3B"] += stats.get("triples", 0)
        rec["SB"] += stats.get("stolenBases", 0)
        rec["CS"] += stats.get("caughtStealing", 0)
        rec["BB"] += stats.get("baseOnBalls", 0)
        rec["HBP"] += stats.get("hitByPitch", 0)
        rec["SF"] += stats.get("sacFlies", 0)
        rec["SH"] += stats.get("sacBunts", 0)
        rec["AB"] += stats.get("atBats", 0)
        rec["PA"] += stats.get("plateAppearances", stats.get("atBats", 0) + stats.get("baseOnBalls", 0) + stats.get("hitByPitch", 0) + stats.get("sacFlies", 0))

    def _accum_pit(pid: str, name: str, pos: str, throws: Optional[str], bats: Optional[str], stats: dict):
        rec = pit_totals.setdefault(
            pid,
            {"Player": name, "Pos": pos or "P", "Hand": throws, "Throws": throws, "Team": team_code, "GS": 0, "IP": 0.0, "ER": 0, "SO": 0, "BB": 0, "GO": 0, "AO": 0},
        )
        rec["GS"] += stats.get("gamesStarted", 0)
        rec["IDmlb"] = int(pid)
        rec["G"] = rec.get("G", 0) + 1
        ip_str = stats.get("inningsPitched", "0.0")
        try:
            outs = int(ip_str.split(".")[0]) * 3 + int(ip_str.split(".")[1])
            ip_val = outs / 3.0
        except Exception:
            try:
                ip_val = float(ip_str)
            except Exception:
                ip_val = 0.0
        rec["IP"] += ip_val
        rec["ER"] += stats.get("earnedRuns", 0)
        rec["SO"] += stats.get("strikeOuts", 0)
        rec["BB"] += stats.get("baseOnBalls", 0)
        rec["GO"] += stats.get("groundOuts", 0)
        rec["AO"] += stats.get("airOuts", 0)
        if rec["Hand"] is None:
            rec["Hand"] = throws or bats

    for game_pk in game_pks:
        box_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
        resp = _fetch_with_rate_limit(box_url, rate_limit_seconds, f"MLB boxscore {game_pk}")
        try:
            box = resp.json()
        except Exception:
            continue
        teams_data = box.get("teams", {})
        for side in ("home", "away"):
            team_data = teams_data.get(side, {})
            if not team_data:
                continue
            if team_data.get("team", {}).get("id") != team_id:
                continue
            players = team_data.get("players", {}) or {}
            for p in players.values():
                person = p.get("person", {})
                pid = str(person.get("id"))
                name = person.get("fullName") or pid
                pos = (p.get("position") or {}).get("abbreviation") or ""
                bats = (p.get("batSide") or {}).get("code")
                throws = (p.get("pitchHand") or {}).get("code")
                stats = p.get("stats", {}) or {}
                bat_stats = stats.get("batting", {})
                pit_stats = stats.get("pitching", {})
                if bat_stats:
                    _accum_bat(pid, name, pos, bats, throws, bat_stats)
                if pit_stats:
                    _accum_pit(pid, name, pos, throws, bats, pit_stats)

    bat_rows = []
    for rec in bat_totals.values():
        ab = rec["AB"]
        pa = rec["PA"]
        h = rec["H"]
        bb = rec["BB"]
        hbp = rec["HBP"]
        sf = rec["SF"]
        rec["AVG"] = h / ab if ab else None
        denom = ab + bb + hbp + sf
        rec["OBP"] = (h + bb + hbp) / denom if denom else None
        bat_rows.append(rec)

    pit_rows = []
    for rec in pit_totals.values():
        ip = rec["IP"]
        er = rec["ER"]
        so = rec["SO"]
        bb = rec["BB"]
        go = rec["GO"]
        ao = rec["AO"]
        rec["ERA"] = (er * 9) / ip if ip else None
        rec["K/9"] = (so * 9) / ip if ip else None
        rec["BB/9"] = (bb * 9) / ip if ip else None
        rec["GB%"] = None  # Ground-out share is not ground-ball percentage.
        pit_rows.append(rec)

    bat_df = pd.DataFrame(bat_rows)
    pit_df = pd.DataFrame(pit_rows)
    return bat_df, pit_df


def fg_team_id(team_code: str) -> int:
    teams = team_ids()
    matches = teams[teams["teamIDBR"].str.upper() == team_code.upper()]
    if matches.empty:
        raise ValueError(f"No Fangraphs team id found for team code '{team_code}'.")
    # take latest entry
    return int(matches.iloc[-1]["teamIDfg"])


def retro_team_to_br(retro_code: str) -> str:
    teams = team_ids()
    matches = teams[teams["teamIDretro"].str.upper() == retro_code.upper()]
    if matches.empty:
        return retro_code.upper()
    return str(matches.iloc[-1]["teamIDBR"]).upper()


def merge_fp(batting: pd.DataFrame, team: str, season: int, rate_limit_seconds: float = 0.0) -> pd.DataFrame:
    if all(c in batting and batting[c].notna().all() for c in ("FP", "DRS")):
        return batting
    if "IDfg" not in batting.columns or not batting["IDfg"].notna().any():
        return batting
    try:
        _announce_request(f"Fangraphs fielding (FP%) for {team} {season}", rate_limit_seconds)
        fielding = fg_fielding_data(
            start_season=season,
            end_season=season,
            team=fg_team_id(team),
            split_seasons=False,
            qual=0,
        )
        # Weight positions by chances; never choose a player's best percentage.
        aggregates = []
        for fg_id, group in fielding.groupby("IDfg"):
            if {"PO", "A", "E"}.issubset(group.columns):
                totals = group[["PO", "A", "E"]].sum(min_count=len(group))
                fp = career.fielding_percentage(totals)
            else:
                fp = group.iloc[0].get("FP") if len(group) == 1 else None
            aggregates.append({"IDfg": fg_id, "FP": fp,
                               "DRS": group["DRS"].sum(min_count=len(group)) if "DRS" in group else None})
        fp_lookup = pd.DataFrame(aggregates)
        _maybe_sleep(rate_limit_seconds)
        merged = batting.merge(fp_lookup, on="IDfg", how="left", suffixes=("", "_fielding"))
        for column in ("FP", "DRS"):
            if f"{column}_fielding" in merged:
                merged[column] = merged[f"{column}_fielding"].combine_first(merged[column])
                merged = merged.drop(columns=f"{column}_fielding")
        return merged
    except Exception:
        return batting


def _trim_columns(df: pd.DataFrame, required_cols: list[str], defaults: Optional[dict[str, object]] = None) -> pd.DataFrame:
    defaults = defaults or {}
    # If Fangraphs returns unnamed index columns, drop them.
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
    for col in required_cols:
        if col not in df.columns:
            df[col] = defaults.get(col)
    return df[required_cols]


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Saved {path} ({len(df)} rows)")


def _mlb_regular_stats(team: str, season: int, rate_limit_seconds: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch team season totals, including unqualified hitters and relievers.

    MLB groundOuts/airOuts is not FanGraphs GB%, so leave GB% unavailable.
    Keep MLB identities separate from the FanGraphs ID namespace.
    """
    team_id = _mlb_team_id(team)
    params = {
        "stats": "season", "group": "hitting,pitching,fielding",
        "season": season, "teamId": team_id, "sportIds": 1,
        "gameType": "R", "playerPool": "ALL", "limit": 1000,
        "hydrate": "person",
    }
    response = _fetch_with_rate_limit(
        "https://statsapi.mlb.com/api/v1/stats?" + urlencode(params),
        rate_limit_seconds, f"MLB regular-season stats for {team} {season}",
    )
    response.raise_for_status()
    groups = {}
    for group in response.json().get("stats", []):
        splits = group.get("splits", [])
        if group.get("totalSplits", len(splits)) > len(splits):
            raise ValueError(f"Incomplete MLB season stats for {team} {season}")
        groups[group.get("group", {}).get("displayName")] = [
            split for split in splits
            if split.get("team", {}).get("id") == team_id
            and str(split.get("season")) == str(season)
        ]

    fielding = {}
    for split in groups.get("fielding", []):
        pid = split["player"]["id"]
        stat = split["stat"]
        fielding.setdefault(pid, []).append(stat)

    def number(value):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def identity(split, pitching=False):
        person, stat = split["player"], split["stat"]
        bats = (person.get("batSide") or {}).get("code")
        throws = (person.get("pitchHand") or {}).get("code")
        return {
            "IDfg": None, "IDmlb": person["id"], "Name": person["fullName"], "Age": stat.get("age"),
            "G": stat.get("gamesPlayed"),
            "Pos": "P" if pitching else (split.get("position") or person.get("primaryPosition") or {}).get("abbreviation", ""),
            "Hand": throws if pitching else bats, "LR": bats, "Throws": throws,
        }

    bat_rows = []
    for split in groups.get("hitting", []):
        stat = split["stat"]
        row = identity(split)
        for column, key in {
            "AB": "atBats", "PA": "plateAppearances", "H": "hits",
            "2B": "doubles", "3B": "triples", "HR": "homeRuns", "R": "runs",
            "RBI": "rbi", "BB": "baseOnBalls", "SO": "strikeOuts",
            "SB": "stolenBases", "CS": "caughtStealing",
            "HBP": "hitByPitch", "SF": "sacFlies",
        }.items():
            row[column] = stat.get(key)
        hit_parts = [number(row[k]) for k in ("H", "2B", "3B", "HR")]
        row["1B"] = hit_parts[0] - sum(hit_parts[1:]) if all(v is not None for v in hit_parts) else None
        for column in ("AVG", "OBP", "SLG", "OPS"):
            row[column] = number(stat.get(column.lower()))
        totals = {column: career._sum(fielding.get(split["player"]["id"], []), key)
                  for column, key in {"PO": "putOuts", "A": "assists", "E": "errors"}.items()}
        row["FP"] = career.fielding_percentage(totals)
        bat_rows.append(row)

    pit_rows = []
    for split in groups.get("pitching", []):
        stat = split["stat"]
        row = identity(split, pitching=True)
        row.update({
            "GS": stat.get("gamesStarted"), "CG": stat.get("completeGames"),
            # Convert baseball's thirds-of-an-inning notation before CSV serialization.
            "IP": career.innings(stat.get("inningsPitched")),
            "ERA": number(stat.get("era")),
            "K/9": number(stat.get("strikeoutsPer9Inn")),
            "BB/9": number(stat.get("walksPer9Inn")), "GB%": None,
        })
        pit_rows.append(row)
    if not bat_rows or not pit_rows:
        raise ValueError(f"MLB returned no batting or pitching stats for {team} {season}")
    return pd.DataFrame(bat_rows), pd.DataFrame(pit_rows)


def _enrich_regular_hands(batting: pd.DataFrame, pitching: pd.DataFrame, season: int) -> None:
    """Fill missing hands without overriding source data or looking up complete rows."""
    missing = []
    for frame, is_pitcher in ((batting, False), (pitching, True)):
        # Empty CSV columns are inferred as numeric by pandas.
        for column in ("Hand", "Throws", "LR"):
            frame[column] = frame.get(column, pd.Series(index=frame.index, dtype=object)).astype(object)
        for index, row in frame.iterrows():
            bats = None
            if not is_pitcher:
                bats = normalize_hand(row.get("Hand")) or normalize_hand(row.get("LR"))
            throws = normalize_hand(row.get("Throws"))
            if is_pitcher:
                throws = throws or normalize_hand(row.get("Hand"))
            if (not is_pitcher and not bats) or not throws:
                missing.append((frame, index, row, is_pitcher, bats, throws))
            else:
                frame.at[index, "Hand"] = throws if is_pitcher else bats
                frame.at[index, "Throws"] = throws
                if not is_pitcher:
                    frame.at[index, "LR"] = bats
    if not missing:
        return
    ids = []
    for _, _, row, _, _, _ in missing:
        if pd.notna(row.get("IDfg")):
            ids.append(int(row["IDfg"]))
    fg_lookup = hands_from_fg_ids(list(set(ids)), season=season) if ids else {}
    name_lookup = hands_from_names([row.get("Name") for _, _, row, _, _, _ in missing], season=season)
    for frame, index, row, is_pitcher, bats, throws in missing:
        fg_id = int(row["IDfg"]) if pd.notna(row.get("IDfg")) else None
        resolved_bats, resolved_throws = resolve_hands(row.get("Name"), fg_id, fg_lookup, name_lookup, season=season)
        bats, throws = bats or resolved_bats, throws or resolved_throws
        frame.at[index, "Hand"] = throws if is_pitcher else bats
        frame.at[index, "Throws"] = throws
        if not is_pitcher:
            frame.at[index, "LR"] = bats


def fetch_regular(team: str, season: int, rate_limit_seconds: float = 0.0, refresh: bool = False) -> None:
    bat_path, pit_path = stat_paths(team, season, postseason=False)
    batting = pitching = None
    cached_stats = False
    if not refresh and bat_path.exists() and pit_path.exists():
        try:
            batting = pd.read_csv(bat_path)
            pitching = pd.read_csv(pit_path)
            if (
                batting.empty or pitching.empty
                or not {"Name", "G", "AVG", "OBP", "HR", "2B", "SB"}.issubset(batting.columns)
                or not {"Name", "ERA", "IP"}.issubset(pitching.columns)
                or "StatsVersion" not in batting or "StatsVersion" not in pitching
                or not batting["StatsVersion"].eq(rules.RULES_VERSION).all()
                or not pitching["StatsVersion"].eq(rules.RULES_VERSION).all()
                or not cache_policy.is_fresh(season, cache_policy.frame_snapshot(batting, "StatsFetchedAt"))
                or not cache_policy.is_fresh(season, cache_policy.frame_snapshot(pitching, "StatsFetchedAt"))
            ):
                raise ValueError("Incomplete cached regular-season statistics")
            print(f"[deadball] Using cached regular-season stats from {bat_path} and {pit_path}")
            cached_stats = True
        except Exception:
            batting = pitching = None

    if batting is None or pitching is None:
        team_id = fg_team_id(team)
        try:
            _announce_request(f"Fangraphs batting for {team} {season}", rate_limit_seconds)
            batting = fg_batting_data(
                start_season=season, end_season=season, team=team_id,
                split_seasons=False, qual=0,
            )
            _maybe_sleep(rate_limit_seconds)
            _announce_request(f"Fangraphs pitching for {team} {season}", rate_limit_seconds)
            pitching = fg_pitching_data(
                start_season=season, end_season=season, team=team_id,
                split_seasons=False, qual=0,
            )
            _maybe_sleep(rate_limit_seconds)
            if batting.empty or pitching.empty:
                raise ValueError("Empty FanGraphs season statistics")
            # FanGraphs IP is baseball notation; MLB's adapter already converts it.
            pitching["IP"] = pitching["IP"].map(career.innings)
        except (requests.RequestException, ValueError) as exc:
            print(f"[deadball] FanGraphs unavailable ({exc}); using MLB season stats")
            try:
                batting, pitching = _mlb_regular_stats(team, season, rate_limit_seconds)
            except (requests.RequestException, ValueError, KeyError, TypeError) as mlb_exc:
                raise RuntimeError(
                    f"Unable to fetch regular-season stats for {team} {season}: "
                    f"FanGraphs failed ({exc}); MLB fallback failed ({mlb_exc})"
                ) from mlb_exc

    _enrich_regular_hands(batting, pitching, season)
    if not cached_stats:
        batting = merge_fp(batting, team, season, rate_limit_seconds=rate_limit_seconds)
        # Timestamp actual acquisition, never a cache hit or a formatting rewrite.
        batting["StatsFetchedAt"] = pitching["StatsFetchedAt"] = cache_policy.now_timestamp()
    for frame, columns in ((batting, ("K%",)), (pitching, ("GB%",))):
        for column in columns:
            if column in frame:
                frame[column] = frame[column].map(normalize_percentage)
    batting["StatsVersion"] = pitching["StatsVersion"] = rules.RULES_VERSION
    batting["Pos"] = batting.get("Pos", "")
    batting["LR"] = batting.get("LR", batting.get("Hand"))
    batting = _trim_columns(batting, REQUIRED_BAT_COLS, defaults={"Pos": "", "Hand": None, "LR": None, "Throws": None})
    pitching["Pos"] = pitching.get("Pos", pd.Series("P", index=pitching.index)).fillna("P")
    pitching = _trim_columns(
        pitching,
        REQUIRED_PIT_COLS,
        defaults={"Pos": "P", "Hand": None, "Throws": None, "GS": None, "CG": 0},
    )
    save_csv(batting, bat_path)
    save_csv(pitching, pit_path)


def normalize_percentage(value):
    """Accept fractions or explicit percentage values at the ingestion boundary."""
    explicit = isinstance(value, str) and "%" in value
    parsed = rules.number(value.replace("%", "") if explicit else value)
    if parsed is None:
        return None
    return parsed / 100 if explicit or parsed > 1 else parsed


def _histories(batting, pitching, season, *, allow_network, refresh, rate_limit_seconds):
    """Resolve stable IDs once, then share each player's history across both roles."""
    frames = (batting, pitching)
    for frame in frames:
        if "IDmlb" not in frame:
            frame["IDmlb"] = None
    missing_fg = {int(r["IDfg"]) for frame in frames for _, r in frame.iterrows()
                  if rules.number(r.get("IDmlb")) is None and rules.number(r.get("IDfg")) is not None}
    id_path = CACHE_ROOT / "career" / "player_ids.json"
    try:
        ids = {int(k): int(v) for k, v in json.loads(id_path.read_text()).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        ids = {}
    missing_fg -= ids.keys()
    if missing_fg and allow_network:
        try:
            lookup = playerid_reverse_lookup(sorted(missing_fg), key_type="fangraphs")
            ids.update({int(r["key_fangraphs"]): int(r["key_mlbam"]) for _, r in lookup.iterrows()
                        if rules.number(r.get("key_mlbam")) is not None and r["key_mlbam"] > 0})
            id_path.parent.mkdir(parents=True, exist_ok=True)
            id_path.write_text(json.dumps(ids))
        except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
            pass
    for frame in frames:
        if "IDfg" in frame:
            frame["IDmlb"] = frame["IDmlb"].combine_first(frame["IDfg"].map(ids))
    player_ids = []
    for frame in frames:
        for pid in frame["IDmlb"].dropna().unique():
            if rules.number(pid) is None or int(pid) in player_ids:
                continue
            player_ids.append(int(pid))
    return career.load_histories(
        player_ids, season, CACHE_ROOT / "career", allow_network=allow_network, refresh=refresh,
        fetch=lambda url: _fetch_with_rate_limit(url, rate_limit_seconds, "MLB career statistics"),
    )


def _extract_table_html(soup: BeautifulSoup, keywords: list[str]) -> str:
    ids = set()
    for t in soup.find_all("table"):
        if t.get("id"):
            ids.add(t.get("id"))
    for c in soup.find_all(string=lambda text: isinstance(text, Comment)):
        for m in re.finditer(r'id="([^"]+)"', c):
            ids.add(m.group(1))
    target_id = None
    for table_id in ids:
        lid = table_id.lower()
        if all(k in lid for k in keywords):
            target_id = table_id
            break
    if target_id is None:
        raise ValueError(f"Could not find table id matching {keywords}")
    # grab from soup or comment
    table = soup.find("table", {"id": target_id})
    if table:
        return str(table)
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if target_id in comment:
            m = re.search(rf"<table[^>]+id=\"{re.escape(target_id)}\"[^>]*>.*?</table>", comment, re.S)
            if m:
                return m.group(0)
    raise ValueError(f"Could not extract table '{target_id}'")


def fetch_postseason(team: str, season: int, rate_limit_seconds: float = 0.0, refresh: bool = False) -> None:
    bat_path, pit_path = stat_paths(team, season, postseason=True)
    batting = pitching = None
    cached_stats = False
    if not refresh and bat_path.exists() and pit_path.exists():
        try:
            batting = pd.read_csv(bat_path)
            pitching = pd.read_csv(pit_path)
            if (
                batting.empty or pitching.empty
                or not set(REQUIRED_POST_BAT_COLS).issubset(batting.columns)
                or not set(REQUIRED_POST_PIT_COLS).issubset(pitching.columns)
                or not batting["StatsVersion"].eq(rules.RULES_VERSION).all()
                or not pitching["StatsVersion"].eq(rules.RULES_VERSION).all()
                or not cache_policy.is_fresh(season, cache_policy.frame_snapshot(batting, "StatsFetchedAt"))
                or not cache_policy.is_fresh(season, cache_policy.frame_snapshot(pitching, "StatsFetchedAt"))
            ):
                raise ValueError("Incomplete cached postseason statistics")
            print(f"[deadball] Using cached postseason stats from {bat_path} and {pit_path}")
            cached_stats = True
        except Exception:
            batting = pitching = None

    if batting is None or pitching is None:
        try:
            batting, pitching = _mlb_postseason_stats(team, season, rate_limit_seconds=rate_limit_seconds)
        except Exception as exc:
            print(f"[deadball] MLB postseason fetch failed for {team} {season}: {exc}; skipping postseason outputs.")
            return

    if batting is None or batting.empty:
        print(f"[deadball] No postseason batting rows returned for {team} {season}; skipping postseason outputs.")
        return
    if pitching is None or pitching.empty:
        print(f"[deadball] No postseason pitching rows returned for {team} {season}; skipping postseason outputs.")
        return

    name_col_bat = "Name" if "Name" in batting.columns else batting.columns[0]
    name_col_pit = "Name" if "Name" in pitching.columns else pitching.columns[0]

    # Backfill BA from AVG if MLB payload lacked BA (common for constructed boxscores).
    if ("BA" not in batting.columns) or batting["BA"].isna().all():
        if "AVG" in batting.columns:
            batting["BA"] = batting["AVG"]
        else:
            batting["BA"] = None

    # Enrich with handedness via name lookup if missing.
    hand_lookup = hands_from_names(
        list(batting.get(name_col_bat, pd.Series(dtype=str)).astype(str))
        + list(pitching.get(name_col_pit, pd.Series(dtype=str)).astype(str)),
        season=season,
    )
    batting["Hand"] = batting[name_col_bat].map(lambda n: resolve_hands(n, None, {}, hand_lookup, season=season)[0])
    batting["LR"] = batting["Hand"]
    batting["Throws"] = batting[name_col_bat].map(lambda n: resolve_hands(n, None, {}, hand_lookup, season=season)[1])
    pitching["Hand"] = pitching[name_col_pit].map(lambda n: resolve_hands(n, None, {}, hand_lookup, season=season)[1])
    pitching["Throws"] = pitching["Hand"]

    if not cached_stats:
        fetched_at = cache_policy.now_timestamp()
        batting["StatsFetchedAt"] = pitching["StatsFetchedAt"] = fetched_at
    batting["StatsVersion"] = pitching["StatsVersion"] = rules.RULES_VERSION
    batting["Pos"] = batting.get("Pos", "")
    batting = _trim_columns(
        batting,
        REQUIRED_POST_BAT_COLS,
        defaults={"Pos": "", "Hand": None, "LR": None, "Throws": None},
    )
    pitching = _trim_columns(
        pitching,
        REQUIRED_POST_PIT_COLS,
        defaults={"Hand": None, "Throws": None, "K/9": None, "BB/9": None, "GS": None, "GB%": None},
    )
    if batting.empty or pitching.empty:
        print(f"[deadball] Postseason data empty after processing for {team} {season}; not writing CSVs.")
        return
    # Standardize to TEAM_YEAR_batting_postseason.csv and TEAM_YEAR_pitching_postseason.csv
    save_csv(batting, bat_path)
    save_csv(pitching, pit_path)



def normalize_player_name(name: str) -> str:
    if name is None:
        return ""
    return re.sub(r"[^A-Za-z0-9 ]+", "", str(name)).strip().lower()


def normalize_hand(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    upper = str(raw).strip().upper()
    if upper.startswith("L"):
        return "L"
    if upper.startswith("R"):
        return "R"
    if upper.startswith(("B", "S", "SWITCH")):
        return "S"
    return None


_load_hand_cache()


def hands_from_fg_ids(fg_ids: list[int], season: Optional[int] = None) -> dict[int, tuple[Optional[str], Optional[str]]]:
    """
    Resolve Fangraphs IDs to (bats, throws) using the chadwick register.
    """
    _load_hand_cache()
    if season:
        _load_retro_rosters(season)
    resolved: dict[int, tuple[Optional[str], Optional[str]]] = {}
    ids_needed = [i for i in fg_ids if i not in HAND_CACHE_FG]
    changed = False
    if ids_needed:
        try:
            lookup = playerid_reverse_lookup(ids_needed, key_type="fangraphs")
        except Exception:
            lookup = pd.DataFrame()
        for _, row in lookup.iterrows():
            try:
                fg_id = int(row.get("key_fangraphs"))
            except Exception:
                continue
            bats = normalize_hand(row.get("bats"))
            throws = normalize_hand(row.get("throws"))
            retro_id = str(row.get("key_retro") or "").strip()
            if (not bats or not throws) and retro_id and retro_id in RETRO_CACHE_BY_ID:
                rb, rt = RETRO_CACHE_BY_ID[retro_id]
                bats = bats or rb
                throws = throws or rt
            HAND_CACHE_FG[fg_id] = (bats, throws)
            changed = True
    for fg_id in fg_ids:
        resolved[fg_id] = HAND_CACHE_FG.get(fg_id, (None, None))
    if changed:
        _save_hand_cache()
    return resolved


def split_name(full_name: str) -> tuple[str | None, str | None]:
    if not full_name:
        return None, None
    nfkd = unicodedata.normalize("NFKD", str(full_name))
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    cleaned = ascii_only.replace("\xa0", " ").replace(".", " ").replace(",", " ").strip()
    parts = cleaned.split()
    if not parts:
        return None, None
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else None
    return first, last


def hands_from_names(names: list[str], season: Optional[int] = None) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """
    Resolve names to (bats, throws) via chadwick register.
    """
    _load_hand_cache()
    if season:
        _load_retro_rosters(season)
    resolved: dict[str, tuple[Optional[str], Optional[str]]] = {}
    changed = False
    for name in names:
        key = normalize_player_name(name)
        if not key:
            continue
        # Allow re-lookup when season-specific data may fill a previously missing entry.
        cached = HAND_CACHE_NAME.get(key)
        if cached and all(cached):
            continue
        first, last = split_name(name)
        if not first or not last:
            continue
        try:
            lookup = playerid_lookup(last, first)
            # If empty, try swapping or using just last name as a fallback.
            if lookup.empty:
                lookup = playerid_lookup(last.split()[-1], " ".join([first] + last.split()[:-1]) or None)
            if lookup.empty:
                lookup = playerid_lookup(last)
        except Exception:
            continue
        if lookup.empty:
            continue
        row = lookup.iloc[0]
        bats = normalize_hand(row.get("bats"))
        throws = normalize_hand(row.get("throws"))
        if (not bats or not throws) and season:
            retro_key = normalize_player_name(f"{first} {last}")
            if retro_key in RETRO_CACHE_BY_NAME:
                rb, rt = RETRO_CACHE_BY_NAME[retro_key]
                bats = bats or rb
                throws = throws or rt
        HAND_CACHE_NAME[key] = (bats, throws)
        changed = True
    for name in names:
        key = normalize_player_name(name)
        resolved[key] = HAND_CACHE_NAME.get(key, (None, None))
    if changed:
        _save_hand_cache()
    return resolved


def resolve_hands(
    name: str,
    fg_id: Optional[int],
    fg_lookup: dict[int, tuple[Optional[str], Optional[str]]],
    name_lookup: dict[str, tuple[Optional[str], Optional[str]]],
    season: Optional[int] = None,
) -> tuple[Optional[str], Optional[str]]:
    _load_hand_cache()
    if season:
        _load_retro_rosters(season)
    norm = normalize_player_name(name)
    if norm in RESOLVED_HANDS:
        return RESOLVED_HANDS[norm]

    bats = throws = None
    if fg_id is not None:
        bats, throws = fg_lookup.get(fg_id, (None, None))
    if not bats or not throws:
        b2, t2 = name_lookup.get(norm, (None, None))
        bats = bats or b2
        throws = throws or t2
    if (not bats or not throws) and season and norm in RETRO_CACHE_BY_NAME:
        rb, rt = RETRO_CACHE_BY_NAME[norm]
        bats = bats or rb
        throws = throws or rt

    if (not bats or not throws) and norm:
        first, last = split_name(name)
        if last:
            try:
                lookup = playerid_lookup(last, first)
                if lookup.empty:
                    lookup = playerid_lookup(last.split()[-1], " ".join([first] + last.split()[:-1]) or None)
                if lookup.empty:
                    lookup = playerid_lookup(last)
            except Exception:
                lookup = pd.DataFrame()
            for _, row in lookup.iterrows():
                candidate_b = normalize_hand(row.get("bats"))
                candidate_t = normalize_hand(row.get("throws"))
                if candidate_b or candidate_t:
                    bats = bats or candidate_b
                    throws = throws or candidate_t
                    if bats and throws:
                        break
    if norm and (bats or throws):
        if HAND_CACHE_NAME.get(norm) != (bats, throws):
            HAND_CACHE_NAME[norm] = (bats, throws)
            _save_hand_cache()
    RESOLVED_HANDS[norm] = (bats, throws)
    return RESOLVED_HANDS[norm]


def parse_positions(raw: Optional[str], default: str = "") -> tuple[str, str]:
    if not raw or pd.isna(raw):
        return default, default
    pos_map = {
        "1": "P",
        "P": "P",
        "2": "C",
        "C": "C",
        "3": "1B",
        "1B": "1B",
        "4": "2B",
        "2B": "2B",
        "5": "3B",
        "3B": "3B",
        "6": "SS",
        "SS": "SS",
        "7": "LF",
        "LF": "LF",
        "8": "CF",
        "CF": "CF",
        "9": "RF",
        "RF": "RF",
        "DH": "DH",
        "PH": "PH",
        "PR": "PR",
    }
    tokens = re.split(r"[-/]", str(raw).upper())
    seen: list[str] = []
    for tok in tokens:
        tok = tok.strip("*# ")
        if tok in pos_map and pos_map[tok] not in seen:
            seen.append(pos_map[tok])
    if not seen:
        return default, default
    return seen[0], ",".join(seen)



def ip_to_float(ip_val) -> float:
    if pd.isna(ip_val):
        return 0.0
    if isinstance(ip_val, (int, float)):
        return float(ip_val)
    s = str(ip_val)
    if "." in s:
        whole, frac = s.split(".", 1)
        try:
            return float(int(whole) + int(frac) / 3)
        except ValueError:
            return float(ip_val)
    try:
        return float(s)
    except ValueError:
        return 0.0




def build_deadball_regular(team: str, season: int, *, trait_mode="standard", allow_network=False,
                           refresh=False, rate_limit_seconds=0.0) -> None:
    rules.validate_mode(trait_mode)
    bat = pd.read_csv(STAT_DIR / f"{team.lower()}_{season}_batting.csv")
    pit = pd.read_csv(STAT_DIR / f"{team.lower()}_{season}_pitching.csv")
    obsolete = any("StatsVersion" not in frame or not frame["StatsVersion"].eq(rules.RULES_VERSION).all()
                   for frame in (bat, pit))
    expired = any(not cache_policy.is_fresh(season, cache_policy.frame_snapshot(frame, "StatsFetchedAt"))
                  for frame in (bat, pit))
    if obsolete or (expired and allow_network):
        if not allow_network:
            raise ValueError("Raw season caches use an obsolete schema/innings format; refresh online before an offline build")
        fetch_regular(team, season, rate_limit_seconds=rate_limit_seconds, refresh=True)
        bat, pit = (pd.read_csv(p) for p in stat_paths(team, season))
    if allow_network:
        _enrich_regular_hands(bat, pit, season)
    histories = _histories(bat, pit, season, allow_network=allow_network, refresh=refresh,
                           rate_limit_seconds=rate_limit_seconds)
    timestamps = [cache_policy.frame_snapshot(frame, "StatsFetchedAt") for frame in (bat, pit)]
    timestamps.extend(career.snapshot_at(pid, season, CACHE_ROOT / "career")
                      for pid, history in histories.items() if history)
    snapshot = cache_policy.oldest(timestamps)
    bat_rows = []
    for _, row in bat.iterrows():
        pid = rules.number(row.get("IDmlb"))
        history = histories.get(int(pid), {}) if pid is not None else {}
        # Rate the whole player's season, not just a traded player's team stint.
        annual = history.get("season_hitter", {})
        row = row.copy()
        traded = annual.get("Stints", 1) > 1
        if traded:
            for key, value in annual.items():
                row[key] = value  # Missing all-team values cannot inherit team-only ones.
            row["BsR"] = row["DRS"] = row["FP"] = None  # Team-only metrics are not comparable.
        # For non-traded players retain one raw-season snapshot across all metrics.
        if history.get("season_fielding") and (traded or rules.number(row.get("FP")) is None):
            row["FP"] = history["season_fielding"].get("FP")
        if history.get("season_fielding"):
            if not row.get("Pos") or pd.isna(row.get("Pos")):
                row["Pos"] = history["season_fielding"].get("Pos", "")
        primary_pos, all_pos = parse_positions(row.get("Pos"), default=row.get("Pos", ""))
        bats_hand = normalize_hand(row.get("Hand"))
        throws_hand = normalize_hand(row.get("Throws"))
        out_row = {
            "Type": "Hitter",
            "Name": row.get("Name"),
            "IDmlb": row.get("IDmlb"),
            "Pos": primary_pos,
            "Positions": all_pos,
            "Age": row.get("Age"),
            "Hand": bats_hand,
            "LR": bats_hand,
            "Throws": throws_hand,
            "BT": fmt_two_digit(row.get("AVG")),
            "OBT": fmt_two_digit(row.get("OBP")),
            "AVG": row.get("AVG"),
            "OBP": row.get("OBP"),
            "HR": row.get("HR"),
            "2B": row.get("2B"),
            "SB": row.get("SB"),
            "G": row.get("G"),
            "Pos": primary_pos,
        }
        out_row.update(rules.evaluate_hitter({**row.to_dict(), **out_row}, trait_mode, history.get("hitter")))
        bat_rows.append(out_row)

    pit_rows = []
    for _, row in pit.iterrows():
        pid = rules.number(row.get("IDmlb"))
        history = histories.get(int(pid), {}) if pid is not None else {}
        row = row.copy()
        annual = history.get("season_pitcher", {})
        if annual.get("Stints", 1) > 1:
            for key, value in annual.items():
                row[key] = value
            row["GB%"] = None
        row["GB%"] = normalize_percentage(row.get("GB%"))
        throws_hand = normalize_hand(row.get("Throws")) or normalize_hand(row.get("Hand"))
        out_row = {
            "Type": "Pitcher",
            "Name": row.get("Name"),
            "IDmlb": row.get("IDmlb"),
            "Pos": row.get("Pos", "P") or "P",
            "Positions": row.get("Positions", row.get("Pos", "P")),
            "Age": row.get("Age"),
            "Hand": throws_hand,
            "Throws": throws_hand,
            "PD": row.get("PD", pitcher_die(row.get("ERA"))),
            "ERA": row.get("ERA"),
            "IP": row.get("IP"),
            "K/9": row.get("K/9"),
            "BB/9": row.get("BB/9"),
            "GB%": row.get("GB%"),
            "GS": row.get("GS"),
            "G": row.get("G"),
        }
        out_row.update(rules.evaluate_pitcher({**row.to_dict(), **out_row}, trait_mode, history.get("pitcher")))
        pit_rows.append(out_row)

    df = pd.DataFrame(bat_rows + pit_rows)
    df["SnapshotAt"] = snapshot
    df["CacheStale"] = not cache_policy.is_fresh(season, snapshot)
    # Standardize file name as TEAM_YEAR_deadball.csv
    season_path = DEADBALL_DIR / f"{team.lower()}_{season}_deadball.csv"
    save_csv(df, season_path)


def build_deadball_postseason(team: str, season: int, *, trait_mode="standard", allow_network=False,
                              refresh=False, rate_limit_seconds=0.0) -> None:
    """Postseason participants retain regular-season/career ratings."""
    rules.validate_mode(trait_mode)
    reg_path = deadball_paths(team, season)[0]
    build_deadball_regular(team, season, trait_mode=trait_mode, allow_network=allow_network,
                           refresh=refresh, rate_limit_seconds=rate_limit_seconds)
    regular = pd.read_csv(reg_path)
    batting, pitching = (pd.read_csv(p) for p in stat_paths(team, season, postseason=True))
    postseason_expired = any(
        not cache_policy.is_fresh(season, cache_policy.frame_snapshot(frame, "StatsFetchedAt"))
        for frame in (batting, pitching)
    )
    if postseason_expired and allow_network:
        fetch_postseason(team, season, rate_limit_seconds=rate_limit_seconds, refresh=True)
        batting, pitching = (pd.read_csv(p) for p in stat_paths(team, season, postseason=True))
    histories = _histories(batting, pitching, season, allow_network=allow_network,
                           refresh=refresh, rate_limit_seconds=rate_limit_seconds)
    timestamps = [cache_policy.frame_snapshot(regular), *(
        cache_policy.frame_snapshot(frame, "StatsFetchedAt") for frame in (batting, pitching)
    )]
    timestamps.extend(career.snapshot_at(pid, season, CACHE_ROOT / "career")
                      for pid, history in histories.items() if history)
    snapshot = cache_policy.oldest(timestamps)
    result = []
    for frame, kind in ((batting, "Hitter"), (pitching, "Pitcher")):
        for _, raw in frame.iterrows():
            name = raw.get("Player", raw.get("Name"))
            pid = rules.number(raw.get("IDmlb"))
            candidates = regular[regular["Type"] == kind]
            matches = candidates[candidates["IDmlb"] == pid] if pid is not None else candidates[candidates["Name"] == name]
            if not matches.empty:
                out = matches.iloc[0].to_dict()
            else:
                history = histories.get(int(pid), {}) if pid is not None else {}
                # No postseason stat line is used to infer season ability.
                sample = {"Name": name, "IDmlb": pid, "Pos": raw.get("Pos"),
                          **history.get("season_hitter" if kind == "Hitter" else "season_pitcher", {})}
                if kind == "Hitter" and history.get("season_fielding"):
                    sample["FP"] = history["season_fielding"].get("FP")
                evaluate = rules.evaluate_hitter if kind == "Hitter" else rules.evaluate_pitcher
                out = {**sample, "Type": kind, "Hand": raw.get("Hand"), "Throws": raw.get("Throws"),
                       **evaluate(sample, trait_mode, history.get("hitter" if kind == "Hitter" else "pitcher"))}
            out["RatingBasis"] = "regular-season/career"
            result.append(out)
    result_frame = pd.DataFrame(result)
    result_frame["SnapshotAt"] = snapshot
    result_frame["CacheStale"] = not cache_policy.is_fresh(season, snapshot)
    save_csv(result_frame, deadball_paths(team, season, postseason=True)[0])


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--team", required=True, help="Team abbreviation, e.g., LAD")
    parser.add_argument("--season", type=int, required=True, help="Season year, e.g., 2025")
    parser.add_argument("--trait-mode", choices=rules.TRAIT_MODES, default="standard")
    parser.add_argument(
        "--rate-limit-seconds",
        type=float,
        default=0.0,
        help="Sleep this many seconds before each network request (gentle rate limit).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-fetching stats even if cached CSVs already exist.",
    )
    parser.add_argument(
        "--skip-postseason",
        action="store_true",
        help="Skip postseason pulls/builds (regular season only).",
    )


def main_from_parsed(args: argparse.Namespace) -> None:
    team = args.team.upper()
    fetch_regular(team, args.season, rate_limit_seconds=args.rate_limit_seconds, refresh=args.refresh)
    build_deadball_regular(team, args.season, trait_mode=args.trait_mode, allow_network=True,
                           refresh=args.refresh, rate_limit_seconds=args.rate_limit_seconds)
    if not args.skip_postseason:
        try:
            fetch_postseason(team, args.season, rate_limit_seconds=args.rate_limit_seconds, refresh=args.refresh)
            post_bat, post_pit = stat_paths(team, args.season, postseason=True)
            if post_bat.exists() and post_pit.exists():
                build_deadball_postseason(team, args.season, trait_mode=args.trait_mode, allow_network=True,
                                          refresh=args.refresh, rate_limit_seconds=args.rate_limit_seconds)
            else:
                print(f"[deadball] No postseason stat files for {team} {args.season}; skipping postseason build.")
        except RuntimeError as exc:
            print(f"[deadball] Skipping postseason build: {exc}")


def main(args: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build team season and postseason batting/pitching CSVs")
    configure_parser(parser)
    opts = parser.parse_args(args)
    main_from_parsed(opts)


if __name__ == "__main__":
    main()
from deadball_generator.paths import RETRO_POST_DIR
