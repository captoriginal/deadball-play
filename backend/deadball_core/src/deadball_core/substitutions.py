"""Second Edition substitutions and defensive-position changes."""

from __future__ import annotations

from dataclasses import replace

from .events import ActionResult, RuleTraceEntry, SubstitutionEvent
from .game_data import PlayerData, TeamData
from .state import DefensiveAssignment, GameState, InitialTeamState, PitcherState


DEFENSIVE_POSITIONS = frozenset({"P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"})
INFIELD_POSITIONS = frozenset({"1B", "2B", "3B", "SS"})
OUTFIELD_POSITIONS = frozenset({"LF", "CF", "RF"})


class SubstitutionError(ValueError):
    """Raised when a roster move is illegal from the supplied state."""


def pinch_hit(state: GameState, replacement_id: str) -> ActionResult:
    """Replace the current batter without changing the fixed lineup slot."""
    _ensure_game_active(state)
    side = _offense_side(state)
    team, data = _team_and_data(state, side)
    slot = team.batting_order_index
    outgoing_id = team.lineup[slot]
    replacement = _available_position_player(team, data, replacement_id)
    updated = _replace_lineup_player(team, outgoing_id, replacement.player_id)
    updated = _replace_defender_player(updated, outgoing_id, replacement.player_id)
    updated = _remove_from_bench(updated, replacement.player_id)
    updated = _retire_player(updated, outgoing_id)
    updated = _vacate_pitcher_if_needed(updated, outgoing_id)
    return _result(
        state,
        side,
        updated,
        SubstitutionEvent(
            "pinch_hit",
            team.team_id,
            replacement.player_id,
            outgoing_id,
            slot + 1,
        ),
        f"{replacement.player_id} pinch hits for {outgoing_id} in lineup slot {slot + 1}",
    )


def pinch_run(state: GameState, base: str, replacement_id: str) -> ActionResult:
    """Replace a runner on base and in that runner's fixed lineup slot."""
    _ensure_game_active(state)
    base_index = _base_index(base)
    base_name = ("1B", "2B", "3B")[base_index]
    outgoing_id = state.bases[base_index]
    if outgoing_id is None:
        raise SubstitutionError(f"no runner occupies {base_name}")
    side = _offense_side(state)
    team, data = _team_and_data(state, side)
    replacement = _available_position_player(team, data, replacement_id)
    try:
        slot = team.lineup.index(outgoing_id)
    except ValueError as exc:
        raise SubstitutionError(f"runner {outgoing_id!r} is not in the active lineup") from exc
    updated = _replace_lineup_player(team, outgoing_id, replacement.player_id)
    updated = _replace_defender_player(updated, outgoing_id, replacement.player_id)
    updated = _remove_from_bench(updated, replacement.player_id)
    updated = _retire_player(updated, outgoing_id)
    updated = _vacate_pitcher_if_needed(updated, outgoing_id)
    bases = list(state.bases)
    bases[base_index] = replacement.player_id
    return _result(
        replace(state, bases=tuple(bases)),
        side,
        updated,
        SubstitutionEvent(
            "pinch_run",
            team.team_id,
            replacement.player_id,
            outgoing_id,
            slot + 1,
            base=base_name,
        ),
        f"{replacement.player_id} pinch runs for {outgoing_id} at {base_name}",
    )


def defensive_substitution(
    state: GameState, side: str, position: str, replacement_id: str
) -> ActionResult:
    """Replace a non-pitcher fielder and inherit the outgoing lineup slot."""
    _ensure_game_active(state)
    position = position.upper()
    if position not in DEFENSIVE_POSITIONS or position == "P":
        raise SubstitutionError("defensive substitution requires a non-pitcher field position")
    team, data = _team_and_data(state, side)
    outgoing_id = _assignment(team, position).player_id
    replacement = _available_position_player(team, data, replacement_id)
    try:
        slot = team.lineup.index(outgoing_id)
    except ValueError as exc:
        raise SubstitutionError(f"fielder {outgoing_id!r} is not in the active lineup") from exc
    updated = _replace_lineup_player(team, outgoing_id, replacement.player_id)
    updated = replace(
        updated,
        active_defense=tuple(
            DefensiveAssignment(item.position, replacement.player_id)
            if item.position == position else item
            for item in updated.active_defense
        ),
    )
    updated = _remove_from_bench(updated, replacement.player_id)
    updated = _retire_player(updated, outgoing_id)
    return _result(
        state,
        side,
        updated,
        SubstitutionEvent(
            "defensive_substitution",
            team.team_id,
            replacement.player_id,
            outgoing_id,
            slot + 1,
            position,
        ),
        f"{replacement.player_id} replaces {outgoing_id} at {position}",
    )


