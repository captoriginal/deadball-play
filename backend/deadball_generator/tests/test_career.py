"""Raw MLB annual fixtures and fully offline career/cache contract tests."""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import json
import pytest
import requests

from deadball_generator import career, rules


def split(year, stat, team=135, position=None, sport=1):
    result = {"season": str(year), "stat": stat, "sport": {"id": sport}}
    if team is not None:
        result["team"] = {"id": team}
    if position is not None:
        result["position"] = {"abbreviation": position}
    return result


def block(group, splits):
    return {"group": {"displayName": group}, "totalSplits": len(splits), "splits": splits}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    network = Mock(side_effect=AssertionError("Live network is forbidden"))
    monkeypatch.setattr(requests.sessions.Session, "request", network)
    yield
    network.assert_not_called()


@pytest.fixture
def payload():
    # Include HBP and SF: OBP cannot be reconstructed from H/AB/BB alone.
    bat_2023 = {"gamesPlayed": 80, "plateAppearances": 320, "atBats": 280,
        "hits": 84, "doubles": 16, "triples": 4, "homeRuns": 8, "stolenBases": 4,
        "baseOnBalls": 28, "strikeOuts": 64, "hitByPitch": 4, "sacFlies": 8}
    bat_2024 = {"gamesPlayed": 160, "plateAppearances": 640, "atBats": 560,
        "hits": 140, "doubles": 20, "triples": 2, "homeRuns": 20, "stolenBases": 16,
        "baseOnBalls": 60, "strikeOuts": 160, "hitByPitch": 8, "sacFlies": 12}
    return {"stats": [
        block("hitting", [split(2023, bat_2023), split(2024, bat_2024)]),
        block("pitching", [
            split(2023, {"inningsPitched": "49.2", "earnedRuns": 20, "strikeOuts": 40,
                         "baseOnBalls": 10, "gamesPlayed": 20, "gamesStarted": 5}),
            split(2024, {"inningsPitched": "50.1", "earnedRuns": 30, "strikeOuts": 60,
                         "baseOnBalls": 20, "gamesPlayed": 25, "gamesStarted": 0}),
        ]),
        block("fielding", [
            split(2023, {"putOuts": 90, "assists": 5, "errors": 5, "gamesPlayed": 80}, position="CF"),
            split(2024, {"putOuts": 900, "assists": 90, "errors": 10, "gamesPlayed": 160}, position="C"),
        ]),
    ]}


@pytest.mark.parametrize("value,expected", [("0.0", 0), ("0.1", 1 / 3), ("0.2", 2 / 3),
    ("49.2", 49 + 2 / 3), ("50", 50), (50, 50), ("50.0", 50), ("50.1", 50 + 1 / 3),
    (None, None), ("", None), ("--", None), ("49.3", None), (float("nan"), None)])
def test_innings_parses_outs_not_decimal_tenths(value, expected):
    result = career.innings(value)
    assert result is None if expected is None else result == pytest.approx(expected)


def test_career_rates_are_weighted_totals_and_counts_are_per_162(payload):
    summary = career.summarize(payload, 2024, current_year=2026)
    hitter = summary["hitter"]
    assert hitter["PA"] == 960 and hitter["G"] == 240
    assert hitter["AVG"] == pytest.approx(224 / 840)
    assert hitter["OBP"] == pytest.approx((224 + 88 + 12) / (840 + 88 + 12 + 20))
    assert hitter["SLG"] == pytest.approx((224 + 36 + 2 * 6 + 3 * 28) / 840)
    assert hitter["ISO"] == pytest.approx(132 / 840)
    assert hitter["K%"] == pytest.approx(224 / 960)
    assert hitter["HR"] == pytest.approx(28 / 240 * 162)
    assert hitter["2B"] == pytest.approx(36 / 240 * 162)
    assert hitter["SB"] == pytest.approx(20 / 240 * 162)
    assert hitter["CareerAverageG"] == 120
    assert hitter["FP"] == pytest.approx(1085 / 1100)
    assert summary["season_hitter"]["HR"] == 20  # no annual per-162 projection
    assert summary["season_fielding"]["Pos"] == "C"
    pitcher = summary["pitcher"]
    assert pitcher["IP"] == pytest.approx(100)
    assert pitcher["ERA"] == pytest.approx(4.5)
    assert pitcher["K/9"] == pytest.approx(9)
    assert pitcher["BB/9"] == pytest.approx(2.7)
    assert summary["season_pitcher"]["IP"] == pytest.approx(50 + 1 / 3)


