from datetime import date
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.api import routes
from app.db import get_session
from app.services.games import GameService


POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")


def generated_stats():
    players = []
    for team_index, (team, abbreviation) in enumerate(
        (("Visitors", "VIS"), ("Hosts", "HST")), start=1
    ):
        for slot, position in enumerate(POSITIONS, start=1):
            players.append(
                {
                    "IDmlb": team_index * 100 + slot,
                    "Type": "Hitter",
                    "Team": team,
                    "BatOrder": str(slot),
                    "Name": f"{team} Hitter {slot}",
                    "Pos": position,
                    "Positions": position,
                    "Hand": "R",
                    "Throws": "R",
                    "BT": 28,
                    "OBT": 36,
                    "Traits": "",
                }
            )
        players.extend(
            (
                {
                    "IDmlb": team_index * 100 + 90,
                    "Type": "Pitcher",
                    "Team": team,
                    "Name": f"{team} Starter",
                    "Pos": "P",
                    "Positions": "P",
                    "Role": "starter",
                    "Throws": "R",
                    "PD": "d8",
                    "Traits": "",
                },
                {
                    "IDmlb": team_index * 100 + 91,
                    "Type": "Pitcher",
                    "Team": team,
                    "Name": f"{team} Reliever",
                    "Pos": "P",
                    "Positions": "P",
                    "Role": "reliever",
                    "Throws": "L",
                    "PD": "d4",
                    "Traits": "",
                },
            )
        )
    return {
        "players": players,
        "teams": {
            "away_team": "Visitors",
            "away_abbr": "VIS",
            "home_team": "Hosts",
            "home_abbr": "HST",
        },
        "meta": {"trait_mode": "standard"},
    }


def test_cached_web_game_exports_canonical_play_contract():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        game = models.Game(
            game_id="123",
            game_date=date(2026, 8, 15),
            game_type="D",
            away_team="Visitors",
            away_team_short="Visitors",
            home_team="Hosts",
            home_team_short="Hosts",
        )
        session.add(game)
        session.commit()
        session.refresh(game)
        session.add(
            models.GameGenerated(
                game_id=game.id,
                stats=json.dumps(generated_stats()),
                game_text="unused",
            )
        )
        session.commit()
        service = GameService(session, allow_network=False)
        direct_payload = json.loads(json.dumps(service.play_json("123")))
        direct_pdf = service.scorecard_pdf("123", side="home")

    def sessions():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        response = client.get("/api/games/123/play.json")
        pdf_response = client.get("/api/games/123/scorecard.pdf?side=home")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == direct_payload
    assert pdf_response.status_code == 200, pdf_response.text
    assert pdf_response.content == direct_pdf
    assert payload["schema_version"] == 1
    assert payload["game"]["game_id"] == "mlb-123"
    assert payload["game"]["game_type"] == "D"
    assert payload["teams"]["away"]["short_name"] == "VIS"
    assert payload["teams"]["home"]["short_name"] == "HST"
    assert len(payload["teams"]["away"]["lineup"]) == 9
    assert payload["teams"]["away"]["starting_pitcher_id"] == "mlb-190"
    engine.dispose()
