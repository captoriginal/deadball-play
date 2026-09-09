"""Structured mechanical events and rule traces."""

from __future__ import annotations

from dataclasses import dataclass

from .state import GameState


@dataclass(frozen=True)
class DiceRecord:
    swing_score: int
    pitch_die: str
    pitch_die_roll: int
    signed_pitch_value: int
    mss: int
    hit_table_roll: int | None = None
    modified_hit_table_roll: int | None = None
    defense_roll: int | None = None
    modified_defense_roll: int | None = None
    oddity_rolls: tuple[int, int] | None = None
    oddity_total: int | None = None
    oddity_detail_rolls: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class StealDiceRecord:
    action: str
    roll: int
    runner_modifier: int
    base_modifier: int
    catcher_modifier: int
    modified_roll: int


@dataclass(frozen=True)
class BuntDiceRecord:
    roll: int
    batter_modifier: int
    modified_roll: int
    defense_roll: int | None = None
    modified_defense_roll: int | None = None


@dataclass(frozen=True)
class HitAndRunDiceRecord:
    steal: StealDiceRecord
    swing: DiceRecord
    target_bonus: int
    adjusted_bt: int
    adjusted_obt: int


@dataclass(frozen=True)
class RuleTraceEntry:
    stage: str
    detail: str
    rule_reference: str


@dataclass(frozen=True)
class RunnerMove:
    runner_id: str
    from_base: str
    to_base: str | None = None
    scored: bool = False
    out: bool = False


@dataclass(frozen=True)
class PlayEvent:
    event_type: str
    classification: str
    batter_id: str
    pitcher_id: str
    resolved: bool
    outs_added: int = 0
    batter_destination: str | None = None
    fielded_by: str | None = None
    scoring_notation: str | None = None
    hit_type: str | None = None
    defense_outcome: str | None = None
    runner_moves: tuple[RunnerMove, ...] = ()
    runs_scored: int = 0
    out_type: str | None = None
    oddity_name: str | None = None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class StealEvent:
    event_type: str
    action: str
    resolved: bool
    runner_moves: tuple[RunnerMove, ...]
    outs_added: int = 0
    runs_scored: int = 0
    scoring_notation: str | None = None


@dataclass(frozen=True)
class SubstitutionEvent:
    event_type: str
    team_id: str
    incoming_player_id: str | None = None
    outgoing_player_id: str | None = None
    lineup_slot: int | None = None
    position: str | None = None
    base: str | None = None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionResult:
    event: PlayEvent | StealEvent | SubstitutionEvent
    new_state: GameState
    dice: DiceRecord | StealDiceRecord | BuntDiceRecord | HitAndRunDiceRecord | None
    rule_trace: tuple[RuleTraceEntry, ...]
