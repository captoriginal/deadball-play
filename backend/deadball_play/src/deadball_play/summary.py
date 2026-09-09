"""Derived scoreboard and final-box-score presentation data."""

from __future__ import annotations

from dataclasses import dataclass

from deadball_core import GameState, PlayEvent, StealEvent

from .session import HistoryEntry


@dataclass(frozen=True)
class TeamBox:
    runs_by_inning: tuple[int | None, ...]
    hits: int
    errors: int

    @property
    def runs(self) -> int:
        return sum(value for value in self.runs_by_inning if value is not None)


@dataclass(frozen=True)
class GameBox:
    away: TeamBox
    home: TeamBox


@dataclass(frozen=True)
class BattingLine:
    plate_appearances: int = 0
    at_bats: int = 0
    hits: int = 0
    runs: int = 0
    rbi: int = 0
    walks: int = 0
    strikeouts: int = 0


@dataclass(frozen=True)
class PitchingLine:
    outs: int = 0
    hits: int = 0
    runs: int = 0
    walks: int = 0
    strikeouts: int = 0

    @property
    def innings_pitched(self) -> str:
        return f"{self.outs // 3}.{self.outs % 3}"


def build_batting_lines(
    history: tuple[HistoryEntry, ...],
) -> dict[str, BattingLine]:
    """Derive standard batter lines from confirmed structured play history."""
    totals: dict[str, BattingLine] = {}
    for entry in history:
        event = entry.event
        if isinstance(event, PlayEvent) and event.resolved:
            previous = totals.get(event.batter_id, BattingLine())
            walk = event.event_type == "walk" or event.classification == "walk"
            sacrifice = event.event_type == "sacrifice_bunt"
            no_at_bat = event.scoring_notation in {"HBP", "CI"}
            hit = (
                event.hit_type is not None
                and event.defense_outcome not in {"out", "error"}
            )
            totals[event.batter_id] = BattingLine(
                plate_appearances=previous.plate_appearances + 1,
                at_bats=previous.at_bats + int(not walk and not sacrifice and not no_at_bat),
                hits=previous.hits + int(hit),
                runs=previous.runs,
                rbi=previous.rbi + (
                    0 if event.event_type == "error" else event.runs_scored
                ),
                walks=previous.walks + int(walk),
                strikeouts=previous.strikeouts + int(
                    event.event_type == "strikeout" or event.out_type == "strikeout"
                ),
            )
        for move in getattr(event, "runner_moves", ()):
            if move.scored:
                previous = totals.get(move.runner_id, BattingLine())
                totals[move.runner_id] = BattingLine(
                    plate_appearances=previous.plate_appearances,
                    at_bats=previous.at_bats,
                    hits=previous.hits,
                    runs=previous.runs + 1,
                    rbi=previous.rbi,
                    walks=previous.walks,
                    strikeouts=previous.strikeouts,
                )
    return totals


def build_pitching_lines(
    history: tuple[HistoryEntry, ...],
) -> dict[str, PitchingLine]:
    """Derive IP/H/R/BB/K pitcher lines from confirmed structured history."""
    totals: dict[str, PitchingLine] = {}
    for entry in history:
        event = entry.event
        if isinstance(event, PlayEvent):
            pitcher_id = event.pitcher_id
        elif isinstance(event, StealEvent):
            defense = (
                entry.state_before.home
                if entry.state_before.half == "top"
                else entry.state_before.away
            )
            pitcher_id = defense.active_pitcher_id
        else:
            continue
        if pitcher_id is None:
            continue
        previous = totals.get(pitcher_id, PitchingLine())
        hit = isinstance(event, PlayEvent) and event.resolved and (
            event.hit_type is not None
            and event.defense_outcome not in {"out", "error"}
        )
        walk = isinstance(event, PlayEvent) and event.resolved and (
            event.event_type == "walk" or event.classification == "walk"
        )
        strikeout = (
            isinstance(event, PlayEvent)
            and event.resolved
            and (event.event_type == "strikeout" or event.out_type == "strikeout")
        )
        totals[pitcher_id] = PitchingLine(
            outs=previous.outs + event.outs_added,
            hits=previous.hits + int(hit),
            runs=previous.runs + event.runs_scored,
            walks=previous.walks + int(walk),
            strikeouts=previous.strikeouts + int(strikeout),
        )
    return totals


def build_game_box(state: GameState, history: tuple[HistoryEntry, ...]) -> GameBox:
    """Derive line-score totals exclusively from structured history."""
    innings = max(
        [state.inning, *(entry.state_before.inning for entry in history)],
        default=state.inning,
    )
    runs: dict[str, list[int | None]] = {
        "away": [None] * innings,
        "home": [None] * innings,
    }
    if not state.is_final:
        current_side = "away" if state.half == "top" else "home"
        runs[current_side][state.inning - 1] = 0
    hits = {"away": 0, "home": 0}
    errors = {"away": 0, "home": 0}
    for entry in history:
        event = entry.event
        offense = "away" if entry.state_before.half == "top" else "home"
        defense = "home" if offense == "away" else "away"
        if isinstance(event, (PlayEvent, StealEvent)):
            index = entry.state_before.inning - 1
            runs[offense][index] = (runs[offense][index] or 0) + event.runs_scored
        if isinstance(event, PlayEvent):
            if (
                event.hit_type is not None
                and event.defense_outcome not in {"out", "error"}
            ):
                hits[offense] += 1
            if event.event_type == "error" or event.defense_outcome == "error":
                errors[defense] += 1
    return GameBox(
        away=TeamBox(tuple(runs["away"]), hits["away"], errors["away"]),
        home=TeamBox(tuple(runs["home"]), hits["home"], errors["home"]),
    )


def pitchers_of_record(
    state: GameState,
    history: tuple[HistoryEntry, ...],
) -> tuple[str, str]:
    """Return the application-defined winning and losing pitchers of record."""
    if state.result is None:
        return "-", "-"
    winner_id = state.result.winner_team_id
    snapshots = [entry.state_before for entry in history[1:]] + [state]
    lead_entry: HistoryEntry | None = None
    for index, entry in enumerate(history):
        after = snapshots[index]
        winner_leads = (
            after.away_score > after.home_score
            if winner_id == state.away.team_id
            else after.home_score > after.away_score
        )
        if winner_leads and all(
            _winner_leads(snapshot, winner_id) for snapshot in snapshots[index:]
        ):
            lead_entry = entry
            break
    if lead_entry is None:
        return "-", "-"
    winner_state = (
        lead_entry.state_before.away
        if winner_id == state.away.team_id
        else lead_entry.state_before.home
    )
    loser_state = (
        lead_entry.state_before.home
        if winner_id == state.away.team_id
        else lead_entry.state_before.away
    )
    winning_id = winner_state.active_pitcher_id
    event = lead_entry.event
    losing_id = (
        event.pitcher_id
        if isinstance(event, PlayEvent)
        else loser_state.active_pitcher_id
    )
    return _player_name(state, winning_id), _player_name(state, losing_id)


def _winner_leads(state: GameState, winner_id: str) -> bool:
    if winner_id == state.away.team_id:
        return state.away_score > state.home_score
    return state.home_score > state.away_score


def _player_name(state: GameState, player_id: str | None) -> str:
    if player_id is None:
        return "-"
    for team in (state.source.teams.away, state.source.teams.home):
        try:
            return team.player(player_id).name
        except KeyError:
            pass
    return "-"
