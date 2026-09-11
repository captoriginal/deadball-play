"""Durable postgame box-score and recap exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from deadball_core import PlayEvent, StealEvent

from .session import APPLICATION_VERSION, GameSession
from .summary import (
    build_batting_lines,
    build_game_box,
    build_pitching_lines,
    pitchers_of_record,
)


@dataclass(frozen=True)
class PostgameExports:
    box_score_path: Path
    recap_path: Path


def export_postgame(session: GameSession, archive_path: Path) -> PostgameExports:
    """Write a portable CSV box score and Markdown recap beside an archive."""
    if not session.state.is_final:
        raise ValueError("postgame exports require a completed game")
    stem = archive_path.name.removesuffix(".json")
    box_score_path = archive_path.with_name(f"{stem}.box-score.csv")
    recap_path = archive_path.with_name(f"{stem}.recap.md")
    box_score_path.parent.mkdir(parents=True, exist_ok=True)
    _write_box_score(session, box_score_path)
    recap_path.write_text(_recap_markdown(session), encoding="utf-8")
    return PostgameExports(box_score_path, recap_path)


def _write_box_score(session: GameSession, path: Path) -> None:
    state = session.state
    batting = build_batting_lines(session.history)
    pitching = build_pitching_lines(session.history)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["section", "team", "player", "PA", "AB", "R", "H", "RBI", "BB", "K", "IP"]
        )
        for team in (state.source.teams.away, state.source.teams.home):
            for player in team.roster:
                line = batting.get(player.player_id)
                if line is None:
                    continue
                writer.writerow(
                    [
                        "batting",
                        team.name,
                        player.name,
                        line.plate_appearances,
                        line.at_bats,
                        line.runs,
                        line.hits,
                        line.rbi,
                        line.walks,
                        line.strikeouts,
                        "",
                    ]
                )
            for player in team.roster:
                line = pitching.get(player.player_id)
                if line is None:
                    continue
                writer.writerow(
                    [
                        "pitching",
                        team.name,
                        player.name,
                        "",
                        "",
                        line.runs,
                        line.hits,
                        "",
                        line.walks,
                        line.strikeouts,
                        line.innings_pitched,
                    ]
                )


def _recap_markdown(session: GameSession) -> str:
    state = session.state
    away = state.source.teams.away
    home = state.source.teams.home
    box = build_game_box(state, session.history)
    winner, loser = pitchers_of_record(state, session.history)
    winning_team = away if state.away_score > state.home_score else home
    losing_team = home if winning_team is away else away
    innings = max(len(box.away.runs_by_inning), len(box.home.runs_by_inning))
    lines = [
        f"# {away.name} at {home.name}",
        "",
        f"**Final: {away.name} {state.away_score}, {home.name} {state.home_score}.**",
        "",
        (
            f"{winning_team.name} defeated {losing_team.name} in "
            f"{innings} inning{'s' if innings != 1 else ''}."
        ),
        "",
        f"Winning pitcher: {winner}  ",
        f"Losing pitcher: {loser}",
        "",
        "## Line score",
        "",
        "| Team | " + " | ".join(str(number) for number in range(1, innings + 1)) + " | R | H | E |",
        "|---|" + "---:|" * (innings + 3),
        _line_score_row(away.name, box.away.runs_by_inning, box.away.hits, box.away.errors, innings),
        _line_score_row(home.name, box.home.runs_by_inning, box.home.hits, box.home.errors, innings),
        "",
        "## Scoring plays",
        "",
    ]
    scoring = []
    for entry in session.history:
        event = entry.event
        if not isinstance(event, (PlayEvent, StealEvent)) or event.runs_scored == 0:
            continue
        side = "away" if entry.state_before.half == "top" else "home"
        team = away if side == "away" else home
        description = event.event_type.replace("_", " ").title()
        if isinstance(event, PlayEvent):
            try:
                batter = team.player(event.batter_id).name
                description = f"{batter}: {description}"
            except KeyError:
                pass
        scoring.append(
            f"- {entry.state_before.half.title()} {entry.state_before.inning}: "
            f"{description} ({event.runs_scored} run"
            f"{'s' if event.runs_scored != 1 else ''})."
        )
    lines.extend(scoring or ["- No scoring plays were recorded."])
    lines.extend(("", f"Generated by Deadball Play {APPLICATION_VERSION}.", ""))
    return "\n".join(lines)


def _line_score_row(team: str, runs, hits: int, errors: int, innings: int) -> str:
    cells = ["-" if value is None else str(value) for value in runs]
    cells.extend("-" for _ in range(innings - len(cells)))
    total = sum(value for value in runs if value is not None)
    safe_team = team.replace("|", "\\|")
    return f"| {safe_team} | " + " | ".join(cells) + f" | {total} | {hits} | {errors} |"