def pitching_change(state: GameState, side: str, replacement_id: str) -> ActionResult:
    """Install an available pitcher, resetting persistent pitcher state."""
    _ensure_game_active(state)
    team, data = _team_and_data(state, side)
    if not pitcher_may_be_replaced(state, side):
        faced = team.pitcher_state.batters_faced_since_entry if team.pitcher_state else 0
        raise SubstitutionError(
            "three-batter minimum: the active pitcher must face "
            f"{3 - faced} more batter{'s' if 3 - faced != 1 else ''} or finish the half-inning"
        )
    replacement = _available_pitcher(team, data, replacement_id)
    outgoing_id = team.active_pitcher_id
    lineup = team.lineup
    removed = list(team.removed_players)
    if outgoing_id is not None and outgoing_id not in removed:
        removed.append(outgoing_id)

    lineup_slot = team.pitcher_lineup_slot
    outgoing_lineup_id = None
    if lineup_slot is not None:
        outgoing_lineup_id = lineup[lineup_slot]
        lineup = (*lineup[:lineup_slot], replacement.player_id, *lineup[lineup_slot + 1:])
        if outgoing_lineup_id != outgoing_id and outgoing_lineup_id not in removed:
            removed.append(outgoing_lineup_id)

    pitch_die = replacement.pitch_die
    if pitch_die is None:
        raise SubstitutionError(f"pitcher {replacement.player_id!r} has no Pitch Die")
    updated = replace(
        team,
        lineup=lineup,
        active_defense=tuple(
            DefensiveAssignment("P", replacement.player_id)
            if item.position == "P" else item
            for item in team.active_defense
        ),
        bullpen=tuple(
            player_id
            for player_id in team.bullpen
            if player_id != replacement.player_id
        ),
        active_pitcher_id=replacement.player_id,
        active_pitch_die=pitch_die,
        pitcher_state=PitcherState(
            player_id=replacement.player_id,
            role=replacement.role,
            base_pitch_die=pitch_die,
            current_pitch_die=pitch_die,
        ),
        removed_players=tuple(removed),
    )
    details = () if outgoing_lineup_id in {None, outgoing_id} else (
        f"replaced temporary lineup occupant {outgoing_lineup_id}",
    )
    return _result(
        state,
        side,
        updated,
        SubstitutionEvent(
            "pitching_change",
            team.team_id,
            replacement.player_id,
            outgoing_id,
            None if lineup_slot is None else lineup_slot + 1,
            "P",
            details=details,
        ),
        f"{replacement.player_id} replaces {outgoing_id or 'the vacated pitcher'} on the mound",
    )


def pitcher_may_be_replaced(state: GameState, side: str) -> bool:
    """Return whether the optional MLB-style minimum permits a pitching change."""
    team, _ = _team_and_data(state, side)
    if not state.source.rules.three_batter_minimum or team.active_pitcher_id is None:
        return True
    progress = team.pitcher_state
    if progress is None:
        return True
    return (
        progress.batters_faced_since_entry >= 3
        or progress.inning_end_removal_window
        or progress.removal_exception in {"injury", "illness"}
    )


