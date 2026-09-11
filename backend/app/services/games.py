"""Canonical schedule, generation, Play export, and scorecard workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Callable

import requests
from sqlmodel import Session, select

from app import models
from app.pdf.scorecard import build_scorecard_field_values, render_scorecard_pdf
from deadball_core import build_generator_game
from deadball_generator import cache_policy
from deadball_generator.generator import generate_game_from_raw
from deadball_generator.rules import RULES_VERSION


class GameServiceError(ValueError):
    """An application-service failure with an API-compatible status code."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ScheduleResult:
    items: tuple[models.Game, ...]
    date: str
    cached: bool
    fallback_used: bool = False
    fallback_reason: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    game: models.Game
    stats: str
    game_text: str
    cached: bool


def game_to_dict(game: models.Game) -> dict:
    """Serialize a database game without coupling callers to API schemas."""
    return {
        "id": game.id,
        "game_id": game.game_id,
        "game_date": game.game_date,
        "game_type": game.game_type,
        "home_team": game.home_team,
        "home_team_short": game.home_team_short,
        "away_team": game.away_team,
        "away_team_short": game.away_team_short,
        "description": game.description,
        "created_at": game.created_at,
        "updated_at": game.updated_at,
    }


def _extract_team_labels(team_payload: dict | None) -> tuple[str | None, str | None]:
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


def _is_stale(updated_at: datetime, ttl_hours: int) -> bool:
    now = datetime.now(UTC)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return now - updated_at > timedelta(hours=ttl_hours)


