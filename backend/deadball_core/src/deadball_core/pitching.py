"""Persistent Second Edition pitcher improvement and fatigue state."""

from __future__ import annotations

from dataclasses import replace

from .events import PlayEvent, StealEvent
from .game_data import PlayerData
from .state import GameState, InitialTeamState, PitchDieAdjustment, PitcherState


PITCH_DIE_LADDER = ("-d20", "-d12", "-d8", "-d4", "d4", "d8", "d12", "d20")


def step_pitch_die(pitch_die: str, levels: int) -> str:
    """Move a Pitch Die along the printed ladder, clamped at both ends."""
    if pitch_die not in PITCH_DIE_LADDER:
        raise ValueError(f"unknown Pitch Die {pitch_die!r}")
    index = PITCH_DIE_LADDER.index(pitch_die)
    return PITCH_DIE_LADDER[max(0, min(len(PITCH_DIE_LADDER) - 1, index + levels))]


def apply_pitcher_progress(
    before: GameState,
    after: GameState,
    event: PlayEvent | StealEvent,
) -> GameState:
    """Apply one completed action to the pitcher who defended it."""
    before_team, after_team, pitcher = _defending_team(before, after)
    progress = before_team.pitcher_state
    if progress is None or before_team.active_pitcher_id is None:
        raise ValueError("a pitcher must be installed before resolving an action")
    if before_team.active_pitch_die is None:
        raise ValueError("active pitcher has no Pitch Die")
    if progress.player_id != before_team.active_pitcher_id:
        raise ValueError("active pitcher and pitcher state disagree")
    if progress.current_pitch_die != before_team.active_pitch_die:
        progress = replace(progress, current_pitch_die=before_team.active_pitch_die)

    inning_ended = (
        (before.inning, before.half) != (after.inning, after.half)
        or (after.is_final and before.outs + event.outs_added >= 3)
    )
    plate_appearance = isinstance(event, PlayEvent) and event.resolved
    strikeout = plate_appearance and (
        event.event_type == "strikeout" or event.out_type == "strikeout"
    )
    jam_active = progress.bases_loaded_no_out_jam or (
        before.outs == 0 and all(runner is not None for runner in before.bases)
    )
    inning_runs = progress.current_inning_runs + event.runs_scored
    runs_since_jam = progress.runs_since_jam + (
        event.runs_scored if jam_active else 0
    )
    old_outs = progress.outs_recorded
    new_outs = old_outs + event.outs_added
    old_runs = progress.runs_allowed
    new_runs = old_runs + event.runs_scored
    progress = replace(
        progress,
        outs_recorded=new_outs,
        runs_allowed=new_runs,
        current_inning_runs=inning_runs,
        current_inning_batters_faced=(
            progress.current_inning_batters_faced + int(plate_appearance)
        ),
        batters_faced_since_entry=(
            progress.batters_faced_since_entry + int(plate_appearance)
        ),
        inning_end_removal_window=(
            False if plate_appearance else progress.inning_end_removal_window
        ),
        current_inning_strikeouts=(
            progress.current_inning_strikeouts + int(strikeout)
        ),
        bases_loaded_no_out_jam=jam_active,
        runs_since_jam=runs_since_jam,
    )

    # A newly created bases-loaded/no-out jam must be remembered for later plays.
    if not inning_ended and after.outs == 0 and all(
        runner is not None for runner in after.bases
    ):
        progress = replace(progress, bases_loaded_no_out_jam=True)

    progress = _apply_run_adjustments(progress, pitcher, before, old_runs)
    if pitcher.role == "reliever":
        out_levels = new_outs // 3 - old_outs // 3
        for _ in range(out_levels):
            progress = _adjust(progress, -1, "reliever_three_outs", before)
    if inning_ended:
        progress = _complete_inning(progress, pitcher, before)

    updated_team = replace(
        after_team,
        active_pitch_die=progress.current_pitch_die,
        pitcher_state=progress,
    )
    return replace(after, **({"home": updated_team} if before.half == "top" else {"away": updated_team}))


