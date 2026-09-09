"""Published Managerial Daring and Deadball Play solo-manager procedures."""

from __future__ import annotations

from dataclasses import dataclass

from .dice import DiceSource
from .game_data import PlayerData, TeamData
from .pitching import PITCH_DIE_LADDER
from .rules import legal_actions
from .state import GameState, InitialTeamState


class ManagerError(ValueError):
    """Raised when a manager decision cannot be made from the supplied input."""


@dataclass(frozen=True)
class ManagerState:
    daring: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.daring, bool)
            or not isinstance(self.daring, int)
            or not 1 <= self.daring <= 19
        ):
            raise ManagerError("Daring must be between 1 and 19")


@dataclass(frozen=True)
class ManagerOpportunity:
    decision_type: str
    risky_choice: str
    conservative_choice: str
    reason: str


@dataclass(frozen=True)
class ManagerDecision:
    decision_type: str
    daring: int
    roll: int
    risky_choice: str
    conservative_choice: str
    selected_choice: str
    reason: str

    @property
    def chose_daring(self) -> bool:
        return self.selected_choice == self.risky_choice


def generate_manager_daring(dice: DiceSource) -> int:
    """Generate a published 1-19 Daring rating, treating a roll of 20 as 19."""
    return min(dice.roll(20), 19)


def resolve_daring(
    daring: int,
    risky_choice: str,
    conservative_choice: str,
    dice: DiceSource,
    *,
    decision_type: str = "managerial_daring",
    reason: str = "published Managerial Daring decision",
) -> ManagerDecision:
    """Resolve the published d20 <= Daring rule without applying game policy."""
    if isinstance(daring, bool) or not isinstance(daring, int) or not 1 <= daring <= 19:
        raise ManagerError("Daring must be between 1 and 19")
    if not risky_choice or not conservative_choice:
        raise ManagerError("both manager choices must be named")
    if risky_choice == conservative_choice:
        raise ManagerError("risky and conservative choices must differ")
    roll = dice.roll(20)
    selected = risky_choice if roll <= daring else conservative_choice
    return ManagerDecision(
        decision_type,
        daring,
        roll,
        risky_choice,
        conservative_choice,
        selected,
        reason,
    )


def decide_opportunity(
    opportunity: ManagerOpportunity, daring: int, dice: DiceSource
) -> ManagerDecision:
    """Apply published Daring math to a documented application opportunity."""
    return resolve_daring(
        daring,
        opportunity.risky_choice,
        opportunity.conservative_choice,
        dice,
        decision_type=opportunity.decision_type,
        reason=opportunity.reason,
    )


def offensive_opportunity(
    state: GameState, *, aggressive_steal_home: bool = False
) -> ManagerOpportunity | None:
    """Return the next Version 1 offensive decision without rolling dice."""
    if state.is_final:
        return None
    actions = set(legal_actions(state))
    first, second, third = state.bases
    score_difference = abs(state.away_score - state.home_score)

    if aggressive_steal_home and "steal_home" in actions:
        return ManagerOpportunity(
            "steal_home",
            "steal_home",
            "swing",
            "aggressive steal-home automation enabled for an eligible runner",
        )

    # A conventional late/close bunt situation takes precedence because its
    # Daring polarity is the inverse of the other offensive tactics.
    if (
        "bunt" in actions
        and state.inning >= 5
        and state.outs < 2
        and score_difference <= 2
        and (first is not None or second is not None)
    ):
        return ManagerOpportunity(
            "bunt",
            "swing",
            "bunt",
            "inning 5 or later, score within two runs, runner on first or second",
        )

    if "hit_and_run" in actions and state.outs < 2:
        return ManagerOpportunity(
            "hit_and_run",
            "hit_and_run",
            "swing",
            "runner on first, other bases empty, fewer than two outs",
        )
    if "steal_third" in actions and state.outs < 2:
        return ManagerOpportunity(
            "steal_third",
            "steal_third",
            "swing",
            "runner on second, third base open, fewer than two outs",
        )
    if "steal_second" in actions:
        return ManagerOpportunity(
            "steal_second",
            "steal_second",
            "swing",
            "runner on first and second base open",
        )
    return None


