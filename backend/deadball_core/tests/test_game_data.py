from dataclasses import FrozenInstanceError
import json
import socket

import pytest

from deadball_core.game_data import (
    GameDataError,
    GeneratorGameContext,
    adapt_generator_game,
    build_generator_game,
    load_generated_game,
)
from deadball_core.state import initialize_game


POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")


def canonical_game() -> dict:
    return {
        "schema_version": 1,
        "game": {
            "game_id": "mlb-123",
            "game_date": "2026-08-15",
            "source": "test",
            "source_game_id": "123",
            "season": 2026,
        },
        "rules": {
            "edition": "second",
            "era": "modern",
            "designated_hitter": True,
            "oddities": False,
            "three_batter_minimum": False,
        },
        "teams": {
            "away": canonical_team("away", "Visitors", "VIS"),
            "home": canonical_team("home", "Hosts", "HST"),
        },
    }


def canonical_team(prefix: str, name: str, short_name: str) -> dict:
    roster = []
    lineup = []
    for slot, position in enumerate(POSITIONS, start=1):
        player_id = f"{prefix}-h{slot}"
        roster.append({
            "player_id": player_id,
            "source_player_id": slot,
            "name": f"{name} Hitter {slot}",
            "role": "position_player",
            "positions": [position],
            "bats": "S" if slot == 1 else "R",
            "throws": "R",
            "bt": 30,
            "obt": 39,
            "traits": ["P+"] if slot == 1 else [],
        })
        lineup.append({"slot": slot, "player_id": player_id, "position": position})
    roster.extend([
        {
            "player_id": f"{prefix}-sp",
            "name": f"{name} Starter",
            "role": "starter",
            "positions": ["P"],
            "throws": "R",
            "pitch_die": "d8",
            "traits": ["K+"],
        },
        {
            "player_id": f"{prefix}-rp",
            "name": f"{name} Reliever",
            "role": "reliever",
            "positions": ["P"],
            "throws": "L",
            "pitch_die": "d4",
            "traits": [],
        },
        {
            "player_id": f"{prefix}-bench",
            "name": f"{name} Bench",
            "role": "position_player",
            "positions": ["UT"],
            "bats": "L",
            "throws": "R",
            "bt": 25,
            "obt": 34,
            "traits": [],
        },
    ])
    return {
        "team_id": f"team-{prefix}",
        "name": name,
        "short_name": short_name,
        "lineup": lineup,
        "roster": roster,
        "starting_pitcher_id": f"{prefix}-sp",
    }


def test_valid_game_round_trips_and_initializes_offline(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")))
    game = load_generated_game(json.dumps(canonical_game()))
    restored = load_generated_game(game.to_json())
    state = initialize_game(restored)

    assert state.inning == 1
    assert state.half == "top"
    assert state.outs == 0
    assert state.bases == (None, None, None)
    assert state.away.lineup[0] == "away-h1"
    assert state.away.active_pitcher_id == "away-sp"
    assert {item.position for item in state.away.active_defense} == {"P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"}
    assert state.away.bench == ("away-bench",)
    assert state.away.bullpen == ("away-rp",)
    with pytest.raises(FrozenInstanceError):
        state.inning = 2


def test_player_mlb_stats_and_source_game_appearance_round_trip():
    data = canonical_game()
    bench = data["teams"]["away"]["roster"][-1]
    bench["appeared_in_source_game"] = True
    bench["mlb_stats"] = {
        "games": 81,
        "average": 0.275,
        "on_base_percentage": 0.351,
        "home_runs": 9,
        "doubles": 14,
        "stolen_bases": 3,
    }

    game = load_generated_game(data)
    restored = load_generated_game(game.to_json())
    player = restored.teams.away.player("away-bench")

    assert player.appeared_in_source_game is True
    assert player.mlb_stats.games == 81
    assert player.mlb_stats.average == 0.275


def test_optional_rules_default_enabled_when_fields_are_absent():
    data = canonical_game()
    del data["rules"]["oddities"]
    del data["rules"]["three_batter_minimum"]

    game = load_generated_game(data)

    assert game.rules.oddities is True
    assert game.rules.three_batter_minimum is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["teams"]["away"]["lineup"].pop(), "slots must be exactly 1 through 9"),
        (lambda data: data["teams"]["away"]["roster"][0].update(bt=None), "requires bats, bt, and obt"),
        (lambda data: data["teams"]["home"]["roster"][9].update(pitch_die="d6"), "pitch_die must be one of"),
        (lambda data: data["teams"]["home"]["roster"][0].update(traits=["MAGIC+"]), "unknown trait"),
    ],
)
def test_invalid_game_data_fails_clearly(change, message):
    data = canonical_game()
    change(data)
    with pytest.raises(GameDataError, match=message):
        load_generated_game(data)


