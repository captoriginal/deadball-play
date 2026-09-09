"""Versioned, immutable generated-game input contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import math
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
VALID_POSITIONS = frozenset({"P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "OF", "DH", "UT", "PH", "PR"})
DEFENSIVE_POSITIONS = frozenset({"P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"})
VALID_BAT_HANDS = frozenset({"R", "L", "S"})
VALID_THROW_HANDS = frozenset({"R", "L"})
VALID_PITCH_DICE = frozenset({"d20", "d12", "d8", "d4", "-d4", "-d8", "-d12", "-d20"})
VALID_TRAITS = frozenset({
    "P+", "P++", "C+", "S+", "D+", "T+",
    "P-", "P--", "C-", "S-", "D-",
    "K+", "GB+", "CN+", "ST+", "CN-",
})
VALID_ROLES = frozenset({"position_player", "starter", "reliever"})


class GameDataError(ValueError):
    """Raised when generated-game data cannot safely initialize a game."""


@dataclass(frozen=True)
class GameMetadata:
    game_id: str
    game_date: str
    source: str
    source_game_id: str | None = None
    season: int | None = None
    game_type: str | None = None
    venue: str | None = None
    generated_at: str | None = None


@dataclass(frozen=True)
class RulesConfig:
    edition: str
    era: str
    designated_hitter: bool
    oddities: bool = True
    three_batter_minimum: bool = True


@dataclass(frozen=True)
class MlbPlayerStats:
    games: float | None = None
    average: float | None = None
    on_base_percentage: float | None = None
    home_runs: float | None = None
    doubles: float | None = None
    stolen_bases: float | None = None
    games_started: float | None = None
    innings_pitched: float | None = None
    era: float | None = None
    strikeouts_per_nine: float | None = None
    walks_per_nine: float | None = None
    ground_ball_percentage: float | None = None


@dataclass(frozen=True)
class PlayerData:
    player_id: str
    name: str
    role: str
    positions: tuple[str, ...]
    source_player_id: str | int | None = None
    bats: str | None = None
    throws: str | None = None
    bt: int | None = None
    obt: int | None = None
    pitch_die: str | None = None
    traits: tuple[str, ...] = ()
    mlb_stats: MlbPlayerStats | None = None
    appeared_in_source_game: bool = False


@dataclass(frozen=True)
class LineupSlot:
    slot: int
    player_id: str
    position: str


@dataclass(frozen=True)
class TeamData:
    team_id: str
    name: str
    short_name: str
    lineup: tuple[LineupSlot, ...]
    roster: tuple[PlayerData, ...]
    starting_pitcher_id: str

    def player(self, player_id: str) -> PlayerData:
        for player in self.roster:
            if player.player_id == player_id:
                return player
        raise KeyError(player_id)


@dataclass(frozen=True)
class GameTeams:
    away: TeamData
    home: TeamData


@dataclass(frozen=True)
class GeneratedGame:
    schema_version: int
    game: GameMetadata
    rules: RulesConfig
    teams: GameTeams

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass(frozen=True)
class GeneratorGameContext:
    """Metadata absent from the generator's current flat player payload."""

    game_id: str
    game_date: str
    away_team_name: str
    away_team_short: str
    home_team_name: str
    home_team_short: str
    away_starting_pitcher_id: str | int | None = None
    home_starting_pitcher_id: str | int | None = None
    designated_hitter: bool = True
    game_type: str | None = None


