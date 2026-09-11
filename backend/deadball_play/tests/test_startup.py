import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from deadball_play.startup import (
    GeneratedArtifacts,
    _input_with_default,
    generate_web_artifacts,
    startup_arguments,
)
from deadball_play import startup
from deadball_play.tui import main


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_date_default_is_visible_in_portable_input_fallback():
    prompts = []

    def fake_input(prompt):
        prompts.append(prompt)
        return ""

    assert _input_with_default(fake_input, "Game date: ", "2026-09-09") == "2026-09-09"
    assert prompts == ["Game date: [2026-09-09]: "]


def test_start_screen_maps_demo_and_manual_resume_choices(tmp_path):
    output = []
    demo = startup_arguments(lambda prompt: "4", output.append)
    answers = iter(("3", "saves/night-game.json"))
    resume = startup_arguments(
        lambda prompt: next(answers), output.append, root=tmp_path
    )

    assert demo == [
        "--demo",
        "--save",
        "saves/demo-game.save.json",
        "--return-to-menu",
    ]
    assert resume == ["--resume", "saves/night-game.json", "--return-to-menu"]
    assert any(line.startswith("┌") for line in output)
    assert any("│ DEADBALL PLAY" in line for line in output)
    assert not any("cached by Deadball Web" in line for line in output)