def _apply_run_adjustments(
    progress: PitcherState,
    pitcher: PlayerData,
    state: GameState,
    old_runs: int,
) -> PitcherState:
    if progress.runs_allowed == old_runs:
        return progress
    if pitcher.role == "reliever":
        for _ in range(progress.runs_allowed - old_runs):
            progress = _adjust(progress, -1, "reliever_run", state)
        return progress

    if state.inning >= 7 and not progress.late_run_reduction_applied:
        if PITCH_DIE_LADDER.index(progress.current_pitch_die) > PITCH_DIE_LADDER.index("d4"):
            progress = _set_die(progress, "d4", "seventh_inning_run", state)
        progress = replace(progress, late_run_reduction_applied=True)

    newly_over_four = max(0, progress.runs_allowed - 4) - max(0, old_runs - 4)
    for _ in range(newly_over_four):
        progress = _adjust(progress, -1, "run_over_four", state)
    return progress


def _complete_inning(
    progress: PitcherState, pitcher: PlayerData, state: GameState
) -> PitcherState:
    inning_runs = progress.current_inning_runs
    scoreless = progress.consecutive_scoreless_innings + 1 if inning_runs == 0 else 0

    if scoreless and scoreless % 3 == 0:
        progress = _adjust(progress, 1, "three_scoreless_innings", state)
    if (
        progress.current_inning_batters_faced > 0
        and progress.current_inning_batters_faced == progress.current_inning_strikeouts
    ):
        progress = _adjust(progress, 1, "strikeout_every_batter", state)
    if progress.bases_loaded_no_out_jam and progress.runs_since_jam == 0:
        progress = _adjust(progress, 1, "escaped_bases_loaded_no_out", state)

    if pitcher.role == "starter":
        if inning_runs >= 3:
            progress = _adjust(progress, -1, "three_runs_in_inning", state)
        if (
            progress.previous_inning_runs is not None
            and progress.previous_inning_runs + inning_runs >= 4
        ):
            progress = _adjust(progress, -1, "four_runs_over_two_innings", state)
        completed = progress.outs_recorded // 3
        fatigue_starts = 7 if "ST+" in pitcher.traits else 6
        fatigue_due = max(0, completed - fatigue_starts + 1)
        fatigue_applied = sum(
            adjustment.reason == "starter_innings_fatigue"
            for adjustment in progress.adjustments
        )
        for _ in range(max(0, fatigue_due - fatigue_applied)):
            progress = _adjust(progress, -1, "starter_innings_fatigue", state)
    else:
        completed = progress.outs_recorded // 3

    return replace(
        progress,
        completed_innings=completed,
        previous_inning_runs=inning_runs,
        current_inning_runs=0,
        current_inning_batters_faced=0,
        current_inning_strikeouts=0,
        consecutive_scoreless_innings=scoreless,
        bases_loaded_no_out_jam=False,
        runs_since_jam=0,
        late_run_reduction_applied=False,
        inning_end_removal_window=True,
    )


def _adjust(
    progress: PitcherState, levels: int, reason: str, state: GameState
) -> PitcherState:
    return _set_die(
        progress, step_pitch_die(progress.current_pitch_die, levels), reason, state
    )


def _set_die(
    progress: PitcherState, new_die: str, reason: str, state: GameState
) -> PitcherState:
    previous = progress.current_pitch_die
    adjustment = PitchDieAdjustment(reason, previous, new_die, state.inning, state.half)
    return replace(
        progress,
        current_pitch_die=new_die,
        adjustments=(*progress.adjustments, adjustment),
    )


def _defending_team(
    before: GameState, after: GameState
) -> tuple[InitialTeamState, InitialTeamState, PlayerData]:
    if before.half == "top":
        team = before.source.teams.home
        pitcher_id = before.home.active_pitcher_id
        if pitcher_id is None:
            raise ValueError("home team has no active pitcher")
        return before.home, after.home, team.player(pitcher_id)
    team = before.source.teams.away
    pitcher_id = before.away.active_pitcher_id
    if pitcher_id is None:
        raise ValueError("away team has no active pitcher")
    return before.away, after.away, team.player(pitcher_id)