def load_generated_game(payload: str | bytes | Mapping[str, Any]) -> GeneratedGame:
    """Load and validate canonical schema-versioned game data."""
    if isinstance(payload, (str, bytes)):
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GameDataError(f"game data is not valid JSON: {exc}") from exc
    else:
        raw = payload
    root = _mapping(raw, "game data")
    version = _integer(root.get("schema_version"), "schema_version")
    if version != SCHEMA_VERSION:
        raise GameDataError(f"schema_version must be {SCHEMA_VERSION}, got {version}")

    game_raw = _mapping(root.get("game"), "game")
    rules_raw = _mapping(root.get("rules"), "rules")
    teams_raw = _mapping(root.get("teams"), "teams")
    game = GameMetadata(
        game_id=_text(game_raw.get("game_id"), "game.game_id"),
        game_date=_iso_date(game_raw.get("game_date"), "game.game_date"),
        source=_text(game_raw.get("source"), "game.source"),
        source_game_id=_optional_text(game_raw.get("source_game_id")),
        season=_optional_integer(game_raw.get("season"), "game.season"),
        game_type=_optional_text(game_raw.get("game_type")),
        venue=_optional_text(game_raw.get("venue")),
        generated_at=_optional_datetime(game_raw.get("generated_at"), "game.generated_at"),
    )
    rules = RulesConfig(
        edition=_text(rules_raw.get("edition"), "rules.edition").lower(),
        era=_text(rules_raw.get("era"), "rules.era").lower(),
        designated_hitter=_boolean(rules_raw.get("designated_hitter"), "rules.designated_hitter"),
        oddities=_boolean(rules_raw.get("oddities", True), "rules.oddities"),
        three_batter_minimum=_boolean(
            rules_raw.get("three_batter_minimum", True),
            "rules.three_batter_minimum",
        ),
    )
    result = GeneratedGame(
        schema_version=version,
        game=game,
        rules=rules,
        teams=GameTeams(
            away=_parse_team(teams_raw.get("away"), "teams.away"),
            home=_parse_team(teams_raw.get("home"), "teams.home"),
        ),
    )
    validate_generated_game(result)
    return result


def validate_generated_game(game: GeneratedGame) -> None:
    """Validate cross-record invariants required by initial game state."""
    if game.rules.edition != "second":
        raise GameDataError("rules.edition must be 'second'")
    if game.rules.era != "modern":
        raise GameDataError("rules.era must be 'modern' for Version 1")
    if game.game.season is not None and game.game.season != int(game.game.game_date[:4]):
        raise GameDataError("game.season must match game.game_date")
    if game.teams.away.team_id == game.teams.home.team_id:
        raise GameDataError("away and home team IDs must differ")

    all_player_ids: set[str] = set()
    for side, team in (("away", game.teams.away), ("home", game.teams.home)):
        path = f"teams.{side}"
        roster_ids = [player.player_id for player in team.roster]
        if len(roster_ids) != len(set(roster_ids)):
            raise GameDataError(f"{path}.roster contains duplicate player IDs")
        duplicates = all_player_ids.intersection(roster_ids)
        if duplicates:
            raise GameDataError(f"player IDs must be unique across teams: {sorted(duplicates)}")
        all_player_ids.update(roster_ids)

        for player in team.roster:
            if len(player.positions) != len(set(player.positions)):
                raise GameDataError(f"{path}.roster player {player.player_id} has duplicate positions")
            if player.role == "position_player" and (
                player.bats is None or player.bt is None or player.obt is None
            ):
                raise GameDataError(
                    f"{path}.position player {player.player_id} requires bats, bt, and obt"
                )
            if player.role in {"starter", "reliever"} and (
                "P" not in player.positions or player.throws is None or player.pitch_die is None
            ):
                raise GameDataError(
                    f"{path}.pitcher {player.player_id} requires position P, throws, and pitch_die"
                )
            if (
                not game.rules.designated_hitter
                and player.role in {"starter", "reliever"}
                and (player.bats is None or player.bt is None or player.obt is None)
            ):
                raise GameDataError(
                    f"{path}.non-DH pitcher {player.player_id} requires bats, bt, and obt"
                )

        slots = [entry.slot for entry in team.lineup]
        if slots != list(range(1, 10)):
            raise GameDataError(f"{path}.lineup slots must be exactly 1 through 9")
        lineup_ids = [entry.player_id for entry in team.lineup]
        if len(lineup_ids) != len(set(lineup_ids)):
            raise GameDataError(f"{path}.lineup contains a player more than once")
        missing = set(lineup_ids) - set(roster_ids)
        if missing:
            raise GameDataError(f"{path}.lineup references players outside the roster: {sorted(missing)}")

        if team.starting_pitcher_id not in roster_ids:
            raise GameDataError(f"{path}.starting_pitcher_id is not on the roster")
        starter = team.player(team.starting_pitcher_id)
        if starter.role not in {"starter", "reliever"}:
            raise GameDataError(f"{path}.starting pitcher must have a pitching role")
        for entry in team.lineup:
            player = team.player(entry.player_id)
            if player.bats is None or player.bt is None or player.obt is None:
                raise GameDataError(f"{path}.lineup player {player.player_id} requires bats, bt, and obt")

        positions = [entry.position for entry in team.lineup]
        if game.rules.designated_hitter:
            if positions.count("DH") != 1 or "P" in positions:
                raise GameDataError(f"{path}.lineup must contain one DH and no P when the DH rule is enabled")
        elif "DH" in positions or positions.count("P") != 1:
            raise GameDataError(f"{path}.lineup must contain one P and no DH when the DH rule is disabled")

        defense = [position for position in positions if position != "DH"]
        if "P" not in defense:
            defense.append("P")
        if set(defense) != DEFENSIVE_POSITIONS or len(defense) != len(DEFENSIVE_POSITIONS):
            raise GameDataError(f"{path}.lineup does not define one player at every defensive position")


