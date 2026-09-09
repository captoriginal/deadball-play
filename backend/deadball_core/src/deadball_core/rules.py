"""Deterministic Second Edition at-bat and hit resolution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re

from .dice import DiceSource
from .events import (
    ActionResult,
    BuntDiceRecord,
    DiceRecord,
    HitAndRunDiceRecord,
    PlayEvent,
    RuleTraceEntry,
    RunnerMove,
    StealDiceRecord,
    StealEvent,
)
from .game_flow import finish_action_state
from .pitching import apply_pitcher_progress, step_pitch_die
from .state import GameState, InjuryRecord, PitcherOddityModifier
from .substitutions import effective_defensive_traits


PITCH_DIE_LADDER = ("-d20", "-d12", "-d8", "-d4", "d4", "d8", "d12", "d20")


class RulesError(ValueError):
    """Raised when an action cannot be resolved from the supplied state."""


class AtBatClassification(str, Enum):
    ODDITY = "oddity"
    CRITICAL_HIT = "critical_hit"
    ORDINARY_HIT = "ordinary_hit"
    WALK = "walk"
    POSSIBLE_ERROR = "possible_error"
    OUT = "out"


@dataclass(frozen=True)
class OutTableResult:
    event_type: str
    fielded_by: str | None
    scoring_notation: str


@dataclass(frozen=True)
class HitTableResult:
    hit_type: str
    bases: int
    runner_advance: int | None = None
    defense_position: str | None = None


@dataclass(frozen=True)
class ResolvedHit:
    result: HitTableResult
    raw_roll: int
    modified_roll: int
    critical: bool


OUT_TABLE = {
    0: OutTableResult("strikeout", None, "K"),
    1: OutTableResult("strikeout", None, "K"),
    2: OutTableResult("strikeout", None, "K"),
    3: OutTableResult("groundout", "1B", "G-3"),
    4: OutTableResult("groundout", "2B", "4-3"),
    5: OutTableResult("groundout", "3B", "5-3"),
    6: OutTableResult("groundout", "SS", "6-3"),
    7: OutTableResult("flyout", "LF", "F-7"),
    8: OutTableResult("flyout", "CF", "F-8"),
    9: OutTableResult("flyout", "RF", "F-9"),
}


HIT_LEVELS = ("single", "double", "triple", "home_run")

ODDITY_NAMES = {
    2: "Fan Interference",
    3: "Animal on Field",
    4: "Rain Delay",
    5: "Fielder Appears Injured",
    6: "Pitcher Appears Injured",
    7: "TOOTBLAN",
    8: "Pick-Off",
    9: "Call Blown at First",
    10: "Call Blown at Home Plate",
    11: "Hit by Pitch",
    12: "Wild Pitch",
    13: "Pitcher Distracted",
    14: "Dropped Third Strike",
    15: "Passed Ball",
    16: "Current Batter Appears Injured",
    17: "Previous Batter Appears Injured",
    18: "Pitcher Error",
    19: "Balk",
    20: "Catcher Interference",
}

INJURY_LOCATIONS = (
    "head", "shoulder", "shoulder", "shoulder", "shoulder",
    "elbow", "elbow", "elbow", "elbow", "forearm", "wrist", "hand",
    "back", "back", "oblique", "hip", "hamstring", "knee", "ankle", "foot",
)


def legal_actions(state: GameState) -> tuple[str, ...]:
    """Return swing and the steals legal from the current base state."""
    if state.is_final:
        return ()
    defense = state.home if state.half == "top" else state.away
    if defense.active_pitcher_id is None or defense.active_pitch_die is None:
        return ()
    offense = state.away if state.half == "top" else state.home
    batter_id = offense.lineup[offense.batting_order_index]
    must_leave = {
        injury.player_id
        for injury in state.oddity_state.injuries
        if injury.must_leave
    }
    required_players = {
        batter_id,
        *(runner for runner in state.bases if runner is not None),
        *(assignment.player_id for assignment in defense.active_defense),
    }
    if must_leave & required_players:
        return ()
    actions = ["swing"]
    first, second, third = state.bases
    if any(runner is not None for runner in state.bases):
        actions.append("bunt")
    if first is not None and second is None and third is None:
        actions.append("hit_and_run")
    if first is not None and second is None:
        actions.append("steal_second")
    if second is not None and third is None:
        actions.append("steal_third")
    if first is not None and second is not None and third is None:
        actions.append("double_steal")
    if third is not None and "S+" in _effective_player_traits(
        state, third, _runner(state, third).traits
    ):
        actions.append("steal_home")
    return tuple(actions)


def resolve_bunt(state: GameState, dice: DiceSource) -> ActionResult:
    """Resolve the Second Edition Bunting Table as a plate appearance."""
    if "bunt" not in legal_actions(state):
        raise RulesError(f"bunt is not legal from bases {state.bases}")
    offense, defense, offense_data, defense_data = _active_sides(state)
    batter_id = offense.lineup[offense.batting_order_index]
    batter = offense_data.player(batter_id)
    pitcher = _active_pitcher(defense, defense_data)
    roll = dice.roll(6)
    batter_traits = _effective_player_traits(state, batter.player_id, batter.traits)
    modifier = 1 if "C+" in batter_traits else -1 if "C-" in batter_traits else 0
    modified = roll + modifier
    lead_index = max(index for index, runner in enumerate(state.bases) if runner is not None)
    lead_id = state.bases[lead_index]
    assert lead_id is not None
    trace = [RuleTraceEntry(
        "bunt_table",
        f"d6 {roll} + batter {modifier} = {modified}; lead runner at "
        f"{_base_name(lead_index + 1)}",
        "Second Edition pp. 24, 33",
    )]
    defense_roll = None
    modified_defense_roll = None

    if modified <= 2 or (modified == 3 and lead_index == 2):
        bases, moves = _bunt_lead_out_batter_safe(state, batter_id, lead_index)
        new_state = _finish_plate_appearance(state, bases, outs_added=1, runs=0)
        event = PlayEvent(
            "bunt_fielders_choice", "bunt", batter_id, pitcher.player_id, True,
            outs_added=1, batter_destination="1B", scoring_notation="FC",
            runner_moves=moves,
        )
    elif modified >= 6 and "S+" in batter_traits:
        defense_roll = dice.roll(12)
        defense_outcome, modified_defense_roll = resolve_defense_roll(
            defense_roll, effective_defensive_traits(state, "3B")
        )
        trace.append(RuleTraceEntry(
            "defense",
            f"3B rolled {defense_roll}, modified to {modified_defense_roll}: "
            f"{defense_outcome}",
            "Second Edition pp. 28, 33",
        ))
        if defense_outcome == "out":
            new_state = _finish_plate_appearance(
                state, state.bases, outs_added=1, runs=0
            )
            event = PlayEvent(
                "bunt_out", "bunt", batter_id, pitcher.player_id, True,
                outs_added=1, fielded_by="3B", defense_outcome="out",
            )
        else:
            runner_advance = 2 if defense_outcome == "error" else 1
            batter_bases = 2 if defense_outcome == "error" else 1
            new_state, moves, runs = _complete_hit(
                state, batter_id, batter_bases, runner_advance=runner_advance
            )
            event_type = "error" if defense_outcome == "error" else "single"
            event = PlayEvent(
                event_type, "bunt", batter_id, pitcher.player_id, True,
                batter_destination=_base_name(batter_bases), fielded_by="3B",
                scoring_notation="E-3B" if defense_outcome == "error" else "1B",
                hit_type="single", defense_outcome=defense_outcome,
                runner_moves=moves, runs_scored=runs,
            )
    else:
        bases = list(state.bases)
        destination = lead_index + 2
        runs = 0
        if destination == 4:
            bases[lead_index] = None
            scores = state.outs < 2
            moves = (RunnerMove(lead_id, "3B", "HOME", scored=scores),)
            runs = 1 if scores else 0
        else:
            bases[lead_index] = None
            bases[destination - 1] = lead_id
            moves = (
                RunnerMove(
                    lead_id, _base_name(lead_index + 1), _base_name(destination)
                ),
            )
        new_state = _finish_plate_appearance(
            state, tuple(bases), outs_added=1, runs=runs
        )
        event = PlayEvent(
            "sacrifice_bunt", "bunt", batter_id, pitcher.player_id, True,
            outs_added=1, scoring_notation="SAC", runner_moves=moves,
            runs_scored=runs,
        )

    record = BuntDiceRecord(
        roll, modifier, modified, defense_roll, modified_defense_roll
    )
    return _finalize_action(
        state, ActionResult(event, new_state, record, tuple(trace))
    )


def resolve_hit_and_run(state: GameState, dice: DiceSource) -> ActionResult:
    """Resolve simultaneous stealing and batting through the Hit & Run Table."""
    if "hit_and_run" not in legal_actions(state):
        raise RulesError(f"hit_and_run is not legal from bases {state.bases}")
    offense, defense, offense_data, defense_data = _active_sides(state)
    runner_id = state.bases[0]
    assert runner_id is not None
    batter_id = offense.lineup[offense.batting_order_index]
    batter = offense_data.player(batter_id)
    pitcher = _active_pitcher(defense, defense_data)
    if batter.bt is None or batter.obt is None or batter.bats is None:
        raise RulesError("current batter lacks required ratings or handedness")
    if pitcher.throws is None:
        raise RulesError("active pitcher lacks throwing hand")

    steal_roll = dice.roll(8)
    runner_modifier = _speed_modifier(
        _effective_player_traits(state, runner_id, _runner(state, runner_id).traits)
    )
    catcher_modifier = _catcher_steal_modifier(state)
    oddity_modifier = _oddity_steal_modifier(state)
    modified_steal = (
        steal_roll + runner_modifier + catcher_modifier + oddity_modifier
    )
    steal_success = modified_steal >= 4
    steal_record = StealDiceRecord(
        "hit_and_run", steal_roll, runner_modifier, 0,
        catcher_modifier, modified_steal,
    )

    base_pitch_die = _oddity_adjusted_pitch_die(state, defense.active_pitch_die, pitcher.player_id)
    pitch_die = effective_pitch_die(
        base_pitch_die, pitcher.role, pitcher.throws, batter.bats
    )
    sides, sign = _parse_pitch_die(pitch_die)
    swing_score = dice.roll(100)
    pitch_roll = dice.roll(sides)
    signed_pitch = sign * pitch_roll
    mss = swing_score + signed_pitch
    batter_traits = _effective_player_traits(state, batter.player_id, batter.traits)
    target_bonus = 10 if "C+" in batter_traits else 0 if "C-" in batter_traits else 5
    adjusted_bt = batter.bt + target_bonus
    adjusted_obt = batter.obt + target_bonus
    swing_record = DiceRecord(
        swing_score, pitch_die, pitch_roll, signed_pitch, mss
    )
    if state.source.rules.oddities and mss in {1, 99}:
        oddity_trace = [
            RuleTraceEntry(
                "hit_and_run_steal",
                f"d8 {steal_roll} + runner {runner_modifier} + catcher "
                f"{catcher_modifier} + oddity {oddity_modifier} = "
                f"{modified_steal}",
                "Second Edition pp. 31-33",
            ),
            RuleTraceEntry(
                "mss",
                f"{swing_score} + ({signed_pitch}) = {mss}",
                "Second Edition p. 26",
            ),
        ]
        oddity = _resolve_oddity(
            state,
            batter_id,
            pitcher.player_id,
            swing_record,
            dice,
            oddity_trace,
        )
        return replace(
            oddity,
            dice=HitAndRunDiceRecord(
                steal_record,
                oddity.dice,
                target_bonus,
                adjusted_bt,
                adjusted_obt,
            ),
        )
    fielded_by = None
    defense_outcome = None
    defense_trace = None
    out_type = None

    if mss <= adjusted_bt:
        batting_result = "hit"
    elif mss <= adjusted_obt:
        batting_result = "walk"
    else:
        out = out_table_result(mss)
        out_type = out.event_type
        fielded_by = out.fielded_by
        if mss <= adjusted_obt + 5:
            location = out_table_result(mss, possible_error=True)
            out_type = location.event_type
            fielded_by = location.fielded_by
            defense_roll = dice.roll(12)
            defense_outcome, modified_defense_roll = resolve_defense_roll(
                defense_roll, effective_defensive_traits(state, fielded_by or "")
            )
            swing_record = replace(
                swing_record,
                defense_roll=defense_roll,
                modified_defense_roll=modified_defense_roll,
            )
            defense_trace = RuleTraceEntry(
                "hit_and_run_defense",
                f"possible error to {fielded_by}: d12 {defense_roll}, modified "
                f"to {modified_defense_roll}: {defense_outcome}",
                "Second Edition pp. 28-29",
            )
            if defense_outcome == "error":
                batting_result = "error"
            else:
                batting_result = (
                    "groundball" if location.event_type == "groundout"
                    else "pop_or_strikeout"
                )
        else:
            batting_result = "groundball" if out.event_type == "groundout" else "pop_or_strikeout"

    if batting_result == "hit":
        bases = (batter_id, None, runner_id) if steal_success else (batter_id, runner_id, None)
        moves = (RunnerMove(runner_id, "1B", "3B" if steal_success else "2B"),)
        event_type, outs_added, notation = "hit_and_run_hit", 0, "1B"
    elif batting_result == "walk":
        bases = (batter_id, runner_id, None)
        moves = (RunnerMove(runner_id, "1B", "2B"),)
        event_type, outs_added, notation = "walk", 0, "BB"
    elif batting_result == "error":
        bases = (batter_id, runner_id, None)
        moves = (RunnerMove(runner_id, "1B", "2B"),)
        event_type, outs_added, notation = "error", 0, f"E-{fielded_by}"
    elif steal_success and batting_result == "pop_or_strikeout":
        bases = (runner_id, None, None)
        moves = (
            RunnerMove(runner_id, "1B", "1B"),
            RunnerMove(batter_id, "BATTER", out=True),
        )
        event_type, outs_added, notation = "hit_and_run_out", 1, "K/F"
    elif steal_success:
        bases = (None, runner_id, None)
        moves = (
            RunnerMove(runner_id, "1B", "2B"),
            RunnerMove(batter_id, "BATTER", out=True),
        )
        event_type, outs_added, notation = "hit_and_run_out", 1, "G"
    else:
        bases = (None, None, None)
        moves = (
            RunnerMove(runner_id, "1B", out=True),
            RunnerMove(batter_id, "BATTER", out=True),
        )
        event_type, outs_added, notation = "double_play", 2, "DP"

    actual_outs = min(outs_added, 3 - state.outs)
    new_state = _finish_plate_appearance(
        state, bases, outs_added=actual_outs, runs=0
    )
    new_state = _consume_catcher_bonus(new_state)
    event = PlayEvent(
        event_type, "hit_and_run", batter_id, pitcher.player_id, True,
        outs_added=actual_outs,
        batter_destination="1B" if batting_result in {"hit", "walk"} else None,
        scoring_notation=notation,
        hit_type="single" if batting_result == "hit" else None,
        fielded_by=fielded_by,
        defense_outcome=defense_outcome,
        runner_moves=moves,
        out_type=out_type,
    )
    trace = [
        RuleTraceEntry(
            "hit_and_run_steal",
            f"d8 {steal_roll} + runner {runner_modifier} + catcher "
            f"{catcher_modifier} + oddity {oddity_modifier} = {modified_steal}: "
            f"{'success' if steal_success else 'failure'}",
            "Second Edition pp. 31-33",
        ),
        RuleTraceEntry(
            "hit_and_run_swing",
            f"MSS {mss}; BT/OBT {batter.bt}/{batter.obt} + {target_bonus} = "
            f"{adjusted_bt}/{adjusted_obt}: {batting_result}",
            "Second Edition pp. 32-33",
        ),
    ]
    if defense_trace is not None:
        trace.append(defense_trace)
    return _finalize_action(
        state,
        ActionResult(
            event,
            new_state,
            HitAndRunDiceRecord(
                steal_record, swing_record, target_bonus, adjusted_bt, adjusted_obt
            ),
            tuple(trace),
        ),
    )


def resolve_steal(state: GameState, action: str, dice: DiceSource) -> ActionResult:
    """Resolve a legal single or double steal without consuming the at-bat."""
    steal_actions = {
        "steal_second", "steal_third", "steal_home", "double_steal"
    }
    if action not in steal_actions:
        raise RulesError(f"unknown steal action {action!r}")
    if action not in legal_actions(state):
        raise RulesError(f"{action} is not legal from bases {state.bases}")

    first, second, third = state.bases
    catcher_modifier = _catcher_steal_modifier(state)
    oddity_modifier = _oddity_steal_modifier(state)
    roll = dice.roll(8)

    if action == "double_steal":
        assert first is not None and second is not None
        runner_modifier = _speed_modifier(
            _effective_player_traits(state, second, _runner(state, second).traits)
        )
        modified = roll + runner_modifier + catcher_modifier + oddity_modifier
        record = StealDiceRecord(
            action, roll, runner_modifier, 0, catcher_modifier, modified
        )
        if modified <= 3:
            bases = (first, None, None)
            moves = (RunnerMove(second, "2B", out=True),)
            event_type, outs_added = "caught_stealing", 1
        elif modified <= 5:
            bases = (None, second, None)
            moves = (RunnerMove(first, "1B", out=True),)
            event_type, outs_added = "caught_stealing", 1
        else:
            bases = (None, first, second)
            moves = (
                RunnerMove(second, "2B", "3B"),
                RunnerMove(first, "1B", "2B"),
            )
            event_type, outs_added = "double_steal", 0
        new_state = _finish_between_pitches(
            state, bases, outs_added=outs_added, runs=0
        )
        new_state = _consume_catcher_bonus(new_state)
        trace = (
            RuleTraceEntry(
                "double_steal",
                f"d8 {roll} + runner {runner_modifier} + catcher "
                f"{catcher_modifier} + oddity {oddity_modifier} = {modified}: {event_type}",
                "Second Edition p. 31",
            ),
        )
        return _finalize_action(
            state,
            ActionResult(
                StealEvent(
                    event_type, action, True, moves,
                    outs_added=outs_added,
                    scoring_notation="SB" if outs_added == 0 else "CS",
                ),
                new_state,
                record,
                trace,
            ),
        )

    origin, destination, runner_id = {
        "steal_second": (1, 2, first),
        "steal_third": (2, 3, second),
        "steal_home": (3, 4, third),
    }[action]
    assert runner_id is not None
    runner_modifier = _speed_modifier(
        _effective_player_traits(state, runner_id, _runner(state, runner_id).traits)
    )
    base_modifier = -1 if action == "steal_third" else 0
    modified = roll + runner_modifier + base_modifier + catcher_modifier + oddity_modifier
    target = 8 if action == "steal_home" else 4
    safe = modified >= target
    record = StealDiceRecord(
        action, roll, runner_modifier, base_modifier,
        catcher_modifier, modified,
    )
    bases = list(state.bases)
    bases[origin - 1] = None
    runs = 0
    if safe and destination == 4:
        moves = (RunnerMove(runner_id, "3B", "HOME", scored=True),)
        runs = 1
    elif safe:
        bases[destination - 1] = runner_id
        moves = (
            RunnerMove(runner_id, _base_name(origin), _base_name(destination)),
        )
    else:
        moves = (RunnerMove(runner_id, _base_name(origin), out=True),)
    outs_added = 0 if safe else 1
    event_type = "stolen_base" if safe else "caught_stealing"
    new_state = _finish_between_pitches(
        state, tuple(bases), outs_added=outs_added, runs=runs
    )
    new_state = _consume_catcher_bonus(new_state)
    trace = (
        RuleTraceEntry(
            "base_stealing",
            f"d8 {roll} + runner {runner_modifier} + base {base_modifier} "
            f"+ catcher {catcher_modifier} + oddity {oddity_modifier} = {modified}; target {target}: "
            f"{event_type}",
            "Second Edition pp. 24, 31",
        ),
    )
    return _finalize_action(
        state,
        ActionResult(
            StealEvent(
                event_type, action, True, moves,
                outs_added=outs_added, runs_scored=runs,
                scoring_notation="SB" if safe else "CS",
            ),
            new_state,
            record,
            trace,
        ),
    )


def effective_pitch_die(
    pitch_die: str, pitcher_role: str, pitcher_throws: str, batter_bats: str
) -> str:
    """Apply the Second Edition same-handed one-level advantage."""
    if pitch_die not in PITCH_DIE_LADDER:
        raise RulesError(f"unknown Pitch Die {pitch_die!r}")
    if pitcher_role not in {"starter", "reliever"}:
        raise RulesError(f"player role {pitcher_role!r} cannot pitch")
    if pitcher_throws not in {"R", "L"} or batter_bats not in {"R", "L", "S"}:
        raise RulesError("invalid batter or pitcher handedness")
    same_handed = batter_bats != "S" and batter_bats == pitcher_throws
    if not same_handed:
        return pitch_die
    ceiling = "d12" if pitcher_role == "starter" else "d20"
    current_index = PITCH_DIE_LADDER.index(pitch_die)
    ceiling_index = PITCH_DIE_LADDER.index(ceiling)
    if current_index >= ceiling_index:
        return pitch_die
    return PITCH_DIE_LADDER[current_index + 1]


def classify_mss(mss: int, bt: int, obt: int, *, oddities: bool = False) -> AtBatClassification:
    """Classify an MSS using the printed Swing Result Table."""
    if oddities and mss in {1, 99}:
        return AtBatClassification.ODDITY
    if mss <= 5:
        return AtBatClassification.CRITICAL_HIT
    if mss <= bt:
        return AtBatClassification.ORDINARY_HIT
    if mss <= obt:
        return AtBatClassification.WALK
    if mss <= obt + 5:
        return AtBatClassification.POSSIBLE_ERROR
    return AtBatClassification.OUT


def out_table_result(mss: int, *, possible_error: bool = False) -> OutTableResult:
    """Resolve the final MSS digit, including possible-error location overrides."""
    digit = abs(mss) % 10
    if possible_error and digit in {0, 1}:
        return OutTableResult("groundout", "SS", "6-3")
    if possible_error and digit == 2:
        return OutTableResult("groundout", "2B", "4-3")
    return OUT_TABLE[digit]


def resolve_hit_table(roll: int, traits: tuple[str, ...], *, critical: bool = False) -> ResolvedHit:
    """Resolve the Modern Hit Table after hitter traits, then apply a critical hit."""
    if not 1 <= roll <= 20:
        raise RulesError("Hit Table roll must be between 1 and 20")
    power_modifier = (
        2 if "P++" in traits else 1 if "P+" in traits
        else -2 if "P--" in traits else -1 if "P-" in traits else 0
    )
    modified = roll + power_modifier

    # Contact and speed results replace the ordinary table result and carry no DEF check.
    if "C+" in traits and modified <= 2:
        result = HitTableResult("double", 2, runner_advance=2)
    elif "S+" in traits and modified == 1:
        result = HitTableResult("double", 2, runner_advance=2)
    elif "S+" in traits and modified == 2:
        result = HitTableResult("triple", 3)
    else:
        result = _modern_hit_table(modified)

    if critical:
        next_index = min(HIT_LEVELS.index(result.hit_type) + 1, len(HIT_LEVELS) - 1)
        hit_type = HIT_LEVELS[next_index]
        bases = next_index + 1
        result = replace(
            result,
            hit_type=hit_type,
            bases=bases,
            runner_advance=min((result.runner_advance or result.bases) + 1, 4),
            defense_position=None,
        )
    return ResolvedHit(result, roll, modified, critical)


def resolve_defense_roll(roll: int, traits: tuple[str, ...]) -> tuple[str, int]:
    """Resolve a d12 DEF check with the printed D+ and D- modifiers."""
    if not 1 <= roll <= 12:
        raise RulesError("DEF roll must be between 1 and 12")
    modifier = 1 if "D+" in traits else -1 if "D-" in traits else 0
    modified = roll + modifier
    if modified <= 2:
        return "error", modified
    if modified <= 9:
        return "no_change", modified
    if modified <= 11:
        return "reduced", modified
    return "out", modified


def _modern_hit_table(roll: int) -> HitTableResult:
    if roll <= 2:
        return HitTableResult("single", 1)
    if roll == 3:
        return HitTableResult("single", 1, defense_position="1B")
    if roll == 4:
        return HitTableResult("single", 1, defense_position="2B")
    if roll == 5:
        return HitTableResult("single", 1, defense_position="3B")
    if roll == 6:
        return HitTableResult("single", 1, defense_position="SS")
    if roll <= 9:
        return HitTableResult("single", 1)
    if roll <= 14:
        return HitTableResult("single", 1, runner_advance=2)
    if roll == 15:
        return HitTableResult("double", 2, defense_position="LF")
    if roll == 16:
        return HitTableResult("double", 2, defense_position="CF")
    if roll == 17:
        return HitTableResult("double", 2, defense_position="RF")
    if roll == 18:
        return HitTableResult("double", 2, runner_advance=3)
    return HitTableResult("home_run", 4)


def resolve_swing(state: GameState, dice: DiceSource) -> ActionResult:
    """Resolve one swing through the Modern Hit and DEF tables."""
    offense, defense, offense_data, defense_data = _active_sides(state)
    if not 0 <= offense.batting_order_index < len(offense.lineup):
        raise RulesError("batting-order index is out of range")
    batter_id = offense.lineup[offense.batting_order_index]
    batter = offense_data.player(batter_id)
    pitcher = _active_pitcher(defense, defense_data)
    if batter.bt is None or batter.obt is None or batter.bats is None:
        raise RulesError("current batter lacks required ratings or handedness")
    if pitcher.throws is None:
        raise RulesError("active pitcher lacks throwing hand")

    pitch_die = effective_pitch_die(
        defense.active_pitch_die, pitcher.role, pitcher.throws, batter.bats
    )
    sides, sign = _parse_pitch_die(pitch_die)
    swing_score = dice.roll(100)
    pitch_roll = dice.roll(sides)
    signed_pitch = sign * pitch_roll
    mss = swing_score + signed_pitch
    bt, obt = batter.bt, batter.obt
    batter_traits = _effective_player_traits(state, batter.player_id, batter.traits)
    injury_penalty = sum(
        injury.batting_penalty
        for injury in state.oddity_state.injuries
        if injury.player_id == batter.player_id
    )
    bt -= injury_penalty
    obt -= injury_penalty
    if state.half == "bottom" and state.oddity_state.home_batting_penalty_inning == state.inning:
        bt -= 5
        obt -= 5
    if "C-" in batter_traits and (state.bases[1] is not None or state.bases[2] is not None):
        bt -= 3
        obt -= 3
    classification = classify_mss(mss, bt, obt, oddities=state.source.rules.oddities)
    trace = [
        RuleTraceEntry(
            "pitch_die",
            f"{defense.active_pitch_die} -> {pitch_die} for {pitcher.throws}P vs {batter.bats}B",
            "Second Edition p. 34",
        ),
        RuleTraceEntry(
            "mss",
            f"{swing_score} + ({signed_pitch}) = {mss}",
            "Second Edition p. 26",
        ),
        RuleTraceEntry(
            "classification",
            f"MSS {mss}, BT {bt}, OBT {obt}: {classification.value}",
            "Second Edition p. 27",
        ),
    ]
    record = DiceRecord(swing_score, pitch_die, pitch_roll, signed_pitch, mss)

    if classification == AtBatClassification.ODDITY:
        return _resolve_oddity(state, batter_id, pitcher.player_id, record, dice, trace)
    if classification == AtBatClassification.WALK:
        new_state, moves, runs = _complete_walk(state, batter_id)
        event = PlayEvent(
            "walk", classification.value, batter_id, pitcher.player_id, True,
            batter_destination="1B", scoring_notation="BB",
            runner_moves=moves, runs_scored=runs,
        )
    elif classification == AtBatClassification.OUT:
        out = out_table_result(mss)
        new_state, moves, outs_added, runs = _complete_out_play(
            state, batter_id, out, mss
        )
        event_type = _out_event_type(out, state, mss)
        notation = "FC" if event_type == "fielders_choice" else out.scoring_notation
        event = PlayEvent(
            event_type, classification.value, batter_id, pitcher.player_id, True,
            outs_added=outs_added, fielded_by=out.fielded_by, scoring_notation=notation,
            runner_moves=moves, runs_scored=runs,
        )
        trace.append(RuleTraceEntry(
            "out_table",
            f"final digit {abs(mss) % 10}: {out.event_type} {out.scoring_notation}",
            "Second Edition p. 29",
        ))
    elif classification in {AtBatClassification.ORDINARY_HIT, AtBatClassification.CRITICAL_HIT}:
        hit_roll = dice.roll(20)
        hit = resolve_hit_table(
            hit_roll, batter_traits,
            critical=classification == AtBatClassification.CRITICAL_HIT,
        )
        result = hit.result
        trace.append(RuleTraceEntry(
            "hit_table",
            f"d20 {hit.raw_roll}, modified to {hit.modified_roll}: {result.hit_type}"
            + ("; critical increases one level" if hit.critical else ""),
            "Second Edition pp. 26, 28",
        ))
        defense_roll = None
        modified_defense_roll = None
        defense_outcome = None
        if result.defense_position:
            defense_roll = dice.roll(12)
            defense_outcome, modified_defense_roll = resolve_defense_roll(
                defense_roll, effective_defensive_traits(state, result.defense_position)
            )
            trace.append(RuleTraceEntry(
                "defense",
                f"{result.defense_position} rolled {defense_roll}, modified to "
                f"{modified_defense_roll}: {defense_outcome}",
                "Second Edition p. 28",
            ))
            if defense_outcome == "out":
                new_state = _finish_plate_appearance(
                    state, state.bases, outs_added=1, runs=0
                )
                event = PlayEvent(
                    "out", classification.value, batter_id, pitcher.player_id, True,
                    outs_added=1, fielded_by=result.defense_position,
                    hit_type=result.hit_type, defense_outcome=defense_outcome,
                )
            elif defense_outcome == "error":
                error_bases = min(result.bases + 1, 4)
                new_state, moves, runs = _complete_hit(
                    state,
                    batter_id,
                    error_bases,
                    runner_advance=min(
                        (result.runner_advance or result.bases) + 1, 4
                    ),
                )
                event = PlayEvent(
                    "error", classification.value, batter_id, pitcher.player_id, True,
                    batter_destination=_base_name(error_bases),
                    fielded_by=result.defense_position,
                    scoring_notation=f"E-{result.defense_position}",
                    hit_type=result.hit_type, defense_outcome=defense_outcome,
                    runner_moves=moves, runs_scored=runs,
                )
            else:
                final_bases = max(1, result.bases - 1) if defense_outcome == "reduced" else result.bases
                final_type = HIT_LEVELS[final_bases - 1]
                runner_advance = (
                    final_bases if defense_outcome == "reduced"
                    else (result.runner_advance or result.bases)
                )
                new_state, moves, runs = _complete_hit(
                    state, batter_id, final_bases, runner_advance=runner_advance
                )
                event = PlayEvent(
                    final_type, classification.value, batter_id, pitcher.player_id, True,
                    batter_destination=_base_name(final_bases),
                    fielded_by=result.defense_position,
                    scoring_notation=_hit_notation(final_type),
                    hit_type=final_type, defense_outcome=defense_outcome,
                    runner_moves=moves, runs_scored=runs,
                )
        else:
            new_state, moves, runs = _complete_hit(
                state, batter_id, result.bases,
                runner_advance=result.runner_advance or result.bases,
            )
            event = PlayEvent(
                result.hit_type, classification.value, batter_id, pitcher.player_id, True,
                batter_destination=_base_name(result.bases),
                scoring_notation=_hit_notation(result.hit_type), hit_type=result.hit_type,
                runner_moves=moves, runs_scored=runs,
            )
        record = replace(
            record,
            hit_table_roll=hit.raw_roll,
            modified_hit_table_roll=hit.modified_roll,
            defense_roll=defense_roll,
            modified_defense_roll=modified_defense_roll,
        )
    elif classification == AtBatClassification.POSSIBLE_ERROR:
        location = out_table_result(mss, possible_error=True)
        defense_roll = dice.roll(12)
        defense_outcome, modified_defense_roll = resolve_defense_roll(
            defense_roll, effective_defensive_traits(state, location.fielded_by or "")
        )
        record = replace(
            record, defense_roll=defense_roll,
            modified_defense_roll=modified_defense_roll,
        )
        trace.extend((
            RuleTraceEntry(
                "possible_error_location",
                f"final digit {abs(mss) % 10}: DEF check for {location.fielded_by}",
                "Second Edition p. 29",
            ),
            RuleTraceEntry(
                "defense",
                f"{location.fielded_by} rolled {defense_roll}, modified to "
                f"{modified_defense_roll}: {defense_outcome}",
                "Second Edition pp. 28-29",
            ),
        ))
        if defense_outcome == "error":
            new_state, moves, runs = _complete_hit(
                state, batter_id, 1, runner_advance=1
            )
            event = PlayEvent(
                "error", classification.value, batter_id, pitcher.player_id, True,
                batter_destination="1B", fielded_by=location.fielded_by,
                scoring_notation=f"E-{location.fielded_by}",
                defense_outcome="error",
                runner_moves=moves, runs_scored=runs,
            )
        else:
            new_state, moves, outs_added, runs = _complete_out_play(
                state, batter_id, location, mss
            )
            event = PlayEvent(
                location.event_type, classification.value, batter_id, pitcher.player_id, True,
                outs_added=outs_added, fielded_by=location.fielded_by,
                scoring_notation=location.scoring_notation,
                defense_outcome="out",
                runner_moves=moves, runs_scored=runs,
            )
    return _finalize_action(
        state, ActionResult(event, new_state, record, tuple(trace))
    )


def _resolve_oddity(
    state: GameState,
    batter_id: str,
    pitcher_id: str,
    record: DiceRecord,
    dice: DiceSource,
    trace: list[RuleTraceEntry],
) -> ActionResult:
    """Roll and apply the complete Second Edition 2d10 Oddities table."""
    rolls = (dice.roll(10), dice.roll(10))
    total = sum(rolls)
    name = ODDITY_NAMES[total]
    detail_rolls: list[tuple[str, int]] = []
    details: list[str] = []
    resolved = False
    outs_added = 0
    runs = 0
    moves: tuple[RunnerMove, ...] = ()
    notation = None
    defense_outcome = None
    new_state = state
    event_type = f"oddity_{name.lower().replace(' ', '_').replace('-', '_')}"

    if total == 2:
        if record.pitch_die_roll % 2 == 0:
            resolved = True
            outs_added = 1
            notation = "FAN-INT"
            details.append("Fan interference overturns a home run; the batter is out.")
            new_state = _finish_plate_appearance(
                state, state.bases, outs_added=1, runs=0
            )
        else:
            details.append("A fan catches a sure out; the at-bat continues.")
    elif total == 3:
        animal_roll = dice.roll(4)
        detail_rolls.append(("animal", animal_roll))
        if animal_roll == 1:
            new_state = _add_pitcher_modifier(
                state, pitcher_id, -1, state.inning, state.half
            )
            details.append("A seagull steals the pitcher's hat; Pitch Die drops one level this inning.")
        elif animal_roll == 2:
            fielder_id = state.oddity_state.last_out_fielder_id
            if fielder_id is None:
                details.append("No fielder completed a prior out, so the raccoon causes no rating change.")
            else:
                poor = state.oddity_state.poor_defenders
                if fielder_id not in poor:
                    poor = (*poor, fielder_id)
                new_state = replace(
                    state,
                    oddity_state=replace(state.oddity_state, poor_defenders=poor),
                )
                details.append("A raccoon bites the fielder from the most recent out; that player is D-.")
        elif animal_roll == 3:
            target_inning = state.inning if state.half == "bottom" else state.inning
            new_state = replace(
                state,
                oddity_state=replace(
                    state.oddity_state,
                    home_batting_penalty_inning=target_inning,
                ),
            )
            details.append("A black cat spooks the home team; its BT and OBT drop by 5 for its inning.")
        else:
            home_pitcher = state.home.active_pitcher_id
            if home_pitcher is not None:
                target_inning = state.inning if state.half == "top" else state.inning + 1
                new_state = _add_pitcher_modifier(
                    state, home_pitcher, 1, target_inning, "top"
                )
            details.append("A streaker inspires the crowd; the home pitcher's Pitch Die rises one level.")
    elif total == 4:
        delay_roll = dice.roll(100)
        delay = delay_roll * 2
        detail_rolls.append(("rain_delay_d100", delay_roll))
        details.append(f"Rain delays the game for {delay} minutes; the at-bat continues.")
    elif total in {5, 6, 16, 17}:
        if total == 5:
            injured_id = state.oddity_state.last_out_fielder_id
        elif total == 6:
            injured_id = pitcher_id
        elif total == 16:
            injured_id = batter_id
        else:
            offense = state.away if state.half == "top" else state.home
            injured_id = offense.lineup[(offense.batting_order_index - 1) % len(offense.lineup)]
        if injured_id is None:
            details.append("There is no eligible player from the previous out; the at-bat continues.")
        else:
            new_state, injury_details, injury_rolls = _resolve_injury(
                state, injured_id, pitcher_id == injured_id, dice
            )
            details.extend(injury_details)
            detail_rolls.extend(injury_rolls)
    elif total in {7, 8}:
        base_index = (
            max((i for i, runner in enumerate(state.bases) if runner is not None), default=-1)
            if total == 7
            else (0 if state.bases[0] is not None else -1)
        )
        if base_index >= 0:
            runner_id = state.bases[base_index]
            assert runner_id is not None
            bases = list(state.bases)
            bases[base_index] = None
            moves = (RunnerMove(runner_id, _base_name(base_index + 1), out=True),)
            outs_added = 1
            new_state = _finish_between_pitches(
                state, tuple(bases), outs_added=1, runs=0
            )
            details.append(
                "The lead runner is thrown out on the bases."
                if total == 7 else "The runner at first is picked off."
            )
        elif total == 7:
            resolved = True
            outs_added = 1
            new_state = _finish_plate_appearance(
                state, state.bases, outs_added=1, runs=0
            )
            details.append("With the bases empty, the batter is tagged out.")
        else:
            defense = state.home if state.half == "top" else state.away
            new_state = replace(
                state,
                oddity_state=replace(
                    state.oddity_state,
                    catcher_steal_bonus_team_id=defense.team_id,
                ),
            )
            details.append("With first empty, the catcher is D+ on the next steal attempt.")
    elif total == 9:
        replay = int(state.source.game.game_date[:4]) >= 2014
        if replay:
            details.append("Video replay corrects the blown call; the at-bat continues.")
        elif record.pitch_die_roll % 2 == 0:
            resolved = True
            new_state, moves, runs = _complete_walk(state, batter_id)
            notation = "BC-1"
            details.append("The batter is wrongly called safe at first.")
        else:
            resolved = True
            outs_added = 1
            new_state = _finish_plate_appearance(state, state.bases, outs_added=1, runs=0)
            notation = "BC-1"
            details.append("The batter is wrongly called out at first.")
    elif total == 10:
        resolved = True
        if record.pitch_die_roll % 2 == 0:
            new_state, moves, runs = _complete_walk(state, batter_id)
            notation = "BB"
            details.append("A ball is called instead of strike three; the batter walks.")
        else:
            outs_added = 1
            new_state = _finish_plate_appearance(state, state.bases, outs_added=1, runs=0)
            notation = "K"
            details.append("A strike is called on ball four; the batter is out.")
    elif total in {11, 20}:
        resolved = True
        new_state, moves, runs = _complete_walk(state, batter_id)
        notation = "HBP" if total == 11 else "CI"
        details.append("The batter is hit by the pitch and takes first." if total == 11 else "Catcher interference sends the batter to first.")
    elif total in {12, 15, 19}:
        new_state, moves, runs = _advance_all_runners(state)
        details.append(
            "All runners advance on a wild pitch."
            if total == 12 else
            "All runners advance on a passed ball."
            if total == 15 else
            "All runners advance on a balk."
        )
    elif total == 13:
        new_state = replace(
            state,
            oddity_state=replace(state.oddity_state, steal_bonus_batter_id=batter_id),
        )
        details.append("The distracted pitcher adds 1 to steal attempts during this at-bat.")
    elif total == 14:
        dropped_roll = dice.roll(8)
        detail_rolls.append(("dropped_third_strike", dropped_roll))
        resolved = True
        if dropped_roll >= 4:
            new_state, moves, runs = _complete_walk(state, batter_id)
            notation = "K-WP"
            details.append("The dropped third strike gets away; the batter reaches first.")
        else:
            outs_added = 1
            new_state = _finish_plate_appearance(state, state.bases, outs_added=1, runs=0)
            notation = "K"
            details.append("The catcher completes the out after the dropped third strike.")
    elif total == 18:
        resolved = True
        new_state, moves, runs = _complete_hit(
            state, batter_id, 1, runner_advance=1
        )
        notation = "E-1"
        defense_outcome = "error"
        details.append("A pitcher error puts the batter on first and advances every runner.")

    record = replace(
        record,
        oddity_rolls=rolls,
        oddity_total=total,
        oddity_detail_rolls=tuple(detail_rolls),
    )
    trace.append(RuleTraceEntry(
        "oddities",
        f"2d10 {rolls[0]} + {rolls[1]} = {total}: {name}",
        "Second Edition pp. 42-43",
    ))
    event = PlayEvent(
        event_type,
        AtBatClassification.ODDITY.value,
        batter_id,
        pitcher_id,
        resolved,
        outs_added=outs_added,
        batter_destination="1B" if resolved and total in {9, 10, 11, 14, 18, 20} and outs_added == 0 else None,
        scoring_notation=notation,
        defense_outcome=defense_outcome,
        runner_moves=moves,
        runs_scored=runs,
        out_type="strikeout" if total == 14 or (total == 10 and outs_added) else None,
        oddity_name=name,
        details=tuple(details),
    )
    return _finalize_action(
        state, ActionResult(event, new_state, record, tuple(trace))
    )


def _advance_all_runners(
    state: GameState,
) -> tuple[GameState, tuple[RunnerMove, ...], int]:
    bases = [None, None, None]
    moves: list[RunnerMove] = []
    runs = 0
    for index in range(2, -1, -1):
        runner_id = state.bases[index]
        if runner_id is None:
            continue
        if index == 2:
            moves.append(RunnerMove(runner_id, "3B", "HOME", scored=True))
            runs += 1
        else:
            bases[index + 1] = runner_id
            moves.append(RunnerMove(runner_id, _base_name(index + 1), _base_name(index + 2)))
    return (
        _finish_between_pitches(state, tuple(bases), outs_added=0, runs=runs),
        tuple(moves),
        runs,
    )


def _add_pitcher_modifier(
    state: GameState, player_id: str, levels: int, inning: int, half: str
) -> GameState:
    modifier = PitcherOddityModifier(player_id, levels, inning, half)
    return replace(
        state,
        oddity_state=replace(
            state.oddity_state,
            pitcher_modifiers=(*state.oddity_state.pitcher_modifiers, modifier),
        ),
    )


def _resolve_injury(
    state: GameState,
    player_id: str,
    is_pitcher: bool,
    dice: DiceSource,
) -> tuple[GameState, list[str], list[tuple[str, int]]]:
    severity_roll = dice.roll(100)
    location_roll = dice.roll(20)
    location = INJURY_LOCATIONS[location_roll - 1]
    rolls = [("injury_severity", severity_roll), ("injury_location", location_roll)]
    games_out = 0
    must_leave = False
    batting_penalty = 0
    pitch_penalty = 0
    traits_nullified = False
    player_name = _player_display_name(state, player_id)
    if severity_roll == 1:
        severity = "catastrophic"
        catastrophic = dice.roll(6)
        rolls.append(("catastrophic", catastrophic))
        if catastrophic == 1:
            must_leave = True
            details = [f"{player_name} suffers a catastrophic {location} injury and must leave the game."]
        else:
            penalty_d10 = dice.roll(10)
            penalty_roll = penalty_d10 + 2
            rolls.append(("permanent_rating_d10", penalty_d10))
            batting_penalty = 0 if is_pitcher else penalty_roll
            pitch_penalty = -1 if is_pitcher else 0
            details = [f"{player_name} suffers a catastrophic {location} injury and loses rating strength."]
    elif severity_roll <= 5:
        severity = "major"
        first_duration = dice.roll(20)
        second_duration = dice.roll(20)
        games_out = first_duration + second_duration
        rolls.extend((
            ("injury_games_d20_1", first_duration),
            ("injury_games_d20_2", second_duration),
        ))
        must_leave = True
        details = [f"{player_name} suffers a major {location} injury and is out {games_out} games."]
    elif severity_roll <= 10:
        severity = "minor"
        games_out = dice.roll(8)
        rolls.append(("injury_games_d8", games_out))
        must_leave = True
        details = [f"{player_name} suffers a minor {location} injury and is out {games_out} games."]
    elif severity_roll <= 75:
        severity = "superficial"
        duration = dice.roll(6)
        rolls.append(("injury_duration_d6", duration))
        batting_penalty = 0 if is_pitcher else 5
        pitch_penalty = -1 if is_pitcher else 0
        traits_nullified = True
        details = [f"{player_name} has a superficial {location} injury; ratings are reduced and traits nullified."]
    else:
        severity = "unhurt"
        details = [f"{player_name} is checked after a possible {location} injury and is unhurt."]

    injury = InjuryRecord(
        player_id,
        severity,
        location,
        must_leave,
        batting_penalty,
        pitch_penalty,
        traits_nullified,
        games_out,
    )
    injuries = tuple(
        prior for prior in state.oddity_state.injuries if prior.player_id != player_id
    ) + (injury,)
    updated = replace(
        state,
        oddity_state=replace(state.oddity_state, injuries=injuries),
    )
    if is_pitcher and must_leave:
        side = "home" if state.half == "top" else "away"
        team = getattr(updated, side)
        if team.pitcher_state is not None:
            team = replace(
                team,
                pitcher_state=replace(team.pitcher_state, removal_exception="injury"),
            )
            updated = replace(updated, **{side: team})
    return updated, details, rolls


def _player_display_name(state: GameState, player_id: str) -> str:
    for team in (state.source.teams.away, state.source.teams.home):
        try:
            return team.player(player_id).name
        except KeyError:
            continue
    return player_id


def _finalize_action(before: GameState, result: ActionResult) -> ActionResult:
    if isinstance(result.event, PlayEvent):
        oddity = result.new_state.oddity_state
        if result.event.resolved:
            oddity = replace(oddity, steal_bonus_batter_id=None)
        if result.event.outs_added and result.event.fielded_by:
            oddity = replace(
                oddity,
                last_out_fielder_id=_defender(
                    before, result.event.fielded_by
                ).player_id,
            )
        if (before.inning, before.half) != (
            result.new_state.inning,
            result.new_state.half,
        ):
            oddity = replace(
                oddity,
                pitcher_modifiers=tuple(
                    modifier
                    for modifier in oddity.pitcher_modifiers
                    if (modifier.inning, 0 if modifier.half == "top" else 1)
                    >= (
                        result.new_state.inning,
                        0 if result.new_state.half == "top" else 1,
                    )
                ),
            )
        result = replace(
            result,
            new_state=replace(result.new_state, oddity_state=oddity),
        )
        if (
            not result.event.resolved
            and result.event.outs_added == 0
            and result.event.runs_scored == 0
        ):
            return result
    return replace(
        result,
        new_state=apply_pitcher_progress(before, result.new_state, result.event),
    )


def _parse_pitch_die(pitch_die: str) -> tuple[int, int]:
    match = re.fullmatch(r"(-?)d(4|8|12|20)", pitch_die)
    if not match:
        raise RulesError(f"unknown Pitch Die {pitch_die!r}")
    return int(match.group(2)), -1 if match.group(1) else 1


def _active_sides(state: GameState):
    if state.is_final:
        raise RulesError("game is final")
    if state.half == "top":
        return state.away, state.home, state.source.teams.away, state.source.teams.home
    if state.half == "bottom":
        return state.home, state.away, state.source.teams.home, state.source.teams.away
    raise RulesError(f"unknown half inning {state.half!r}")


def _active_pitcher(defense, defense_data):
    if defense.active_pitcher_id is None or defense.active_pitch_die is None:
        raise RulesError("a pitcher must be installed before resolving a play")
    return defense_data.player(defense.active_pitcher_id)


def _defender(state: GameState, position: str):
    _, defense, _, defense_data = _active_sides(state)
    for assignment in defense.active_defense:
        if assignment.position == position:
            return defense_data.player(assignment.player_id)
    raise RulesError(f"active defense has no {position}")


def _runner(state: GameState, player_id: str):
    _, _, offense_data, _ = _active_sides(state)
    try:
        return offense_data.player(player_id)
    except KeyError as exc:
        raise RulesError(f"runner {player_id!r} is not on the batting team") from exc


def _speed_modifier(traits: tuple[str, ...]) -> int:
    if "S+" in traits:
        return 1
    if "S-" in traits:
        return -2
    return 0


def _catcher_steal_modifier(state: GameState) -> int:
    traits = effective_defensive_traits(state, "C")
    defense = state.home if state.half == "top" else state.away
    oddity_bonus = state.oddity_state.catcher_steal_bonus_team_id == defense.team_id
    if "D+" in traits or oddity_bonus:
        return -1
    if "D-" in traits:
        return 1
    return 0


def _oddity_steal_modifier(state: GameState) -> int:
    offense = state.away if state.half == "top" else state.home
    batter_id = offense.lineup[offense.batting_order_index]
    return int(state.oddity_state.steal_bonus_batter_id == batter_id)


def _consume_catcher_bonus(state: GameState) -> GameState:
    if state.oddity_state.catcher_steal_bonus_team_id is None:
        return state
    return replace(
        state,
        oddity_state=replace(
            state.oddity_state, catcher_steal_bonus_team_id=None
        ),
    )


def _effective_player_traits(
    state: GameState, player_id: str, traits: tuple[str, ...]
) -> tuple[str, ...]:
    if any(
        injury.player_id == player_id and injury.traits_nullified
        for injury in state.oddity_state.injuries
    ):
        return ()
    return traits


def _oddity_adjusted_pitch_die(
    state: GameState, pitch_die: str, pitcher_id: str
) -> str:
    levels = sum(
        modifier.levels
        for modifier in state.oddity_state.pitcher_modifiers
        if modifier.player_id == pitcher_id
        and modifier.inning == state.inning
        and modifier.half == state.half
    )
    levels += sum(
        injury.pitch_die_penalty
        for injury in state.oddity_state.injuries
        if injury.player_id == pitcher_id
    )
    return step_pitch_die(pitch_die, levels)


def _advance_offense(state: GameState) -> GameState:
    if state.half == "top":
        offense = replace(
            state.away,
            batting_order_index=(state.away.batting_order_index + 1) % len(state.away.lineup),
        )
        return replace(state, away=offense)
    offense = replace(
        state.home,
        batting_order_index=(state.home.batting_order_index + 1) % len(state.home.lineup),
    )
    return replace(state, home=offense)


def _complete_walk(state: GameState, batter_id: str) -> tuple[GameState, tuple[RunnerMove, ...], int]:
    first, second, third = state.bases
    moves: list[RunnerMove] = []
    runs = 0
    if first is not None:
        moves.append(RunnerMove(first, "1B", "2B"))
        if second is not None:
            moves.append(RunnerMove(second, "2B", "3B"))
            if third is not None:
                moves.append(RunnerMove(third, "3B", "HOME", scored=True))
                runs = 1
    new_bases = (
        batter_id,
        first if first is not None else second,
        second if first is not None and second is not None else third,
    )
    # Non-forced runners retain their bases.
    if first is None:
        new_bases = (batter_id, second, third)
    elif second is None:
        new_bases = (batter_id, first, third)
    return _finish_plate_appearance(state, new_bases, outs_added=0, runs=runs), tuple(moves), runs


def _bunt_lead_out_batter_safe(
    state: GameState, batter_id: str, lead_index: int
) -> tuple[tuple[str | None, str | None, str | None], tuple[RunnerMove, ...]]:
    """Remove the lead runner and force trailing runners for a safe batter."""
    bases = list(state.bases)
    lead_id = bases[lead_index]
    assert lead_id is not None
    moves = [RunnerMove(lead_id, _base_name(lead_index + 1), out=True)]
    bases[lead_index] = None
    for index in range(lead_index - 1, -1, -1):
        runner_id = bases[index]
        if runner_id is None:
            continue
        bases[index + 1] = runner_id
        bases[index] = None
        moves.append(
            RunnerMove(runner_id, _base_name(index + 1), _base_name(index + 2))
        )
    bases[0] = batter_id
    return tuple(bases), tuple(moves)


def _complete_hit(
    state: GameState, batter_id: str, batter_bases: int, *, runner_advance: int
) -> tuple[GameState, tuple[RunnerMove, ...], int]:
    occupied: list[str | None] = [None, None, None]
    moves: list[RunnerMove] = []
    runs = 0
    for index, runner_id in enumerate(state.bases, start=1):
        if runner_id is None:
            continue
        destination = index + runner_advance
        if destination >= 4:
            moves.append(RunnerMove(runner_id, _base_name(index), "HOME", scored=True))
            runs += 1
        else:
            occupied[destination - 1] = runner_id
            moves.append(RunnerMove(runner_id, _base_name(index), _base_name(destination)))
    if batter_bases == 4:
        moves.append(RunnerMove(batter_id, "BATTER", "HOME", scored=True))
        runs += 1
    else:
        occupied[batter_bases - 1] = batter_id
    new_state = _finish_plate_appearance(
        state, tuple(occupied), outs_added=0, runs=runs
    )
    return new_state, tuple(moves), runs


def _complete_out_play(
    state: GameState, batter_id: str, out: OutTableResult, mss: int
) -> tuple[GameState, tuple[RunnerMove, ...], int, int]:
    bases = list(state.bases)
    moves: list[RunnerMove] = []
    runs = 0
    requested_outs = 1
    infield = out.fielded_by in {"1B", "2B", "3B", "SS"}
    productive_location = out.fielded_by in {"1B", "2B", "LF", "CF", "RF"}

    # Advance the original runners from third and second before resolving a
    # runner on first, so a newly advanced runner is never moved twice.
    if mss < 70 and state.outs < 2 and productive_location:
        if bases[2] is not None:
            moves.append(RunnerMove(bases[2], "3B", "HOME", scored=True))
            bases[2] = None
            runs += 1
        if bases[1] is not None:
            moves.append(RunnerMove(bases[1], "2B", "3B"))
            bases[2] = bases[1]
            bases[1] = None

    if infield and bases[0] is not None:
        first_runner = bases[0]
        if state.outs >= 2:
            moves.append(RunnerMove(batter_id, "BATTER", out=True))
        elif mss >= 100 and bases[1] is not None and state.outs == 0:
            moves.extend((
                RunnerMove(first_runner, "1B", out=True),
                RunnerMove(bases[1], "2B", out=True),
                RunnerMove(batter_id, "BATTER", out=True),
            ))
            bases[0] = bases[1] = None
            requested_outs = 3
        elif mss >= 70:
            moves.extend((
                RunnerMove(first_runner, "1B", out=True),
                RunnerMove(batter_id, "BATTER", out=True),
            ))
            bases[0] = None
            requested_outs = 2
        elif mss >= 50:
            moves.append(RunnerMove(first_runner, "1B", out=True))
            bases[0] = batter_id
        else:
            bases[1] = first_runner
            bases[0] = None
            moves.append(RunnerMove(first_runner, "1B", "2B"))

    outs_added = min(requested_outs, 3 - state.outs)
    if state.outs + outs_added >= 3:
        # No run scores on an inning-ending force/double/triple play.
        if requested_outs > 1 or (infield and state.bases[0] is not None and mss >= 50):
            runs = 0
        bases = [None, None, None]
    new_state = _finish_plate_appearance(
        state, tuple(bases), outs_added=outs_added, runs=runs
    )
    return new_state, tuple(moves), outs_added, runs


def _out_event_type(out: OutTableResult, state: GameState, mss: int) -> str:
    infield = out.fielded_by in {"1B", "2B", "3B", "SS"}
    if not infield or state.bases[0] is None:
        return out.event_type
    if state.outs >= 2:
        return out.event_type
    if mss >= 100 and state.bases[1] is not None and state.outs == 0:
        return "triple_play"
    if mss >= 70:
        return "double_play"
    if mss >= 50:
        return "fielders_choice"
    return out.event_type


def _finish_plate_appearance(
    state: GameState,
    bases: tuple[str | None, str | None, str | None],
    *,
    outs_added: int,
    runs: int,
) -> GameState:
    return _finish_state(
        _advance_offense(state), state, bases,
        outs_added=outs_added, runs=runs,
    )


def _finish_between_pitches(
    state: GameState,
    bases: tuple[str | None, str | None, str | None],
    *,
    outs_added: int,
    runs: int,
) -> GameState:
    return _finish_state(
        state, state, bases, outs_added=outs_added, runs=runs
    )


def _finish_state(
    working: GameState,
    original: GameState,
    bases: tuple[str | None, str | None, str | None],
    *,
    outs_added: int,
    runs: int,
) -> GameState:
    return finish_action_state(
        working,
        original,
        bases,
        outs_added=outs_added,
        runs=runs,
    )


def _base_name(bases: int) -> str:
    return {1: "1B", 2: "2B", 3: "3B", 4: "HOME"}[bases]


def _hit_notation(hit_type: str) -> str:
    return {"single": "1B", "double": "2B", "triple": "3B", "home_run": "HR"}[hit_type]


def _complete_out(state: GameState) -> GameState:
    return _finish_plate_appearance(
        state, state.bases, outs_added=1, runs=0
    )