def test_completed_seasons_exclude_current_future_but_include_short_seasons(payload):
    template = payload["stats"][0]["splits"][0]["stat"]
    payload["stats"][0] = block("hitting", [
        split(2020, {**template, "gamesPlayed": 50}),  # short schedule
        split(2024, {**template, "gamesPlayed": 10}),  # partial debut/injury
        split(2025, {**template, "gamesPlayed": 160}),
        split(2026, {**template, "gamesPlayed": 162}),
        split(2027, {**template, "gamesPlayed": 162}),
    ])
    summary = career.summarize(payload, 2027, current_year=2026)
    assert summary["hitter"]["CareerAverageG"] == pytest.approx(220 / 3)
    assert "T+" not in rules.batter_traits({"PA": 250, "Pos": "C"}, career=summary["hitter"])


def test_only_current_season_has_no_completed_season_average(payload):
    payload["stats"][0]["splits"] = [payload["stats"][0]["splits"][1]]
    assert career.summarize(payload, 2024, current_year=2024)["hitter"]["CareerAverageG"] is None


def test_missing_completed_season_games_make_average_unknown(payload):
    del payload["stats"][0]["splits"][0]["stat"]["gamesPlayed"]
    hitter = career.summarize(payload, 2024, current_year=2026)["hitter"]
    assert hitter["CareerAverageG"] is None
    assert hitter["HR"] is None  # Cannot project per-162 without complete G.
    assert "T+" not in rules.batter_traits({"PA": 250, "Pos": "C"}, career=hitter)


def test_as_of_year_excludes_later_stats_and_minor_leagues(payload):
    original = career.summarize(payload, 2023, current_year=2026)
    payload["stats"][0]["splits"].append(split(2023, {"gamesPlayed": 999}, sport=11))
    payload["stats"][0]["splits"].append({"season": "bad", "stat": {}})
    payload["stats"].append(block("unrelated", [split(2023, {"gamesPlayed": 999})]))
    assert career.summarize(payload, 2023, current_year=2026) == original
    assert original["hitter"]["G"] == 80
    assert original["pitcher"]["IP"] == pytest.approx(49 + 2 / 3)


