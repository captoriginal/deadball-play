"""Regulation, extra-inning, and game-ending state transitions."""

from __future__ import annotations

from dataclasses import replace

from .state import GameResult, GameState


EMPTY_BASES = (None, None, None)


def finish_action_state(
    working: GameState,
    original: GameState,
    bases: tuple[str | None, str | None, str | None],
    *,
    outs_added: int,
    runs: int,
) -> GameState:
    """Apply score and outs, then advance or finish the game."""
    if original.is_final:
        raise ValueError("cannot advance a final game")
    if original.half not in {"top", "bottom"}:
        raise ValueError(f"unknown half inning {original.half!r}")
    if not 0 <= original.outs <= 2:
        raise ValueError("active game outs must be between zero and two")
    if not 0 <= outs_added <= 3 - original.outs:
        raise ValueError("outs added exceed the outs remaining in the half inning")
    if runs < 0:
        raise ValueError("runs cannot be negative")

    score_field = "away_score" if original.half == "top" else "home_score"
    updated = replace(
        working,
        bases=bases,
        outs=original.outs + outs_added,
        **{score_field: getattr(original, score_field) + runs},
    )

    if _is_walk_off(updated, original):
        return _finalize(updated, "walk_off")
    if updated.outs < 3:
        return updated

    if original.half == "top":
        if original.inning >= 9 and updated.home_score > updated.away_score:
            return _finalize(updated, _completed_game_ending(original.inning))
        return replace(
            updated,
            half="bottom",
            outs=0,
            bases=_half_inning_bases(updated, original.inning, "home"),
        )

    if original.inning >= 9 and updated.home_score != updated.away_score:
        return _finalize(updated, _completed_game_ending(original.inning))
    return replace(
        updated,
        inning=original.inning + 1,
        half="top",
        outs=0,
        bases=_half_inning_bases(updated, original.inning + 1, "away"),
    )


def _half_inning_bases(
    state: GameState,
    inning: int,
    offense: str,
) -> tuple[str | None, str | None, str | None]:
    """Return MLB's automatic runner for regular-season extra innings."""
    game_type = state.source.game.game_type
    regular_season = game_type is None or game_type.upper() == "R"
    if inning < 10 or not regular_season:
        return EMPTY_BASES
    team = getattr(state, offense)
    runner_index = (team.batting_order_index - 1) % len(team.lineup)
    return None, team.lineup[runner_index], None


def _is_walk_off(updated: GameState, original: GameState) -> bool:
    return (
        original.half == "bottom"
        and original.inning >= 9
        and updated.home_score > updated.away_score
    )


def _completed_game_ending(inning: int) -> str:
    return "regulation" if inning == 9 else "extra_innings"


def _finalize(state: GameState, ending: str) -> GameState:
    winner = state.home.team_id if state.home_score > state.away_score else state.away.team_id
    return replace(
        state,
        bases=EMPTY_BASES,
        result=GameResult(winner, ending, state.inning, state.half),
    )