def adapt_generator_game(
    stats: str | bytes | Mapping[str, Any], context: GeneratorGameContext
) -> GeneratedGame:
    """Adapt the generator's current flat scorecard payload to schema v1.

    The context makes game identity, team identity, DH use, and actual starting
    pitchers explicit because the legacy payload does not encode them reliably.
    """
    if isinstance(stats, (str, bytes)):
        try:
            raw = json.loads(stats)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GameDataError(f"generator stats are not valid JSON: {exc}") from exc
    else:
        raw = stats
    root = _mapping(raw, "generator stats")
    rows = _sequence(root.get("players"), "generator stats.players")
    year = int(_iso_date(context.game_date, "context.game_date")[:4])

    contract = {
        "schema_version": SCHEMA_VERSION,
        "game": {
            "game_id": context.game_id,
            "game_date": context.game_date,
            "source": "deadball-generator",
            "source_game_id": context.game_id,
            "season": year,
            "game_type": context.game_type,
        },
        "rules": {
            "edition": "second",
            "era": "modern",
            "designated_hitter": context.designated_hitter,
            "oddities": True,
            "three_batter_minimum": True,
        },
        "teams": {
            "away": _adapt_team_rows(
                rows, context.away_team_name, context.away_team_short,
                context.away_starting_pitcher_id,
            ),
            "home": _adapt_team_rows(
                rows, context.home_team_name, context.home_team_short,
                context.home_starting_pitcher_id,
            ),
        },
    }
    return load_generated_game(contract)


def build_generator_game(
    stats: str | bytes | Mapping[str, Any],
    *,
    game_id: str,
    game_date: str,
    away_team: str,
    home_team: str,
    away_short: str | None = None,
    home_short: str | None = None,
    game_type: str | None = None,
) -> GeneratedGame:
    """Build schema v1 using identity metadata plus a flat generator result."""
    if isinstance(stats, (str, bytes)):
        try:
            payload = json.loads(stats)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GameDataError(f"generator stats are not valid JSON: {exc}") from exc
    else:
        payload = stats
    root = _mapping(payload, "generator stats")
    teams = root.get("teams") if isinstance(root.get("teams"), Mapping) else {}
    rows = _sequence(root.get("players"), "generator stats.players")
    away_abbr = _optional_text(teams.get("away_abbr")) or away_short or away_team
    home_abbr = _optional_text(teams.get("home_abbr")) or home_short or home_team
    designated_hitter = any(
        str(_mapping(row, "generator player").get("Type", "")).casefold()
        == "hitter"
        and str(_mapping(row, "generator player").get("Pos", "")).upper()
        == "DH"
        and (
            (order := _batting_order(
                _mapping(row, "generator player").get("BatOrder")
            ))
            is not None
            and order.is_integer()
            and 1 <= order <= 9
        )
        for row in rows
    )
    return adapt_generator_game(
        root,
        GeneratorGameContext(
            game_id=f"mlb-{game_id}" if not game_id.startswith("mlb-") else game_id,
            game_date=game_date,
            away_team_name=away_team,
            away_team_short=away_abbr,
            home_team_name=home_team,
            home_team_short=home_abbr,
            designated_hitter=designated_hitter,
            game_type=game_type,
        ),
    )