def switch_defensive_positions(
    state: GameState, side: str, first_position: str, second_position: str
) -> ActionResult:
    """Swap two non-pitcher field assignments without changing the lineup."""
    _ensure_game_active(state)
    first_position = first_position.upper()
    second_position = second_position.upper()
    if first_position == second_position:
        raise SubstitutionError("position switch requires two different positions")
    if "P" in {first_position, second_position}:
        raise SubstitutionError("use pitching_change to change the pitcher")
    team, _ = _team_and_data(state, side)
    first = _assignment(team, first_position)
    second = _assignment(team, second_position)
    updated = replace(
        team,
        active_defense=tuple(
            DefensiveAssignment(item.position, second.player_id)
            if item.position == first_position
            else DefensiveAssignment(item.position, first.player_id)
            if item.position == second_position
            else item
            for item in team.active_defense
        ),
    )
    return _result(
        state,
        side,
        updated,
        SubstitutionEvent(
            "position_change",
            team.team_id,
            details=(
                f"{first.player_id}: {first_position} to {second_position}",
                f"{second.player_id}: {second_position} to {first_position}",
            ),
        ),
        f"{first.player_id} and {second.player_id} switch {first_position}/{second_position}",
    )


def effective_defensive_traits(state: GameState, position: str) -> tuple[str, ...]:
    """Return traits after applying the cross-infield/outfield D- penalty."""
    side = _defense_side(state)
    team, data = _team_and_data(state, side)
    player = data.player(_assignment(team, position.upper()).player_id)
    if any(
        injury.player_id == player.player_id and injury.traits_nullified
        for injury in state.oddity_state.injuries
    ):
        return ("D-",) if player.player_id in state.oddity_state.poor_defenders else ()
    if player.player_id in state.oddity_state.poor_defenders:
        traits = tuple(trait for trait in player.traits if trait != "D+")
        return traits if "D-" in traits else (*traits, "D-")
    if _plays_position_without_penalty(player, position.upper()):
        return player.traits
    traits = tuple(trait for trait in player.traits if trait != "D+")
    return traits if "D-" in traits else (*traits, "D-")


def validate_team_state(state: GameState, side: str) -> None:
    """Validate the active-roster invariants required after every transaction."""
    team, data = _team_and_data(state, side)
    if len(team.lineup) != 9 or len(set(team.lineup)) != 9:
        raise SubstitutionError("active lineup must contain nine unique players")
    assignments = team.active_defense
    positions = [item.position for item in assignments]
    players = [item.player_id for item in assignments]
    if set(positions) != DEFENSIVE_POSITIONS or len(positions) != 9:
        raise SubstitutionError("active defense must assign every field position exactly once")
    if len(set(players)) != 9:
        raise SubstitutionError("a player cannot occupy two defensive positions")
    roster_ids = {player.player_id for player in data.roster}
    active_ids = set(team.lineup) | set(players)
    if not active_ids <= roster_ids:
        raise SubstitutionError("active players must belong to the team roster")
    if not (set(team.bench) | set(team.bullpen) | set(team.removed_players)) <= roster_ids:
        raise SubstitutionError(
            "all active, available, and removed players must belong to the team roster"
        )
    if len(team.removed_players) != len(set(team.removed_players)):
        raise SubstitutionError("removed-player history contains a duplicate")
    if set(team.removed_players) & active_ids:
        raise SubstitutionError("removed players cannot re-enter the active lineup or defense")
    available = set(team.bench) | set(team.bullpen)
    if available & (active_ids | set(team.removed_players)):
        raise SubstitutionError("available reserves overlap active or removed players")
    pitcher_assignment = _assignment(team, "P").player_id
    if team.active_pitcher_id is None:
        if team.active_pitch_die is not None or team.pitcher_state is not None:
            raise SubstitutionError("vacated pitcher must not retain pitcher state")
    else:
        if pitcher_assignment != team.active_pitcher_id:
            raise SubstitutionError("active pitcher and P assignment disagree")
        if team.pitcher_state is None or team.active_pitch_die is None:
            raise SubstitutionError("active pitcher requires a Pitch Die and pitcher state")
        if team.pitcher_state.player_id != team.active_pitcher_id:
            raise SubstitutionError("active pitcher and pitcher state disagree")


def _result(
    state: GameState,
    side: str,
    team: InitialTeamState,
    event: SubstitutionEvent,
    detail: str,
) -> ActionResult:
    new_state = replace(state, **{side: team})
    validate_team_state(new_state, side)
    return ActionResult(
        event,
        new_state,
        None,
        (RuleTraceEntry("substitution", detail, "Second Edition p. 23"),),
    )


def _team_and_data(state: GameState, side: str) -> tuple[InitialTeamState, TeamData]:
    if side == "away":
        return state.away, state.source.teams.away
    if side == "home":
        return state.home, state.source.teams.home
    raise SubstitutionError(f"unknown team side {side!r}")