@pytest.mark.parametrize("with_total", [False, True])
def test_traded_hitter_stints_total_and_repeated_blocks_are_not_double_counted(payload, with_total):
    total = payload["stats"][0]["splits"][0]["stat"]
    half = {key: value // 2 for key, value in total.items()}
    stints = [split(2023, half, team=135), split(2023, half, team=119)]
    if with_total:
        stints.insert(0, {**split(2023, total, team=None), "numTeams": 2})
    annual = block("hitting", stints)
    row = career.annual_rows({"stats": [annual, deepcopy(annual)]}, "hitting", 2023)[2023]
    assert row["G"] == 80 and row["PA"] == 320
    assert row["HR"] == 8
    assert row["AVG"] == pytest.approx(.3)
    assert row["OBP"] == pytest.approx(116 / 320)
    assert row["Stints"] == 2


@pytest.mark.parametrize("with_total", [False, True])
def test_traded_pitcher_sums_outs_before_calculating_rates(with_total):
    stat = {"earnedRuns": 10, "strikeOuts": 30, "baseOnBalls": 5, "gamesPlayed": 10, "gamesStarted": 0}
    stints = [split(2024, {**stat, "inningsPitched": "24.2"}, team=135),
              split(2024, {**stat, "inningsPitched": "25.1"}, team=119)]
    if with_total:
        stints.append(split(2024, {**{k: v * 2 for k, v in stat.items()}, "inningsPitched": "50.0"}, team=None))
    row = career.annual_rows({"stats": [block("pitching", stints)]}, "pitching", 2024)[2024]
    assert row["IP"] == pytest.approx(50)
    assert row["ERA"] == pytest.approx(3.6)
    assert row["K/9"] == pytest.approx(10.8)
    assert row["BB/9"] == pytest.approx(1.8)


@pytest.mark.parametrize("with_totals", [False, True])
def test_fielding_traded_stints_and_combined_totals_count_each_position_once(with_totals):
    first = {"putOuts": 900, "assists": 50, "errors": 10, "gamesPlayed": 100}
    second = {"putOuts": 200, "assists": 300, "errors": 20, "gamesPlayed": 40}
    splits = []
    for position, total in (("1B", first), ("2B", second)):
        half = {key: value // 2 for key, value in total.items()}
        splits.extend(split(2024, half, team=team, position=position) for team in (135, 119))
        if with_totals:
            splits.append({**split(2024, total, team=None, position=position), "numTeams": 2})
    annual = block("fielding", splits)
    row = career.annual_rows({"stats": [annual, deepcopy(annual)]}, "fielding", 2024)[2024]
    assert row["FP"] == pytest.approx(1450 / 1480)
    assert row["PO"] == 1100 and row["A"] == 350 and row["E"] == 30
    assert row["Pos"] == "1B"
    assert row["Stints"] == 2


def test_total_only_uses_split_level_num_teams(payload):
    total = payload["stats"][0]["splits"][0]["stat"]
    payload = {"stats": [block("hitting", [{**split(2023, total, team=None), "numTeams": 3}])]}
    assert career.annual_rows(payload, "hitting", 2023)[2023]["Stints"] == 3


def test_fielding_omits_of_aggregate_when_detailed_positions_exist():
    stat = {"putOuts": 90, "assists": 5, "errors": 5, "gamesPlayed": 50}
    rows = [split(2024, stat, position="CF"), split(2024, stat, position="LF"),
            split(2024, {k: v * 2 for k, v in stat.items()}, position="OF"),
            split(2024, {}, position="DH")]
    row = career.annual_rows({"stats": [block("fielding", rows)]}, "fielding", 2024)[2024]
    assert row["PO"] == 180 and row["E"] == 10
    assert row["FP"] == pytest.approx(.95)


@pytest.mark.parametrize("missing,metrics", [("hitByPitch", ["OBP"]), ("sacFlies", ["OBP"]),
    ("homeRuns", ["HR", "SLG", "ISO"]), ("stolenBases", ["SB"]),
    ("strikeOuts", ["K%"]), ("plateAppearances", ["PA", "K%"]), ("hits", ["AVG", "OBP", "SLG", "ISO"])])
def test_missing_annual_counts_propagate_unknown_not_zero(payload, missing, metrics):
    del payload["stats"][0]["splits"][0]["stat"][missing]
    hitter = career.summarize(payload, 2024, current_year=2026)["hitter"]
    for metric in metrics:
        assert hitter[metric] is None


def test_missing_pitching_and_fielding_values_do_not_become_good_ratings(payload):
    del payload["stats"][1]["splits"][0]["stat"]["baseOnBalls"]
    del payload["stats"][2]["splits"][0]["stat"]["errors"]
    summary = career.summarize(payload, 2024, current_year=2026)
    assert summary["pitcher"]["BB/9"] is None
    assert summary["hitter"]["FP"] is None
    assert "CN+" not in rules.pitcher_traits({"IP": 10}, career=summary["pitcher"])


def test_zero_denominators_are_unassessed():
    assert all(value is None for value in career.hitting_rates({k: 0 for k in ("PA", "AB", "H", "BB", "HBP", "SF", "2B", "3B", "HR", "SO")}).values())
    assert career.fielding_percentage({"PO": 0, "A": 0, "E": 0}) is None


def response_for(payload):
    response = Mock()
    response.json.return_value = payload
    return response


def cache_path(tmp_path, season):
    return tmp_path / f"mlb-123-{season}-v{career.CACHE_VERSION}.json"


def seed_cache(tmp_path, payload, season=2024, *, age=0, version=None, fetched_at=None):
    path = cache_path(tmp_path, season)
    path.write_text(json.dumps({"version": career.CACHE_VERSION if version is None else version,
        "fetched_at": datetime.now(timezone.utc).timestamp() - age if fetched_at is None else fetched_at,
        "payload": payload}))
    return path


def test_load_history_success_uses_regular_mlb_history_query_and_caches(tmp_path, payload):
    fetch = Mock(return_value=response_for(payload))
    result = career.load_history(123, 2024, tmp_path, allow_network=True, fetch=fetch)
    assert result == career.summarize(payload, 2024)
    fetch.assert_called_once()
    url = urlparse(fetch.call_args.args[0])
    assert url.path == "/api/v1/people/123/stats"
    assert parse_qs(url.query) == {"stats": ["yearByYear"], "group": ["hitting,pitching,fielding"],
                                 "sportIds": ["1"], "gameType": ["R"]}
    cached = json.loads(cache_path(tmp_path, 2024).read_text())
    assert cached["payload"] == payload and cached["version"] == career.CACHE_VERSION
    fetch.reset_mock()
    assert career.load_history(123, 2024, tmp_path, fetch=fetch) == result
    fetch.assert_not_called()


def test_load_histories_batches_ids_and_preserves_individual_caches(tmp_path, payload):
    def response(url):
        ids = [int(value) for value in parse_qs(urlparse(url).query)["personIds"][0].split(",")]
        return response_for({"people": [
            {"id": player_id, "stats": deepcopy(payload["stats"])} for player_id in ids
        ]})

    fetch = Mock(side_effect=response)
    result = career.load_histories(
        [101, 102, 102, 103], 2024, tmp_path,
        allow_network=True, fetch=fetch, batch_size=2,
    )

    assert set(result) == {101, 102, 103}
    assert fetch.call_count == 2
    first = parse_qs(urlparse(fetch.call_args_list[0].args[0]).query)
    assert first["personIds"] == ["101,102"]
    assert first["hydrate"] == ["stats(group=[hitting,pitching,fielding],type=[yearByYear],sportIds=[1])"]
    for player_id in result:
        assert (tmp_path / f"mlb-{player_id}-2024-v{career.CACHE_VERSION}.json").exists()

    fetch.reset_mock()
    assert career.load_histories([101, 102, 103], 2024, tmp_path, allow_network=True, fetch=fetch) == result
    fetch.assert_not_called()


def test_load_histories_falls_back_only_for_player_missing_from_batch(tmp_path, payload):
    def response(url):
        parsed = urlparse(url)
        if parsed.path == "/api/v1/people":
            return response_for({"people": [{"id": 101, "stats": deepcopy(payload["stats"])}]})
        assert parsed.path == "/api/v1/people/102/stats"
        return response_for(payload)

    fetch = Mock(side_effect=response)
    result = career.load_histories([101, 102], 2024, tmp_path, allow_network=True, fetch=fetch)

    assert all(result.values())
    assert fetch.call_count == 2
    assert urlparse(fetch.call_args_list[1].args[0]).path == "/api/v1/people/102/stats"


def test_default_request_has_timeout(monkeypatch, tmp_path, payload):
    fetch = Mock(return_value=response_for(payload))
    monkeypatch.setattr(career.requests, "get", fetch)
    assert career.load_history(123, 2024, tmp_path, allow_network=True)
    assert fetch.call_args.kwargs == {"timeout": 30}


@pytest.mark.parametrize("season,age,allow_network,expected_fetch", [
    (2024, 172800, True, False), (2026, 0, True, False),
    (2026, 172800, True, True), (2026, 172800, False, False),
])
def test_cache_freshness_historical_vs_current_offline(monkeypatch, tmp_path, payload, season, age, allow_network, expected_fetch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 3)
    monkeypatch.setattr(career, "date", FixedDate)
    seed_cache(tmp_path, payload, season, age=age)
    fetch = Mock(return_value=response_for(payload))
    assert career.load_history(123, season, tmp_path, allow_network=allow_network, fetch=fetch)
    assert fetch.call_count == int(expected_fetch)


@pytest.mark.parametrize("fetched_at,completed", [
    (datetime(2024, 9, 1, tzinfo=timezone.utc).timestamp(), False),
    (datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp(), False),
    (datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp(), True),
], ids=["midseason-2024", "last-second-2024", "completed-snapshot-2025"])
@pytest.mark.parametrize("allow_network", [False, True], ids=["offline", "online"])
def test_historical_cache_requires_snapshot_fetched_after_selected_season(
    monkeypatch, tmp_path, payload, fetched_at, completed, allow_network,
):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 3)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 3, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(career, "date", FixedDate)
    monkeypatch.setattr(career, "datetime", FixedDatetime)
    cached_payload = deepcopy(payload)
    cached_payload["stats"][0]["splits"][1]["stat"]["gamesPlayed"] = 100
    path = seed_cache(tmp_path, cached_payload, season=2024, fetched_at=fetched_at)
    original = path.read_bytes()
    fetch = Mock(return_value=response_for(payload))

    result = career.load_history(123, 2024, tmp_path, allow_network=allow_network, fetch=fetch)

    if not completed and allow_network:
        fetch.assert_called_once()
        assert result == career.summarize(payload, 2024, current_year=2026)
        refreshed = json.loads(path.read_text())
        assert refreshed["payload"] == payload
        assert refreshed["fetched_at"] == datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp()
    else:
        fetch.assert_not_called()
        expected = career.summarize(cached_payload, 2024, current_year=2026)
        assert result == expected
        assert path.read_bytes() == original


@pytest.mark.parametrize("refresh", [False, True])
def test_offline_without_cache_never_fetches(tmp_path, refresh):
    fetch = Mock(side_effect=AssertionError("Unexpected fetch"))
    assert career.load_history(123, 2024, tmp_path, refresh=refresh, fetch=fetch) == {}
    fetch.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid_id", [None, "", "unknown", float("nan"), float("inf")])
def test_unknown_player_id_does_not_fetch(tmp_path, invalid_id):
    fetch = Mock()
    assert career.load_history(invalid_id, 2024, tmp_path, allow_network=True, fetch=fetch) == {}
    fetch.assert_not_called()


@pytest.mark.parametrize("failure", ["http", "timeout", "json", "empty", "truncated", "no-history"])
def test_refresh_failure_returns_no_history_and_preserves_good_cache(tmp_path, payload, failure):
    path = seed_cache(tmp_path, payload)
    original = path.read_bytes()
    response = response_for(payload)
    fetch = Mock(return_value=response)
    if failure == "http":
        response.raise_for_status.side_effect = requests.HTTPError("503")
    elif failure == "timeout":
        fetch.side_effect = requests.Timeout("timeout")
    elif failure == "json":
        response.json.side_effect = ValueError("not JSON")
    elif failure == "empty":
        response.json.return_value = {"stats": []}
    elif failure == "truncated":
        response.json.return_value = deepcopy(payload)
        response.json.return_value["stats"][0]["totalSplits"] = 1000
    else:
        response.json.return_value = {"stats": [block("hitting", [])]}
    assert career.load_history(123, 2024, tmp_path, allow_network=True, refresh=True, fetch=fetch) == {}
    fetch.assert_called_once()
    assert path.read_bytes() == original


@pytest.mark.parametrize("invalid_cache", ["broken json", "null", "[]", "{}"])
def test_corrupt_cache_is_ignored_offline_and_repaired_online(tmp_path, payload, invalid_cache):
    path = cache_path(tmp_path, 2024)
    path.write_text(invalid_cache)
    assert career.load_history(123, 2024, tmp_path) == {}
    fetch = Mock(return_value=response_for(payload))
    assert career.load_history(123, 2024, tmp_path, allow_network=True, fetch=fetch)
    fetch.assert_called_once()
    assert json.loads(path.read_text())["payload"] == payload


def test_wrong_cache_version_is_ignored(tmp_path, payload):
    seed_cache(tmp_path, payload, version=-1)
    assert career.load_history(123, 2024, tmp_path) == {}


@pytest.mark.parametrize("malformed", [{"stats": [None]}, {"stats": [{"group": "hitting", "splits": []}]}])
@pytest.mark.parametrize("source", ["cache", "network"])
def test_malformed_response_shapes_fail_closed(tmp_path, malformed, source):
    if source == "cache":
        seed_cache(tmp_path, malformed)
        result = career.load_history(123, 2024, tmp_path)
    else:
        result = career.load_history(123, 2024, tmp_path, allow_network=True,
                                     fetch=Mock(return_value=response_for(malformed)))
        assert not cache_path(tmp_path, 2024).exists()
    assert result == {}


def test_cache_write_failure_is_nonfatal(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=PermissionError("read-only cache")))
    assert career.load_history(123, 2024, tmp_path, allow_network=True,
                               fetch=Mock(return_value=response_for(payload))) == {}