def _parse_team(value: Any, path: str) -> TeamData:
    raw = _mapping(value, path)
    roster_raw = _sequence(raw.get("roster"), f"{path}.roster")
    lineup_raw = _sequence(raw.get("lineup"), f"{path}.lineup")
    return TeamData(
        team_id=_text(raw.get("team_id"), f"{path}.team_id"),
        name=_text(raw.get("name"), f"{path}.name"),
        short_name=_text(raw.get("short_name"), f"{path}.short_name"),
        lineup=tuple(sorted(
            (_parse_lineup(entry, f"{path}.lineup[{index}]") for index, entry in enumerate(lineup_raw)),
            key=lambda entry: entry.slot,
        )),
        roster=tuple(_parse_player(entry, f"{path}.roster[{index}]") for index, entry in enumerate(roster_raw)),
        starting_pitcher_id=_text(raw.get("starting_pitcher_id"), f"{path}.starting_pitcher_id"),
    )


def _parse_player(value: Any, path: str) -> PlayerData:
    raw = _mapping(value, path)
    positions_value = raw.get("positions", [raw.get("position")] if raw.get("position") else [])
    positions = tuple(_position(item, f"{path}.positions") for item in _sequence(positions_value, f"{path}.positions"))
    if not positions:
        raise GameDataError(f"{path}.positions must not be empty")
    role = _text(raw.get("role"), f"{path}.role").lower()
    if role not in VALID_ROLES:
        raise GameDataError(f"{path}.role must be one of {sorted(VALID_ROLES)}")
    bats = _optional_upper(raw.get("bats"))
    throws = _optional_upper(raw.get("throws"))
    if bats is not None and bats not in VALID_BAT_HANDS:
        raise GameDataError(f"{path}.bats must be R, L, or S")
    if throws is not None and throws not in VALID_THROW_HANDS:
        raise GameDataError(f"{path}.throws must be R or L")
    pitch_die = _optional_text(raw.get("pitch_die"))
    if pitch_die is not None and pitch_die not in VALID_PITCH_DICE:
        raise GameDataError(f"{path}.pitch_die must be one of {sorted(VALID_PITCH_DICE)}")
    traits = tuple(_trait(item, f"{path}.traits") for item in _sequence(raw.get("traits", []), f"{path}.traits"))
    if len(traits) != len(set(traits)):
        raise GameDataError(f"{path}.traits contains duplicates")
    bt = _optional_target(raw.get("bt"), f"{path}.bt")
    obt = _optional_target(raw.get("obt"), f"{path}.obt")
    if bt is not None and obt is not None and bt > obt:
        raise GameDataError(f"{path}.bt cannot exceed obt")
    return PlayerData(
        player_id=_text(raw.get("player_id"), f"{path}.player_id"),
        source_player_id=raw.get("source_player_id"),
        name=_text(raw.get("name"), f"{path}.name"),
        role=role,
        positions=positions,
        bats=bats,
        throws=throws,
        bt=bt,
        obt=obt,
        pitch_die=pitch_die,
        traits=traits,
        mlb_stats=_parse_mlb_stats(raw.get("mlb_stats"), f"{path}.mlb_stats"),
        appeared_in_source_game=_boolean(
            raw.get("appeared_in_source_game", False),
            f"{path}.appeared_in_source_game",
        ),
    )


def _parse_mlb_stats(value: Any, path: str) -> MlbPlayerStats | None:
    if value is None:
        return None
    raw = _mapping(value, path)
    return MlbPlayerStats(
        games=_optional_number(raw.get("games"), f"{path}.games"),
        average=_optional_number(raw.get("average"), f"{path}.average"),
        on_base_percentage=_optional_number(
            raw.get("on_base_percentage"), f"{path}.on_base_percentage"
        ),
        home_runs=_optional_number(raw.get("home_runs"), f"{path}.home_runs"),
        doubles=_optional_number(raw.get("doubles"), f"{path}.doubles"),
        stolen_bases=_optional_number(
            raw.get("stolen_bases"), f"{path}.stolen_bases"
        ),
        games_started=_optional_number(
            raw.get("games_started"), f"{path}.games_started"
        ),
        innings_pitched=_optional_number(
            raw.get("innings_pitched"), f"{path}.innings_pitched"
        ),
        era=_optional_number(raw.get("era"), f"{path}.era"),
        strikeouts_per_nine=_optional_number(
            raw.get("strikeouts_per_nine"), f"{path}.strikeouts_per_nine"
        ),
        walks_per_nine=_optional_number(
            raw.get("walks_per_nine"), f"{path}.walks_per_nine"
        ),
        ground_ball_percentage=_optional_number(
            raw.get("ground_ball_percentage"),
            f"{path}.ground_ball_percentage",
        ),
    )


