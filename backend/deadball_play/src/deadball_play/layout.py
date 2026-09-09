"""Pure three-column laptop layout for Deadball Play."""

from __future__ import annotations

from dataclasses import dataclass
import textwrap
from typing import Iterable, Mapping

from deadball_core import GameState


MIN_COLUMNS = 120
MIN_ROWS = 44
CONTEXT_MODES = ("field", "narration", "lineups", "mlb_stats")


@dataclass
class DashboardView:
    """Non-mechanical presentation state for the third column."""

    context_mode: str = "field"
    narration_offset: int = 0

    def toggle(self) -> None:
        index = CONTEXT_MODES.index(self.context_mode)
        self.context_mode = CONTEXT_MODES[(index + 1) % len(CONTEXT_MODES)]
        self.narration_offset = 0

    def scroll_up(self, amount: int = 1) -> None:
        if self.context_mode == "narration":
            self.narration_offset += max(1, amount)

    def scroll_down(self, amount: int = 1) -> None:
        if self.context_mode == "narration":
            self.narration_offset = max(0, self.narration_offset - max(1, amount))


def column_widths(width: int) -> tuple[int, int, int]:
    """Return inner widths for state, options, and context columns."""
    if width < MIN_COLUMNS:
        raise ValueError(f"terminal must be at least {MIN_COLUMNS} columns wide")
    available = width - 4
    left = max(36, int(available * 0.30))
    middle = max(22, int(available * 0.22))
    return left, middle, available - left - middle


def compose_columns(
    left: Iterable[str],
    middle: Iterable[str],
    right: Iterable[str],
    *,
    width: int,
    height: int,
) -> str:
    """Compose three bordered panels into an exact terminal-sized frame."""
    if height < MIN_ROWS:
        raise ValueError(f"terminal must be at least {MIN_ROWS} rows high")
    widths = column_widths(width)
    panels = [
        _panel_lines(lines, panel_width)
        for lines, panel_width in zip((left, middle, right), widths)
    ]
    body_height = height - 2
    border = "+" + "+".join("-" * panel_width for panel_width in widths) + "+"
    rows = [border]
    for index in range(body_height):
        cells = [
            panel[index] if index < len(panel) else " " * panel_width
            for panel, panel_width in zip(panels, widths)
        ]
        rows.append("|" + "|".join(cells) + "|")
    rows.append(border)
    return "\n".join(rows)


def compose_dashboard(
    header: Iterable[str],
    outcome: Iterable[str],
    left: Iterable[str],
    middle: Iterable[str],
    right: Iterable[str],
    *,
    width: int,
    height: int,
) -> str:
    """Compose a scoreboard, full-width outcome, and three-column body."""
    if height < MIN_ROWS:
        raise ValueError(f"terminal must be at least {MIN_ROWS} rows high")
    widths = column_widths(width)
    header_height = 3
    outcome_height = 12
    body_height = height - header_height - outcome_height - 4
    border = "+" + "-" * (width - 2) + "+"
    column_border = "+" + "+".join("-" * item for item in widths) + "+"
    rows = [border]
    rows.extend(_full_width_rows(header, width - 2, header_height))
    rows.append(border)
    rows.extend(_centered_width_rows(outcome, width - 2, outcome_height))
    rows.append(column_border)
    panels = [
        _panel_lines(lines, panel_width)
        for lines, panel_width in zip((left, middle, right), widths)
    ]
    for index in range(body_height):
        cells = [
            panel[index] if index < len(panel) else " " * panel_width
            for panel, panel_width in zip(panels, widths)
        ]
        rows.append("|" + "|".join(cells) + "|")
    rows.append(border)
    return "\n".join(rows)