def _ensure_game_active(state: GameState) -> None:
    if state.is_final:
        raise SubstitutionError("game is final")


def _offense_side(state: GameState) -> str:
    if state.half == "top":
        return "away"
    if state.half == "bottom":
        return "home"
    raise SubstitutionError(f"unknown half inning {state.half!r}")


def _defense_side(state: GameState) -> str:
    return "home" if _offense_side(state) == "away" else "away"


def _available_position_player(
    team: InitialTeamState, data: TeamData, player_id: str
) -> PlayerData:
    _ensure_no_reentry(team, player_id)
    if player_id not in team.bench:
        raise SubstitutionError(f"position player {player_id!r} is not available from the bench")
    player = _roster_player(data, player_id)
    if player.role != "position_player":
        raise SubstitutionError(f"{player_id!r} is not a position player")
    return player


def _available_pitcher(
    team: InitialTeamState, data: TeamData, player_id: str
) -> PlayerData:
    _ensure_no_reentry(team, player_id)
    if player_id not in team.bullpen:
        raise SubstitutionError(f"pitcher {player_id!r} is not available from the bullpen")
    player = _roster_player(data, player_id)
    if player.role not in {"starter", "reliever"}:
        raise SubstitutionError(f"{player_id!r} is not a pitcher")
    return player


def _roster_player(data: TeamData, player_id: str) -> PlayerData:
    try:
        return data.player(player_id)
    except KeyError as exc:
        raise SubstitutionError(f"player {player_id!r} is not on {data.team_id}'s roster") from exc


def _ensure_no_reentry(team: InitialTeamState, player_id: str) -> None:
    if player_id in team.removed_players:
        raise SubstitutionError(f"removed player {player_id!r} cannot re-enter")


def _replace_lineup_player(
    team: InitialTeamState, outgoing_id: str, incoming_id: str
) -> InitialTeamState:
    try:
        slot = team.lineup.index(outgoing_id)
    except ValueError as exc:
        raise SubstitutionError(f"player {outgoing_id!r} is not in the active lineup") from exc
    return replace(
        team,
        lineup=(*team.lineup[:slot], incoming_id, *team.lineup[slot + 1:]),
    )


def _replace_defender_player(
    team: InitialTeamState, outgoing_id: str, incoming_id: str
) -> InitialTeamState:
    return replace(
        team,
        active_defense=tuple(
            DefensiveAssignment(item.position, incoming_id)
            if item.player_id == outgoing_id else item
            for item in team.active_defense
        ),
    )


def _remove_from_bench(team: InitialTeamState, player_id: str) -> InitialTeamState:
    return replace(team, bench=tuple(item for item in team.bench if item != player_id))


def _retire_player(team: InitialTeamState, player_id: str) -> InitialTeamState:
    if player_id in team.removed_players:
        return team
    return replace(team, removed_players=(*team.removed_players, player_id))


def _vacate_pitcher_if_needed(
    team: InitialTeamState, outgoing_id: str
) -> InitialTeamState:
    if team.active_pitcher_id != outgoing_id:
        return team
    return replace(
        team,
        active_pitcher_id=None,
        active_pitch_die=None,
        pitcher_state=None,
    )


def _assignment(team: InitialTeamState, position: str) -> DefensiveAssignment:
    for item in team.active_defense:
        if item.position == position:
            return item
    raise SubstitutionError(f"active defense has no {position}")


def _base_index(base: str) -> int:
    normalized = base.upper()
    try:
        return {"1B": 0, "2B": 1, "3B": 2}[normalized]
    except KeyError as exc:
        raise SubstitutionError(f"unknown base {base!r}") from exc


def _plays_position_without_penalty(player: PlayerData, position: str) -> bool:
    natural = set(player.positions)
    if "UT" in natural and position != "P":
        return True
    if position in natural or (position in OUTFIELD_POSITIONS and "OF" in natural):
        return True
    if position in INFIELD_POSITIONS and natural & INFIELD_POSITIONS:
        return True
    if position in OUTFIELD_POSITIONS and natural & (OUTFIELD_POSITIONS | {"OF"}):
        return True
    return False
