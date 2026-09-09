import json
import re
from datetime import UTC, datetime, timedelta
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
from deadball_generator import cache_policy
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


def _is_stale(updated_at: datetime, ttl_hours: int) -> bool:
    now = datetime.now(UTC)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return now - updated_at > timedelta(hours=ttl_hours)


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
    return Game(
        id=game.id,
        game_id=game.game_id,
        game_date=game.game_date,
        game_type=game.game_type,
        home_team=game.home_team,
        home_team_short=game.home_team_short,
        away_team=game.away_team,
        away_team_short=game.away_team_short,
        description=game.description,
        created_at=game.created_at,
        updated_at=game.updated_at,
    )


def _extract_team_labels(team_payload: dict | None) -> tuple[str | None, str | None]:
    """
    The schedule endpoint returns only id/name/link; we also want shortName when present.
    Prefer abbreviation for the main label, then teamCode, then name.
    For short label, prefer the nickname/teamName (e.g., Phillies), then shortName, then abbreviation.
    """
    if not team_payload:
        return None, None
    label = (
        team_payload.get("abbreviation")
        or team_payload.get("teamCode")
        or team_payload.get("name")
    )
    short = (
        team_payload.get("teamName")
        or team_payload.get("shortName")
        or team_payload.get("abbreviation")
        or team_payload.get("teamCode")
        or team_payload.get("name")
    )
    return label, short


def _get_or_create_game(
    session: Session,
    game_id: str,
    game_date: datetime,
    home: str | None,
    away: str | None,
    desc: str | None,
    game_type: str | None = None,
):
    game = session.exec(select(models.Game).where(models.Game.game_id == game_id)).first()
    if game:
        return game
    game = models.Game(
        game_id=game_id,
        game_date=game_date,
        game_type=game_type,
        home_team=home,
        away_team=away,
        description=desc,
    )
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


