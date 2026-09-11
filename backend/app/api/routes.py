import re
from typing import Iterable, List

import requests

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app import models
from app.db import get_session
from app.core.config import get_settings
from app.pdf.scorecard import build_scorecard_field_values, render_scorecard_pdf
from app.services.games import GameService, GameServiceError, game_to_dict
from app.schemas import (
    Game,
    GameGenerateRequest,
    GameGenerateResponse,
    GameListResponse,
    GenerateRequest,
    GenerateResponse,
    Player,
    Roster,
    RostersResponse,
)
from deadball_generator.generator import (
    generate_game_from_raw,
    generate_roster as generate_deadball_roster,
)
from deadball_generator.rules import RULES_VERSION
from deadball_core import build_generator_game

router = APIRouter()
settings = get_settings()


def _slugify(value: str) -> str:
    """Create a simple, URL-safe slug from a string."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "roster"


def _unique_slug(session: Session, base: str) -> str:
    """Generate a unique slug by appending a counter when needed."""
    slug = _slugify(base)
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid roster name")
    candidate = slug
    counter = 1
    while session.exec(select(models.Roster).where(models.Roster.slug == candidate)).first():
        counter += 1
        candidate = f"{slug}-{counter}"
    return candidate


def _serialize_roster(model: models.Roster) -> Roster:
    return Roster(
        id=model.id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        source_type=model.source_type,
        source_ref=model.source_ref,
        public=model.public,
        created_at=model.created_at,
    )


def _serialize_players(records: Iterable[models.Player]) -> List[Player]:
    return [
        Player(
            id=player.id,
            name=player.name,
            team=player.team,
            role=player.role,
            positions=player.positions.split(",") if player.positions else None,
            bt=player.bt,
            obt=player.obt,
            traits=player.traits.split(",") if player.traits else None,
            pd=player.pd,
        )
        for player in records
    ]


def _store_str_list(values: List[str] | None) -> str | None:
    if not values:
        return None
    return ",".join(values)


def _stub_generate_players(payload: str, mode: str) -> List[dict]:
    """Deprecated placeholder; kept for reference."""
    base = payload.strip() or "Sample"
    return [
        dict(name=f"{base} Player One", team="TEAM", role="batter", bt=0.280, obt=0.340),
        dict(name=f"{base} Player Two", team="TEAM", role="pitcher", pd="SP"),
    ]


@router.post("/generate", response_model=GenerateResponse, tags=["rosters"])
def generate_roster(request: GenerateRequest, session: Session = Depends(get_session)) -> GenerateResponse:
    """Persist roster + players using embedded generator logic."""
    if not request.payload.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload is required")

    roster_name = request.name or "Generated Roster"
    slug = _unique_slug(session, roster_name)

    generated = generate_deadball_roster(
        mode=request.mode,
        payload=request.payload,
        name=roster_name,
        description=request.description or f"Generated from {request.mode} payload",
        public=request.public,
        trait_mode=request.trait_mode,
        allow_network=settings.allow_generator_network,
    )

    roster_model = models.Roster(
        slug=slug,
        name=generated.name,
        description=generated.description,
        source_type=generated.source_type,
        source_ref=generated.source_ref,
        public=generated.public,
    )
    session.add(roster_model)
    session.commit()
    session.refresh(roster_model)

    player_models = []
    for payload in generated.players:
        player_models.append(
            models.Player(
                roster_id=roster_model.id,
                name=payload.name,
                team=payload.team,
                role=payload.role,
                positions=_store_str_list(payload.positions),
                bt=payload.bt,
                obt=payload.obt,
                traits=_store_str_list(payload.traits),
                pd=payload.pd,
            )
        )

    session.add_all(player_models)
    session.commit()
    session.refresh(roster_model)

    return GenerateResponse(
        roster=_serialize_roster(roster_model),
        players=_serialize_players(player_models),
    )


@router.get("/rosters/{slug}", response_model=GenerateResponse, tags=["rosters"])
def get_roster(slug: str, session: Session = Depends(get_session)) -> GenerateResponse:
    roster = session.exec(select(models.Roster).where(models.Roster.slug == slug)).first()
    if not roster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Roster not found")
    players = session.exec(select(models.Player).where(models.Player.roster_id == roster.id)).all()
    roster = Roster(
        id=roster.id,
        slug=roster.slug,
        name=roster.name,
        description=roster.description,
        source_type=roster.source_type,
        source_ref=roster.source_ref,
        public=roster.public,
        created_at=roster.created_at,
    )
    return GenerateResponse(roster=roster, players=_serialize_players(players))


@router.get("/rosters", response_model=RostersResponse, tags=["rosters"])
def list_rosters(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> RostersResponse:
    total = session.exec(select(func.count()).select_from(models.Roster)).one()
    rosters = session.exec(
        select(models.Roster)
        .order_by(models.Roster.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return RostersResponse(
        items=[_serialize_roster(r) for r in rosters],
        count=total,
        offset=offset,
        limit=limit,
    )


def _serialize_game(game: models.Game) -> Game:
    return Game(**game_to_dict(game))


def _game_service(session: Session) -> GameService:
    """Build the shared service with patchable route-level dependencies."""
    return GameService(
        session,
        allow_network=settings.allow_generator_network,
        http_get=requests.get,
        generator=generate_game_from_raw,
        rules_version=RULES_VERSION,
        play_builder=build_generator_game,
        scorecard_builder=build_scorecard_field_values,
        pdf_renderer=render_scorecard_pdf,
    )


def _service_call(function):
    try:
        return function()
    except GameServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/games", response_model=GameListResponse, tags=["games"])
def list_games(
    date: str = Query(..., description="YYYY-MM-DD"),
    force: bool = Query(False, description="Force refresh of cached games"),
    cache_ttl_hours: int = Query(24, ge=1, le=168, description="TTL for cached games"),
    session: Session = Depends(get_session),
) -> GameListResponse:
    """List games through the shared application service."""
    result = _service_call(
        lambda: _game_service(session).list_games(
            date, force=force, cache_ttl_hours=cache_ttl_hours
        )
    )
    return GameListResponse(
        items=[_serialize_game(game) for game in result.items],
        count=len(result.items),
        date=result.date,
        cached=result.cached,
        fallback_used=result.fallback_used,
        fallback_reason=result.fallback_reason,
    )


@router.post("/games/{game_id}/generate", response_model=GameGenerateResponse, tags=["games"])
def generate_game(
    game_id: str,
    request: GameGenerateRequest,
    session: Session = Depends(get_session),
) -> GameGenerateResponse:
    """Generate through the shared application service."""
    result = _service_call(
        lambda: _game_service(session).generate_game(
            game_id,
            force=request.force,
            trait_mode=request.trait_mode,
            payload=request.payload,
        )
    )
    return GameGenerateResponse(
        game=_serialize_game(result.game),
        stats=result.stats,
        game_text=result.game_text,
        cached=result.cached,
    )


@router.get("/games/{game_id}/play.json", tags=["games"])
def get_play_game(
    game_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Export through the shared application service."""
    return _service_call(lambda: _game_service(session).play_json(game_id))


@router.get("/games/{game_id}/scorecard.pdf", tags=["games"])
def get_scorecard_pdf(
    game_id: str,
    side: str = Query("home", description="home or away"),
    session: Session = Depends(get_session),
):
    """Return a filled scorecard PDF through the shared application service."""
    service = _game_service(session)
    pdf_bytes = _service_call(lambda: service.scorecard_pdf(game_id, side=side))
    game = _service_call(lambda: service.get_game(game_id))

    def _safe_team_label(name: str | None, fallback: str) -> str:
        text = (name or fallback).strip()
        if not text:
            text = fallback
        # Keep letters/numbers/space/@/dash/dot; drop other characters to keep filenames safe.
        return re.sub(r"[^A-Za-z0-9 @.-]", "", text)

    try:
        date_str = game.game_date.strftime("%Y-%m-%d")
    except Exception:
        date_str = str(game.game_date)

    away_label = _safe_team_label(game.away_team, "Away")
    home_label = _safe_team_label(game.home_team, "Home")
    matchup_label = f"{away_label} @ {home_label}"
    filename = f"{date_str} - {matchup_label} - Deadball.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