def test_cache_read_failure_is_nonfatal(monkeypatch, tmp_path, payload):
    seed_cache(tmp_path, payload)
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("unreadable cache")))
    assert career.load_history(123, 2024, tmp_path) == {}


def test_failed_initial_network_request_does_not_cache_failure(tmp_path):
    fetch = Mock(side_effect=requests.ConnectionError("offline"))
    assert career.load_history(123, 2024, tmp_path, allow_network=True, fetch=fetch) == {}
    assert not cache_path(tmp_path, 2024).exists()


def test_rules_use_mocked_load_history_without_fetching(monkeypatch, tmp_path, payload):
    import pandas as pd
    from deadball_generator.stats_fetchers import team_stats

    history = career.summarize(payload, 2024, current_year=2026)
    load = Mock(return_value=history)
    monkeypatch.setattr(career, "load_history", load)
    monkeypatch.setattr(team_stats, "CACHE_ROOT", tmp_path)
    batting = pd.DataFrame([{"IDmlb": 123}, {"IDmlb": None}])
    pitching = pd.DataFrame([{"IDmlb": 123}])
    histories = team_stats._histories(batting, pitching, 2024, allow_network=False,
                                     refresh=False, rate_limit_seconds=0)
    assert set(histories) == {123}  # One fetch shared across both roles; unknown ID skipped.
    fetched = histories[123]
    hitter = rules.evaluate_hitter({"PA": 249, "AVG": .9, "HR": 99}, career=fetched["hitter"])
    pitcher = rules.evaluate_pitcher({"IP": 49 + 2 / 3, "ERA": 0}, career=fetched["pitcher"])
    assert hitter["RatingSource"] == pitcher["RatingSource"] == "career"
    assert hitter["BT"] == "27" and pitcher["PD"] == "d4"
    assert "P++" not in hitter["Traits"]
    load.assert_called_once()
    assert load.call_args.args == (123, 2024, tmp_path / "career")
    assert load.call_args.kwargs["allow_network"] is False
    assert load.call_args.kwargs["refresh"] is False
