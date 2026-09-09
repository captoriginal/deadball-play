"""Offline contract tests for game/roster rules plumbing."""
import argparse
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from deadball_generator import career, deadball_api, generator, roster_api, rules
from deadball_generator.cli import game
from deadball_generator.stats_fetchers import team_stats


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    request = Mock(side_effect=AssertionError("Live requests forbidden"))
    monkeypatch.setattr(requests.sessions.Session, "request", request)
    monkeypatch.setattr(team_stats, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(game, "CACHE_HTML_DIR", tmp_path / "http")
    yield
    request.assert_not_called()


@pytest.fixture
def caches(monkeypatch, tmp_path):
    for module, name, path in (
        (game, "SEASON_DIR", tmp_path / "season"),
        (game, "LEGACY_DEADBALL_DIR", tmp_path / "legacy"),
        (team_stats, "DEADBALL_DIR", tmp_path / "season"),
        (team_stats, "STAT_DIR", tmp_path / "raw"),
    ):
        path.mkdir(exist_ok=True)
        monkeypatch.setattr(module, name, path)
    monkeypatch.setattr(team_stats, "fetch_postseason", Mock(side_effect=AssertionError("No postseason fetch")))
    return tmp_path


def rated_row(**overrides):
    return {
        "Name": "Source Name", "Type": "Hitter", "IDmlb": 10,
        "BT": "30", "OBT": "40", "Traits": "P+", "Hand": "L", "Throws": "R",
        "RatingNotes": '{"source": "career"}', "Provisional": False,
        "RatingSource": "career", "RulesVersion": rules.RULES_VERSION,
        "TraitMode": "standard", "Role": "batter",
        "SnapshotAt": 1735689600, "CacheStale": False, **overrides,
    }


def write_generated(rows):
    path = team_stats.deadball_paths("LAD", 2024)[0]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_raw():
    for path in team_stats.stat_paths("LAD", 2024):
        pd.DataFrame([{"Name": "Raw Player"}]).to_csv(path, index=False)


def test_postseason_uses_valid_regular_cache(caches, monkeypatch):
    write_generated([rated_row()])
    pd.DataFrame([rated_row(Traits="wrong")]).to_csv(
        game.SEASON_DIR / "lad_2024_deadball_postseason.csv", index=False,
    )
    builder = Mock(side_effect=AssertionError("Should reuse current regular cache"))
    monkeypatch.setattr(team_stats, "build_deadball_regular", builder)
    result = game.load_deadball_source("LAD", 2024, postseason=True, allow_fetch=False)
    assert result.iloc[0].Traits == "P+"
    builder.assert_not_called()


@pytest.mark.parametrize("stale", [
    {"RulesVersion": "old"}, {"TraitMode": "sabr"}, {"RulesVersion": None},
])
def test_every_row_must_match_and_offline_raw_rebuild(caches, monkeypatch, stale):
    write_generated([rated_row(), rated_row(IDmlb=11, **stale)])
    write_raw()
    fetch = Mock(side_effect=AssertionError("No offline fetch"))
    monkeypatch.setattr(team_stats, "fetch_regular", fetch)
    builder = Mock(side_effect=lambda *a, **kw: write_generated([rated_row(Traits="rebuilt")]))
    monkeypatch.setattr(team_stats, "build_deadball_regular", builder)
    result = game.load_deadball_source("LAD", 2024, True, allow_fetch=False)
    assert result.iloc[0].Traits == "rebuilt"
    builder.assert_called_once_with(
        "LAD", 2024, trait_mode="standard", allow_network=False,
        refresh=False, rate_limit_seconds=0.0,
    )
    fetch.assert_not_called()


@pytest.mark.parametrize("allow_fetch", [False, True])
def test_refresh_bypasses_generated_cache(caches, monkeypatch, allow_fetch):
    write_generated([rated_row()])
    write_raw()
    fetch = Mock()
    monkeypatch.setattr(team_stats, "fetch_regular", fetch)
    builder = Mock(side_effect=lambda *a, **kw: write_generated([rated_row(Traits="fresh")]))
    monkeypatch.setattr(team_stats, "build_deadball_regular", builder)
    result = game.load_deadball_source("LAD", 2024, refresh=True, allow_fetch=allow_fetch)
    assert result.iloc[0].Traits == "fresh"
    assert fetch.call_count == int(allow_fetch)
    assert builder.call_args.kwargs["refresh"] is True
    assert builder.call_args.kwargs["allow_network"] is allow_fetch


def test_missing_raw_fetches_regular_only(caches, monkeypatch):
    fetch = Mock(side_effect=lambda *a, **kw: write_raw())
    monkeypatch.setattr(team_stats, "fetch_regular", fetch)
    builder = Mock(side_effect=lambda *a, **kw: write_generated([rated_row(TraitMode="adaptive")]))
    monkeypatch.setattr(team_stats, "build_deadball_regular", builder)
    game.load_deadball_source("LAD", 2024, True, trait_mode="adaptive", rate_limit_seconds=.25)
    fetch.assert_called_once_with("LAD", 2024, rate_limit_seconds=.25, refresh=False)
    assert builder.call_args.kwargs["trait_mode"] == "adaptive"


def test_rebuild_validates_existing_raw_when_online(caches, monkeypatch):
    write_raw()
    write_generated([rated_row(RulesVersion="old")])
    events = []
    monkeypatch.setattr(team_stats, "fetch_regular", Mock(side_effect=lambda *a, **kw: events.append("validate raw")))
    def build(*args, **kwargs):
        events.append("build")
        write_generated([rated_row()])
    monkeypatch.setattr(team_stats, "build_deadball_regular", build)
    game.load_deadball_source("LAD", 2024)
    assert events == ["validate raw", "build"]
    events.clear()
    roster_api.convert_roster_from_season("LAD", 2024)
    assert events == ["validate raw", "build"]


def test_unversioned_cache_is_not_used_offline(caches):
    pd.DataFrame([{"Name": "Old Player", "Traits": "P++"}]).to_csv(
        game.SEASON_DIR / "lad_2024_deadball.csv", index=False,
    )
    with pytest.raises(FileNotFoundError, match="raw stats unavailable"):
        game.load_deadball_source("LAD", 2024, allow_fetch=False)


def test_actual_builder_rebuilds_raw_offline_and_switches_modes(caches, monkeypatch):
    bat_path, pit_path = team_stats.stat_paths("LAD", 2024)
    pd.DataFrame([{
        "Name": "Offline Hitter", "IDmlb": 10, "Pos": "SS", "PA": 400, "StatsVersion": rules.RULES_VERSION,
        "AVG": .300, "OBP": .400, "HR": 30, "2B": 40, "SB": 25, "G": 120,
    }]).to_csv(bat_path, index=False)
    pd.DataFrame([{
        "Name": "Offline Pitcher", "IDmlb": 20, "Pos": "P", "IP": 80, "StatsVersion": rules.RULES_VERSION,
        "ERA": 3.5, "K/9": 11, "BB/9": 1.5, "GB%": .60, "G": 70, "GS": 0,
    }]).to_csv(pit_path, index=False)
    monkeypatch.setattr(team_stats, "_enrich_regular_hands", Mock(side_effect=AssertionError("Offline hand fetch")))
    for mode in ("standard", "sabr", "adaptive"):
        result = game.load_deadball_source("LAD", 2024, True, allow_fetch=False, trait_mode=mode)
        assert result.TraitMode.eq(mode).all()
        assert result.RulesVersion.eq(rules.RULES_VERSION).all()
        assert list(result.IDmlb) == [10, 20]
        assert result.iloc[1].Role == "reliever"
    roster = roster_api.convert_roster_from_season("LAD", 2024, allow_network=False)
    assert len(roster["players"]) == 2
    assert "nan" not in roster["players"][0]["traits"]


def player(pid=10, name="Boxscore Name", pitching=False, season_stats=None):
    return {
        "person": {"id": pid, "fullName": name},
        "position": {"abbreviation": "P" if pitching else "SS"},
        "batSide": {"code": "L"}, "pitchHand": {"code": "R"},
        "battingOrder": None if pitching else "100",
        "stats": {"pitching" if pitching else "batting": {
            "inningsPitched": "9.0", "earnedRuns": 0, "strikeOuts": 20,
            "homeRuns": 4, "doubles": 4, "stolenBases": 4,
        }},
        "seasonStats": {"pitching" if pitching else "batting": season_stats or {}},
    }


def build_box(tmp_path, monkeypatch, players, sources, **kwargs):
    box = {"teams": {"home": {
        "team": {"name": "Los Angeles Dodgers", "abbreviation": "LAD"},
        "players": {str(i): p for i, p in enumerate(players)},
    }}}
    path = tmp_path / "box.json"
    path.write_text(json.dumps(box))
    loader = Mock(return_value=pd.DataFrame(sources))
    monkeypatch.setattr(game, "load_deadball_source", loader)
    monkeypatch.setattr(game, "mlb_game_type", Mock(side_effect=AssertionError("Unneeded schedule fetch")))
    monkeypatch.setattr(team_stats, "hands_from_names", Mock(side_effect=AssertionError("Offline hand fetch")))
    df, _ = game.build_deadball_for_game(
        "2024-10-20", "LAD", box_file=path, no_fetch=True, auto_postseason=True, **kwargs,
    )
    assert loader.call_args.kwargs["postseason"] is False
    return df


def test_id_lookup_wins_and_metadata_survives(tmp_path, monkeypatch):
    source = rated_row()
    result = build_box(tmp_path, monkeypatch, [player()], [
        source, rated_row(Name="Boxscore Name", IDmlb=999, BT="99"),
    ])
    row = result.iloc[0]
    assert row.BT == "30" and row.Name == "Boxscore Name"
    for key in game.RATING_METADATA:
        assert row[key] == source[key]


@pytest.mark.parametrize("legacy", [False, True])
def test_name_lookup_only_for_sources_without_id(tmp_path, monkeypatch, legacy):
    result = build_box(tmp_path, monkeypatch, [player()], [
        rated_row(Name="Boxscore Name", IDmlb=None if legacy else 999),
    ])
    row = result.iloc[0]
    assert row.IDmlb == 10
    assert bool(row.Provisional) is not legacy
    assert row.Traits == ("P+" if legacy else "")


@pytest.mark.parametrize("mode", rules.TRAIT_MODES)
def test_missing_hitter_uses_season_sample_gating(tmp_path, monkeypatch, mode):
    result = build_box(tmp_path, monkeypatch, [player(season_stats={
        "plateAppearances": 20, "avg": ".250", "obp": ".350", "homeRuns": 4,
        "doubles": 4, "stolenBases": 0,
    })], [], trait_mode=mode)
    row = result.iloc[0]
    assert row.BT == "25" and row.OBT == "35"
    assert row.Traits == "" and row.Provisional
    assert row.RulesVersion == rules.RULES_VERSION and row.TraitMode == mode
    assert json.loads(row.RatingNotes)["provisional"] is True


@pytest.mark.parametrize("season_stats,expected_pd", [
    ({}, None),
    ({"inningsPitched": "20.2", "era": "6.50", "strikeOuts": 50, "baseOnBalls": 1}, "-d8"),
])
def test_missing_pitcher_never_uses_single_game_era(tmp_path, monkeypatch, season_stats, expected_pd):
    result = build_box(tmp_path, monkeypatch, [player(pitching=True, season_stats=season_stats)], [])
    row = result.iloc[0]
    assert row.PD == expected_pd
    assert row.Traits == "" and row.Provisional
    if season_stats:
        assert row.IP == pytest.approx(20 + 2 / 3)


def test_pitcher_metadata_and_role_survive(tmp_path, monkeypatch):
    source = rated_row(Type="Pitcher", PD="d8", Role="reliever")
    result = build_box(tmp_path, monkeypatch, [player(pitching=True)], [source])
    assert result.iloc[0].PD == "d8"
    for key in game.RATING_METADATA:
        assert result.iloc[0][key] == source[key]


def test_game_rows_preserve_starting_position_and_pitcher(tmp_path, monkeypatch):
    hitter = player()
    hitter["position"] = {"abbreviation": "RF"}
    hitter["allPositions"] = [{"abbreviation": "LF"}, {"abbreviation": "RF"}]
    pitcher = player(pitching=True)
    pitcher["stats"]["pitching"]["gamesStarted"] = 1

    result = build_box(tmp_path, monkeypatch, [hitter, pitcher], [])

    assert result[result.Type == "Hitter"].iloc[0].Pos == "LF"
    assert bool(result[result.Type == "Pitcher"].iloc[0].GameStarted) is True


def test_missing_player_recovers_cached_career_offline(tmp_path, monkeypatch):
    cache_dir = team_stats.CACHE_ROOT / "career"
    cache_dir.mkdir(parents=True)
    payload = {"stats": [{"group": {"displayName": "hitting"}, "splits": [{
        "season": "2023", "stat": {
            "gamesPlayed": 150, "plateAppearances": 600, "atBats": 500, "hits": 150,
            "doubles": 40, "triples": 0, "homeRuns": 30, "stolenBases": 25,
            "baseOnBalls": 80, "strikeOuts": 50, "hitByPitch": 10, "sacFlies": 10,
        },
    }]}]}
    (cache_dir / f"mlb-10-2024-v{career.CACHE_VERSION}.json").write_text(json.dumps({
        "version": career.CACHE_VERSION, "fetched_at": 1735689600, "payload": payload,  # 2025-01-01: completed 2024 snapshot.
    }))
    row = build_box(tmp_path, monkeypatch, [player(season_stats={"plateAppearances": 10})], []).iloc[0]
    assert row.BT == "30"
    assert row.RatingSource == "career" and not row.Provisional
    assert "P+" in row.Traits and "T+" in row.Traits
    assert "recovered MLB" in json.loads(row.RatingNotes)["reasons"]["source"]


def test_missing_player_fetches_history_once_for_both_roles(tmp_path, monkeypatch):
    # Cached team ratings can omit a call-up who both bats and pitches.
    two_way = player(season_stats={"plateAppearances": 10})
    two_way["stats"]["pitching"] = {"inningsPitched": "1.0"}
    path = tmp_path / "two-way.json"
    path.write_text(json.dumps({"teams": {"home": {
        "team": {"name": "Dodgers", "abbreviation": "LAD"},
        "players": {"10": two_way}, "batters": [10], "pitchers": [10],
    }}}))
    monkeypatch.setattr(game, "load_deadball_source", Mock(return_value=pd.DataFrame()))
    response = Mock()
    response.json.return_value = {"people": [{"id": 10, "stats": [{
        "group": {"displayName": "pitching"}, "splits": [{"season": "2023", "stat": {
            "inningsPitched": "100.0", "earnedRuns": 30, "strikeOuts": 120,
            "baseOnBalls": 20, "gamesPlayed": 25, "gamesStarted": 25,
        }}],
    }]}]}
    fetch = Mock(return_value=response)
    monkeypatch.setattr(game, "_fetch_with_rate_limit", fetch)
    result, _ = game.build_deadball_for_game(
        "2024-10-20", "LAD", box_file=path, rate_limit_seconds=.1, refresh=True,
    )
    assert len(result) == 2
    fetch.assert_called_once()
    assert "/api/v1/people?" in fetch.call_args.args[0]
    assert "personIds=10" in fetch.call_args.args[0]
    assert "yearByYear" in fetch.call_args.args[0]
    assert fetch.call_args.kwargs == {"refresh_cache": True, "allow_network": True}
    pitcher = result[result.Type == "Pitcher"].iloc[0]
    assert pitcher.PD == "d12" and pitcher.RatingSource == "career"


def test_offline_missing_hands_stay_unknown(tmp_path, monkeypatch):
    entry = player()
    entry.pop("batSide")
    entry.pop("pitchHand")
    row = build_box(tmp_path, monkeypatch, [entry], []).iloc[0]
    assert pd.isna(row.Hand) and pd.isna(row.Throws)


def test_game_json_metadata_and_mode_forwarding(monkeypatch):
    captured_paths = []

    def build(**kwargs):
        captured_paths.append(Path(kwargs["box_file"]))
        assert json.loads(captured_paths[0].read_text()) == {"teams": {}}
        return pd.DataFrame([rated_row(TraitMode="sabr")]), {"home_team": "Dodgers"}

    builder = Mock(side_effect=build)
    monkeypatch.setattr(deadball_api, "build_deadball_for_game", builder)
    result = generator.generate_game_from_raw(
        game_id="1", date="2024-10-20", home_team="LAD", away_team=None,
        raw_stats='{"teams": {}}', allow_network=False, trait_mode="sabr",
    )
    assert builder.call_args.kwargs["trait_mode"] == "sabr"
    assert builder.call_args.kwargs["no_fetch"] is True
    assert builder.call_args.kwargs["auto_postseason"] is False
    assert json.loads(result["stats"])["meta"] == {
        "rules_version": rules.RULES_VERSION, "trait_mode": "sabr",
        "rating_basis": "regular-season/career", "snapshot_at": 1735689600, "stale": False,
        "roster_scope": "available",
    }
    assert not captured_paths[0].exists()


@pytest.mark.parametrize("allow_network", [False, True])
def test_roster_adapter_forwards_mode_and_network(caches, monkeypatch, allow_network):
    fetch = Mock(side_effect=lambda *a, **kw: write_raw())
    monkeypatch.setattr(team_stats, "fetch_regular", fetch)
    builder = Mock(side_effect=lambda *a, **kw: write_generated([rated_row(TraitMode="adaptive")]))
    monkeypatch.setattr(team_stats, "build_deadball_regular", builder)
    roster = generator.generate_roster(
        mode="season", payload='{"team": "LAD", "season": 2024}',
        name="Roster", description=None, public=False, trait_mode="adaptive",
        allow_network=allow_network,
    )
    assert len(roster.players) == 1
    assert fetch.call_count == int(allow_network)
    builder.assert_called_once_with(
        "LAD", 2024, trait_mode="adaptive", allow_network=allow_network,
        refresh=False, rate_limit_seconds=0.0,
    )


def test_season_errors_are_not_sample_rosters(monkeypatch):
    monkeypatch.setattr(deadball_api, "convert_roster_from_season", Mock(side_effect=RuntimeError("real error")))
    with pytest.raises(RuntimeError, match="real error"):
        generator.generate_roster(
            mode="season", payload='{"team": "LAD", "season": 2024}',
            name="Roster", description=None, public=False, allow_network=False,
        )
    with pytest.raises(ValueError):
        deadball_api.convert_roster("season", "invalid")


def test_shared_rules_helpers_and_cli_modes():
    assert game.batter_traits is rules.batter_traits
    assert game.pitcher_traits is rules.pitcher_traits
    assert game.pitcher_die is rules.pitcher_die
    assert game.fmt_two_digit is rules.target
    parser = argparse.ArgumentParser()
    game.configure_parser(parser)
    for mode in rules.TRAIT_MODES:
        assert parser.parse_args(["--date", "2024-10-20", "--team", "LAD", "--trait-mode", mode]).trait_mode == mode
    with pytest.raises(ValueError, match="trait mode"):
        game.build_deadball_for_game("2024-10-20", "LAD", trait_mode="invalid")
    with pytest.raises(ValueError, match="trait mode"):
        roster_api.convert_roster_from_season("LAD", 2024, trait_mode="invalid")