@router.get("/games", response_model=GameListResponse, tags=["games"])
def list_games(
    date: str = Query(..., description="YYYY-MM-DD"),
    force: bool = Query(False, description="Force refresh of cached games"),
    cache_ttl_hours: int = Query(24, ge=1, le=168, description="TTL for cached games"),
    session: Session = Depends(get_session),
) -> GameListResponse:
    """List games by date with caching; falls back to stub if network is unavailable."""
    try:
        parsed_date = datetime.fromisoformat(date).date()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format")

    games = session.exec(select(models.Game).where(models.Game.game_date == parsed_date)).all()
    cached = len(games) > 0 and not force
    fallback_used = False
    fallback_reason: str | None = None

    use_cache = False
    if games and not force:
        fresh = all(not _is_stale(g.updated_at, cache_ttl_hours) for g in games)
        missing_metadata = any(
            (
                not g.home_team
                or not g.away_team
                or not g.home_team_short
                or not g.away_team_short
                or not g.game_type
            )
            for g in games
        )
        # Refresh old cache rows that predate team labels or MLB game type.
        if fresh and not missing_metadata:
            use_cache = True

    if not use_cache and settings.allow_generator_network:
        try:
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            dates = data.get("dates") or []
            schedule_games = dates[0].get("games") if dates else []
            if schedule_games:
                # Replace existing games for this date
                for g in schedule_games:
                    game_pk = g.get("gamePk")
                    home_label, home_short = _extract_team_labels(g.get("teams", {}).get("home", {}).get("team"))
                    away_label, away_short = _extract_team_labels(g.get("teams", {}).get("away", {}).get("team"))
                    desc = g.get("description") or g.get("seriesDescription")
                    game = session.exec(select(models.Game).where(models.Game.game_id == str(game_pk))).first()
                    if not game:
                        game = models.Game(
                            game_id=str(game_pk),
                            game_date=parsed_date,
                            game_type=g.get("gameType"),
                            home_team=home_label,
                            home_team_short=home_short,
                            away_team=away_label,
                            away_team_short=away_short,
                            description=desc,
                        )
                        session.add(game)
                    else:
                        game.game_date = parsed_date
                        game.game_type = g.get("gameType")
                        game.home_team = home_label
                        game.home_team_short = home_short
                        game.away_team = away_label
                        game.away_team_short = away_short
                        game.description = desc
                        game.updated_at = datetime.now(UTC)
                        session.add(game)
                session.commit()
                games = session.exec(select(models.Game).where(models.Game.game_date == parsed_date)).all()
                cached = False
        except Exception:
            pass

    if not games:
        fallback_used = False
        fallback_reason = "There were no MLB games on this date! BooOOO!"

    return GameListResponse(
        items=[_serialize_game(g) for g in games],
        count=len(games),
        date=date,
        cached=cached,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


@router.post("/games/{game_id}/generate", response_model=GameGenerateResponse, tags=["games"])
def generate_game(
    game_id: str,
    request: GameGenerateRequest,
    session: Session = Depends(get_session),
) -> GameGenerateResponse:
    """Generate stats/game; reuse cache only for matching rules and trait mode."""
    game = session.exec(select(models.Game).where(models.Game.game_id == game_id)).first()
    if not game:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found; list games first")

    # Check generated cache
    cached_generated = session.exec(select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)).first()
    if cached_generated and not request.force and not request.payload:
        cache_valid = False
        try:
            parsed_cache = json.loads(cached_generated.stats)
            players = parsed_cache.get("players") if isinstance(parsed_cache, dict) else None
            meta = parsed_cache.get("meta") if isinstance(parsed_cache, dict) else None
            cache_valid = (
                isinstance(players, list)
                and bool(players)
                and isinstance(meta, dict)
                and meta.get("rules_version") == RULES_VERSION
                and meta.get("trait_mode") == request.trait_mode
            )
            if cache_valid:
                fresh = cache_policy.is_fresh(game.game_date.year, meta.get("snapshot_at"))
                cache_valid = fresh or not settings.allow_generator_network
                meta["stale"] = not fresh
        except (ValueError, TypeError):
            pass
        if cache_valid:
            return GameGenerateResponse(
                game=_serialize_game(game),
                stats=json.dumps(parsed_cache),
                game_text=cached_generated.game_text,
                cached=True,
            )

    # Determine raw stats: use provided payload, existing raw cache, or fetch
    raw_stats_row = session.exec(select(models.GameRawStats).where(models.GameRawStats.game_id == game.id)).first()
    if request.payload:
        if raw_stats_row:
            session.delete(raw_stats_row)
            session.commit()
        raw_payload = request.payload
        raw_stats_row = models.GameRawStats(game_id=game.id, payload=raw_payload)
        session.add(raw_stats_row)
        session.commit()
    elif not raw_stats_row or (settings.allow_generator_network and (
        request.force or not cache_policy.is_fresh(
            game.game_date.year, raw_stats_row.created_at.replace(tzinfo=UTC).timestamp()
        )
    )):
        if settings.allow_generator_network:
            try:
                url = f"https://statsapi.mlb.com/api/v1/game/{game.game_id}/boxscore"
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                if raw_stats_row is None:
                    raw_stats_row = models.GameRawStats(game_id=game.id, payload=resp.text)
                else:
                    raw_stats_row.payload = resp.text
                    raw_stats_row.created_at = datetime.now(UTC)
                session.add(raw_stats_row)
                session.commit()
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to fetch boxscore: {exc}") from exc
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Network disabled and no cached raw stats available for this game.",
            )

    raw_payload = raw_stats_row.payload

    # If cached payload is non-JSON and we allow network, try refetching the real boxscore; otherwise fail.
    if settings.allow_generator_network:
        try:
            json.loads(raw_payload)
        except Exception:
            try:
                url = f"https://statsapi.mlb.com/api/v1/game/{game.game_id}/boxscore"
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                raw_payload = resp.text
                raw_stats_row.payload = raw_payload
                raw_stats_row.created_at = datetime.now(UTC)
                session.add(raw_stats_row)
                session.commit()
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to refresh boxscore: {exc}") from exc

    # Backfill missing team names/short names from raw payload if we can parse JSON.
    # Also allow replacing short names if they match the long name (schedule lacked a true short name).
    if raw_payload and (
        not game.home_team
        or not game.away_team
        or not game.home_team_short
        or not game.away_team_short
        or game.home_team_short == game.home_team
        or game.away_team_short == game.away_team
    ):
        try:
            payload_json = json.loads(raw_payload)
            teams = payload_json.get("teams", {})
            home, home_short = _extract_team_labels(teams.get("home", {}).get("team"))
            away, away_short = _extract_team_labels(teams.get("away", {}).get("team"))
            updated = False
            if home and (not game.home_team):
                game.home_team = home
                updated = True
            if home_short and (not game.home_team_short or game.home_team_short == game.home_team):
                game.home_team_short = home_short
                updated = True
            if away and (not game.away_team):
                game.away_team = away
                updated = True
            if away_short and (not game.away_team_short or game.away_team_short == game.away_team):
                game.away_team_short = away_short
                updated = True
            if updated:
                game.updated_at = datetime.now(UTC)
                session.add(game)
                session.commit()
                session.refresh(game)
        except Exception:
            pass

    try:
        generated = generate_game_from_raw(
            game_id=game.game_id,
            date=str(game.game_date),
            home_team=game.home_team,
            away_team=game.away_team,
            raw_stats=raw_payload,
            allow_network=settings.allow_generator_network,
            trait_mode=request.trait_mode,
            refresh=request.force,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate game stats: {exc}") from exc

    # A fresh rebuild cannot extend the life of an older boxscore or stat snapshot.
    parsed_generated = json.loads(generated["stats"])
    metadata = parsed_generated.setdefault("meta", {})
    metadata["snapshot_at"] = cache_policy.oldest([
        metadata.get("snapshot_at"), raw_stats_row.created_at.replace(tzinfo=UTC).timestamp(),
    ])
    metadata["stale"] = not cache_policy.is_fresh(game.game_date.year, metadata["snapshot_at"])
    generated["stats"] = json.dumps(parsed_generated)

    if cached_generated:
        session.delete(cached_generated)
        session.commit()

    generated_row = models.GameGenerated(
        game_id=game.id,
        stats=generated["stats"],
        game_text=generated["game_text"],
    )
    session.add(generated_row)
    game.updated_at = datetime.now(UTC)
    session.add(game)
    session.commit()
    session.refresh(game)

    return GameGenerateResponse(
        game=_serialize_game(game),
        stats=generated_row.stats,
        game_text=generated_row.game_text,
        cached=False,
    )


@router.get("/games/{game_id}/play.json", tags=["games"])
def get_play_game(
    game_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Export cached generated stats as the canonical offline gameplay file."""
    game = session.exec(
        select(models.Game).where(models.Game.game_id == game_id)
    ).first()
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found; list games first",
        )
    generated = session.exec(
        select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)
    ).first()
    if generated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generate the game before exporting it for Deadball Play",
        )
    if not game.away_team or not game.home_team:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Game is missing team identity required for gameplay export",
        )
    arguments = {
        "game_id": game.game_id,
        "game_date": str(game.game_date),
        "game_type": game.game_type,
        "away_team": game.away_team,
        "home_team": game.home_team,
        "away_short": game.away_team_short,
        "home_short": game.home_team_short,
    }
    try:
        return build_generator_game(
            generated.stats,
            **arguments,
        ).to_dict()
    except (TypeError, ValueError, json.JSONDecodeError) as cached_error:
        raw = session.exec(
            select(models.GameRawStats).where(models.GameRawStats.game_id == game.id)
        ).first()
        if raw is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Generated game cannot be exported for play: {cached_error}",
            ) from cached_error
    try:
        parsed = json.loads(generated.stats)
        trait_mode = parsed.get("meta", {}).get("trait_mode", "standard")
        for include_reserves in (True, False):
            regenerated = generate_game_from_raw(
                game_id=game.game_id,
                date=str(game.game_date),
                home_team=game.home_team,
                away_team=game.away_team,
                raw_stats=raw.payload,
                allow_network=False,
                trait_mode=trait_mode,
                include_reserves=include_reserves,
            )
            try:
                return build_generator_game(
                    regenerated["stats"], **arguments
                ).to_dict()
            except (TypeError, ValueError, json.JSONDecodeError):
                if not include_reserves:
                    raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Generated game cannot be regenerated for play: {exc}",
        ) from exc


@router.get("/games/{game_id}/scorecard.pdf", tags=["games"])
def get_scorecard_pdf(
    game_id: str,
    side: str = Query("home", description="home or away"),
    session: Session = Depends(get_session),
):
    """Return a filled scorecard PDF for the requested side (home/away)."""
    game = session.exec(select(models.Game).where(models.Game.game_id == game_id)).first()
    if not game:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found; list games first")

    generated = session.exec(select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)).first()
    if not generated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated stats not found for this game; generate first.",
        )

    try:
        field_values = build_scorecard_field_values(game, generated.stats)
        pdf_bytes = render_scorecard_pdf(field_values)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to build scorecard PDF: {exc}") from exc

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