def test_legacy_generator_payload_adapts_to_contract_and_state():
    rows = [*generator_rows("Visitors", 100), *generator_rows("Hosts", 200)]
    context = GeneratorGameContext(
        game_id="123",
        game_date="2026-08-15",
        away_team_name="Visitors",
        away_team_short="VIS",
        home_team_name="Hosts",
        home_team_short="HST",
    )

    game = adapt_generator_game({"players": rows, "teams": {}, "meta": {}}, context)
    state = initialize_game(game)

    assert game.teams.away.lineup[0].player_id == "mlb-101"
    assert game.teams.away.starting_pitcher_id == "mlb-199"
    assert game.teams.away.player("mlb-101").traits == ("P-",)
    assert game.teams.away.player("mlb-101").mlb_stats.average == 0.271
    assert game.teams.away.player("mlb-110").appeared_in_source_game is True
    assert game.teams.away.player("mlb-199").mlb_stats.era == 3.42
    assert state.away.bench == ("mlb-110",)
    assert state.away.bullpen == ("mlb-198",)


def test_web_generator_metadata_builds_contract_and_accepts_role_starter():
    rows = [*generator_rows("Visitors", 100), *generator_rows("Hosts", 200)]
    for row in rows:
        if row.get("GameStarted"):
            row.pop("GameStarted")
            row["Role"] = "starter"
    game = build_generator_game(
        {
            "players": rows,
            "teams": {"away_abbr": "VIS", "home_abbr": "HST"},
        },
        game_id="123",
        game_date="2026-08-15",
        away_team="Visitors",
        home_team="Hosts",
        away_short="Visitors",
        home_short="Hosts",
        game_type="R",
    )

    assert game.game.game_id == "mlb-123"
    assert game.game.game_type == "R"
    assert game.teams.away.short_name == "VIS"
    assert game.teams.home.short_name == "HST"
    assert game.teams.away.starting_pitcher_id == "mlb-199"
    assert game.rules.oddities is True
    assert game.rules.three_batter_minimum is True


def test_legacy_adapter_requires_explicit_present_starter():
    rows = [*generator_rows("Visitors", 100), *generator_rows("Hosts", 200)]
    context = GeneratorGameContext(
        game_id="123",
        game_date="2026-08-15",
        away_team_name="Visitors",
        away_team_short="VIS",
        home_team_name="Hosts",
        home_team_short="HST",
        away_starting_pitcher_id=999,
        home_starting_pitcher_id=299,
    )
    with pytest.raises(GameDataError, match="starting pitcher mlb-999 is absent"):
        adapt_generator_game({"players": rows}, context)


def test_non_dh_contract_requires_batting_ratings_for_every_pitcher():
    data = canonical_game()
    data["rules"]["designated_hitter"] = False
    for side in ("away", "home"):
        team = data["teams"][side]
        team["lineup"][8] = {
            "slot": 9,
            "player_id": f"{side}-sp",
            "position": "P",
        }
        for player in team["roster"]:
            if player["role"] == "starter":
                player.update({"bats": "R", "bt": 10, "obt": 15})

    with pytest.raises(GameDataError, match="non-DH pitcher away-rp"):
        load_generated_game(data)


def generator_rows(team: str, base: int) -> list[dict]:
    rows = []
    for slot, position in enumerate(POSITIONS, start=1):
        rows.append({
            "IDmlb": base + slot,
            "Type": "Hitter",
            "Team": team,
            "BatOrder": str(slot),
            "Name": f"{team} Hitter {slot}",
            "Pos": position,
            "Positions": position,
            "Hand": "L" if slot == 1 else "R",
            "Throws": "R",
            "BT": "30",
            "OBT": "39",
            "Traits": "P−" if slot == 1 else "",
            "G": 120,
            "AVG": 0.271,
            "OBP": 0.344,
            "HR": 18,
            "2B": 24,
            "SB": 7,
        })
    rows.extend([
        {
            "IDmlb": base + 10,
            "Type": "Hitter",
            "Team": team,
            "BatOrder": "1.01",
            "Name": f"{team} Substitute",
            "Pos": "UT",
            "Positions": "UT",
            "Hand": "S",
            "Throws": "R",
            "BT": 26,
            "OBT": 35,
            "Traits": "S+",
        },
        {
            "IDmlb": base + 99,
            "Type": "Pitcher",
            "Team": team,
            "Name": f"{team} Starter",
            "Pos": "P",
            "Positions": "P",
            "Throws": "R",
            "PD": "d8",
            "GameStarted": True,
            "GS": 25,
            "IP": 142.0,
            "ERA": 3.42,
            "K/9": 9.8,
            "BB/9": 2.7,
            "GB%": 44.1,
            "Traits": "K+",
        },
        {
            "IDmlb": base + 98,
            "Type": "Pitcher",
            "Team": team,
            "Name": f"{team} Reliever",
            "Pos": "P",
            "Positions": "P",
            "Throws": "L",
            "PD": "d4",
            "Traits": "CN+",
        },
    ])
    return rows
