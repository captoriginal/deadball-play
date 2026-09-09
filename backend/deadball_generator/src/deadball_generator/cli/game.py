"""
Generate deadball-style stats for a single game by date and team (team must be the HOME team for schedule lookup).

Given a date (YYYY-MM-DD) and a team abbreviation (e.g., LAD), the script:
1. Uses the MLB Stats API to find the game and pull the boxscore JSON.
2. Builds a Deadball CSV for the complete boxscore roster, keeping batting order.
3. Generates a filled scorecard HTML alongside the CSV (unless --skip-scorecard).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import unicodedata
import warnings
from pathlib import Path
import time
from typing import List, Optional, Sequence, Tuple

import pandas as pd
import requests
from deadball_generator import career, paths, rules, cache_policy
from deadball_generator.rules import batter_traits, pitcher_traits, pitcher_die, target as fmt_two_digit
from deadball_generator.scorecards import fill as fill_scorecard
from deadball_generator.stats_fetchers import team_stats

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; game-deadball/1.0)"}
ROOT = paths.PROJECT_ROOT
GAMES_DIR = paths.DEADBALL_GAMES_DIR
SEASON_DIR = paths.DEADBALL_SEASON_DIR
LEGACY_DEADBALL_DIR = ROOT / "deadball"
CACHE_ROOT = ROOT / ".cache"
CACHE_HTML_DIR = CACHE_ROOT / "boxscores"


def _maybe_sleep(rate_limit_seconds: float) -> None:
    if rate_limit_seconds > 0:
        time.sleep(rate_limit_seconds)


def _cache_path_for_url(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return CACHE_HTML_DIR / f"{digest}.html"


def _fetch_with_rate_limit(
    url: str,
    rate_limit_seconds: float,
    label: str,
    refresh_cache: bool = False,
    allow_network: bool = True,
) -> requests.Response:
    cache_path = _cache_path_for_url(url)
    if cache_path.exists() and not refresh_cache:
        print(f"[deadball] Using cached {label}: {url}")
        resp = requests.Response()
        resp.status_code = 200
        resp.url = url
        resp._content = cache_path.read_bytes()
        resp.encoding = "utf-8"
        return resp

    if not allow_network:
        raise RuntimeError(f"Network fetch disabled for {label} and no cached response found: {url}")

    print(f"[deadball] Requesting {label}: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.ok:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)
    if rate_limit_seconds > 0:
        print(f"[deadball] Waiting {rate_limit_seconds:.1f}s before next request")
        time.sleep(rate_limit_seconds)
    return resp


def mlb_game_type(
    date: str,
    team_code: str,
    rate_limit_seconds: float = 0.0,
    allow_network: bool = True,
) -> tuple[str | None, str | None]:
    """
    Return (game_type, description) for a given date/team using MLB Stats API schedule.
    game_type values: R (regular), F/D/L/W/C (postseason rounds), S (spring), E (exhibition).
    """
    team_id = mlb_team_id(team_code)
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}&date={date}"
    resp = _fetch_with_rate_limit(
        url,
        rate_limit_seconds,
        "MLB schedule",
        refresh_cache=False,
        allow_network=allow_network,
    )
    try:
        data = resp.json()
    except Exception:
        return None, None
    dates = data.get("dates") or []
    if not dates:
        return None, None
    games = dates[0].get("games") or []
    if not games:
        return None, None
    game = games[0]
    gtype = game.get("gameType")
    desc = game.get("description") or game.get("seriesDescription")
    return gtype, desc


def find_mlb_game(
    date: str,
    team_code: str,
    rate_limit_seconds: float = 0.0,
    allow_network: bool = True,
) -> tuple[int, str, str]:
    team_id = MLB_TEAM_IDS.get(team_code.upper())
    if team_id is None:
        raise ValueError(f"No MLB team id for {team_code}")
    sched_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}&date={date}"
    resp = _fetch_with_rate_limit(
        sched_url,
        rate_limit_seconds,
        "MLB schedule",
        allow_network=allow_network,
    )
    resp.raise_for_status()
    data = resp.json()
    dates = data.get("dates") or []
    if not dates:
        raise ValueError(f"No games scheduled for {team_code} on {date}")
    games = dates[0].get("games") or []
    if not games:
        raise ValueError(f"No games scheduled for {team_code} on {date}")
    game = games[0]
    game_pk = game.get("gamePk")
    home_abbr = game.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation")
    away_abbr = game.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation")
    return int(game_pk), home_abbr or "", away_abbr or ""


def fetch_mlb_boxscore(game_pk: int, rate_limit_seconds: float = 0.0, allow_network: bool = True) -> dict:
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    resp = _fetch_with_rate_limit(
        url,
        rate_limit_seconds,
        f"MLB boxscore {game_pk}",
        allow_network=allow_network,
    )
    resp.raise_for_status()
    return resp.json()
TEAM_NAME_TO_BR = {
    "arizonadiamondbacks": "ARI",
    "atlantabraves": "ATL",
    "baltimoreorioles": "BAL",
    "bostonredsox": "BOS",
    "chicagocubs": "CHC",
    "chicagowhitesox": "CHW",
    "cincinnatireds": "CIN",
    "clevelandguardians": "CLE",
    "clevelandindians": "CLE",
    "coloradorockies": "COL",
    "detroittigers": "DET",
    "houstonastros": "HOU",
    "kansascityroyals": "KCR",
    "losangelesangels": "LAA",
    "losangelesangelsofanaheim": "LAA",
    "losangelesdodgers": "LAD",
    "miamimarlins": "MIA",
    "milwaukeebrewers": "MIL",
    "minnesotatwins": "MIN",
    "newyorkmets": "NYM",
    "newyorkyankees": "NYY",
    "oaklandathletics": "OAK",
    "philadelphiaphillies": "PHI",
    "pittsburghpirates": "PIT",
    "sandiegopadres": "SDP",
    "sanfranciscogiants": "SFG",
    "seattlemariners": "SEA",
    "saintlouiscardinals": "STL",
    "stlouiscardinals": "STL",
    "tampabayrays": "TBR",
    "texasrangers": "TEX",
    "torontobluejays": "TOR",
    "washingtonnationals": "WSN",
    # Baseball-Reference codes sometimes appear in boxscore IDs
    "lan": "LAD",
    "sfn": "SFG",
    "chn": "CHC",
    "chw": "CHW",
    "nya": "NYY",
    "nyn": "NYM",
    "kca": "KCR",
    "cha": "CHW",
    "bos": "BOS",
    "det": "DET",
    "sea": "SEA",
    "col": "COL",
    "ana": "LAA",
    "hou": "HOU",
    "oak": "OAK",
    "phi": "PHI",
    "pit": "PIT",
    "atl": "ATL",
    "sdp": "SDP",
    "bal": "BAL",
    "cin": "CIN",
    "cle": "CLE",
    "mil": "MIL",
    "min": "MIN",
    "mia": "MIA",
    "stl": "STL",
    "ari": "ARI",
    "sff": "SFG",
    "tex": "TEX",
    "tor": "TOR",
    "tba": "TBR",
    "tbd": "TBR",
    "was": "WSN",
    "wsh": "WSN",
    "cws": "CHW",
    "sf": "SFG",
    "sd": "SDP",
    "kc": "KCR",
    "tb": "TBR",
    "az": "ARI",
}

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


def normalize_player_name(name: str) -> str:
    """
    Strip accents/punctuation for lookup so Enrique Hernández matches Enrique Hernandez.
    """
    if name is None:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9 ]+", "", ascii_only).strip().lower()


def normalize_team_key(team: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(team)).lower()


def team_code_from_name(team: str) -> str:
    """
    Translate a boxscore team name/id into a Baseball-Reference/pybaseball code.
    """
    if not team:
        return ""
    norm = normalize_team_key(team)
    if norm in TEAM_NAME_TO_BR:
        return TEAM_NAME_TO_BR[norm].upper()
    if len(team) == 3 and norm.isalpha():
        return team.upper()
    return team.upper()


def clean_hand(val: object | None) -> str | None:
    """
    Normalize a hand value; treat empty/NaN as missing.
    """
    if val is None:
        return None
    try:
        if pd.isna(val):  # type: ignore[arg-type]
            return None
    except Exception:
        pass
    text = str(val).strip()
    return text or None


def fill_missing_hands(df: pd.DataFrame, season: int | None) -> pd.DataFrame:
    """
    Backfill missing Hand/LR/Throws using chadwick/retro lookups when available.
    """
    if df.empty or season is None:
        return df

    def missing(val: object | None) -> bool:
        return clean_hand(val) is None

    needs = df[df["Hand"].apply(missing) | df["Throws"].apply(missing)]
    if needs.empty:
        return df

    hand_aliases = {
        "dee strangegordon": "dee gordon",
    }
    names_for_lookup = set(needs["Name"])
    for name in list(names_for_lookup):
        norm = normalize_player_name(name)
        if norm in hand_aliases:
            names_for_lookup.add(hand_aliases[norm])

    lookup = team_stats.hands_from_names(list(names_for_lookup), season=season)
    for idx, row in needs.iterrows():
        norm = normalize_player_name(row["Name"])
        bats, throws = lookup.get(norm, (None, None))
        if not bats and not throws and norm in hand_aliases:
            bats, throws = lookup.get(hand_aliases[norm], (None, None))
        bats = clean_hand(bats)
        throws = clean_hand(throws)
        if missing(df.at[idx, "Hand"]) and bats:
            df.at[idx, "Hand"] = bats
        if row.get("Type") == "Hitter" and (missing(df.at[idx, "LR"]) and bats):
            df.at[idx, "LR"] = bats
        if missing(df.at[idx, "Throws"]) and throws:
            df.at[idx, "Throws"] = throws
    return df


def mlb_team_id(team_code: str) -> int:
    code = team_code.upper()
    if code not in MLB_TEAM_IDS:
        raise ValueError(f"No MLB team id known for code '{team_code}'")
    return MLB_TEAM_IDS[code]


def parse_positions(raw: Optional[str], default: str = "") -> Tuple[str, str]:
    """
    Normalize a raw BR position string (e.g., RF, PH-RF, 7/8) into
    (primary, comma-separated).
    """
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
    seen: List[str] = []
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


def mlb_batting_order(raw: str | None) -> tuple[str | None, float]:
    """
    Convert MLB Stats API battingOrder (e.g., "100", "502") into a BatOrder string and sort key.
    """
    if not raw:
        return None, 999.0
    try:
        num = int(raw)
    except (TypeError, ValueError):
        return None, 999.0
    slot = num // 100
    sub = num % 100
    if slot <= 0:
        return None, 999.0
    bat_order = str(slot) if sub == 0 else f"{slot}.{sub}"
    sort_key = float(f"{slot}.{sub:02d}")
    return bat_order, sort_key


def mlb_positions(player_entry: dict, default: str = "") -> tuple[str, str]:
    """
    Return (primary, comma-separated) positions from an MLB Stats API player entry.

    MLB's top-level position can reflect the final position after substitutions.
    The first allPositions entry preserves the starting/first-played position.
    """
    positions = player_entry.get("allPositions") or []
    abbrs: list[str] = []

    def _add(pos_code: str | None) -> None:
        if not pos_code:
            return
        if pos_code not in abbrs:
            abbrs.append(pos_code)

    for p in positions:
        _add(p.get("abbreviation") or p.get("code"))

    pos = player_entry.get("position", {})
    _add(pos.get("abbreviation") or pos.get("code"))

    if not abbrs:
        return default, default

    primary = abbrs[0]
    return primary, ",".join(abbrs)


def mlb_team_label(team_entry: dict) -> tuple[str, str]:
    """
    Return (display_name, abbreviation) for a team entry from the boxscore payload.
    """
    team = team_entry.get("team", {})
    name = (
        team.get("fullName")
        or team.get("name")
        or f"{team.get('locationName', '')} {team.get('teamName', '')}".strip()
        or team.get("teamName")
        or team.get("clubName")
        or "Unknown"
    )
    abbr = team.get("abbreviation") or team.get("teamCode") or team.get("fileCode") or name
    return name, abbr


def mlb_roster_ids(team_entry: dict, *groups: str) -> set[str]:
    """Return normalized player IDs from MLB team roster groups."""
    ids: set[str] = set()
    for group in groups:
        for value in team_entry.get(group) or []:
            player_id = _player_id(value)
            if player_id is not None:
                ids.add(player_id)
    return ids


def mlb_person_hands(
    person_id: int | None,
    rate_limit_seconds: float = 0.0,
    allow_network: bool = True,
    refresh: bool = False,
) -> tuple[str | None, str | None]:
    if not person_id:
        return None, None
    url = f"https://statsapi.mlb.com/api/v1/people/{person_id}"
    try:
        resp = _fetch_with_rate_limit(
            url,
            rate_limit_seconds,
            f"MLB person {person_id}",
            refresh_cache=refresh,
            allow_network=allow_network,
        )
        data = resp.json()
    except Exception:
        return None, None
    people = data.get("people") or []
    if not people:
        return None, None
    person = people[0]
    bat = clean_hand((person.get("batSide") or {}).get("code"))
    throws = clean_hand((person.get("pitchHand") or {}).get("code"))
    return bat, throws


def load_deadball_source(
    team: str,
    season: int,
    postseason: bool = False,
    rate_limit_seconds: float = 0.0,
    allow_fetch: bool = True,
    refresh: bool = False,
    trait_mode: str = "standard",
) -> pd.DataFrame:
    """Load current regular-season ratings, regardless of the game's round.

    Filenames remain mode-independent; every row must match the requested rules.
    Refresh bypasses generated ratings but can rebuild from raw caches offline.
    """
    rules.validate_mode(trait_mode)
    tlow = team.lower()
    candidates = [
        SEASON_DIR / f"{tlow}_{season}_deadball.csv",
        SEASON_DIR / f"{tlow}_{season}_deadball_seaason.csv",  # legacy with typo
        SEASON_DIR / f"{tlow}_deadball_{season}.csv",  # legacy naming
        SEASON_DIR / f"{tlow}_deadball.csv",
        LEGACY_DEADBALL_DIR / f"{tlow}_{season}_deadball.csv",
        LEGACY_DEADBALL_DIR / f"{tlow}_{season}_deadball_seaason.csv",
        LEGACY_DEADBALL_DIR / f"{tlow}_deadball_{season}.csv",
        LEGACY_DEADBALL_DIR / f"{tlow}_deadball.csv",
    ]

    def read_current(path: Path, *, just_built=False) -> pd.DataFrame | None:
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            return None
        if (not df.empty and {"RulesVersion", "TraitMode"}.issubset(df.columns)
                and df["RulesVersion"].eq(rules.RULES_VERSION).all()
                and df["TraitMode"].eq(trait_mode).all()):
            fresh = cache_policy.is_fresh(season, cache_policy.frame_snapshot(df))
            if not fresh and allow_fetch and not just_built:
                return None
            df["CacheStale"] = not fresh
            return df
        return None

    if not refresh:
        for path in candidates:
            current = read_current(path)
            if current is not None:
                return current

    raw_paths = team_stats.stat_paths(team, season, postseason=False)
    if allow_fetch:
        # fetch_regular validates StatsVersion and preserves current raw caches.
        team_stats.fetch_regular(team, season, rate_limit_seconds=rate_limit_seconds, refresh=refresh)
    if not all(path.exists() for path in raw_paths):
        policy = "after fetch" if allow_fetch else "network fetch disabled"
        raise FileNotFoundError(f"Regular-season raw stats unavailable for {team} {season}; {policy}")
    team_stats.build_deadball_regular(
        team, season, trait_mode=trait_mode, allow_network=allow_fetch,
        refresh=refresh, rate_limit_seconds=rate_limit_seconds,
    )
    # Only accept the newly built canonical output, not a stale legacy file.
    current = read_current(team_stats.deadball_paths(team, season, postseason=False)[0], just_built=True)
    if current is None:
        raise ValueError(f"Generated regular-season ratings for {team} {season} do not match {rules.RULES_VERSION}/{trait_mode}")
    return current


RATING_METADATA = ("IDmlb", "RatingNotes", "Provisional", "RatingSource", "RulesVersion", "TraitMode", "Role", "SnapshotAt", "CacheStale")


def _player_id(value: object) -> str | None:
    number = rules.number(value)
    return str(int(number)) if number is not None and number > 0 and number.is_integer() else None


def _missing_player_source(player: dict, pitching: bool, trait_mode: str, history: dict) -> dict:
    """Prefer regular-season history; otherwise evaluate provisional seasonStats."""
    stats = (player.get("seasonStats") or {}).get("pitching" if pitching else "batting") or {}
    fields = {
        "G": "gamesPlayed", "GS": "gamesStarted", "PA": "plateAppearances",
        "AVG": "avg", "OBP": "obp", "SLG": "slg", "HR": "homeRuns",
        "2B": "doubles", "SB": "stolenBases", "SO": "strikeOuts",
        "ERA": "era", "K/9": "strikeoutsPer9Inn", "BB/9": "walksPer9Inn",
    }
    sample = {key: rules.number(stats.get(api_key)) for key, api_key in fields.items()}
    sample["Pos"] = mlb_positions(player, default="P" if pitching else "")[0]
    sample["IDmlb"] = (player.get("person") or {}).get("id")
    if pitching:
        ip = career.innings(stats.get("inningsPitched"))
        sample["IP"] = ip
        for key, api_key in (("K/9", "strikeOuts"), ("BB/9", "baseOnBalls"), ("ERA", "earnedRuns")):
            count = rules.number(stats.get(api_key))
            if sample[key] is None and ip and ip > 0 and count is not None:
                sample[key] = count * 9 / ip
        # groundOuts/airOuts are not GB%; leave the latter unassessed.
    kind = "pitcher" if pitching else "hitter"
    annual = history.get(f"season_{kind}") or {}
    sample.update({key: value for key, value in annual.items() if value is not None})
    if not pitching and history.get("season_fielding"):
        sample["FP"] = history["season_fielding"].get("FP")
    evaluate = rules.evaluate_pitcher if pitching else rules.evaluate_hitter
    rating = evaluate(sample, mode=trait_mode, career=history.get(kind))
    notes = json.loads(rating["RatingNotes"])
    if annual or history.get(kind):
        notes.setdefault("reasons", {})["source"] = "Player absent from team ratings; recovered MLB regular-season/career history by ID"
    else:
        notes.update(source="boxscore-season-provisional", provisional=True)
        notes.setdefault("reasons", {})["source"] = "Regular-season player source unavailable; using boxscore seasonStats without career history"
        rating.update(RatingSource="boxscore-season-provisional", Provisional=True)
    rating["RatingNotes"] = json.dumps(notes, sort_keys=True)
    return {**sample, **rating}


def build_deadball_for_game(
    date: str,
    team: str,
    box_url_override: str | None = None,
    box_file: Path | None = None,
    postseason: bool = False,
    auto_postseason: bool = False,
    rate_limit_seconds: float = 0.0,
    no_fetch: bool = False,
    refresh: bool = False,
    trait_mode: str = "standard",
    include_reserves: bool = True,
) -> tuple[pd.DataFrame, dict[str, str]]:
    rules.validate_mode(trait_mode)
    if box_file and box_url_override:
        raise ValueError("Specify only one of --box-url or --box-file.")

    season = int(date.split("-")[0])

    allow_network = not no_fetch

    # postseason/auto_postseason remain accepted for CLI compatibility. The
    # boxscore selects participants; ratings always use regular-season/career.

    boxscore: dict | None = None
    team_labels: dict[str, str] = {}
    if box_file:
        try:
            boxscore = json.loads(Path(box_file).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Boxscore file must be MLB Stats API JSON.") from exc
    else:
        if box_url_override:
            box_url = box_url_override
            box_label = "MLB boxscore override"
        else:
            game_pk, home_abbr, away_abbr = find_mlb_game(
                date,
                team,
                rate_limit_seconds=rate_limit_seconds,
                allow_network=allow_network,
            )
            box_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
            box_label = f"MLB boxscore {game_pk}"
            print(f"[deadball] Game {date}: home={home_abbr} away={away_abbr}")
        resp = _fetch_with_rate_limit(
            box_url,
            rate_limit_seconds,
            box_label,
            refresh_cache=refresh,
            allow_network=allow_network,
        )
        resp.raise_for_status()
        boxscore = resp.json()

    if boxscore is None:
        raise ValueError("No MLB boxscore data available.")

    teams = boxscore.get("teams") or {}
    team_entries: list[tuple[str, dict, str, str]] = []
    for side in ("away", "home"):
        entry = teams.get(side)
        if entry:
            name, abbr = mlb_team_label(entry)
            team_entries.append((side, entry, name, abbr))
            team_labels[f"{side}_team"] = name
            team_labels[f"{side}_abbr"] = abbr
    if not team_entries:
        raise ValueError("MLB boxscore missing team data.")

    # Load deadball sources per-team as they appear in the boxscore (home + away).
    lookup_cache: dict[str, tuple[dict[str, pd.Series], dict[str, pd.Series]]] = {}
    history_cache: dict[str, dict] = {}

    def get_lookups(raw_team_name: str, team_abbr: str) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
        team_code = team_code_from_name(team_abbr or raw_team_name)
        if not team_code:
            raise ValueError(f"Could not determine team code for '{raw_team_name}'")
        if team_code not in lookup_cache:
            try:
                deadball_df = load_deadball_source(
                    team_code, season, postseason=False,
                    rate_limit_seconds=rate_limit_seconds, allow_fetch=allow_network,
                    refresh=refresh, trait_mode=trait_mode,
                )
            except FileNotFoundError as exc:
                warnings.warn(
                    f"{exc}; trying player career history, then provisional boxscore seasonStats",
                    RuntimeWarning, stacklevel=2,
                )
                deadball_df = pd.DataFrame()
            hitter_lookup, pitcher_lookup = {}, {}
            for _, row in deadball_df.iterrows():
                kind = str(row.get("Type", "")).lower()
                if kind not in ("hitter", "pitcher"):
                    continue
                lookup = hitter_lookup if kind == "hitter" else pitcher_lookup
                pid = _player_id(row.get("IDmlb"))
                # Name lookup is only for legacy rows with no MLB identity.
                key = f"id:{pid}" if pid else f"name:{normalize_player_name(row['Name'])}"
                lookup[key] = row
            lookup_cache[team_code] = (hitter_lookup, pitcher_lookup)
        return lookup_cache[team_code]

    def player_source(lookup: dict, player: dict, pitching: bool) -> dict:
        person = player.get("person") or {}
        pid = _player_id(person.get("id"))
        source = lookup.get(f"id:{pid}") if pid else None
        if source is None:
            source = lookup.get(f"name:{normalize_player_name(person.get('fullName', ''))}")
        if source is not None:
            return source.to_dict()
        if pid is not None and pid not in history_cache:
            history_cache[pid] = career.load_history(
                int(pid), season, team_stats.CACHE_ROOT / "career",
                allow_network=allow_network, refresh=refresh,
                fetch=lambda url: _fetch_with_rate_limit(
                    url, rate_limit_seconds, "MLB career statistics",
                    refresh_cache=True, allow_network=allow_network,
                ),
            )
        source = _missing_player_source(player, pitching, trait_mode, history_cache.get(pid, {}))
        stamp = career.snapshot_at(pid, season, team_stats.CACHE_ROOT / "career") if pid and history_cache.get(pid) else None
        source.update(SnapshotAt=stamp, CacheStale=not cache_policy.is_fresh(season, stamp))
        return source

    rows = []
    for _, team_entry, team_name, team_abbr in team_entries:
        players = list((team_entry.get("players") or {}).values())
        hitter_lookup, pitcher_lookup = get_lookups(team_name, team_abbr)
        hitter_groups = ("batters", "bench") if include_reserves else ("batters",)
        pitcher_groups = ("pitchers", "bullpen") if include_reserves else ("pitchers",)
        available_hitters = mlb_roster_ids(team_entry, *hitter_groups)
        available_pitchers = mlb_roster_ids(team_entry, *pitcher_groups)

        # A late call-up or newly traded player may be absent from the cached
        # team-season ratings. Resolve all such roster gaps in one request
        # rather than discovering and fetching them one at a time below.
        missing_history_ids = set()
        for player in players:
            person = player.get("person") or {}
            pid = _player_id(person.get("id"))
            if pid is None:
                continue
            name_key = f"name:{normalize_player_name(person.get('fullName', ''))}"
            if pid in available_hitters and not (
                hitter_lookup.get(f"id:{pid}") is not None or hitter_lookup.get(name_key) is not None
            ):
                missing_history_ids.add(int(pid))
            if pid in available_pitchers and not (
                pitcher_lookup.get(f"id:{pid}") is not None or pitcher_lookup.get(name_key) is not None
            ):
                missing_history_ids.add(int(pid))
        if missing_history_ids:
            batched_histories = career.load_histories(
                sorted(missing_history_ids), season, team_stats.CACHE_ROOT / "career",
                allow_network=allow_network, refresh=refresh,
                fetch=lambda url: _fetch_with_rate_limit(
                    url, rate_limit_seconds, "MLB career statistics",
                    refresh_cache=True, allow_network=allow_network,
                ),
            )
            history_cache.update({str(player_id): history for player_id, history in batched_histories.items()})

        hitters: list[tuple[float, str, dict, dict]] = []
        for player in players:
            bat_stats = player.get("stats", {}).get("batting") or {}
            bat_order, sort_key = mlb_batting_order(player.get("battingOrder"))
            person_id = _player_id((player.get("person") or {}).get("id"))
            if bat_order is None and person_id not in available_hitters:
                continue
            hitters.append((sort_key, bat_order or "", player, bat_stats))

        hitters.sort(key=lambda t: (t[0], t[2].get("person", {}).get("fullName", "")))
        uses_designated_hitter = any(
            mlb_positions(player)[0] == "DH"
            and mlb_batting_order(player.get("battingOrder"))[0] is not None
            and mlb_batting_order(player.get("battingOrder"))[1].is_integer()
            for _, _, player, _ in hitters
        )

        for _, bat_order, player, bat_stats in hitters:
            name = player.get("person", {}).get("fullName") or ""
            if not name:
                continue
            primary_pos, all_pos = mlb_positions(player, default="")
            norm_name = normalize_player_name(name)
            source = player_source(hitter_lookup, player, pitching=False)
            bats_hand = clean_hand(source.get("Hand")) or clean_hand(source.get("LR")) or clean_hand(player.get("batSide", {}).get("code"))
            throws_hand = clean_hand(source.get("Throws")) or clean_hand(player.get("pitchHand", {}).get("code"))
            if (not bats_hand or not throws_hand) and allow_network:
                try:
                    year = int(date.split("-")[0])
                except Exception:
                    year = None
                if year:
                    hand_lookup = team_stats.hands_from_names([name], season=year)
                    b2, t2 = hand_lookup.get(norm_name, (None, None))
                    bats_hand = bats_hand or b2
                    throws_hand = throws_hand or t2
                    if not bats_hand or not throws_hand:
                        fg_lookup = team_stats.hands_from_fg_ids([], season=year)  # triggers cache load
                        b3, t3 = team_stats.resolve_hands(name, None, fg_lookup, hand_lookup, season=year)
                        bats_hand = bats_hand or clean_hand(b3)
                        throws_hand = throws_hand or clean_hand(t3)
            if (not bats_hand or not throws_hand) and allow_network:
                pid = (player.get("person") or {}).get("id")
                b4, t4 = mlb_person_hands(pid, rate_limit_seconds=rate_limit_seconds, allow_network=allow_network, refresh=refresh)
                bats_hand = bats_hand or b4
                throws_hand = throws_hand or t4
            bat_row = {
                "Type": "Hitter",
                "Team": team_name,
                "BatOrder": bat_order,
                "Name": name,
                "Pos": primary_pos,
                "Positions": all_pos,
                "LR": bats_hand,
                "Hand": bats_hand,
                "Throws": throws_hand,
                "Age": source.get("Age"),
                "BT": source.get("BT"),
                "OBT": source.get("OBT"),
                "AVG": source.get("AVG"),
                "OBP": source.get("OBP"),
                "HR": source.get("HR"),
                "2B": source.get("2B"),
                "SB": source.get("SB"),
                "G": source.get("G"),
                "GameAppeared": bool(bat_stats) or bool(bat_order),
                "Traits": source.get("Traits", ""),
                **{key: source.get(key) for key in RATING_METADATA},
            }
            if _player_id(bat_row["IDmlb"]) is None:
                bat_row["IDmlb"] = (player.get("person") or {}).get("id")
            rows.append(bat_row)

        for player in players:
            pit_stats = player.get("stats", {}).get("pitching") or {}
            person_id = _player_id((player.get("person") or {}).get("id"))
            if not pit_stats and person_id not in available_pitchers:
                continue
            name = (player.get("person", {}) or {}).get("fullName") or ""
            if not name:
                continue
            source = player_source(pitcher_lookup, player, pitching=True)
            throws_hand = clean_hand(source.get("Throws")) or clean_hand(player.get("pitchHand", {}).get("code")) or clean_hand(player.get("batSide", {}).get("code"))
            if not throws_hand and allow_network:
                pid = (player.get("person") or {}).get("id")
                _, t4 = mlb_person_hands(pid, rate_limit_seconds=rate_limit_seconds, allow_network=allow_network, refresh=refresh)
                throws_hand = throws_hand or t4
            primary_pos, all_pos = mlb_positions(player, default="P")
            batting = {}
            if not uses_designated_hitter:
                batting_source = player_source(hitter_lookup, player, pitching=False)
                batting = {
                    "Bats": clean_hand(batting_source.get("Hand"))
                    or clean_hand(batting_source.get("LR"))
                    or clean_hand(player.get("batSide", {}).get("code")),
                    "BT": batting_source.get("BT"),
                    "OBT": batting_source.get("OBT"),
                    "BattingTraits": batting_source.get("Traits", ""),
                }
            pit_row = {
                "Type": "Pitcher",
                "Team": team_name,
                "BatOrder": None,
                "Name": name,
                "Pos": primary_pos or "P",
                "Positions": all_pos or "P",
                "Hand": throws_hand,
                "Throws": throws_hand,
                "Age": source.get("Age"),
                "PD": source.get("PD"),
                "ERA": source.get("ERA"),
                "IP": source.get("IP"),
                "K/9": source.get("K/9"),
                "BB/9": source.get("BB/9"),
                "GB%": source.get("GB%"),
                "GS": source.get("GS"),
                "GameStarted": bool(pit_stats.get("gamesStarted")),
                "GameAppeared": bool(pit_stats),
                "Traits": source.get("Traits", ""),
                **batting,
                **{key: source.get(key) for key in RATING_METADATA},
            }
            if _player_id(pit_row["IDmlb"]) is None:
                pit_row["IDmlb"] = (player.get("person") or {}).get("id")
            rows.append(pit_row)

    df_out = pd.DataFrame(rows)
    pitcher_cols = ["PD", "ERA", "IP", "K/9", "BB/9", "GB%", "GS"]
    if not df_out.empty:
        hitter_mask = df_out["Type"] == "Hitter"
        df_out.loc[hitter_mask, pitcher_cols] = None

        def bat_order_key(val):
            try:
                return float(val)
            except Exception:
                return 999.0

        df_out["_type_order"] = df_out["Type"].map({"Hitter": 0, "Pitcher": 1}).fillna(2)
        df_out["_bat_order_sort"] = df_out["BatOrder"].apply(bat_order_key)
        df_out = df_out.sort_values(["Team", "_type_order", "_bat_order_sort"], na_position="last").reset_index(drop=True)
        df_out = df_out.drop(columns=["_type_order", "_bat_order_sort"])
        if allow_network:
            df_out = fill_missing_hands(df_out, season)
    return df_out, team_labels


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trait-mode", choices=rules.TRAIT_MODES, default="standard")
    parser.add_argument("--date", required=True, help="Game date in YYYY-MM-DD")
    parser.add_argument("--team", required=True, help="HOME team abbreviation (e.g., LAD for a home Dodgers game)")
    parser.add_argument("--output", default=None, help="Output CSV path")
    parser.add_argument("--box-url", default=None, help="Override MLB Stats API boxscore URL (if schedule lookup fails)")
    parser.add_argument(
        "--box-file",
        type=Path,
        default=None,
        help="Path to local MLB boxscore JSON; add --no-fetch to disable all network access.",
    )
    parser.add_argument("--postseason", action="store_true", help="Compatibility flag; game ratings always use regular-season/career stats.")
    parser.add_argument(
        "--auto-postseason",
        action="store_true",
        help="Compatibility flag; boxscore participants need no postseason detection for ratings.",
    )
    parser.add_argument(
        "--rate-limit-seconds",
        type=float,
        default=0.0,
        help="Sleep this many seconds before each network request (gentle rate limit).",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use only cached regular-season sources; missing players receive provisional seasonStats ratings.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-downloading schedule/boxscore data and refresh season stats even if cached locally.",
    )
    parser.add_argument(
        "--skip-scorecard",
        action="store_true",
        help="Skip generating a scorecard HTML after writing the game CSV.",
    )
    parser.add_argument(
        "--scorecard-template",
        type=Path,
        default=None,
        help="Template to use when auto-filling the scorecard HTML (defaults to the built-in template).",
    )


def main_from_parsed(args: argparse.Namespace) -> None:
    # Normalize date to YYYY-MM-DD to keep filenames consistent (pad single-digit days).
    try:
        normalized_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        normalized_date = args.date  # fall back to user input if parsing fails

    df, team_labels = build_deadball_for_game(
        normalized_date,
        args.team.upper(),
        box_url_override=args.box_url,
        box_file=args.box_file,
        postseason=args.postseason,
        auto_postseason=args.auto_postseason,
        rate_limit_seconds=args.rate_limit_seconds,
        no_fetch=args.no_fetch,
        refresh=args.refresh,
        trait_mode=args.trait_mode,
    )
    if df.empty:
        print(
            "No rows were parsed from the boxscore. This usually means the boxscore is missing "
            "(future game), or the page structure lacked batting/pitching tables. "
            "Provide a valid boxscore URL or HTML file, and ensure season Deadball sources exist "
            "when using --no-fetch."
        )
        return
    out_path = (
        Path(args.output)
        if args.output
        else GAMES_DIR / f"{args.team.lower()}_{normalized_date}_deadball_game.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")
    if not args.skip_scorecard:
        away_team = team_labels.get("away_team")
        home_team = team_labels.get("home_team")
        template = args.scorecard_template or fill_scorecard.DEFAULT_TEMPLATE
        fill_args = argparse.Namespace(
            csv=out_path,
            away_team=away_team,
            home_team=home_team,
            template=template,
            output=None,
        )
        try:
            fill_scorecard.main_from_parsed(fill_args)
        except Exception as exc:
            print(f"[deadball] Failed to generate scorecard HTML: {exc}")


def main(args: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build deadball stats for a single game (team must be the HOME team).")
    configure_parser(parser)
    opts = parser.parse_args(args)
    main_from_parsed(opts)


if __name__ == "__main__":
    main()