def compose_modal(lines: Iterable[str], *, width: int, height: int) -> str:
    """Center a bordered information box within an exact-sized screen."""
    content = list(lines)
    box_width = min(width - 4, max(64, *(len(line) + 4 for line in content)))
    box_height = len(content) + 2
    top = max(0, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)
    canvas = [list(" " * width) for _ in range(height)]
    border = "+" + "-" * (box_width - 2) + "+"
    for row, text in enumerate([border, *content, border]):
        rendered = border if row in {0, box_height - 1} else (
            "| " + text[: box_width - 4].ljust(box_width - 4) + " |"
        )
        canvas[top + row][left:left + box_width] = rendered
    return "\n".join("".join(row) for row in canvas)


def field_panel(state: GameState, width: int) -> list[str]:
    """Render every active defender and named runner on a roomy diamond."""
    defense_side = "home" if state.half == "top" else "away"
    offense_side = "away" if state.half == "top" else "home"
    defense_state = getattr(state, defense_side)
    offense_state = getattr(state, offense_side)
    defense_team = getattr(state.source.teams, defense_side)
    offense_team = getattr(state.source.teams, offense_side)
    batter_id = offense_state.lineup[offense_state.batting_order_index]
    batter = offense_team.player(batter_id)
    pitcher = defense_team.player(defense_state.active_pitcher_id or "")
    batting_side = batter.bats
    if batting_side == "S":
        batting_side = "L" if pitcher.throws == "R" else "R"
    content_width = max(1, width - 2)
    two_cell_limit = max(8, content_width // 2)
    three_cell_limit = max(8, content_width // 3)
    five_cell_limit = max(8, content_width // 5)
    defenders = {
        item.position: defense_team.player(item.player_id).name
        for item in defense_state.active_defense
    }
    runners = {}
    for base, player_id in zip(("1B", "2B", "3B"), state.bases):
        runners[base] = (
            offense_team.player(player_id).name
            if player_id is not None
            else ""
        )

    right_hand_batter = batter.name if batting_side == "R" else ""
    left_hand_batter = batter.name if batting_side == "L" else ""
    right_hand_label = "RH BATTER" if right_hand_batter else ""
    left_hand_label = "LH BATTER" if left_hand_batter else ""

    return [
        _ends("FIELD", "[Tab: Narration]", content_width),
        _center(f"DEFENSE: {defense_team.name}", content_width),
        "",
        "",
        _center(
            _short_name(defenders.get("CF", "-"), content_width),
            content_width,
        ),
        _columns(
            (
                _short_name(defenders.get("LF", "-"), three_cell_limit),
                "[CF]",
                _short_name(defenders.get("RF", "-"), three_cell_limit),
            ),
            content_width,
        ),
        _columns(("[LF]", "", "[RF]"), content_width),
        "",
        "",
        "",
        _center(
            _short_name(defenders.get("2B", "-"), content_width),
            content_width,
        ),
        _columns_shifted(
            (
                _short_name(defenders.get("SS", "-"), three_cell_limit),
                "[2B]",
                "",
            ),
            content_width,
            shifts=(9, 0, 0),
        ),
        _columns_shifted(
            ("[SS]", _runner_text(runners["2B"], three_cell_limit), ""),
            content_width,
            shifts=(9, 0, 0),
        ),
        "",
        "",
        "",
        _columns(
            (
                _short_name(defenders.get("3B", "-"), three_cell_limit),
                f"--{pitcher.throws}HP--",
                _short_name(defenders.get("1B", "-"), three_cell_limit),
            ),
            content_width,
        ),
        _columns(
            (
                "[3B]",
                _short_name(defenders.get("P", "-"), three_cell_limit),
                "[1B]",
            ),
            content_width,
        ),
        _columns(
            (
                _runner_text(runners["3B"], three_cell_limit),
                "",
                _runner_text(runners["1B"], three_cell_limit),
            ),
            content_width,
        ),
        "",
        "",
        _columns(
            ("", right_hand_label, "", left_hand_label, ""),
            content_width,
        ),
        _columns(
            (
                "",
                _short_name(right_hand_batter, five_cell_limit),
                "( )",
                _short_name(left_hand_batter, five_cell_limit),
                "",
            ),
            content_width,
        ),
        _center(_short_name(defenders.get("C", "-"), content_width), content_width),
        _center("[C]", content_width),
    ]


def narration_panel(
    narration: Iterable[str],
    *,
    width: int,
    height: int,
    offset: int,
) -> tuple[list[str], int]:
    """Return a bottom-following, vertically scrollable narration viewport."""
    header = ["NARRATION              [Tab: Lineups]", ""]
    lines = []
    for item in narration:
        lines.extend(textwrap.wrap(item, width=max(10, width - 2)) or [""])
        lines.append("")
    content_height = max(1, height - len(header))
    max_offset = max(0, len(lines) - content_height)
    safe_offset = min(max(0, offset), max_offset)
    end = len(lines) - safe_offset
    start = max(0, end - content_height)
    visible = lines[start:end]
    if safe_offset:
        marker = f"[scrolled up {safe_offset}; Down/PageDown returns]"
        if visible:
            visible[0] = marker[:width]
        else:
            visible = [marker[:width]]
    return [*header, *visible], max_offset


def lineups_panel(
    state: GameState,
    width: int,
    batting: Mapping[str, object] | None = None,
    pitching: Mapping[str, object] | None = None,
) -> list[str]:
    """Render both clubs' lineups, bench players, pitchers, and box stats."""
    content_width = max(1, width - 2)
    batting = batting or {}
    pitching = pitching or {}
    table_gap = 2
    table_width = max(22, (content_width - table_gap) // 2)
    teams = [
        (getattr(state, side), getattr(state.source.teams, side))
        for side in ("away", "home")
    ]
    batting_stats_head = (
        f"{'AB':>2} {'R':>1} {'H':>1} {'RBI':>3} {'BB':>2} {'K':>1}"
    )
    pitching_stats_head = f"{'IP':>4} {'H':>2} {'R':>1} {'BB':>2} {'K':>1}"
    batting_name_limit = max(
        3, table_width - 6 - 3 - len(batting_stats_head)
    )
    pitching_name_limit = max(
        4, table_width - 1 - 3 - len(pitching_stats_head)
    )
    batting_name_width = max(
        len(_short_name(team.player(player_id).name, batting_name_limit))
        for team_state, team in teams
        for player_id in team_state.lineup
    )
    pitching_name_width = max(
        len(_short_name(player.name, pitching_name_limit))
        for _, team in teams
        for player in team.roster
        if player.pitch_die
    )
    team_columns = []
    for team_state, team in teams:
        defense = {
            assignment.player_id: assignment.position
            for assignment in team_state.active_defense
        }
        column = [
            team.short_name.upper().center(table_width),
            "# POS PLAYER".ljust(6 + batting_name_width)
            + " " * 3
            + batting_stats_head,
        ]
        for index, player_id in enumerate(team_state.lineup):
            player = team.player(player_id)
            position = defense.get(player_id, player.positions[0])
            marker = ">" if index == team_state.batting_order_index else " "
            stats = batting.get(player_id)
            ab = getattr(stats, "at_bats", 0)
            hits = getattr(stats, "hits", 0)
            runs = getattr(stats, "runs", 0)
            rbi = getattr(stats, "rbi", 0)
            walks = getattr(stats, "walks", 0)
            strikeouts = getattr(stats, "strikeouts", 0)
            prefix = f"{marker}{index + 1} {position:<2} "
            stats_text = (
                f"{ab:>2} {runs:>1} {hits:>1} {rbi:>3} {walks:>2} {strikeouts:>1}"
            )
            name = _short_name(player.name, batting_name_limit)
            column.append(
                prefix
                + name.ljust(batting_name_width)
                + " " * 3
                + stats_text
            )
        lineup_ids = set(team_state.lineup)
        others = [
            player
            for player in team.roster
            if player.player_id not in lineup_ids and not player.pitch_die
        ]
        column.extend(("", "BENCH / REMOVED"))
        column.extend(
            " " * 6
            + _short_name(
                player.name + ("*" if player.appeared_in_source_game else ""),
                table_width - 6,
            )
            for player in others
        )
        pitchers = [player for player in team.roster if player.pitch_die]
        column.extend(
            (
                "",
                "PITCHERS".ljust(1 + pitching_name_width)
                + " " * 3
                + pitching_stats_head,
            )
        )
        for player in pitchers:
            stats = pitching.get(player.player_id)
            ip = getattr(stats, "innings_pitched", "0.0")
            stats_text = (
                f"{ip:>4} {getattr(stats, 'hits', 0):>2}"
                f" {getattr(stats, 'runs', 0):>1} {getattr(stats, 'walks', 0):>2}"
                f" {getattr(stats, 'strikeouts', 0):>1}"
            )
            marker = ">" if player.player_id == team_state.active_pitcher_id else " "
            name = _short_name(player.name, pitching_name_limit)
            column.append(
                marker
                + name.ljust(pitching_name_width)
                + " " * 3
                + stats_text
            )
        team_columns.append(column)
    lines = [_ends("BOX SCORE / LINEUPS", "[Tab: Field]", content_width), ""]
    for index in range(max(map(len, team_columns))):
        items = tuple(
            column[index] if index < len(column) else ""
            for column in team_columns
        )
        lines.append(_paired_columns(items, content_width, gap=table_gap))
    return lines


def mlb_stats_panel(state: GameState, width: int) -> list[str]:
    """Render generator-source season statistics for every rostered player."""
    content_width = max(1, width - 2)
    table_gap = 2
    table_width = max(22, (content_width - table_gap) // 2)
    team_columns = []
    for side in ("away", "home"):
        team = getattr(state.source.teams, side)
        lineup_ids = {entry.player_id for entry in team.lineup}
        hitters = [player for player in team.roster if not player.pitch_die]
        pitchers = [player for player in team.roster if player.pitch_die]
        hitter_header = "PLAYER   G  AVG  OBP HR 2B SB"
        pitcher_header = "PITCHER GS    IP  ERA  K/9 BB/9 GB%"
        hitter_name_width = max(6, table_width - len(hitter_header) + len("PLAYER"))
        pitcher_name_width = max(7, table_width - len(pitcher_header) + len("PITCHER"))
        column = [team.short_name.upper().center(table_width), "MLB HITTER STATS"]
        column.append("PLAYER".ljust(hitter_name_width) + "   G  AVG  OBP HR 2B SB")
        for player in hitters:
            stats = player.mlb_stats
            name = player.name + (
                "*"
                if player.player_id not in lineup_ids
                and player.appeared_in_source_game
                else ""
            )
            column.append(
                _short_name(name, hitter_name_width).ljust(hitter_name_width)
                + f" {_whole(stats.games if stats else None):>3}"
                + f" {_average(stats.average if stats else None):>4}"
                + f" {_average(stats.on_base_percentage if stats else None):>4}"
                + f" {_whole(stats.home_runs if stats else None):>2}"
                + f" {_whole(stats.doubles if stats else None):>2}"
                + f" {_whole(stats.stolen_bases if stats else None):>2}"
            )
        column.extend(("", "MLB PITCHER STATS"))
        column.append(
            "PITCHER".ljust(pitcher_name_width) + " GS    IP  ERA  K/9 BB/9 GB%"
        )
        for player in pitchers:
            stats = player.mlb_stats
            column.append(
                _short_name(player.name, pitcher_name_width).ljust(pitcher_name_width)
                + f" {_whole(stats.games_started if stats else None):>2}"
                + f" {_innings(stats.innings_pitched if stats else None):>5}"
                + f" {_decimal(stats.era if stats else None):>4}"
                + f" {_decimal(stats.strikeouts_per_nine if stats else None):>4}"
                + f" {_decimal(stats.walks_per_nine if stats else None):>4}"
                + f" {_percent(stats.ground_ball_percentage if stats else None):>3}"
            )
        team_columns.append(column)
    lines = [_ends("MLB STATS", "[Tab: Field]", content_width), ""]
    for index in range(max(map(len, team_columns))):
        items = tuple(
            column[index] if index < len(column) else "" for column in team_columns
        )
        lines.append(_paired_columns(items, content_width, gap=table_gap))
    lines.extend(("", "* Appeared as a substitute in the source MLB game"))
    return lines


def _whole(value: float | None) -> str:
    return "-" if value is None else str(int(round(value)))


def _average(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}".lstrip("0")


def _decimal(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _innings(value: float | None) -> str:
    if value is None:
        return "-"
    whole = int(value)
    outs = round((value - whole) * 3)
    if outs == 3:
        whole += 1
        outs = 0
    return f"{whole}.{outs}"


def _percent(value: float | None) -> str:
    if value is None:
        return "-"
    percent = value * 100 if abs(value) <= 1 else value
    return f"{percent:.0f}"


def _panel_lines(lines: Iterable[str], width: int) -> list[str]:
    result = []
    for line in lines:
        line = str(line)
        content_width = max(1, width - 2)
        wrapped = (
            [line]
            if len(line) <= content_width
            else textwrap.wrap(
                line,
                width=content_width,
                replace_whitespace=False,
                drop_whitespace=True,
            )
        ) or [""]
        result.extend(f" {item:<{width - 1}}"[:width] for item in wrapped)
    return result


def _full_width_rows(
    lines: Iterable[str], width: int, height: int
) -> list[str]:
    panel = _panel_lines(lines, width)
    return [
        "|" + (panel[index] if index < len(panel) else " " * width) + "|"
        for index in range(height)
    ]


def _centered_width_rows(
    lines: Iterable[str], width: int, height: int
) -> list[str]:
    content_width = max(1, width - 2)
    rendered = [
        textwrap.shorten(str(line), width=content_width, placeholder="…")
        if len(str(line)) > content_width
        else str(line)
        for line in lines
    ]
    return [
        "| "
        + (rendered[index] if index < len(rendered) else "").center(content_width)
        + " |"
        for index in range(height)
    ]


def _center(text: str, width: int) -> str:
    return text[:width].center(width)


def _ends(left: str, right: str, width: int) -> str:
    if len(left) + len(right) + 1 > width:
        return f"{left} {right}"[:width]
    return left + " " * (width - len(left) - len(right)) + right


def _columns(items: tuple[str, ...], width: int) -> str:
    """Center items in equal-width cells while retaining exact alignment."""
    count = len(items)
    base, remainder = divmod(width, count)
    cells = []
    for index, item in enumerate(items):
        cell_width = base + (1 if index < remainder else 0)
        cells.append(item[:cell_width].center(cell_width))
    return "".join(cells)


def _paired_columns(items: tuple[str, str], width: int, *, gap: int = 2) -> str:
    """Left-align two fixed tables with a stable gutter between them."""
    usable = max(2, width - gap)
    left_width = usable // 2
    right_width = usable - left_width
    return (
        items[0][:left_width].ljust(left_width)
        + " " * gap
        + items[1][:right_width].ljust(right_width)
    )


def _columns_shifted(
    items: tuple[str, ...], width: int, *, shifts: tuple[int, ...]
) -> str:
    """Place items in equal cells with explicit horizontal offsets."""
    count = len(items)
    base, remainder = divmod(width, count)
    row = [" "] * width
    cell_start = 0
    for index, item in enumerate(items):
        cell_width = base + (1 if index < remainder else 0)
        visible = item[:width]
        start = max(
            0,
            min(
                width - len(visible),
                cell_start + (cell_width - len(visible)) // 2 + shifts[index],
            ),
        )
        for offset, character in enumerate(visible):
            row[start + offset] = character
        cell_start += cell_width
    return "".join(row)


def _short_name(name: str, limit: int) -> str:
    if len(name) <= limit:
        return name
    parts = name.split()
    if len(parts) > 1:
        compact = f"{parts[0][0]}. {parts[-1]}"
        if len(compact) <= limit:
            return compact
    return name[: max(1, limit - 3)] + "..."


def _runner_text(name: str, limit: int) -> str:
    if not name:
        return ""
    return "Runner: " + _short_name(name, max(1, limit - 8))