def _parse_lineup(value: Any, path: str) -> LineupSlot:
    raw = _mapping(value, path)
    return LineupSlot(
        slot=_integer(raw.get("slot"), f"{path}.slot"),
        player_id=_text(raw.get("player_id"), f"{path}.player_id"),
        position=_position(raw.get("position"), f"{path}.position"),
    )


def _adapt_team_rows(
    rows: Sequence[Any], team_name: str, short_name: str, starting_pitcher_id: str | int | None
) -> dict[str, Any]:
    aliases = {team_name.strip().casefold(), short_name.strip().casefold()}
    team_rows = [
        _mapping(row, "generator player") for row in rows
        if str(_mapping(row, "generator player").get("Team", "")).strip().casefold() in aliases
    ]
    if not team_rows:
        raise GameDataError(f"generator stats contain no players for {team_name}")
    if starting_pitcher_id is None:
        started = [
            row.get("IDmlb") for row in team_rows
            if str(row.get("Type", "")).strip().casefold() == "pitcher" and _truthy(row.get("GameStarted"))
        ]
        if not started:
            started = [
                row.get("IDmlb") for row in team_rows
                if str(row.get("Type", "")).strip().casefold() == "pitcher"
                and str(row.get("Role", "")).strip().casefold() == "starter"
            ]
        if len(started) != 1:
            raise GameDataError(
                f"{team_name} requires exactly one GameStarted or Role=starter pitcher"
            )
        starting_pitcher_id = started[0]
    starter_id = _canonical_mlb_id(starting_pitcher_id, "starting pitcher ID")
    players: dict[str, dict[str, Any]] = {}
    lineup: dict[int, dict[str, Any]] = {}

    for row in team_rows:
        player_id = _canonical_mlb_id(row.get("IDmlb"), f"{team_name} player ID")
        entry = players.setdefault(player_id, {
            "player_id": player_id,
            "source_player_id": _source_id(row.get("IDmlb")),
            "name": _text(row.get("Name"), f"{team_name} player name"),
            "role": "position_player",
            "positions": [],
            "traits": [],
            "mlb_stats": {},
            "appeared_in_source_game": False,
        })
        positions = _legacy_positions(row.get("Positions") or row.get("Pos"))
        entry["positions"] = list(dict.fromkeys([*entry["positions"], *positions]))
        entry["traits"] = list(dict.fromkeys([
            *entry["traits"],
            *_legacy_traits(row.get("Traits")),
            *_legacy_traits(row.get("BattingTraits")),
        ]))

        row_type = str(row.get("Type", "")).strip().casefold()
        if row_type == "pitcher":
            entry["role"] = "starter" if player_id == starter_id else "reliever"
            entry["bats"] = entry.get("bats") or _optional_upper(row.get("Bats"))
            entry["throws"] = _optional_upper(row.get("Throws") or row.get("Hand"))
            if entry.get("bt") is None:
                entry["bt"] = _legacy_target(row.get("BT"))
            if entry.get("obt") is None:
                entry["obt"] = _legacy_target(row.get("OBT"))
            entry["pitch_die"] = _optional_text(row.get("PD"))
            if "P" not in entry["positions"]:
                entry["positions"].append("P")
            entry["mlb_stats"].update(_legacy_mlb_stats(row, pitching=True))
            entry["appeared_in_source_game"] = bool(
                entry["appeared_in_source_game"] or _truthy(row.get("GameAppeared"))
            )
        elif row_type == "hitter":
            entry["bats"] = _optional_upper(row.get("Hand") or row.get("LR"))
            entry["throws"] = entry.get("throws") or _optional_upper(row.get("Throws"))
            entry["bt"] = _legacy_target(row.get("BT"))
            entry["obt"] = _legacy_target(row.get("OBT"))
            order = _batting_order(row.get("BatOrder"))
            entry["mlb_stats"].update(_legacy_mlb_stats(row, pitching=False))
            entry["appeared_in_source_game"] = bool(
                entry["appeared_in_source_game"] or order is not None
            )
            if order is not None and order.is_integer():
                slot = int(order)
                if slot in lineup:
                    raise GameDataError(f"{team_name} has multiple starting hitters in lineup slot {slot}")
                lineup[slot] = {
                    "slot": slot,
                    "player_id": player_id,
                    "position": _position(row.get("Pos"), f"{team_name} lineup position"),
                }
        else:
            raise GameDataError(f"{team_name} player {player_id} has unknown Type {row.get('Type')!r}")

    if starter_id not in players:
        raise GameDataError(f"starting pitcher {starter_id} is absent from {team_name} generator rows")
    return {
        "team_id": f"mlb-team-{_slug(short_name)}",
        "name": team_name,
        "short_name": short_name.upper(),
        "lineup": [lineup[key] for key in sorted(lineup)],
        "roster": list(players.values()),
        "starting_pitcher_id": starter_id,
    }