def pitching_opportunity(
    state: GameState,
    side: str,
    *,
    at_inning_boundary: bool = False,
) -> ManagerOpportunity | None:
    """Return the next documented pitcher-use decision without rolling dice."""
    if state.is_final:
        return None
    from .substitutions import pitcher_may_be_replaced

    if not pitcher_may_be_replaced(state, side):
        return None
    team, _ = _team_and_data(state, side)
    pitcher = team.pitcher_state
    if pitcher is None or not team.bullpen:
        return None

    if pitcher.role == "starter" and state.inning < 5 and (
        pitcher.runs_allowed >= 4
        or _pitch_die_index(pitcher.current_pitch_die)
        <= _pitch_die_index(pitcher.base_pitch_die) - 2
    ):
        return ManagerOpportunity(
            "early_starter_hook",
            "change_pitcher",
            "leave_pitcher",
            "starter before the fifth has allowed four runs or lost two Pitch Die levels",
        )
    if (
        pitcher.role == "starter"
        and at_inning_boundary
        and pitcher.completed_innings >= 6
    ):
        return ManagerOpportunity(
            "starter_after_sixth",
            "leave_pitcher",
            "change_pitcher",
            "starter has completed at least six innings",
        )
    if (
        pitcher.role == "reliever"
        and at_inning_boundary
        and pitcher.completed_innings == 1
    ):
        return ManagerOpportunity(
            "reliever_second_inning",
            "leave_pitcher",
            "change_pitcher",
            "reliever has completed one inning",
        )
    return None


def select_replacement_pitcher(
    state: GameState,
    side: str,
    *,
    upcoming_batter_id: str | None = None,
) -> str | None:
    """Choose the best available base Pitch Die, then handedness, then roster order."""
    team, data = _team_and_data(state, side)
    if state.is_final or not team.bullpen:
        return None
    batter = _upcoming_batter(state, side, upcoming_batter_id)
    candidates = [data.player(player_id) for player_id in team.bullpen]
    best = max(
        enumerate(candidates),
        key=lambda item: (
            _pitch_die_index(_required_pitch_die(item[1])),
            _handedness_advantage(item[1], batter),
            -item[0],
        ),
    )[1]
    return best.player_id


def _team_and_data(state: GameState, side: str) -> tuple[InitialTeamState, TeamData]:
    if side == "away":
        return state.away, state.source.teams.away
    if side == "home":
        return state.home, state.source.teams.home
    raise ManagerError(f"unknown team side {side!r}")


def _upcoming_batter(
    state: GameState, pitching_side: str, player_id: str | None
) -> PlayerData | None:
    batting_data = (
        state.source.teams.home if pitching_side == "away"
        else state.source.teams.away
    )
    if player_id is None:
        batting_state = state.home if pitching_side == "away" else state.away
        player_id = batting_state.lineup[batting_state.batting_order_index]
    try:
        return batting_data.player(player_id)
    except KeyError as exc:
        raise ManagerError(
            f"upcoming batter {player_id!r} is not on the opposing roster"
        ) from exc


def _required_pitch_die(pitcher: PlayerData) -> str:
    if pitcher.pitch_die is None:
        raise ManagerError(f"pitcher {pitcher.player_id!r} has no Pitch Die")
    return pitcher.pitch_die


def _pitch_die_index(pitch_die: str) -> int:
    try:
        return PITCH_DIE_LADDER.index(pitch_die)
    except ValueError as exc:
        raise ManagerError(f"unknown Pitch Die {pitch_die!r}") from exc


def _handedness_advantage(pitcher: PlayerData, batter: PlayerData | None) -> int:
    if batter is None or batter.bats == "S":
        return 0
    return int(pitcher.throws == batter.bats)
