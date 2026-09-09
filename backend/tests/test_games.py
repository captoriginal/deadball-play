from datetime import UTC, datetime
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.api import routes


class StubResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def offline_mlb(monkeypatch):
    schedule = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 1001,
                        "gameType": "R",
                        "teams": {
                            "away": {
                                "team": {
                                    "abbreviation": "VIS",
                                    "teamName": "Visitors",
                                }
                            },
                            "home": {
                                "team": {
                                    "abbreviation": "HST",
                                    "teamName": "Hosts",
                                }
                            },
                        },
                        "seriesDescription": "Test Series",
                    },
                    {
                        "gamePk": 1002,
                        "gameType": "R",
                        "teams": {
                            "away": {
                                "team": {
                                    "abbreviation": "ALT",
                                    "teamName": "Alternates",
                                }
                            },
                            "home": {
                                "team": {
                                    "abbreviation": "RIV",
                                    "teamName": "Rivals",
                                }
                            },
                        },
                        "seriesDescription": "Test Series",
                    },
                ]
            }
        ]
    }

    def get(url, timeout):
        if "/schedule?" in url:
            return StubResponse(schedule)
        return StubResponse({"teams": {}})

    def generate_game_from_raw(**kwargs):
        stats = {
            "players": [{"Name": "Fixture Player"}],
            "raw": kwargs["raw_stats"],
            "meta": {
                "rules_version": routes.RULES_VERSION,
                "trait_mode": kwargs["trait_mode"],
                "snapshot_at": datetime.now(UTC).timestamp(),
            },
        }
        return {"stats": json.dumps(stats), "game_text": "fixture game"}

    monkeypatch.setattr(routes.settings, "allow_generator_network", True)
    monkeypatch.setattr(routes.requests, "get", get)
    monkeypatch.setattr(routes, "generate_game_from_raw", generate_game_from_raw)


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture(name="client")
def client_fixture(engine):
    def _get_test_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


def test_list_games_caches(client: TestClient):
    resp = client.get("/api/games", params={"date": "2024-04-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["cached"] is False
    assert all(game["game_type"] == "R" for game in data["items"])

    resp_again = client.get("/api/games", params={"date": "2024-04-01"})
    assert resp_again.status_code == 200
    data2 = resp_again.json()
    assert data2["cached"] is True
    assert data2["count"] == 2


def test_generate_game_uses_cache(client: TestClient):
    # seed game via list endpoint
    resp = client.get("/api/games", params={"date": "2024-04-02"})
    game_id = resp.json()["items"][0]["game_id"]

    # first generate
    gen1 = client.post(f"/api/games/{game_id}/generate", json={"force": False})
    assert gen1.status_code == 200
    assert gen1.json()["cached"] is False

    # second generate should hit cache
    gen2 = client.post(f"/api/games/{game_id}/generate", json={"force": False})
    assert gen2.status_code == 200
    assert gen2.json()["cached"] is True


def test_generate_game_force_refresh(client: TestClient):
    resp = client.get("/api/games", params={"date": "2024-04-03"})
    game_id = resp.json()["items"][0]["game_id"]

    client.post(f"/api/games/{game_id}/generate", json={"force": False})
    force_resp = client.post(
        f"/api/games/{game_id}/generate",
        json={"force": True, "payload": '{"override": "override-raw"}'},
    )
    assert force_resp.status_code == 200
    assert force_resp.json()["cached"] is False
    assert "override-raw" in force_resp.json()["stats"]
