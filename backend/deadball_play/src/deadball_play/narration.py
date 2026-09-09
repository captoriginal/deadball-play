"""Mechanically neutral narration and stable paper-scoring guidance."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Mapping

from deadball_core import GameState, PlayEvent, RunnerMove, StealEvent, SubstitutionEvent


NarratedEvent = PlayEvent | StealEvent | SubstitutionEvent


class NarrationError(ValueError):
    """Raised when structured facts are insufficient or inconsistent."""


@dataclass(frozen=True)
class NarrationResult:
    family: str
    play_text: str
    scoring_guidance: tuple[str, ...]
    transition_text: str | None = None

    @property
    def spoken_text(self) -> str:
        """Return prose only, without abbreviated scoring notation."""
        return " ".join(
            text for text in (self.play_text, self.transition_text) if text
        )


@dataclass(frozen=True)
class _Template:
    text: str
    required: tuple[str, ...]


TEMPLATES: Mapping[str, tuple[_Template, ...]] = {
    "strikeout": (
        _Template("{batter} strikes out.", ("batter",)),
        _Template("{pitcher} retires {batter} on strikes.", ("pitcher", "batter")),
        _Template("{batter} is retired on strikes.", ("batter",)),
        _Template("{pitcher} wins the matchup and strikes out {batter}.", ("pitcher", "batter")),
        _Template("{batter} goes down swinging against {pitcher}.", ("batter", "pitcher")),
    ),
    "walk": (
        _Template("{batter} draws a walk.", ("batter",)),
        _Template("{batter} takes ball four.", ("batter",)),
        _Template("{pitcher} issues a walk to {batter}.", ("pitcher", "batter")),
        _Template("{batter} reaches on a base on balls.", ("batter",)),
        _Template("{batter} works a walk from {pitcher}.", ("batter", "pitcher")),
        _Template("Ball four sends {batter} to first.", ("batter",)),
    ),
    "single": (
        _Template("{batter} singles.", ("batter",)),
        _Template("A base hit for {batter}.", ("batter",)),
        _Template("{batter} reaches on a single.", ("batter",)),
        _Template("{batter} lines a clean single.", ("batter",)),
        _Template("{batter} finds open grass for a base hit.", ("batter",)),
    ),
    "double": (
        _Template("{batter} doubles.", ("batter",)),
        _Template("A two-base hit for {batter}.", ("batter",)),
        _Template("{batter} reaches second with a double.", ("batter",)),
        _Template("{batter} drives the ball for two bases.", ("batter",)),
        _Template("{batter} pulls into second with a stand-up double.", ("batter",)),
    ),
    "triple": (
        _Template("{batter} triples.", ("batter",)),
        _Template("A three-base hit for {batter}.", ("batter",)),
        _Template("{batter} reaches third with a triple.", ("batter",)),
        _Template("{batter} races into third with a triple.", ("batter",)),
    ),
    "home_run": (
        _Template("{batter} homers.", ("batter",)),
        _Template("A home run for {batter}.", ("batter",)),
        _Template("{batter} sends one out of the park.", ("batter",)),
        _Template("{batter} goes deep.", ("batter",)),
        _Template("{batter} launches a home run.", ("batter",)),
        _Template("{batter} circles the bases after sending it over the fence.", ("batter",)),
    ),
    "grand_slam": (
        _Template("Grand slam! {batter} clears the bases!", ("batter",)),
        _Template("{batter} launches a grand slam!", ("batter",)),
        _Template("A grand slam for {batter}!", ("batter",)),
    ),
    "groundout": (
        _Template("{batter} grounds to {fielder}.", ("batter", "fielder")),
        _Template("A ground ball to {fielder} retires {batter}.", ("fielder", "batter")),
        _Template("{batter} is retired on a grounder to {fielder}.", ("batter", "fielder")),
        _Template(
            "{fielder_cap} fields {batter}'s ground ball for the out.",
            ("fielder_cap", "batter"),
        ),
        _Template("{batter} bounces one to {fielder} and is retired.", ("batter", "fielder")),
    ),
    "flyout": (
        _Template("{batter} flies out to {fielder}.", ("batter", "fielder")),
        _Template(
            "{fielder_cap} retires {batter} on a fly ball.",
            ("fielder_cap", "batter"),
        ),
        _Template("A fly ball to {fielder} is caught for the out.", ("fielder",)),
        _Template(
            "{fielder_cap} settles under {batter}'s fly ball.",
            ("fielder_cap", "batter"),
        ),
        _Template("{batter} lifts a routine fly to {fielder}.", ("batter", "fielder")),
    ),
    "fielders_choice": (
        _Template("{batter} reaches on a fielder's choice.", ("batter",)),
        _Template("The defense gets the lead runner on {batter}'s ground ball.", ("batter",)),
    ),
    "double_play": (
        _Template("{batter} grounds into a double play.", ("batter",)),
        _Template("The defense turns two on {batter}.", ("batter",)),
        _Template("A ground ball from {batter} becomes a double play.", ("batter",)),
        _Template(
            "{fielder_cap} starts a double play on {batter}'s grounder.",
            ("fielder_cap", "batter"),
        ),
        _Template("Two are gone as the defense doubles up {batter}.", ("batter",)),
    ),
    "hit_and_run_double_play": (
        _Template("The hit-and-run results in a double play on {batter}.", ("batter",)),
        _Template("The defense turns two with the hit-and-run in motion.", ()),
    ),
    "triple_play": (
        _Template("The defense turns a triple play on {batter}.", ("batter",)),
        _Template("{batter}'s ground ball becomes a triple play.", ("batter",)),
    ),
    "error": (
        _Template("{batter} reaches on an error by {fielder}.", ("batter", "fielder")),
        _Template(
            "{fielder_cap} commits an error, and {batter} is safe.",
            ("fielder_cap", "batter"),
        ),
        _Template("An error by {fielder} allows {batter} to reach.", ("fielder", "batter")),
    ),
    "defensive_out": (
        _Template(
            "{fielder_cap} takes a hit away from {batter}.",
            ("fielder_cap", "batter"),
        ),
        _Template(
            "{fielder_cap} turns {batter}'s hit into an out.",
            ("fielder_cap", "batter"),
        ),
    ),
    "bunt_fielders_choice": (
        _Template("{batter} bunts, and the defense gets the lead runner.", ("batter",)),
        _Template("The bunt by {batter} results in a fielder's choice.", ("batter",)),
    ),
    "bunt_out": (
        _Template("{batter} bunts and is retired by {fielder}.", ("batter", "fielder")),
        _Template(
            "{fielder_cap} handles the bunt and retires {batter}.",
            ("fielder_cap", "batter"),
        ),
    ),
    "sacrifice_bunt": (
        _Template("{batter} lays down a sacrifice bunt.", ("batter",)),
        _Template("A sacrifice bunt from {batter} advances the runner.", ("batter",)),
    ),
    "hit_and_run_hit": (
        _Template("{batter} singles on the hit-and-run.", ("batter",)),
        _Template("The hit-and-run produces a single for {batter}.", ("batter",)),
    ),
    "hit_and_run_out": (
        _Template("{batter} is retired with the hit-and-run in motion.", ("batter",)),
        _Template("The hit-and-run is on, but {batter} makes an out.", ("batter",)),
    ),
    "generic_out": (
        _Template("{batter} is retired.", ("batter",)),
        _Template("The defense records the out on {batter}.", ("batter",)),
    ),
    "oddity": (
        _Template("The play requires an Oddities resolution.", ()),
    ),
    "stolen_base": (
        _Template("{runner} steals {destination}.", ("runner", "destination")),
        _Template(
            "{runner} is safe at {destination} with a stolen base.",
            ("runner", "destination"),
        ),
    ),
    "caught_stealing": (
        _Template("{runner} is caught stealing.", ("runner",)),
        _Template("The defense throws out {runner} on the steal attempt.", ("runner",)),
    ),
    "double_steal": (
        _Template("The runners execute a double steal.", ()),
        _Template("Both runners advance on the double steal.", ()),
    ),
    "pinch_hit": (
        _Template("{incoming} will pinch hit for {outgoing}.", ("incoming", "outgoing")),
        _Template("{incoming} enters as a pinch hitter for {outgoing}.", ("incoming", "outgoing")),
    ),
    "pinch_run": (
        _Template("{incoming} will pinch run for {outgoing}.", ("incoming", "outgoing")),
        _Template("{incoming} replaces {outgoing} on the bases.", ("incoming", "outgoing")),
    ),
    "pitching_change": (
        _Template("{incoming} takes over on the mound.", ("incoming",)),
        _Template("The call goes to {incoming} from the bullpen.", ("incoming",)),
        _Template("{incoming} is the new pitcher.", ("incoming",)),
    ),
    "defensive_substitution": (
        _Template(
            "{incoming} replaces {outgoing} at {position}.",
            ("incoming", "outgoing", "position"),
        ),
        _Template("{incoming} enters defensively at {position}.", ("incoming", "position")),
    ),
    "position_change": (
        _Template("The defense changes its alignment.", ()),
        _Template("The fielders switch positions.", ()),
    ),
}


class Narrator:
    """Render events with an RNG independent from mechanical game dice."""

    def __init__(
        self,
        rng: random.Random | None = None,
        *,
        recent_window: int = 2,
    ) -> None:
        if recent_window < 0:
            raise NarrationError("recent_window cannot be negative")
        self._rng = rng or random.Random()
        self._recent_window = recent_window
        self._recent: dict[str, list[int]] = {}

    def render(
        self,
        event: NarratedEvent,
        before: GameState,
        after: GameState,
    ) -> NarrationResult:
        """Describe one event without mutating or re-resolving mechanics."""
        if before.source != after.source:
            raise NarrationError("before and after states come from different games")
        _validate_context(event, before, after)
        fields = self._fields(event, before)
        family = _family(event)
        if isinstance(event, PlayEvent) and event.oddity_name:
            explanation = " ".join(event.details)
            sentences = [f"Oddity - {event.oddity_name}. {explanation}".strip()]
        else:
            template = self._choose_template(family, fields)
            sentences = [template.text.format(**fields)]
        sentences.extend(_runner_sentences(event, before))
        outs_sentence = _outs_sentence(before, after, event)
        if outs_sentence is not None:
            sentences.append(outs_sentence)
        context_sentence = _scoring_context(before, after, event)
        if context_sentence is not None:
            sentences.append(context_sentence)
        return NarrationResult(
            family=family,
            play_text=" ".join(sentences),
            scoring_guidance=_scoring_guidance(event, before, after),
            transition_text=_transition_text(before, after, event),
        )

    def _choose_template(self, family: str, fields: Mapping[str, str]) -> _Template:
        templates = TEMPLATES.get(family)
        if not templates:
            raise NarrationError(f"no narration templates for {family!r}")
        available = [
            index
            for index, template in enumerate(templates)
            if all(fields.get(name) for name in template.required)
        ]
        if not available:
            raise NarrationError(f"event lacks fields required to narrate {family}")
        recent = self._recent.get(family, [])
        choices = [index for index in available if index not in recent]
        if not choices:
            choices = [index for index in available if not recent or index != recent[-1]]
        if not choices:
            choices = available
        selected = self._rng.choice(choices)
        if self._recent_window:
            self._recent[family] = [*recent, selected][-self._recent_window:]
        return templates[selected]

    def _fields(self, event: NarratedEvent, state: GameState) -> dict[str, str]:
        if isinstance(event, PlayEvent):
            fielder = _fielder_name(state, event.fielded_by)
            return {
                "batter": _player_name(state, event.batter_id),
                "pitcher": _player_name(state, event.pitcher_id),
                "fielder": fielder[:1].lower() + fielder[1:],
                "fielder_cap": fielder,
            }
        if isinstance(event, StealEvent):
            move = event.runner_moves[0] if event.runner_moves else None
            return {
                "runner": "" if move is None else _player_name(state, move.runner_id),
                "destination": "" if move is None else _base_name(move.to_base),
            }
        if isinstance(event, SubstitutionEvent):
            return {
                "incoming": _optional_player_name(state, event.incoming_player_id),
                "outgoing": _optional_player_name(state, event.outgoing_player_id),
                "position": _position_name(event.position),
            }
        raise NarrationError(f"unsupported event type {type(event).__name__}")


def _family(event: NarratedEvent) -> str:
    if isinstance(event, SubstitutionEvent):
        return event.event_type
    if isinstance(event, StealEvent):
        return event.event_type
    if event.oddity_name:
        return "oddity"
    if not event.resolved:
        return "oddity"
    if event.event_type == "home_run" and event.runs_scored == 4:
        return "grand_slam"
    if event.event_type == "double_play" and event.classification == "hit_and_run":
        return "hit_and_run_double_play"
    if event.defense_outcome == "out" and event.hit_type is not None:
        return "defensive_out"
    if event.event_type in TEMPLATES:
        return event.event_type
    if event.hit_type in {"single", "double", "triple", "home_run"}:
        return event.hit_type
    if event.outs_added:
        return event.out_type if event.out_type in TEMPLATES else "generic_out"
    raise NarrationError(f"unsupported play event {event.event_type!r}")


def _runner_sentences(event: NarratedEvent, state: GameState) -> list[str]:
    if isinstance(event, SubstitutionEvent):
        return []
    if isinstance(event, StealEvent):
        return []
    moves = event.runner_moves
    batter_id = event.batter_id if isinstance(event, PlayEvent) else None
    sentences = []
    for move in moves:
        if move.runner_id == batter_id and move.from_base == "BATTER":
            continue
        runner = _player_name(state, move.runner_id)
        if move.scored:
            sentences.append(f"{runner} scores.")
        elif move.out:
            sentences.append(f"{runner} is out from {_base_name(move.from_base)}.")
        elif move.to_base is not None:
            if move.from_base == move.to_base:
                sentences.append(f"{runner} returns to {_base_name(move.to_base)}.")
            else:
                sentences.append(
                    f"{runner} advances from {_base_name(move.from_base)} "
                    f"to {_base_name(move.to_base)}."
                )
    return sentences


def _validate_context(
    event: NarratedEvent, before: GameState, after: GameState
) -> None:
    away_delta = after.away_score - before.away_score
    home_delta = after.home_score - before.home_score
    expected_runs = (
        event.runs_scored if isinstance(event, (PlayEvent, StealEvent)) else 0
    )
    expected_deltas = (
        (expected_runs, 0) if before.half == "top" else (0, expected_runs)
    )
    if (away_delta, home_delta) != expected_deltas:
        raise NarrationError("event runs do not match the supplied state transition")

    offense = before.source.teams.away if before.half == "top" else before.source.teams.home
    defense = before.source.teams.home if before.half == "top" else before.source.teams.away
    offense_ids = {player.player_id for player in offense.roster}
    defense_ids = {player.player_id for player in defense.roster}
    known_ids = offense_ids | defense_ids
    if isinstance(event, PlayEvent):
        if event.batter_id not in known_ids or event.pitcher_id not in known_ids:
            raise NarrationError("event references an unknown player")
        if event.batter_id not in offense_ids or event.pitcher_id not in defense_ids:
            raise NarrationError("batter or pitcher does not match the active sides")
        if any(move.runner_id not in known_ids for move in event.runner_moves):
            raise NarrationError("event references an unknown player")
        if any(move.runner_id not in offense_ids for move in event.runner_moves):
            raise NarrationError("runner does not belong to the batting team")
    elif isinstance(event, StealEvent):
        if any(move.runner_id not in known_ids for move in event.runner_moves):
            raise NarrationError("event references an unknown player")
        if any(move.runner_id not in offense_ids for move in event.runner_moves):
            raise NarrationError("runner does not belong to the batting team")
    else:
        team = (
            before.source.teams.away
            if event.team_id == before.away.team_id
            else before.source.teams.home
            if event.team_id == before.home.team_id
            else None
        )
        if team is None:
            raise NarrationError("substitution references an unknown team")
        team_ids = {player.player_id for player in team.roster}
        if any(
            player_id is not None and player_id not in team_ids
            for player_id in (event.incoming_player_id, event.outgoing_player_id)
        ):
            raise NarrationError("substitution player does not belong to the team")


def _scoring_context(
    before: GameState, after: GameState, event: NarratedEvent
) -> str | None:
    runs = event.runs_scored if isinstance(event, (PlayEvent, StealEvent)) else 0
    if runs == 0:
        return None
    before_tied = before.away_score == before.home_score
    after_tied = after.away_score == after.home_score
    if after_tied and not before_tied:
        return f"That ties the game at {after.away_score}."
    offense_was_behind_or_tied = (
        before.away_score <= before.home_score
        if before.half == "top"
        else before.home_score <= before.away_score
    )
    offense_now_leads = (
        after.away_score > after.home_score
        if before.half == "top"
        else after.home_score > after.away_score
    )
    if offense_was_behind_or_tied and offense_now_leads:
        team = (
            before.source.teams.away
            if before.half == "top"
            else before.source.teams.home
        )
        return f"{team.short_name} takes the lead, {after.away_score}-{after.home_score}."
    return None


def _scoring_guidance(
    event: NarratedEvent, before: GameState, after: GameState
) -> tuple[str, ...]:
    if isinstance(event, SubstitutionEvent):
        return _substitution_guidance(event, before)
    lines = []
    notation = event.scoring_notation
    if isinstance(event, PlayEvent) and event.event_type == "double_play":
        fielder = _position_name(event.fielded_by) or "the infield"
        lines.append(f"Score: DP (ground ball initiated by {fielder})")
    elif isinstance(event, PlayEvent) and event.event_type == "triple_play":
        fielder = _position_name(event.fielded_by) or "the infield"
        lines.append(f"Score: TP (ground ball initiated by {fielder})")
    elif notation:
        lines.append(f"Score: {notation}")
    elif isinstance(event, PlayEvent) and event.defense_outcome == "out":
        lines.append(f"Score: OUT (DEF {event.fielded_by})")
    elif isinstance(event, PlayEvent) and not event.resolved:
        lines.append("Score: Oddity; at-bat continues")
    outs = [move for move in event.runner_moves if move.out]
    if isinstance(event, PlayEvent) and event.event_type in {
        "double_play",
        "triple_play",
    }:
        descriptions = "; ".join(
            f"{_player_name(before, move.runner_id)} ({move.from_base})"
            for move in outs
        )
        lines.append(f"Outs: {descriptions}")
    else:
        for move in event.runner_moves:
            runner = _player_name(before, move.runner_id)
            if move.scored:
                lines.append(f"Runner: {runner} -> HOME")
            elif move.out:
                lines.append(f"Runner: {runner} OUT ({move.from_base})")
            elif move.to_base is not None:
                lines.append(f"Runner: {runner} -> {move.to_base}")
    if event.runs_scored:
        lines.append(f"Runs: {event.runs_scored}")
    if event.runs_scored or before.half != after.half or after.is_final:
        away = before.source.teams.away.short_name
        home = before.source.teams.home.short_name
        lines.append(f"Scoreboard: {away} {after.away_score}, {home} {after.home_score}")
    return tuple(lines)


def _substitution_guidance(
    event: SubstitutionEvent, state: GameState
) -> tuple[str, ...]:
    incoming = _optional_player_name(state, event.incoming_player_id)
    outgoing = _optional_player_name(state, event.outgoing_player_id)
    if event.event_type == "position_change":
        return tuple(f"Defense: {detail}" for detail in event.details)
    action = event.event_type.replace("_", " ").title()
    line = f"{action}: {incoming} for {outgoing}" if outgoing else f"{action}: {incoming}"
    details = []
    if event.lineup_slot is not None:
        details.append(f"Lineup slot: {event.lineup_slot}")
    if event.position is not None:
        details.append(f"Position: {event.position}")
    if event.base is not None:
        details.append(f"Base: {event.base}")
    return (line, *details)


def _transition_text(
    before: GameState, after: GameState, event: NarratedEvent
) -> str | None:
    away = before.source.teams.away.short_name
    home = before.source.teams.home.short_name
    score = f"{away} {after.away_score}, {home} {after.home_score}."
    if after.result is not None:
        winner = home if after.result.winner_team_id == after.home.team_id else away
        if after.result.ending == "walk_off":
            return f"{winner} wins on a walk-off. Final: {score}"
        return f"Final: {score} {winner} wins."
    if (before.inning, before.half) != (after.inning, after.half):
        half = "top" if before.half == "top" else "bottom"
        team = before.source.teams.away if before.half == "top" else before.source.teams.home
        left = _runners_left_on_base(before, event)
        lob = (
            f" {team.short_name} leaves {left} runner{'s' if left != 1 else ''} on base."
            if left
            else ""
        )
        automatic_runner = ""
        if after.inning >= 10 and after.bases[1] is not None:
            name = _player_name(after, after.bases[1])
            next_half = "top" if after.half == "top" else "bottom"
            automatic_runner = (
                f" {name} starts the {next_half} of the "
                f"{_ordinal(after.inning)} on second as the automatic runner."
            )
        return (
            f"That ends the {half} of the {_ordinal(before.inning)}."
            f"{lob} {score}{automatic_runner}"
        )
    return None


def _outs_sentence(
    before: GameState, after: GameState, event: NarratedEvent
) -> str | None:
    if not isinstance(event, (PlayEvent, StealEvent)) or event.outs_added == 0:
        return None
    outs = min(3, before.outs + event.outs_added)
    if outs == 1:
        return "There is one out."
    if outs == 2:
        return "There are two outs."
    return "That is the third out."


def _runners_left_on_base(before: GameState, event: NarratedEvent) -> int:
    """Count occupied bases immediately before an inning-ending play."""
    remaining = {runner for runner in before.bases if runner is not None}
    for move in getattr(event, "runner_moves", ()):
        if move.out or move.scored:
            remaining.discard(move.runner_id)
    return len(remaining)


def _player_name(state: GameState, player_id: str) -> str:
    for team in (state.source.teams.away, state.source.teams.home):
        try:
            return team.player(player_id).name
        except KeyError:
            continue
    raise NarrationError(f"event references unknown player {player_id!r}")


def _optional_player_name(state: GameState, player_id: str | None) -> str:
    return "" if player_id is None else _player_name(state, player_id)


def _position_name(position: str | None) -> str:
    if position is None:
        return ""
    return {
        "P": "pitcher",
        "C": "catcher",
        "1B": "first base",
        "2B": "second base",
        "3B": "third base",
        "SS": "shortstop",
        "LF": "left field",
        "CF": "center field",
        "RF": "right field",
    }.get(position, position)


def _fielder_name(state: GameState, position: str | None) -> str:
    if position is None:
        return ""
    defense_state = state.home if state.half == "top" else state.away
    defense_team = (
        state.source.teams.home
        if state.half == "top"
        else state.source.teams.away
    )
    player_id = next(
        (
            assignment.player_id
            for assignment in defense_state.active_defense
            if assignment.position == position
        ),
        None,
    )
    roles = {
        "P": "Pitcher",
        "C": "Catcher",
        "1B": "First baseman",
        "2B": "Second baseman",
        "3B": "Third baseman",
        "SS": "Shortstop",
        "LF": "Left fielder",
        "CF": "Center fielder",
        "RF": "Right fielder",
    }
    role = roles.get(position, position)
    if player_id is None:
        return role
    return f"{role} {defense_team.player(player_id).name}"


def _base_name(base: str | None) -> str:
    if base is None:
        return ""
    return {
        "1B": "first",
        "2B": "second",
        "3B": "third",
        "HOME": "home",
        "BATTER": "the batter's box",
    }.get(base, base)


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