def test_start_screen_resumes_most_recent_valid_save(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    older = saves / "older.save.json"
    newest = saves / "newest.save.json"
    document = {
        "save_format_version": 1,
        "generated_game": {
            "teams": {
                "away": {"name": "Visitors"},
                "home": {"name": "Hosts"},
            }
        },
        "current_state": {
            "inning": 5,
            "half": "top",
            "away_score": 2,
            "home_score": 1,
            "result": None,
        },
    }
    older.write_text(json.dumps(document), encoding="utf-8")
    newest.write_text(json.dumps(document), encoding="utf-8")
    older.touch()
    newest.touch()
    import os

    os.utime(older, (1, 1))
    os.utime(newest, (2, 2))

    arguments = startup_arguments(
        lambda prompt: "R", lambda message: None, root=tmp_path
    )

    assert arguments == ["--resume", str(newest), "--return-to-menu"]


def test_saved_game_browser_shows_matchup_and_state(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    path = saves / "game.save.json"
    path.write_text(
        json.dumps(
            {
                "save_format_version": 1,
                "generated_game": {
                    "teams": {
                        "away": {"name": "Visitors"},
                        "home": {"name": "Hosts"},
                    }
                },
                "current_state": {
                    "inning": 5,
                    "half": "top",
                    "away_score": 2,
                    "home_score": 1,
                    "result": None,
                },
            }
        ),
        encoding="utf-8",
    )
    output = []
    answers = iter(("3", "1"))

    arguments = startup_arguments(
        lambda prompt: next(answers), output.append, root=tmp_path
    )

    assert arguments == ["--resume", str(path), "--return-to-menu"]
    assert any("Visitors at Hosts" in line and "Top 5, 2-1" in line for line in output)


def test_start_screen_browses_web_games_with_generator_options(monkeypatch):
    monkeypatch.setattr(
        "deadball_play.startup.list_web_games",
        lambda game_date, base_url: (
            {
                "game_id": 123,
                "away_team": "St Louis Cardinals",
                "home_team": "Los Angeles Dodgers",
            },
        ),
    )
    answers = iter(("1", "", "1", "2", "y", "", "g"))
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    arguments = startup_arguments(
        answer,
        lambda message: None,
        base_url="http://example.test/api",
        today=date(2026, 9, 6),
    )

    assert arguments == [
        "--generate-only",
        "123",
        "--web-base-url",
        "http://example.test/api",
        "--trait-mode",
        "sabr",
        "--scorecard-side",
        "both",
        "--away-team-label",
        "St Louis Cardinals",
        "--home-team-label",
        "Los Angeles Dodgers",
        "--force-generate",
        "--return-to-menu",
    ]
    assert any("Game date: [2026-09-06]" in prompt for prompt in prompts)


def test_generate_and_play_collects_control_for_each_team(monkeypatch):
    monkeypatch.setattr(
        "deadball_play.startup.list_web_games",
        lambda game_date, base_url: (
            {"game_id": 321, "away_team": "Visitors", "home_team": "Hosts"},
        ),
    )
    answers = iter(("1", "", "1", "", "n", "", "p", "c", "h"))

    arguments = startup_arguments(
        lambda prompt: next(answers),
        lambda message: None,
        today=date(2026, 9, 7),
    )

    assert arguments[-5:-1] == [
        "--away-control",
        "computer",
        "--home-control",
        "human",
    ]
    assert arguments[-1] == "--return-to-menu"


def test_start_screen_loads_generated_json_from_saves(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    generated = saves / "2026-09-06-Away-at-Home-DeadballPlay.json"
    generated.write_text("{}", encoding="utf-8")
    (saves / "ignored.save.json").write_text("{}", encoding="utf-8")
    (saves / "another-session.json").write_text("{}", encoding="utf-8")
    answers = iter(("2", "1", "c", "h"))

    arguments = startup_arguments(
        lambda prompt: next(answers),
        lambda message: None,
        root=tmp_path,
    )

    assert arguments == [
        "--game",
        str(generated),
        "--save",
        "saves/20260906AwayatHomeDeadballPlay.save.json",
        "--away-control",
        "computer",
        "--home-control",
        "human",
        "--return-to-menu",
    ]


def test_web_generation_writes_shell_safe_game_and_scorecard_paths(
    tmp_path, monkeypatch
):
    game = {
        "schema_version": 1,
        "game": {"game_date": "2026-09-03"},
        "teams": {
            "away": {"name": "St Louis Cardinals"},
            "home": {"name": "Los Angeles Dodgers"},
        },
    }

    requests = []

    def fake_urlopen(request, timeout):
        url = request.full_url if hasattr(request, "full_url") else request
        requests.append(request)
        if url.endswith("scorecard.pdf?side=away"):
            return FakeResponse(b"%PDF-test")
        if url.endswith("play.json"):
            return FakeResponse(json.dumps(game).encode())
        return FakeResponse(b"{}")

    monkeypatch.setattr("deadball_play.startup.urlopen", fake_urlopen)

    result = generate_web_artifacts(
        "123",
        base_url="https://example.test/api",
        root=tmp_path,
        trait_mode="adaptive",
        force=True,
        scorecard_side="away",
    )

    assert " " not in result.game_path.name
    assert " " not in result.scorecard_path.name
    assert result.game_path.parent.name == "saves"
    assert result.scorecard_path.parent.name == "saves"
    assert result.save_path.parent.name == "saves"
    assert json.loads(result.game_path.read_text())["schema_version"] == 1
    assert result.scorecard_path.read_bytes() == b"%PDF-test"
    generate_request = requests[0]
    assert generate_request.method == "POST"
    assert json.loads(generate_request.data) == {
        "force": True,
        "trait_mode": "adaptive",
    }


def test_web_generation_downloads_both_scorecard_sides_by_default(
    tmp_path, monkeypatch
):
    game = {
        "schema_version": 1,
        "game": {"game_date": "2026-09-06"},
        "teams": {
            "away": {"name": "Away Team"},
            "home": {"name": "Home Team"},
        },
    }
    requested_urls = []
    progress = []

    def fake_urlopen(request, timeout):
        url = request.full_url if hasattr(request, "full_url") else request
        requested_urls.append(url)
        if url.endswith("play.json"):
            return FakeResponse(json.dumps(game).encode())
        if "scorecard.pdf" in url:
            side = url.rsplit("=", 1)[-1]
            return FakeResponse(f"%PDF-{side}".encode())
        return FakeResponse(b"{}")

    monkeypatch.setattr("deadball_play.startup.urlopen", fake_urlopen)

    result = generate_web_artifacts(
        "456",
        base_url="https://example.test/api",
        root=tmp_path,
        progress_func=progress.append,
    )

    assert [path.name for path in result.scorecard_paths] == [
        "2026-09-06-AwayTeam-at-HomeTeam-DeadballPlay-home.pdf",
        "2026-09-06-AwayTeam-at-HomeTeam-DeadballPlay-away.pdf",
    ]
    assert [path.read_bytes() for path in result.scorecard_paths] == [
        b"%PDF-home",
        b"%PDF-away",
    ]
    assert any(url.endswith("scorecard.pdf?side=home") for url in requested_urls)
    assert any(url.endswith("scorecard.pdf?side=away") for url in requested_urls)
    assert progress == [
        "[#-----] 1/6 Generating Away team ratings and roster...",
        "[##----] 2/6 Generating Home team ratings and roster...",
        "[###---] 3/6 Downloading game JSON...",
        "[####--] 4/6 Downloading home PDF score sheet...",
        "[#####-] 5/6 Downloading away PDF score sheet...",
        "[######] 6/6 Saving artifact bundle...",
    ]


def test_local_schedule_uses_shared_service_without_http(monkeypatch):
    game = Mock(
        game_id="123",
        game_date=date(2026, 9, 10),
        game_type="R",
        home_team="Hosts",
        home_team_short="Hosts",
        away_team="Visitors",
        away_team_short="Visitors",
        description="Regular Season",
    )
    service = Mock()
    service.list_games.return_value = Mock(items=(game,))
    monkeypatch.setattr(startup, "_call_local_service", lambda callback: callback(service))
    network = Mock(side_effect=AssertionError("HTTP must not be used for local service calls"))
    monkeypatch.setattr(startup, "urlopen", network)

    games = startup.list_web_games("2026-09-10")

    assert games[0]["game_id"] == "123"
    service.list_games.assert_called_once_with("2026-09-10")
    network.assert_not_called()


def test_local_generation_uses_one_shared_service_for_all_artifacts(
    tmp_path, monkeypatch
):
    game = {
        "schema_version": 1,
        "game": {"game_date": "2026-09-10"},
        "teams": {
            "away": {"name": "Visitors"},
            "home": {"name": "Hosts"},
        },
    }
    service = Mock()
    service.play_json.return_value = game
    service.scorecard_pdf.side_effect = lambda game_id, side: f"%PDF-{side}".encode()
    monkeypatch.setattr(startup, "_call_local_service", lambda callback: callback(service))
    network = Mock(side_effect=AssertionError("HTTP must not be used for local generation"))
    monkeypatch.setattr(startup, "urlopen", network)

    artifacts = generate_web_artifacts("123", root=tmp_path)

    service.generate_game.assert_called_once_with(
        "123", force=False, trait_mode="standard"
    )
    service.play_json.assert_called_once_with("123")
    assert service.scorecard_pdf.call_count == 2
    assert artifacts.game_path.read_text().endswith("\n")
    assert [path.read_bytes() for path in artifacts.scorecard_paths] == [
        b"%PDF-home",
        b"%PDF-away",
    ]
    network.assert_not_called()


def test_remote_request_reports_connection_failure(monkeypatch):
    monkeypatch.setattr(startup, "urlopen", Mock(side_effect=URLError("offline")))

    with pytest.raises(ValueError, match="Could not reach Deadball Web"):
        startup._request_json("https://example.test/api/games")


def test_http_error_is_reported_without_process_management(monkeypatch):
    error = HTTPError(
        "http://127.0.0.1:8000/api/games/unknown",
        404,
        "Not Found",
        {},
        None,
    )
    monkeypatch.setattr(startup, "urlopen", Mock(side_effect=error))

    with pytest.raises(ValueError, match="HTTP 404"):
        startup._request_json("http://127.0.0.1:8000/api/games/unknown")


def test_generate_only_downloads_bundle_without_starting_game(monkeypatch, capsys):
    artifacts = GeneratedArtifacts(
        Path("saves/game.json"),
        Path("saves/game.pdf"),
        Path("saves/game.save.json"),
    )
    calls = []

    def fake_generate(game_id, **options):
        calls.append((game_id, options))
        return artifacts

    monkeypatch.setattr("deadball_play.startup.generate_web_artifacts", fake_generate)

    assert main(
        [
            "--generate-only",
            "123",
            "--trait-mode",
            "sabr",
            "--scorecard-side",
            "away",
            "--force-generate",
        ]
    ) == 0
    assert calls == [
        (
            "123",
            {
                "base_url": "http://127.0.0.1:8000/api",
                "trait_mode": "sabr",
                "force": True,
                "scorecard_side": "away",
                "progress_func": print,
                "away_team_label": "Away team",
                "home_team_label": "Home team",
            },
        )
    ]
    assert "Game JSON: saves/game.json" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda prompt: "")
    menu_calls = []

    def fake_startup():
        menu_calls.append(True)
        return None

    monkeypatch.setattr("deadball_play.startup.startup_arguments", fake_startup)
    assert main(["--generate-only", "123", "--return-to-menu"]) == 0
    assert menu_calls == [True]


def test_start_screen_generation_failure_returns_to_menu(monkeypatch, capsys):
    def fail_generate(game_id, **options):
        raise TimeoutError("timed out")

    menu_calls = []
    monkeypatch.setattr("deadball_play.startup.generate_web_artifacts", fail_generate)
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    def fake_startup():
        menu_calls.append(True)
        return None

    monkeypatch.setattr("deadball_play.startup.startup_arguments", fake_startup)

    assert main(["--generate-game", "123", "--return-to-menu"]) == 0
    output = capsys.readouterr().out
    assert "Could not continue: timed out" in output
    assert "usage: deadball-play" not in output
    assert menu_calls == [True]