def _legacy_mlb_stats(row: Mapping[str, Any], *, pitching: bool) -> dict[str, float]:
    fields = (
        {
            "games_started": "GS",
            "innings_pitched": "IP",
            "era": "ERA",
            "strikeouts_per_nine": "K/9",
            "walks_per_nine": "BB/9",
            "ground_ball_percentage": "GB%",
        }
        if pitching
        else {
            "games": "G",
            "average": "AVG",
            "on_base_percentage": "OBP",
            "home_runs": "HR",
            "doubles": "2B",
            "stolen_bases": "SB",
        }
    )
    return {
        name: number
        for name, legacy_name in fields.items()
        if (number := _optional_number(row.get(legacy_name), legacy_name)) is not None
    }


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GameDataError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise GameDataError(f"{path} must be an array")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GameDataError(f"{path} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_upper(value: Any) -> str | None:
    text = _optional_text(value)
    return text.upper() if text is not None else None


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GameDataError(f"{path} must be an integer")
    return value


def _optional_integer(value: Any, path: str) -> int | None:
    return None if value is None else _integer(value, path)


def _optional_number(value: Any, path: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GameDataError(f"{path} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GameDataError(f"{path} must be numeric") from exc
    if not math.isfinite(result):
        raise GameDataError(f"{path} must be finite")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise GameDataError(f"{path} must be true or false")
    return value


def _iso_date(value: Any, path: str) -> str:
    text = _text(value, path)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise GameDataError(f"{path} must be an ISO date") from exc
    return text


def _optional_datetime(value: Any, path: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GameDataError(f"{path} must be an ISO datetime") from exc
    return text


def _optional_target(value: Any, path: str) -> int | None:
    if value is None:
        return None
    target = _integer(value, path)
    if not 0 <= target <= 100:
        raise GameDataError(f"{path} must be between 0 and 100")
    return target


def _position(value: Any, path: str) -> str:
    position = _text(value, path).upper()
    if position not in VALID_POSITIONS:
        raise GameDataError(f"{path} has unknown position {position!r}")
    return position


def _trait(value: Any, path: str) -> str:
    trait = _text(value, path).replace("−", "-").upper()
    if trait not in VALID_TRAITS:
        raise GameDataError(f"{path} has unknown trait {trait!r}")
    return trait


def _legacy_traits(value: Any) -> list[str]:
    if not _present(value):
        return []
    items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else re.split(r"[\s,]+", str(value).strip())
    return [_trait(item, "generator trait") for item in items if str(item).strip()]


def _legacy_positions(value: Any) -> list[str]:
    if not _present(value):
        return []
    items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else str(value).split(",")
    return [_position(item, "generator position") for item in items if str(item).strip()]


def _legacy_target(value: Any) -> int | None:
    if not _present(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GameDataError(f"generator target {value!r} is not numeric") from exc
    if not number.is_integer():
        raise GameDataError(f"generator target {value!r} is not an integer")
    target = int(number)
    if not 0 <= target <= 100:
        raise GameDataError(f"generator target {value!r} must be between 0 and 100")
    return target


def _batting_order(value: Any) -> float | None:
    if not _present(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise GameDataError(f"generator BatOrder {value!r} is not numeric") from exc


def _canonical_mlb_id(value: Any, path: str) -> str:
    if not _present(value):
        raise GameDataError(f"{path} is required")
    source = _source_id(value)
    text = str(source)
    return text if text.startswith("mlb-") else f"mlb-{text}"


def _source_id(value: Any) -> str | int:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return str(value).strip()


def _present(value: Any) -> bool:
    return value is not None and value != "" and not (isinstance(value, float) and math.isnan(value))


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().casefold() in {"1", "true", "yes"}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise GameDataError("team short name cannot form a stable team ID")
    return slug
