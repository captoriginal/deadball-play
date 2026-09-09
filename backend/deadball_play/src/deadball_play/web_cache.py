"""Read-only adapter from the local Deadball Web cache to gameplay data."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from deadball_core import GeneratedGame


class CachedGameError(ValueError):
    """Raised when a web-cached game cannot become a gameplay contract."""


def load_cached_game(game_id: str, database: str | Path) -> GeneratedGame:
    """Load one generated web game by MLB game ID without modifying its DB."""
    from deadball_core import build_generator_game

    database_path = Path(database)
    if not database_path.is_file():
        raise CachedGameError(f"Deadball Web database not found: {database_path}")
    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(game)").fetchall()
        }
        game_type_column = (
            "g.game_type" if "game_type" in columns else "NULL AS game_type"
        )
        with connection:
            row = connection.execute(
                f"""
                SELECT g.game_id, g.game_date, {game_type_column},
                       g.away_team, g.home_team,
                       g.away_team_short, g.home_team_short, gg.stats,
                       gr.payload AS raw_payload
                FROM game AS g
                JOIN gamegenerated AS gg ON gg.game_id = g.id
                LEFT JOIN gamerawstats AS gr ON gr.game_id = g.id
                WHERE g.game_id = ?
                """,
                (game_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise CachedGameError(f"could not read {database_path}: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()
    if row is None:
        raise CachedGameError(
            f"generated game {game_id!r} is not present in {database_path}"
        )
    if not row["away_team"] or not row["home_team"]:
        raise CachedGameError("cached game is missing team identity")
    arguments = {
        "game_id": row["game_id"],
        "game_date": row["game_date"],
        "game_type": row["game_type"],
        "away_team": row["away_team"],
        "home_team": row["home_team"],
        "away_short": row["away_team_short"],
        "home_short": row["home_team_short"],
    }
    try:
        return build_generator_game(row["stats"], **arguments)
    except (json.JSONDecodeError, TypeError, ValueError) as cached_error:
        if not row["raw_payload"]:
            raise CachedGameError(
                f"cached game {game_id!r} is not playable: {cached_error}"
            ) from cached_error
    try:
        from deadball_generator.generator import generate_game_from_raw

        old_stats = json.loads(row["stats"])
        trait_mode = old_stats.get("meta", {}).get("trait_mode", "standard")
        for include_reserves in (True, False):
            regenerated = generate_game_from_raw(
                game_id=row["game_id"],
                date=row["game_date"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                raw_stats=row["raw_payload"],
                allow_network=False,
                trait_mode=trait_mode,
                include_reserves=include_reserves,
            )
            try:
                return build_generator_game(regenerated["stats"], **arguments)
            except (json.JSONDecodeError, TypeError, ValueError):
                if not include_reserves:
                    raise
    except ImportError as exc:
        raise CachedGameError(
            "this cached game needs regeneration; run through the repository "
            "launcher so deadball_generator is available"
        ) from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CachedGameError(
            f"cached game {game_id!r} could not be regenerated offline: {exc}"
        ) from exc