class GameService:
    """Coordinate all persistent MLB game artifact operations."""

    def __init__(
        self,
        session: Session,
        *,
        allow_network: bool,
        http_get: Callable = requests.get,
        generator: Callable = generate_game_from_raw,
        rules_version: str = RULES_VERSION,
        play_builder: Callable = build_generator_game,
        scorecard_builder: Callable = build_scorecard_field_values,
        pdf_renderer: Callable = render_scorecard_pdf,
    ) -> None:
        self.session = session
        self.allow_network = allow_network
        self.http_get = http_get
        self.generator = generator
        self.rules_version = rules_version
        self.play_builder = play_builder
        self.scorecard_builder = scorecard_builder
        self.pdf_renderer = pdf_renderer

    def list_games(
        self, game_date: str, *, force: bool = False, cache_ttl_hours: int = 24
    ) -> ScheduleResult:
        try:
            parsed_date = datetime.fromisoformat(game_date).date()
        except ValueError as exc:
            raise GameServiceError(400, "Invalid date format") from exc

        games = self.session.exec(
            select(models.Game).where(models.Game.game_date == parsed_date)
        ).all()
        cached = bool(games) and not force
        use_cache = False
        if games and not force:
            fresh = all(not _is_stale(game.updated_at, cache_ttl_hours) for game in games)
            missing_metadata = any(
                not game.home_team
                or not game.away_team
                or not game.home_team_short
                or not game.away_team_short
                or not game.game_type
                for game in games
            )
            use_cache = fresh and not missing_metadata

        if not use_cache and self.allow_network:
            try:
                response = self.http_get(
                    f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={game_date}",
                    timeout=10,
                )
                response.raise_for_status()
                dates = response.json().get("dates") or []
                schedule_games = dates[0].get("games") if dates else []
                if schedule_games:
                    for payload in schedule_games:
                        game_id = str(payload.get("gamePk"))
                        home, home_short = _extract_team_labels(
                            payload.get("teams", {}).get("home", {}).get("team")
                        )
                        away, away_short = _extract_team_labels(
                            payload.get("teams", {}).get("away", {}).get("team")
                        )
                        description = payload.get("description") or payload.get("seriesDescription")
                        game = self.session.exec(
                            select(models.Game).where(models.Game.game_id == game_id)
                        ).first()
                        if game is None:
                            game = models.Game(game_id=game_id, game_date=parsed_date)
                        game.game_date = parsed_date
                        game.game_type = payload.get("gameType")
                        game.home_team = home
                        game.home_team_short = home_short
                        game.away_team = away
                        game.away_team_short = away_short
                        game.description = description
                        game.updated_at = datetime.now(UTC)
                        self.session.add(game)
                    self.session.commit()
                    games = self.session.exec(
                        select(models.Game).where(models.Game.game_date == parsed_date)
                    ).all()
                    cached = False
            except Exception:
                # Schedule browsing deliberately falls back to the persistent cache.
                pass

        reason = None if games else "There were no MLB games on this date! BooOOO!"
        return ScheduleResult(tuple(games), game_date, cached, False, reason)

    def generate_game(
        self,
        game_id: str,
        *,
        force: bool = False,
        trait_mode: str = "standard",
        payload: str | None = None,
    ) -> GenerationResult:
        game = self._game(game_id)
        generated = self.session.exec(
            select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)
        ).first()
        if generated and not force and not payload:
            parsed_cache = None
            try:
                parsed_cache = json.loads(generated.stats)
                players = parsed_cache.get("players") if isinstance(parsed_cache, dict) else None
                meta = parsed_cache.get("meta") if isinstance(parsed_cache, dict) else None
                valid = (
                    isinstance(players, list)
                    and bool(players)
                    and isinstance(meta, dict)
                    and meta.get("rules_version") == self.rules_version
                    and meta.get("trait_mode") == trait_mode
                )
                if valid:
                    fresh = cache_policy.is_fresh(game.game_date.year, meta.get("snapshot_at"))
                    valid = fresh or not self.allow_network
                    meta["stale"] = not fresh
                if valid:
                    return GenerationResult(
                        game, json.dumps(parsed_cache), generated.game_text, True
                    )
            except (ValueError, TypeError, AttributeError):
                pass

        raw = self.session.exec(
            select(models.GameRawStats).where(models.GameRawStats.game_id == game.id)
        ).first()
        if payload:
            if raw:
                self.session.delete(raw)
                self.session.commit()
            raw = models.GameRawStats(game_id=game.id, payload=payload)
            self.session.add(raw)
            self.session.commit()
        elif not raw or (
            self.allow_network
            and (
                force
                or not cache_policy.is_fresh(
                    game.game_date.year,
                    raw.created_at.replace(tzinfo=UTC).timestamp(),
                )
            )
        ):
            if not self.allow_network:
                raise GameServiceError(
                    503, "Network disabled and no cached raw stats available for this game."
                )
            raw = self._fetch_boxscore(game, raw)

        assert raw is not None
        raw_payload = raw.payload
        if self.allow_network:
            try:
                json.loads(raw_payload)
            except Exception:
                raw = self._fetch_boxscore(game, raw)
                raw_payload = raw.payload

        self._backfill_teams(game, raw_payload)
        try:
            fresh = self.generator(
                game_id=game.game_id,
                date=str(game.game_date),
                home_team=game.home_team,
                away_team=game.away_team,
                raw_stats=raw_payload,
                allow_network=self.allow_network,
                trait_mode=trait_mode,
                refresh=force,
            )
        except Exception as exc:
            raise GameServiceError(500, f"Failed to generate game stats: {exc}") from exc

        parsed = json.loads(fresh["stats"])
        metadata = parsed.setdefault("meta", {})
        metadata["snapshot_at"] = cache_policy.oldest(
            [metadata.get("snapshot_at"), raw.created_at.replace(tzinfo=UTC).timestamp()]
        )
        metadata["stale"] = not cache_policy.is_fresh(
            game.game_date.year, metadata["snapshot_at"]
        )
        fresh["stats"] = json.dumps(parsed)
        if generated:
            self.session.delete(generated)
            self.session.commit()
        generated = models.GameGenerated(
            game_id=game.id, stats=fresh["stats"], game_text=fresh["game_text"]
        )
        self.session.add(generated)
        game.updated_at = datetime.now(UTC)
        self.session.add(game)
        self.session.commit()
        self.session.refresh(game)
        return GenerationResult(game, generated.stats, generated.game_text, False)

    def play_json(self, game_id: str) -> dict:
        game = self._game(game_id)
        generated = self.session.exec(
            select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)
        ).first()
        if generated is None:
            raise GameServiceError(404, "Generate the game before exporting it for Deadball Play")
        if not game.away_team or not game.home_team:
            raise GameServiceError(422, "Game is missing team identity required for gameplay export")
        arguments = self._play_arguments(game)
        try:
            return self.play_builder(generated.stats, **arguments).to_dict()
        except (TypeError, ValueError, json.JSONDecodeError) as cached_error:
            raw = self.session.exec(
                select(models.GameRawStats).where(models.GameRawStats.game_id == game.id)
            ).first()
            if raw is None:
                raise GameServiceError(
                    422, f"Generated game cannot be exported for play: {cached_error}"
                ) from cached_error
        try:
            parsed = json.loads(generated.stats)
            trait_mode = parsed.get("meta", {}).get("trait_mode", "standard")
            for include_reserves in (True, False):
                rebuilt = self.generator(
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
                    return self.play_builder(rebuilt["stats"], **arguments).to_dict()
                except (TypeError, ValueError, json.JSONDecodeError):
                    if not include_reserves:
                        raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GameServiceError(
                422, f"Generated game cannot be regenerated for play: {exc}"
            ) from exc
        raise AssertionError("unreachable")

    def scorecard_pdf(self, game_id: str, *, side: str = "home") -> bytes:
        if side not in {"home", "away"}:
            raise GameServiceError(422, "Scorecard side must be home or away")
        game = self._game(game_id)
        generated = self.session.exec(
            select(models.GameGenerated).where(models.GameGenerated.game_id == game.id)
        ).first()
        if generated is None:
            raise GameServiceError(404, "Generated stats not found for this game; generate first.")
        try:
            values = self.scorecard_builder(game, generated.stats)
            return self.pdf_renderer(values)
        except GameServiceError:
            raise
        except Exception as exc:
            raise GameServiceError(500, f"Failed to build scorecard PDF: {exc}") from exc

    def get_game(self, game_id: str) -> models.Game:
        """Return one persisted game or the service's canonical not-found error."""
        return self._game(game_id)

    def _game(self, game_id: str) -> models.Game:
        game = self.session.exec(
            select(models.Game).where(models.Game.game_id == game_id)
        ).first()
        if game is None:
            raise GameServiceError(404, "Game not found; list games first")
        return game

    def _fetch_boxscore(
        self, game: models.Game, raw: models.GameRawStats | None
    ) -> models.GameRawStats:
        try:
            response = self.http_get(
                f"https://statsapi.mlb.com/api/v1/game/{game.game_id}/boxscore", timeout=10
            )
            response.raise_for_status()
        except Exception as exc:
            raise GameServiceError(502, f"Failed to fetch boxscore: {exc}") from exc
        if raw is None:
            raw = models.GameRawStats(game_id=game.id, payload=response.text)
        else:
            raw.payload = response.text
            raw.created_at = datetime.now(UTC)
        self.session.add(raw)
        self.session.commit()
        return raw

    def _backfill_teams(self, game: models.Game, raw_payload: str) -> None:
        if not raw_payload or not (
            not game.home_team
            or not game.away_team
            or not game.home_team_short
            or not game.away_team_short
            or game.home_team_short == game.home_team
            or game.away_team_short == game.away_team
        ):
            return
        try:
            teams = json.loads(raw_payload).get("teams", {})
            home, home_short = _extract_team_labels(teams.get("home", {}).get("team"))
            away, away_short = _extract_team_labels(teams.get("away", {}).get("team"))
        except Exception:
            return
        updated = False
        for attribute, value in (
            ("home_team", home),
            ("away_team", away),
        ):
            if value and not getattr(game, attribute):
                setattr(game, attribute, value)
                updated = True
        for attribute, long_attribute, value in (
            ("home_team_short", "home_team", home_short),
            ("away_team_short", "away_team", away_short),
        ):
            if value and (
                not getattr(game, attribute)
                or getattr(game, attribute) == getattr(game, long_attribute)
            ):
                setattr(game, attribute, value)
                updated = True
        if updated:
            game.updated_at = datetime.now(UTC)
            self.session.add(game)
            self.session.commit()
            self.session.refresh(game)

    @staticmethod
    def _play_arguments(game: models.Game) -> dict:
        return {
            "game_id": game.game_id,
            "game_date": str(game.game_date),
            "game_type": game.game_type,
            "away_team": game.away_team,
            "home_team": game.home_team,
            "away_short": game.away_team_short,
            "home_short": game.home_team_short,
        }
