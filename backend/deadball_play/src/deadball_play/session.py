"""Versioned save, resume, autosave, undo, and structured history."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping

from deadball_core import (
    ActionResult,
    BuntDiceRecord,
    DefensiveAssignment,
    DiceRecord,
    GameResult,
    GameState,
    HitAndRunDiceRecord,
    InjuryRecord,
    InitialTeamState,
    ManagerState,
    OddityState,
    PitchDieAdjustment,
    PitcherOddityModifier,
    PitcherState,
    PlayEvent,
    RandomDice,
    RuleTraceEntry,
    RunnerMove,
    StealDiceRecord,
    StealEvent,
    SubstitutionEvent,
    load_generated_game,
    validate_team_state,
)


SAVE_FORMAT_VERSION = 1
APPLICATION_VERSION = "1.0.0rc1"
RULESET_ID = "deadball_second_edition_modern"
DEFAULT_COMPUTER_DARING = 10

SessionAction = Callable[[GameState, RandomDice], ActionResult]
HistoryEvent = PlayEvent | StealEvent | SubstitutionEvent
HistoryDice = DiceRecord | StealDiceRecord | BuntDiceRecord | HitAndRunDiceRecord | None


class SessionError(ValueError):
    """Base error for invalid session operations."""


class SessionSaveError(SessionError):
    """Raised when a session cannot be written atomically."""


class SessionLoadError(SessionError):
    """Raised when saved data cannot be restored safely."""


@dataclass(frozen=True)
class SessionConfig:
    away_control: str = "human"
    home_control: str = "human"
    away_daring: int | None = None
    home_daring: int | None = None

    def __post_init__(self) -> None:
        for side, control, daring, attribute in (
            ("away", self.away_control, self.away_daring, "away_daring"),
            ("home", self.home_control, self.home_daring, "home_daring"),
        ):
            if control not in {"human", "computer"}:
                raise SessionError(f"{side}_control must be 'human' or 'computer'")
            if daring is not None:
                try:
                    ManagerState(daring)
                except ValueError as exc:
                    raise SessionError(f"invalid {side} Daring: {exc}") from exc
            if control == "computer" and daring is None:
                object.__setattr__(self, attribute, DEFAULT_COMPUTER_DARING)


@dataclass(frozen=True)
class HistoryEntry:
    sequence: int
    event: HistoryEvent
    dice: HistoryDice
    rule_trace: tuple[RuleTraceEntry, ...]
    state_before: GameState
    rng_state_before: object
    scorekeeping_confirmed: bool = False


class GameSession:
    """Mutable application coordinator around immutable core state snapshots."""

    def __init__(
        self,
        state: GameState,
        *,
        rng: RandomDice | None = None,
        config: SessionConfig | None = None,
        history: tuple[HistoryEntry, ...] = (),
        autosave_path: str | Path | None = None,
        restored_from_save: bool = False,
    ) -> None:
        self.state = state
        self.rng = rng or RandomDice()
        self.config = config or SessionConfig()
        self.history = history
        self.autosave_path = Path(autosave_path) if autosave_path is not None else None
        self._revision = 0
        self._saved_revision = 0 if restored_from_save else -1
        self.last_save_path = self.autosave_path if restored_from_save else None
        self.last_save_at = (
            datetime.fromtimestamp(self.autosave_path.stat().st_mtime)
            if restored_from_save
            and self.autosave_path is not None
            and self.autosave_path.exists()
            else None
        )

    @property
    def is_saved(self) -> bool:
        """Whether the current in-memory revision has a durable save."""
        return self._saved_revision == self._revision and self.last_save_path is not None

    @property
    def autosave_status(self) -> str:
        """Describe the protected path and the latest successful write."""
        if self.autosave_path is None:
            return "Autosave: not configured"
        if self.is_saved and self.last_save_at is not None:
            return (
                f"Autosave: saved {self.last_save_at.strftime('%H:%M:%S')} to "
                f"{self.autosave_path.name} ({self.autosave_path})"
            )
        return (
            f"Autosave: pending for {self.autosave_path.name} "
            f"({self.autosave_path})"
        )

    @property
    def scorekeeping_confirmed(self) -> bool:
        return not self.history or self.history[-1].scorekeeping_confirmed

    @property
    def pending_event(self) -> HistoryEvent | None:
        if self.scorekeeping_confirmed:
            return None
        return self.history[-1].event

    def perform(self, action: SessionAction) -> ActionResult:
        """Run one action transaction and autosave its exact undo snapshot."""
        if self.state.is_final:
            raise SessionError("game is final")
        if not self.scorekeeping_confirmed:
            raise SessionError("scorekeeping confirmation is pending")
        state_before = self.state
        rng_before = self.rng.getstate()
        try:
            result = action(state_before, self.rng)
            if not isinstance(result, ActionResult):
                raise SessionError("session action must return ActionResult")
            if result.new_state.source != state_before.source:
                raise SessionError("session action changed the generated-game source")
        except Exception:
            self.rng.setstate(rng_before)
            raise

        entry = HistoryEntry(
            len(self.history) + 1,
            result.event,
            result.dice,
            result.rule_trace,
            state_before,
            rng_before,
        )
        self.state = result.new_state
        self.history = (*self.history, entry)
        self._revision += 1
        self._autosave()
        return result

    def confirm_scorekeeping(self) -> None:
        """Acknowledge the latest structured event without advancing play."""
        if not self.history:
            raise SessionError("there is no event to confirm")
        if self.history[-1].scorekeeping_confirmed:
            return
        self.history = (
            *self.history[:-1],
            replace(self.history[-1], scorekeeping_confirmed=True),
        )
        self._revision += 1
        self._autosave()

    def undo(self) -> HistoryEntry:
        """Restore the state and RNG from immediately before the latest action."""
        if not self.history:
            raise SessionError("there is no action to undo")
        entry = self.history[-1]
        self.state = entry.state_before
        self.rng.setstate(entry.rng_state_before)
        self.history = self.history[:-1]
        self._revision += 1
        self._autosave()
        return entry

    def update_config(self, config: SessionConfig) -> None:
        """Replace active mechanical manager settings and autosave them."""
        if not isinstance(config, SessionConfig):
            raise SessionError("config must be SessionConfig")
        self.config = config
        self._revision += 1
        self._autosave()

    def save(self, path: str | Path | None = None) -> Path:
        """Write a complete session document using flush-and-replace semantics."""
        target = Path(path) if path is not None else self.autosave_path
        if target is None:
            raise SessionSaveError("no save path is configured")
        target.parent.mkdir(parents=True, exist_ok=True)
        document = self.to_document()
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, target)
        except (OSError, TypeError, ValueError) as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise SessionSaveError(f"could not save session to {target}: {exc}") from exc
        self.autosave_path = target
        self.last_save_path = target
        self.last_save_at = datetime.now()
        self._saved_revision = self._revision
        return target

    def save_copy(self, path: str | Path) -> Path:
        """Write a copy without changing the active autosave destination."""
        autosave_path = self.autosave_path
        last_save_path = self.last_save_path
        last_save_at = self.last_save_at
        saved_revision = self._saved_revision
        try:
            return self.save(path)
        finally:
            self.autosave_path = autosave_path
            self.last_save_path = last_save_path
            self.last_save_at = last_save_at
            self._saved_revision = saved_revision

    def to_document(self) -> dict[str, Any]:
        """Build the JSON-compatible, versioned hybrid snapshot document."""
        return {
            "save_format_version": SAVE_FORMAT_VERSION,
            "application_version": APPLICATION_VERSION,
            "ruleset": RULESET_ID,
            "generated_game": self.state.source.to_dict(),
            "config": asdict(self.config),
            "current_state": _encode_state(self.state),
            "rng_state": self.rng.getstate(),
            "history": [_encode_history(entry) for entry in self.history],
        }

    @classmethod
    def load(cls, path: str | Path) -> GameSession:
        """Restore a session without modifying an unreadable or obsolete save."""
        source_path = Path(path)
        try:
            with source_path.open("r", encoding="utf-8") as stream:
                document = json.load(stream)
            root = _mapping(document, "save document")
            version = root.get("save_format_version")
            if version != SAVE_FORMAT_VERSION:
                raise SessionLoadError(
                    f"save_format_version must be {SAVE_FORMAT_VERSION}, got {version!r}"
                )
            if root.get("ruleset") != RULESET_ID:
                raise SessionLoadError(f"unsupported ruleset {root.get('ruleset')!r}")
            game = load_generated_game(_mapping(root.get("generated_game"), "generated_game"))
            state = _decode_state(root.get("current_state"), game)
            config = SessionConfig(**_mapping(root.get("config"), "config"))
            rng = RandomDice()
            rng.setstate(_tuplify(root.get("rng_state")))
            history = tuple(
                _decode_history(item, game)
                for item in _list(root.get("history"), "history")
            )
            _validate_history(history)
        except SessionLoadError:
            raise
        except Exception as exc:
            raise SessionLoadError(f"could not load session from {source_path}: {exc}") from exc
        return cls(
            state,
            rng=rng,
            config=config,
            history=history,
            autosave_path=source_path,
            restored_from_save=True,
        )

    def _autosave(self) -> None:
        if self.autosave_path is not None:
            self.save()


def _encode_history(entry: HistoryEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "event": _encode_typed(entry.event),
        "dice": None if entry.dice is None else _encode_typed(entry.dice),
        "rule_trace": [asdict(item) for item in entry.rule_trace],
        "state_before": _encode_state(entry.state_before),
        "rng_state_before": entry.rng_state_before,
        "scorekeeping_confirmed": entry.scorekeeping_confirmed,
    }


def _decode_history(raw: object, game) -> HistoryEntry:
    item = _mapping(raw, "history entry")
    trace = tuple(
        RuleTraceEntry(**_mapping(value, "rule trace"))
        for value in _list(item.get("rule_trace"), "rule_trace")
    )
    dice_raw = item.get("dice")
    return HistoryEntry(
        sequence=_positive_integer(item.get("sequence"), "history sequence"),
        event=_decode_event(item.get("event")),
        dice=None if dice_raw is None else _decode_dice(dice_raw),
        rule_trace=trace,
        state_before=_decode_state(item.get("state_before"), game),
        rng_state_before=_tuplify(item.get("rng_state_before")),
        scorekeeping_confirmed=_boolean(
            item.get("scorekeeping_confirmed"), "scorekeeping_confirmed"
        ),
    )


def _encode_typed(value: object) -> dict[str, Any]:
    return {"kind": type(value).__name__, "data": asdict(value)}


def _decode_event(raw: object) -> HistoryEvent:
    kind, data = _typed(raw, "event")
    if kind == "PlayEvent":
        data["runner_moves"] = tuple(
            RunnerMove(**_mapping(item, "runner move"))
            for item in _list(data.get("runner_moves", []), "runner_moves")
        )
        data["details"] = tuple(_list(data.get("details", []), "details"))
        return PlayEvent(**data)
    if kind == "StealEvent":
        data["runner_moves"] = tuple(
            RunnerMove(**_mapping(item, "runner move"))
            for item in _list(data.get("runner_moves", []), "runner_moves")
        )
        return StealEvent(**data)
    if kind == "SubstitutionEvent":
        data["details"] = tuple(_list(data.get("details", []), "details"))
        return SubstitutionEvent(**data)
    raise SessionLoadError(f"unknown event kind {kind!r}")


def _decode_dice(raw: object) -> HistoryDice:
    kind, data = _typed(raw, "dice")
    if kind == "DiceRecord":
        rolls = data.get("oddity_rolls")
        if rolls is not None:
            parsed = tuple(_list(rolls, "oddity rolls"))
            if len(parsed) != 2:
                raise SessionLoadError("oddity rolls must contain two values")
            data["oddity_rolls"] = parsed
        data["oddity_detail_rolls"] = tuple(
            tuple(_list(item, "oddity detail roll"))
            for item in _list(data.get("oddity_detail_rolls", []), "oddity detail rolls")
        )
        return DiceRecord(**data)
    if kind == "StealDiceRecord":
        return StealDiceRecord(**data)
    if kind == "BuntDiceRecord":
        return BuntDiceRecord(**data)
    if kind == "HitAndRunDiceRecord":
        data["steal"] = StealDiceRecord(**_mapping(data.get("steal"), "steal dice"))
        swing = dict(_mapping(data.get("swing"), "swing dice"))
        oddity_rolls = swing.get("oddity_rolls")
        if oddity_rolls is not None:
            swing["oddity_rolls"] = tuple(_list(oddity_rolls, "oddity rolls"))
        swing["oddity_detail_rolls"] = tuple(
            tuple(_list(item, "oddity detail roll"))
            for item in _list(
                swing.get("oddity_detail_rolls", []), "oddity detail rolls"
            )
        )
        data["swing"] = DiceRecord(**swing)
        return HitAndRunDiceRecord(**data)
    raise SessionLoadError(f"unknown dice kind {kind!r}")


def _typed(raw: object, name: str) -> tuple[str, dict[str, Any]]:
    item = _mapping(raw, name)
    kind = item.get("kind")
    if not isinstance(kind, str):
        raise SessionLoadError(f"{name}.kind must be text")
    return kind, dict(_mapping(item.get("data"), f"{name}.data"))


def _encode_state(state: GameState) -> dict[str, Any]:
    return {
        "inning": state.inning,
        "half": state.half,
        "outs": state.outs,
        "away_score": state.away_score,
        "home_score": state.home_score,
        "bases": list(state.bases),
        "away": _encode_team(state.away),
        "home": _encode_team(state.home),
        "result": None if state.result is None else asdict(state.result),
        "oddity_state": asdict(state.oddity_state),
    }


def _decode_state(raw: object, game) -> GameState:
    item = _mapping(raw, "game state")
    bases = tuple(_list(item.get("bases"), "bases"))
    if len(bases) != 3 or any(
        value is not None and not isinstance(value, str) for value in bases
    ):
        raise SessionLoadError("bases must contain three player IDs or null values")
    result_raw = item.get("result")
    result = None if result_raw is None else GameResult(**_mapping(result_raw, "result"))
    oddity_raw = _mapping(item.get("oddity_state", {}), "oddity state")
    oddity_state = OddityState(
        last_out_fielder_id=_optional_text(
            oddity_raw.get("last_out_fielder_id"), "last out fielder"
        ),
        poor_defenders=_text_tuple(oddity_raw.get("poor_defenders", []), "poor defenders"),
        home_batting_penalty_inning=(
            None
            if oddity_raw.get("home_batting_penalty_inning") is None
            else _positive_integer(
                oddity_raw.get("home_batting_penalty_inning"),
                "home batting penalty inning",
            )
        ),
        steal_bonus_batter_id=_optional_text(
            oddity_raw.get("steal_bonus_batter_id"), "steal bonus batter"
        ),
        catcher_steal_bonus_team_id=_optional_text(
            oddity_raw.get("catcher_steal_bonus_team_id"), "catcher steal bonus team"
        ),
        pitcher_modifiers=tuple(
            PitcherOddityModifier(**_mapping(value, "pitcher oddity modifier"))
            for value in _list(oddity_raw.get("pitcher_modifiers", []), "pitcher modifiers")
        ),
        injuries=tuple(
            InjuryRecord(**_mapping(value, "injury record"))
            for value in _list(oddity_raw.get("injuries", []), "injuries")
        ),
    )
    state = GameState(
        source=game,
        inning=_positive_integer(item.get("inning"), "inning"),
        half=_choice(item.get("half"), "half", {"top", "bottom"}),
        outs=_integer(item.get("outs"), "outs"),
        away_score=_nonnegative_integer(item.get("away_score"), "away_score"),
        home_score=_nonnegative_integer(item.get("home_score"), "home_score"),
        bases=bases,
        away=_decode_team(item.get("away")),
        home=_decode_team(item.get("home")),
        result=result,
        oddity_state=oddity_state,
    )
    _validate_state(state)
    return state


def _encode_team(team: InitialTeamState) -> dict[str, Any]:
    return {
        "team_id": team.team_id,
        "lineup": list(team.lineup),
        "batting_order_index": team.batting_order_index,
        "active_defense": [asdict(item) for item in team.active_defense],
        "bench": list(team.bench),
        "bullpen": list(team.bullpen),
        "active_pitcher_id": team.active_pitcher_id,
        "active_pitch_die": team.active_pitch_die,
        "pitcher_state": (
            None if team.pitcher_state is None else asdict(team.pitcher_state)
        ),
        "pitcher_lineup_slot": team.pitcher_lineup_slot,
        "removed_players": list(team.removed_players),
    }


def _decode_team(raw: object) -> InitialTeamState:
    item = _mapping(raw, "team state")
    pitcher_raw = item.get("pitcher_state")
    pitcher = None
    if pitcher_raw is not None:
        pitcher_data = dict(_mapping(pitcher_raw, "pitcher_state"))
        pitcher_data["adjustments"] = tuple(
            PitchDieAdjustment(**_mapping(value, "pitch adjustment"))
            for value in _list(pitcher_data.get("adjustments", []), "adjustments")
        )
        pitcher = PitcherState(**pitcher_data)
    lineup_slot = item.get("pitcher_lineup_slot")
    return InitialTeamState(
        team_id=_text(item.get("team_id"), "team_id"),
        lineup=_text_tuple(item.get("lineup"), "lineup"),
        batting_order_index=_integer(
            item.get("batting_order_index"), "batting_order_index"
        ),
        active_defense=tuple(
            DefensiveAssignment(**_mapping(value, "defensive assignment"))
            for value in _list(item.get("active_defense"), "active_defense")
        ),
        bench=_text_tuple(item.get("bench"), "bench"),
        bullpen=_text_tuple(item.get("bullpen"), "bullpen"),
        active_pitcher_id=_optional_text(
            item.get("active_pitcher_id"), "active_pitcher_id"
        ),
        active_pitch_die=_optional_text(
            item.get("active_pitch_die"), "active_pitch_die"
        ),
        pitcher_state=pitcher,
        pitcher_lineup_slot=(
            None if lineup_slot is None else _integer(lineup_slot, "pitcher_lineup_slot")
        ),
        removed_players=_text_tuple(item.get("removed_players"), "removed_players"),
    )


def _validate_state(state: GameState) -> None:
    if not 0 <= state.outs <= (3 if state.is_final else 2):
        raise SessionLoadError("outs are invalid for active/final game state")
    if state.away.team_id != state.source.teams.away.team_id:
        raise SessionLoadError("away team identity does not match generated game")
    if state.home.team_id != state.source.teams.home.team_id:
        raise SessionLoadError("home team identity does not match generated game")
    roster_ids = {
        player.player_id
        for team in (state.source.teams.away, state.source.teams.home)
        for player in team.roster
    }
    oddity_player_ids = {
        *state.oddity_state.poor_defenders,
        *(injury.player_id for injury in state.oddity_state.injuries),
        *(modifier.player_id for modifier in state.oddity_state.pitcher_modifiers),
    }
    if not oddity_player_ids <= roster_ids:
        raise SessionLoadError("Oddities state references an unknown player")
    if len(state.oddity_state.injuries) != len(
        {injury.player_id for injury in state.oddity_state.injuries}
    ):
        raise SessionLoadError("Oddities state contains duplicate injury records")
    try:
        validate_team_state(state, "away")
        validate_team_state(state, "home")
    except ValueError as exc:
        raise SessionLoadError(f"invalid active roster state: {exc}") from exc
    offense = state.away if state.half == "top" else state.home
    for team in (state.away, state.home):
        if not 0 <= team.batting_order_index < len(team.lineup):
            raise SessionLoadError("batting-order index is out of range")
        if team.pitcher_lineup_slot is not None and not 0 <= team.pitcher_lineup_slot < 9:
            raise SessionLoadError("pitcher lineup slot is out of range")
        if team.pitcher_state is not None:
            if team.active_pitch_die != team.pitcher_state.current_pitch_die:
                raise SessionLoadError("active Pitch Die and pitcher state disagree")
            counters = (
                team.pitcher_state.outs_recorded,
                team.pitcher_state.runs_allowed,
                team.pitcher_state.completed_innings,
                team.pitcher_state.current_inning_runs,
                team.pitcher_state.current_inning_batters_faced,
                team.pitcher_state.batters_faced_since_entry,
                team.pitcher_state.current_inning_strikeouts,
                team.pitcher_state.consecutive_scoreless_innings,
                team.pitcher_state.runs_since_jam,
            )
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in counters
            ):
                raise SessionLoadError("pitcher counters must be nonnegative integers")
    occupied = [runner for runner in state.bases if runner is not None]
    if len(occupied) != len(set(occupied)):
        raise SessionLoadError("a runner cannot occupy two bases")
    if any(runner is not None and runner not in offense.lineup for runner in state.bases):
        raise SessionLoadError("base runner is not in the active offensive lineup")
    if state.result is not None:
        winner = state.home if state.home_score > state.away_score else state.away
        if state.home_score == state.away_score:
            raise SessionLoadError("final game cannot be tied")
        if state.result.winner_team_id != winner.team_id:
            raise SessionLoadError("final winner does not match the score")
        if (state.result.inning, state.result.half) != (state.inning, state.half):
            raise SessionLoadError("final result location does not match game state")
        if state.inning < 9 or state.result.ending not in {
            "regulation",
            "extra_innings",
            "walk_off",
        }:
            raise SessionLoadError("final result has an invalid ending")
        if any(runner is not None for runner in state.bases):
            raise SessionLoadError("final game must have empty bases")


def _validate_history(history: tuple[HistoryEntry, ...]) -> None:
    expected = list(range(1, len(history) + 1))
    if [entry.sequence for entry in history] != expected:
        raise SessionLoadError("history sequence must be contiguous from one")
    if any(not entry.scorekeeping_confirmed for entry in history[:-1]):
        raise SessionLoadError("only the latest history event may await confirmation")
    for entry in history:
        try:
            RandomDice().setstate(entry.rng_state_before)
        except Exception as exc:
            raise SessionLoadError("history contains invalid RNG state") from exc


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SessionLoadError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise SessionLoadError(f"{name} must be an array")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SessionLoadError(f"{name} must be an integer")
    return value


def _positive_integer(value: object, name: str) -> int:
    result = _integer(value, name)
    if result < 1:
        raise SessionLoadError(f"{name} must be positive")
    return result


def _nonnegative_integer(value: object, name: str) -> int:
    result = _integer(value, name)
    if result < 0:
        raise SessionLoadError(f"{name} cannot be negative")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SessionLoadError(f"{name} must be boolean")
    return value


def _choice(value: object, name: str, choices: set[str]) -> str:
    result = _text(value, name)
    if result not in choices:
        raise SessionLoadError(f"{name} must be one of {sorted(choices)}")
    return result


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionLoadError(f"{name} must be non-empty text")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    values = _list(value, name)
    if any(not isinstance(item, str) or not item for item in values):
        raise SessionLoadError(f"{name} must contain non-empty text values")
    return tuple(values)


def _tuplify(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuplify(item) for item in value)
    return value
