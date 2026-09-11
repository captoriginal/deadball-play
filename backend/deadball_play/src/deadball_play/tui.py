"""Keyboard-first terminal conductor built on structured core APIs."""

from __future__ import annotations

import argparse
from dataclasses import fields, replace
from datetime import datetime
import json
import random
from pathlib import Path
import sys
import textwrap
from typing import Callable, TextIO

from deadball_core import (
    ActionResult,
    GameState,
    RandomDice,
    decide_opportunity,
    defensive_substitution,
    initialize_game,
    legal_actions,
    load_generated_game,
    offensive_opportunity,
    pinch_hit,
    pinch_run,
    pitching_change,
    pitcher_may_be_replaced,
    pitching_opportunity,
    resolve_bunt,
    resolve_hit_and_run,
    resolve_steal,
    resolve_swing,
    select_replacement_pitcher,
    switch_defensive_positions,
)

from .narration import NarrationResult, Narrator
from .exports import export_postgame
from .demo import load_demo_game
from .layout import (
    DashboardView,
    column_widths,
    compose_dashboard,
    field_panel,
    lineups_panel,
    mlb_stats_panel,
    narration_panel,
)
from .session import GameSession, HistoryEntry, SessionConfig, SessionError
from .summary import (
    TeamBox,
    build_batting_lines,
    build_game_box,
    build_pitching_lines,
    pitchers_of_record,
)
from .web_cache import load_cached_game


InputFunction = Callable[[str], str]

ACTION_LABELS = {
    "swing": "Swing",
    "bunt": "Bunt",
    "hit_and_run": "Hit & Run",
    "steal_second": "Steal second",
    "steal_third": "Steal third",
    "double_steal": "Double steal",
    "steal_home": "Steal home",
}


class TerminalApp:
    """Synchronous terminal UI with injectable input and output."""

    def __init__(
        self,
        session: GameSession,
        *,
        narrator: Narrator | None = None,
        input_func: InputFunction = input,
        output: TextIO = sys.stdout,
        clear_screen: bool = False,
        played_games_dir: str | Path = "played-games",
    ) -> None:
        self.session = session
        self.narrator = narrator or Narrator()
        self.input = input_func
        self.output = output
        self.clear_screen = clear_screen
        self.played_games_dir = Path(played_games_dir)
        self._narrations: dict[int, NarrationResult] = {}
        self._notice: str | None = None
        self._intro_pending = not session.history

    def run(self) -> int:
        """Run until the game ends or the user saves and quits."""
        if self._intro_pending:
            try:
                self._show(self.intro_screen())
                self.input("Press Enter to begin. ")
            except (EOFError, KeyboardInterrupt):
                self._write("\nInput ended. The current autosave has been preserved.")
                return 130
            self._intro_pending = False
        while True:
            try:
                if self.session.pending_event is not None:
                    self._show(self.pending_screen())
                    command = self.input(
                        "Enter=recorded  [?] Rule  [Y] History  [U] Undo  "
                        f"[K] {self._save_label()}  [Q] {self._quit_label()}: "
                    ).strip().upper()
                    if command == "":
                        self.session.confirm_scorekeeping()
                        self._archive_completed_game()
                    elif command == "?":
                        self._pause(self.rule_screen())
                    elif command == "Y":
                        self._pause(self.history_screen())
                    elif command == "U":
                        self._undo()
                    elif command == "K":
                        self._save()
                    elif command == "Q" and self._quit():
                        return 0
                    else:
                        self._notice = (
                            "Unknown command; the play is still awaiting confirmation."
                        )
                    continue

                if self.session.state.is_final:
                    self._archive_completed_game()
                    self._show("\n".join(self.final_summary_lines()))
                    return 0

                self._show(self.game_screen())
                prompt = (
                    "Enter/S=continue computer; choose a defensive option: "
                    if self._offense_is_computer()
                    else "Choose an action: "
                )
                command = self.input(prompt).strip().upper()
                if self._offense_is_computer() and command in {"", "S"}:
                    self._perform_computer_action()
                    continue
                if not self._offense_is_computer() and command == "":
                    command = "S"
                if self._handle_ready_command(command):
                    return 0
            except (EOFError, KeyboardInterrupt):
                self._write("\nInput ended. The current autosave has been preserved.")
                return 130
            except (SessionError, ValueError, OSError) as exc:
                self._notice = f"Could not complete that action: {exc}"

    def pending_screen(self) -> str:
        entry = self.session.history[-1]
        narration = self._narration_for(len(self.session.history) - 1)
        sections = [self.game_screen()]
        if self._notice:
            sections.append(self._consume_notice())
        sections.extend(("PLAY", render_dice(entry), _wrap(narration.play_text)))
        if narration.transition_text:
            sections.append(_wrap(narration.transition_text))
        if narration.scoring_guidance:
            sections.append("\n".join(narration.scoring_guidance))
        sections.append("Press Enter when scored.")
        return "\n\n".join(section for section in sections if section)

    def intro_screen(self) -> str:
        state = self.session.state
        away = state.source.teams.away.name
        home = state.source.teams.home.name
        return (
            "DEADBALL PLAY\n\n"
            f"Welcome to {away} at {home}.\n"
            "The clubs are set, the field is ready, and the first pitch awaits.\n\n"
            "Press Enter to begin."
        )

    def game_screen(self) -> str:
        screen = render_game_screen(self._presentation_state(), self.session)
        if not self.session.history:
            return screen
        lines = ["RECENT PLAYS"]
        start = max(0, len(self.session.history) - 3)
        for index in range(start, len(self.session.history)):
            entry = self.session.history[index]
            prefix = f"{_half_label(entry.state_before)}: "
            lines.append(
                textwrap.fill(
                    self._narration_for(index).play_text,
                    width=88,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            )
        return screen + "\n\n" + "\n".join(lines)

    def rule_screen(self) -> str:
        if not self.session.history:
            return "RULE EXPLANATION\n\nNo ruling has been made yet."
        entry = self.session.history[-1]
        lines = ["RULE EXPLANATION", render_dice(entry)]
        lines.extend(
            f"{trace.stage}: {trace.detail}\n  {trace.rule_reference}"
            for trace in entry.rule_trace
        )
        return "\n\n".join(line for line in lines if line)

    def history_screen(self) -> str:
        if not self.session.history:
            return "HISTORY\n\nNo plays yet."
        lines = ["HISTORY"]
        for index, entry in enumerate(self.session.history):
            before = entry.state_before
            narration = self._narration_for(index)
            marker = "" if entry.scorekeeping_confirmed else " (awaiting scorecard)"
            prefix = f"{entry.sequence:>3}. {_half_label(before)} "
            lines.append(
                textwrap.fill(
                    narration.play_text + marker,
                    width=88,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
            )
        return "\n".join(lines)

    def lineup_screen(self) -> str:
        return "\n\n".join(
            render_lineup(self.session.state, side) for side in ("away", "home")
        )

    def bullpen_screen(self) -> str:
        return "\n\n".join(
            render_bullpen(self.session.state, side) for side in ("away", "home")
        )

    def _handle_ready_command(self, command: str) -> bool:
        actions = set(legal_actions(self.session.state))
        has_steal = any(
            action.startswith("steal") or action == "double_steal"
            for action in actions
        )
        if command == "S" and "swing" in actions:
            self._perform_play_action("swing")
        elif command == "B" and "bunt" in actions:
            self._perform_play_action("bunt")
        elif command == "H" and "hit_and_run" in actions:
            self._perform_play_action("hit_and_run")
        elif command == "T" and has_steal:
            self._steal_flow(actions)
        elif command == "P" and self._offense_team_state().bench:
            self._pinch_hit_flow()
        elif (
            command == "R"
            and self._offense_team_state().bench
            and any(self.session.state.bases)
        ):
            self._pinch_run_flow()
        elif (
            command == "M"
            and self._defense_team_state().bullpen
            and pitcher_may_be_replaced(
                self.session.state, _defense_side(self.session.state)
            )
        ):
            self._pitching_change_flow()
        elif command == "D" and self._defense_team_state().bench:
            self._defensive_substitution_flow()
        elif command == "X":
            self._position_switch_flow()
        elif command == "L":
            self._pause(self.lineup_screen())
        elif command == "V":
            self._pause(self.bullpen_screen())
        elif command == "Y":
            self._pause(self.history_screen())
        elif command == "?":
            self._pause(self.rule_screen())
        elif command == "U":
            self._undo()
        elif command == "K":
            self._save()
        elif command == "Q":
            return self._quit()
        else:
            self._notice = "Unknown or unavailable command; game state was not changed."
        return False

    def _steal_flow(self, actions: set[str]) -> None:
        steals = [
            action
            for action in (
                "steal_second",
                "steal_third",
                "double_steal",
                "steal_home",
            )
            if action in actions
        ]
        selected = self._choose_value(
            "STEAL", steals, lambda item: ACTION_LABELS[item]
        )
        if selected and self._confirm(ACTION_LABELS[selected] + "?"):
            self._perform_play_action(selected)

    def _pinch_hit_flow(self) -> None:
        team_state, team_data = self._offense_team()
        selected = self._choose_player(
            "AVAILABLE PINCH HITTERS", team_state.bench, team_data
        )
        if selected is None:
            return
        batter = team_data.player(team_state.lineup[team_state.batting_order_index])
        incoming = team_data.player(selected)
        if self._confirm(
            f"{incoming.name} will pinch hit for {batter.name}. Continue?"
        ):
            self.session.perform(lambda state, dice: pinch_hit(state, selected))

    def _pinch_run_flow(self) -> None:
        team_state, team_data = self._offense_team()
        occupied = [
            base
            for base, runner in zip(
                ("1B", "2B", "3B"), self.session.state.bases
            )
            if runner is not None
        ]
        base = self._choose_value("SELECT RUNNER", occupied, lambda item: item)
        if base is None:
            return
        selected = self._choose_player(
            "AVAILABLE PINCH RUNNERS", team_state.bench, team_data
        )
        if selected is None:
            return
        runner_id = self.session.state.bases[("1B", "2B", "3B").index(base)]
        assert runner_id is not None
        if self._confirm(
            f"{team_data.player(selected).name} will run for "
            f"{team_data.player(runner_id).name} at {base}. Continue?"
        ):
            self.session.perform(lambda state, dice: pinch_run(state, base, selected))

    def _pitching_change_flow(self) -> None:
        side = _defense_side(self.session.state)
        team_state, team_data = _team(self.session.state, side)
        selected = self._choose_player(
            "AVAILABLE RELIEVERS", team_state.bullpen, team_data
        )
        if selected is None:
            return
        if self._confirm(f"Bring in {team_data.player(selected).name}?"):
            self.session.perform(
                lambda state, dice: pitching_change(state, side, selected)
            )

    def _defensive_substitution_flow(self) -> None:
        side = _defense_side(self.session.state)
        team_state, team_data = _team(self.session.state, side)
        positions = [
            item.position
            for item in team_state.active_defense
            if item.position != "P"
        ]
        position = self._choose_value(
            "DEFENSIVE POSITION", positions, lambda item: item
        )
        if position is None:
            return
        selected = self._choose_player(
            "AVAILABLE DEFENDERS", team_state.bench, team_data
        )
        if selected is None:
            return
        if self._confirm(f"Put {team_data.player(selected).name} at {position}?"):
            self.session.perform(
                lambda state, dice: defensive_substitution(
                    state, side, position, selected
                )
            )

    def _position_switch_flow(self) -> None:
        side = _defense_side(self.session.state)
        team_state, _ = _team(self.session.state, side)
        positions = [
            item.position
            for item in team_state.active_defense
            if item.position != "P"
        ]
        first = self._choose_value("FIRST POSITION", positions, lambda item: item)
        if first is None:
            return
        second = self._choose_value(
            "SECOND POSITION",
            [position for position in positions if position != first],
            lambda item: item,
        )
        if second and self._confirm(f"Switch {first} and {second}?"):
            self.session.perform(
                lambda state, dice: switch_defensive_positions(
                    state, side, first, second
                )
            )

    def _perform_computer_action(self) -> None:
        side = _offense_side(self.session.state)
        daring = (
            self.session.config.away_daring
            if side == "away"
            else self.session.config.home_daring
        )
        assert daring is not None
        offense_decisions = []
        pitching_decisions = []
        injury_notices = []

        def choose_and_resolve(state: GameState, dice: RandomDice) -> ActionResult:
            injury_result = self._computer_injury_result(state, injury_notices)
            if injury_result is not None:
                return injury_result
            pitching_result = self._computer_pitching_result(
                state, dice, pitching_decisions
            )
            if pitching_result is not None:
                return pitching_result
            opportunity = offensive_opportunity(state)
            if opportunity is None:
                return resolve_swing(state, dice)
            decision = decide_opportunity(opportunity, daring, dice)
            offense_decisions.append(decision)
            return _resolve_action(state, decision.selected_choice, dice)

        self.session.perform(choose_and_resolve)
        team_data = _team(self.session.state, side)[1]
        notices = self._pitching_notices(pitching_decisions)
        notices[:0] = injury_notices
        if offense_decisions:
            decision = offense_decisions[0]
            notices.append(
                f"{team_data.short_name} manager: Daring {decision.daring}, "
                f"d20 {decision.roll}; {ACTION_LABELS[decision.selected_choice]}."
            )
        elif not pitching_decisions:
            notices.append(f"{team_data.short_name} manager chooses Swing.")
        self._notice = " ".join(notices)

    def _perform_play_action(self, action: str) -> None:
        pitching_decisions = []
        injury_notices = []

        def resolve_with_manager(state: GameState, dice: RandomDice) -> ActionResult:
            injury_result = self._computer_injury_result(state, injury_notices)
            if injury_result is not None:
                return injury_result
            pitching_result = self._computer_pitching_result(
                state, dice, pitching_decisions
            )
            if pitching_result is not None:
                return pitching_result
            return _resolve_action(state, action, dice)

        self.session.perform(resolve_with_manager)
        notices = self._pitching_notices(pitching_decisions)
        notices[:0] = injury_notices
        if notices:
            self._notice = " ".join(notices)

    def _computer_injury_result(self, state, notices):
        injured = {
            injury.player_id
            for injury in state.oddity_state.injuries
            if injury.must_leave
        }
        offense_side = _offense_side(state)
        offense, offense_data = _team(state, offense_side)
        if self._team_control(offense_side) == "computer" and offense.bench:
            batter_id = offense.lineup[offense.batting_order_index]
            if batter_id in injured:
                replacement = offense.bench[0]
                notices.append(
                    f"{offense_data.short_name} replaces injured "
                    f"{offense_data.player(batter_id).name} with "
                    f"{offense_data.player(replacement).name}."
                )
                return pinch_hit(state, replacement)
            for base, runner_id in zip(("1B", "2B", "3B"), state.bases):
                if runner_id in injured:
                    replacement = offense.bench[0]
                    notices.append(
                        f"{offense_data.short_name} replaces injured "
                        f"{offense_data.player(runner_id).name} with "
                        f"{offense_data.player(replacement).name}."
                    )
                    return pinch_run(state, base, replacement)

        defense_side = _defense_side(state)
        defense, defense_data = _team(state, defense_side)
        if self._team_control(defense_side) != "computer":
            return None
        if defense.active_pitcher_id in injured and defense.bullpen:
            replacement = select_replacement_pitcher(state, defense_side)
            if replacement is not None:
                notices.append(
                    f"{defense_data.short_name} replaces injured pitcher "
                    f"{defense_data.player(defense.active_pitcher_id).name} with "
                    f"{defense_data.player(replacement).name}."
                )
                return pitching_change(state, defense_side, replacement)
        if defense.bench:
            for assignment in defense.active_defense:
                if assignment.position != "P" and assignment.player_id in injured:
                    replacement = defense.bench[0]
                    notices.append(
                        f"{defense_data.short_name} replaces injured "
                        f"{defense_data.player(assignment.player_id).name} with "
                        f"{defense_data.player(replacement).name}."
                    )
                    return defensive_substitution(
                        state, defense_side, assignment.position, replacement
                    )
        return None

    def _computer_pitching_result(self, state, dice, decisions):
        side = _defense_side(state)
        if self._team_control(side) != "computer":
            return None
        opportunity = pitching_opportunity(
            state,
            side,
            at_inning_boundary=self._at_inning_boundary(),
        )
        if opportunity is None:
            return None
        daring = (
            self.session.config.away_daring
            if side == "away"
            else self.session.config.home_daring
        )
        assert daring is not None
        decision = decide_opportunity(opportunity, daring, dice)
        decisions.append((side, decision))
        if decision.selected_choice != "change_pitcher":
            return None
        replacement = select_replacement_pitcher(state, side)
        if replacement is None:
            return None
        return pitching_change(state, side, replacement)

    def _pitching_notices(self, decisions):
        notices = []
        for side, decision in decisions:
            team_data = _team(self.session.state, side)[1]
            choice = decision.selected_choice.replace("_", " ").capitalize()
            notices.append(
                f"{team_data.short_name} pitching decision: Daring "
                f"{decision.daring}, d20 {decision.roll}; {choice}."
            )
        return notices

    def _at_inning_boundary(self) -> bool:
        if not self.session.history:
            return False
        before = self.session.history[-1].state_before
        after = self.session.state
        return (before.inning, before.half) != (after.inning, after.half)

    def _team_control(self, side: str) -> str:
        return (
            self.session.config.away_control
            if side == "away"
            else self.session.config.home_control
        )

    def _offense_is_computer(self) -> bool:
        side = _offense_side(self.session.state)
        return self._team_control(side) == "computer"

    def _offense_team_state(self):
        return self._offense_team()[0]

    def _defense_team_state(self):
        return _team(self.session.state, _defense_side(self.session.state))[0]

    def _offense_team(self):
        return _team(self.session.state, _offense_side(self.session.state))

    def _choose_player(self, title, player_ids, team_data):
        return self._choose_value(
            title,
            list(player_ids),
            lambda player_id: _player_summary(team_data.player(player_id)),
        )

    def _choose_value(self, title, values, label):
        if not values:
            self._notice = f"{title.title()}: none available."
            return None
        self._write(title)
        for index, value in enumerate(values, start=1):
            self._write(f"{index}. {label(value)}")
        raw = self.input("Select number (Enter cancels): ").strip()
        if not raw:
            return None
        try:
            selected = int(raw)
        except ValueError:
            self._notice = "Selection must be a number."
            return None
        if not 1 <= selected <= len(values):
            self._notice = "Selection is outside the available range."
            return None
        return values[selected - 1]

    def _confirm(self, prompt: str) -> bool:
        response = self.input(f"{prompt} [Y/n] ").strip().upper()
        return response in {"", "Y", "YES"}

    def _undo(self) -> None:
        if not self.session.history:
            self._notice = "There is no action to undo."
            return
        entry = self.session.history[-1]
        narration = self._narration_for(len(self.session.history) - 1)
        if self._confirm(f"Undo: {narration.play_text}"):
            self.session.undo()
            self._narrations.pop(entry.sequence, None)
            self._notice = "Last action undone; game state and dice were restored."

    def _save(self) -> bool:
        if self.session.autosave_path is None:
            name = self.input("Save as path (Enter cancels): ").strip()
            if not name:
                self._notice = "Save cancelled."
                return False
            path = self.session.save(Path(name).expanduser())
            self._notice = f"Autosave enabled at {path}."
        else:
            name = self.input("Save a copy path (Enter cancels): ").strip()
            if not name:
                self._notice = "Save copy cancelled."
                return False
            path = self.session.save_copy(Path(name).expanduser())
            self._notice = f"Saved a copy to {path}."
        return True

    def _save_label(self) -> str:
        return "Save a copy" if self.session.autosave_path is not None else "Save as"

    def _quit_label(self) -> str:
        return "Quit" if self.session.is_saved else "Save & quit"

    def _quit(self) -> bool:
        if not self.session.is_saved:
            if self.session.autosave_path is None:
                name = self.input("Save as path before quitting (Enter cancels): ").strip()
                if not name:
                    self._notice = "Quit cancelled; the game is not saved."
                    return False
                self.session.save(Path(name).expanduser())
            else:
                self.session.save()
        if self.session.state.is_final:
            return True
        assert self.session.autosave_path is not None
        return self._confirm(
            f"Quit? Your unfinished game is protected at {self.session.autosave_path}."
        )

    def _narration_for(self, index: int) -> NarrationResult:
        entry = self.session.history[index]
        cached = self._narrations.get(entry.sequence)
        if cached is not None:
            return cached
        after = (
            self.session.history[index + 1].state_before
            if index + 1 < len(self.session.history)
            else self.session.state
        )
        result = self.narrator.render(entry.event, entry.state_before, after)
        self._narrations[entry.sequence] = result
        return result

    def dashboard_screen(
        self,
        view: DashboardView,
        *,
        width: int,
        height: int,
    ) -> str:
        """Render the full-screen three-column laptop dashboard."""
        _, _, right_width = column_widths(width)
        state = self._presentation_state()
        history = self._confirmed_history()
        left = self._dashboard_state_lines()
        middle = self._dashboard_option_lines(view)
        if view.context_mode == "field":
            right = field_panel(state, right_width)
        elif view.context_mode == "narration":
            right, maximum = narration_panel(
                self._narration_log(),
                width=right_width,
                height=height - 19,
                offset=view.narration_offset,
            )
            view.narration_offset = min(view.narration_offset, maximum)
        elif view.context_mode == "lineups":
            right = lineups_panel(
                state,
                right_width,
                build_batting_lines(history),
                build_pitching_lines(history),
            )
        else:
            right = mlb_stats_panel(state, right_width)
        return compose_dashboard(
            self._scoreboard_lines(width),
            self._dashboard_outcome_lines(width),
            left,
            middle,
            right,
            width=width,
            height=height,
        )

    def _scoreboard_lines(self, width: int) -> list[str]:
        state = self._presentation_state()
        box = build_game_box(state, self._confirmed_history())
        inning_count = min(max(9, state.inning), 12)
        first, second, third = (
            "◆" if runner else "◇" for runner in state.bases
        )
        outs = " ".join(
            "●" if index < state.outs else "○" for index in range(3)
        )
        content_width = width - 4
        name_width = max(
            len(state.source.teams.away.name),
            len(state.source.teams.home.name),
        )
        headings = " " * (name_width + 1) + "".join(
            f"{inning:>3}" for inning in range(1, inning_count + 1)
        )
        headings += " |  R  H  E"
        away_line = self._scoreboard_team_line(
            state.source.teams.away.name,
            box.away,
            inning_count,
            name_width=name_width,
        )
        home_line = self._scoreboard_team_line(
            state.source.teams.home.name,
            box.home,
            inning_count,
            name_width=name_width,
        )
        top_arrow = "■" if state.is_final else "▲" if state.half == "top" else " "
        bottom_arrow = "▼" if not state.is_final and state.half == "bottom" else " "
        inning_stack = (
            f"{top_arrow:^3}",
            f"{state.inning:^3}",
            f"{bottom_arrow:^3}",
        )
        left_rows = (
            f"{inning_stack[0]}  B: ○ ○ ○ ○",
            f"{inning_stack[1]}  S: ○ ○ ○",
            f"{inning_stack[2]}  O: {outs}",
        )
        center_rows = (second, f"{third}       {first}", "")
        right_rows = (headings, away_line, home_line)
        return [
            _header_cluster(
                left_rows[index],
                center_rows[index],
                right_rows[index],
                content_width,
            )
            for index in range(3)
        ]

    @staticmethod
    def _scoreboard_team_line(
        name: str,
        box: TeamBox,
        innings: int,
        *,
        name_width: int = 5,
    ) -> str:
        values = [
            str(box.runs_by_inning[index])
            if index < len(box.runs_by_inning)
            and box.runs_by_inning[index] is not None
            else "-"
            for index in range(innings)
        ]
        return (
            f"{name:<{name_width}} "
            + "".join(f"{value:>3}" for value in values)
            + f" | {box.runs:>2} {box.hits:>2} {box.errors:>2}"
        )

    def _dashboard_outcome_lines(self, width: int) -> list[str]:
        if self.session.pending_event is None:
            lines = [""]
            if self._notice:
                lines.extend((f"MANAGER NOTICE: {self._notice}", ""))
            lines.append(
                "Computer offense is paused for your defensive decision. "
                "Press Enter or S to continue."
                if self._offense_is_computer() and not self.session.state.is_final
                else "Waiting for the next play."
            )
            return lines
        index = len(self.session.history) - 1
        entry = self.session.history[index]
        narration = self._narration_for(index)
        lines = [
            _half_label(entry.state_before).upper(),
            "",
        ]
        if self._notice:
            lines[0] += f" | MANAGER NOTICE: {self._notice}"
        lines.extend((render_dice(entry).replace("\n", " "), ""))
        prose = narration.play_text
        if narration.transition_text:
            prose += " " + narration.transition_text
        lines.extend(_narration_box(prose, width=max(40, width - 8)))
        lines.append("")
        lines.append(
            " | ".join(
                line
                for line in narration.scoring_guidance
                if not line.startswith("Scoreboard:")
            )
        )
        lines.append("Press Enter when scored.")
        return lines

    def _dashboard_state_lines(self) -> list[str]:
        state = self._presentation_state()
        away = state.source.teams.away
        home = state.source.teams.home
        status = (
            "FINAL"
            if state.is_final
            else f"{state.half.upper()} {_ordinal(state.inning)}"
        )
        lines = [
            "CURRENT STATE",
            "",
            f"{away.short_name} {state.away_score}   {status}   "
            f"{home.short_name} {state.home_score}",
            f"Outs: {state.outs}",
            f"Runners: {_bases_text(state)}",
            self.session.autosave_status,
        ]
        if not state.is_final:
            offense_state, offense_data = _team(state, _offense_side(state))
            defense_state, defense_data = _team(state, _defense_side(state))
            batter = offense_data.player(
                offense_state.lineup[offense_state.batting_order_index]
            )
            pitcher = defense_data.player(defense_state.active_pitcher_id or "")
            position = _active_position(
                offense_state, batter.player_id, batter.positions
            )
            lines.extend(
                (
                    "",
                    "BATTER",
                    f"{batter.name} - {position} - {batter.bats}",
                    f"BT {batter.bt}   OBT {batter.obt}{_traits(batter.traits)}",
                    "",
                    "PITCHER",
                    f"{pitcher.name} - {pitcher.throws}HP",
                    f"Pitch Die {defense_state.active_pitch_die}"
                    f"{_traits(pitcher.traits)}",
                )
            )
        if state.is_final:
            lines.extend(("", "Game complete. Press Q to exit."))
        elif self.session.pending_event is None and not self._offense_is_computer():
            lines.extend(("", "What do you want to do?"))
        return lines

    def _dashboard_option_lines(self, view: DashboardView) -> list[str]:
        if self.session.pending_event is not None:
            options = [
                "[Enter] Scored",
                "[?] Rule",
                "[Y] Detailed history",
                "[U] Undo",
                f"[K] {self._save_label()}",
                f"[Q] {self._quit_label()}",
            ]
        elif self.session.state.is_final:
            options = [
                "[Y] Detailed history",
                f"[K] {self._save_label()}",
                "[Q] Exit",
            ]
        elif self._offense_is_computer():
            options = ["[Enter/S] Continue computer"]
            defense = self._defense_team_state()
            if defense.bullpen:
                options.append("[M] Mound change")
            if defense.bench:
                options.append("[D] Defensive sub")
            options.extend(
                (
                    "[X] Position switch",
                    "[L] Lineups",
                    "[V] Pitchers",
                    "[Y] History",
                    "[?] Rule",
                    f"[K] {self._save_label()}",
                    f"[Q] {self._quit_label()}",
                )
            )
        else:
            options = _command_options(self.session.state, self.session)
        options.extend(("", "[Tab] Field / narration / lineups / MLB stats"))
        if view.context_mode == "narration":
            options.extend(("[Up/Down] Scroll", "[PgUp/PgDn] Page"))
        return ["CURRENT OPTIONS", "", *options]

    def final_summary_lines(self) -> list[str]:
        state = self.session.state
        box = build_game_box(state, self.session.history)
        winner, loser = pitchers_of_record(state, self.session.history)
        innings = max(len(box.away.runs_by_inning), len(box.home.runs_by_inning))
        headings = "      " + "".join(f"{item:>3}" for item in range(1, innings + 1))
        headings += " |  R  H  E"
        return [
            "FINAL BOX SCORE",
            "",
            headings,
            self._scoreboard_team_line(state.source.teams.away.short_name, box.away, innings),
            self._scoreboard_team_line(state.source.teams.home.short_name, box.home, innings),
            "",
            f"Winning pitcher: {winner}",
            f"Losing pitcher:  {loser}",
            "",
            f"[Y] Detailed history   [K] {self._save_label()}   [Q] Exit",
        ]

    def _archive_completed_game(self) -> Path | None:
        if not self.session.state.is_final or self.session.pending_event is not None:
            return None
        if getattr(self, "_played_game_path", None) is not None:
            return self._played_game_path
        state = self.session.state
        game = state.source.game
        away = _filename_part(state.source.teams.away.name)
        home = _filename_part(state.source.teams.home.name)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.played_games_dir / (
            f"{game.game_date}-{away}-at-{home}-{stamp}.json"
        )
        archive = (
            self.session.save(path)
            if self.session.autosave_path is None
            else self.session.save_copy(path)
        )
        exports = export_postgame(self.session, archive)
        self._played_game_path = archive
        self._postgame_exports = exports
        self._notice = (
            f"Final game saved to {archive}; box score: {exports.box_score_path}; "
            f"recap: {exports.recap_path}."
        )
        return archive

    def _narration_log(self) -> list[str]:
        if not self.session.history:
            return ["No plays yet."]
        lines = []
        for index, entry in enumerate(self.session.history):
            marker = "" if entry.scorekeeping_confirmed else " [scorecard pending]"
            lines.append(
                f"{entry.sequence}. {_half_label(entry.state_before)}: "
                f"{self._narration_for(index).play_text}{marker}"
            )
        return lines

    def _presentation_state(self) -> GameState:
        if self.session.pending_event is not None:
            return self.session.history[-1].state_before
        return self.session.state

    def _confirmed_history(self) -> tuple[HistoryEntry, ...]:
        if self.session.pending_event is not None:
            return self.session.history[:-1]
        return self.session.history

    def _show(self, text: str) -> None:
        if self.clear_screen:
            self.output.write("\033[2J\033[H")
        if self._notice and self.session.pending_event is None:
            text = f"{text}\n\n{self._consume_notice()}"
        self._write(text)

    def _pause(self, text: str) -> None:
        self._write(text)
        self.input("Press Enter to return. ")

    def _consume_notice(self) -> str:
        notice = self._notice or ""
        self._notice = None
        return _wrap(notice)

    def _write(self, text: str) -> None:
        self.output.write(text.rstrip() + "\n")
        self.output.flush()


def render_game_screen(
    state: GameState, session: GameSession | None = None
) -> str:
    """Render the always-visible game information and available commands."""
    away = state.source.teams.away
    home = state.source.teams.home
    status = (
        "FINAL" if state.is_final else f"{state.half.upper()} {_ordinal(state.inning)}"
    )
    lines = [
        "=" * 60,
        (
            f"{away.short_name} {state.away_score:<3} "
            f"{status:^34} {home.short_name} {state.home_score}"
        ),
        f"Outs: {state.outs}    Runners: {_bases_text(state)}",
    ]
    if session is not None:
        lines.append(session.autosave_status)
    if not state.is_final:
        offense_state, offense_data = _team(state, _offense_side(state))
        defense_state, defense_data = _team(state, _defense_side(state))
        batter = offense_data.player(
            offense_state.lineup[offense_state.batting_order_index]
        )
        pitcher = defense_data.player(defense_state.active_pitcher_id or "")
        position = _active_position(
            offense_state, batter.player_id, batter.positions
        )
        lines.extend(
            (
                "",
                f"Batter:  {batter.name} - {position} - {batter.bats}",
                (
                    f"         BT {batter.bt}   OBT {batter.obt}"
                    f"{_traits(batter.traits)}"
                ),
                f"Pitcher: {pitcher.name} - {pitcher.throws}HP",
                (
                    f"         Pitch Die {defense_state.active_pitch_die}"
                    f"{_traits(pitcher.traits)}"
                ),
                "",
                _command_menu(state, session),
            )
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def render_dice(entry: HistoryEntry) -> str:
    if entry.dice is None:
        return "Dice: none"
    labels = {
        "swing_score": "d100",
        "pitch_die_roll": (
            entry.dice.pitch_die if hasattr(entry.dice, "pitch_die") else "Pitch"
        ),
        "mss": "MSS",
        "roll": "Roll",
        "modified_roll": "Modified",
        "hit_table_roll": "Hit d20",
        "modified_hit_table_roll": "Modified hit",
        "defense_roll": "DEF d12",
        "modified_defense_roll": "Modified DEF",
        "target_bonus": "Target bonus",
        "adjusted_bt": "Adjusted BT",
        "adjusted_obt": "Adjusted OBT",
        "oddity_total": "Oddity 2d10",
    }
    hidden = {
        "pitch_die",
        "signed_pitch_value",
        "runner_modifier",
        "base_modifier",
        "catcher_modifier",
        "action",
        "steal",
        "swing",
        "oddity_rolls",
        "oddity_detail_rolls",
    }
    parts = []
    for field in fields(entry.dice):
        value = getattr(entry.dice, field.name)
        if value is None or field.name in hidden:
            continue
        label = labels.get(field.name, field.name.replace("_", " ").title())
        parts.append(f"{label}: {value}")
    if hasattr(entry.dice, "signed_pitch_value"):
        parts.insert(2, f"Pitch value: {entry.dice.signed_pitch_value:+d}")
    if getattr(entry.dice, "oddity_rolls", None) is not None:
        rolls = entry.dice.oddity_rolls
        parts.append(f"Oddity dice: {rolls[0]} + {rolls[1]}")
        parts.extend(
            f"{label.replace('_', ' ').title()}: {value}"
            for label, value in entry.dice.oddity_detail_rolls
        )
    if hasattr(entry.dice, "steal"):
        steal = entry.dice.steal
        parts.insert(0, f"Steal d20: {steal.roll} -> {steal.modified_roll}")
    if hasattr(entry.dice, "swing"):
        swing = entry.dice.swing
        parts.insert(1, f"Swing d100: {swing.swing_score}; MSS {swing.mss}")
    return textwrap.fill(
        "Dice: " + " | ".join(parts),
        width=88,
        subsequent_indent="      ",
        break_long_words=False,
    )


def render_lineup(state: GameState, side: str) -> str:
    team_state, team_data = _team(state, side)
    assignments = {
        item.player_id: item.position for item in team_state.active_defense
    }
    lines = [f"{team_data.name.upper()} LINEUP"]
    for index, player_id in enumerate(team_state.lineup):
        player = team_data.player(player_id)
        marker = ">" if index == team_state.batting_order_index else " "
        position = assignments.get(player_id) or _active_position(
            team_state, player_id, player.positions
        )
        lines.append(
            f"{marker}{index + 1} {player.name:<24} {position:<3} "
            f"{player.bats or '-':<1} BT {player.bt or '-':>2} "
            f"OBT {player.obt or '-':>2}{_traits(player.traits)}"
        )
    if team_state.removed_players:
        names = ", ".join(
            team_data.player(item).name for item in team_state.removed_players
        )
        lines.append(f"Out of game: {names}")
    return "\n".join(lines)


def render_bullpen(state: GameState, side: str) -> str:
    team_state, team_data = _team(state, side)
    current = team_data.player(team_state.active_pitcher_id or "")
    lines = [
        f"{team_data.name.upper()} PITCHERS",
        f"Current: {current.name} ({current.throws}HP, {team_state.active_pitch_die})",
        "Available:",
    ]
    if not team_state.bullpen:
        lines.append("  None")
    for index, player_id in enumerate(team_state.bullpen, start=1):
        player = team_data.player(player_id)
        lines.append(
            f"  {index}. {player.name} ({player.throws}HP, {player.pitch_die})"
        )
    return "\n".join(lines)


def _command_menu(state: GameState, session: GameSession | None) -> str:
    commands = _command_options(state, session)
    return "\n".join(
        "  ".join(commands[index:index + 3])
        for index in range(0, len(commands), 3)
    )


def _command_options(
    state: GameState, session: GameSession | None
) -> list[str]:
    actions = set(legal_actions(state))
    commands = []
    for key, action in (
        ("S", "swing"),
        ("B", "bunt"),
        ("H", "hit_and_run"),
    ):
        if action in actions:
            binding = "S/Enter" if action == "swing" else key
            commands.append(f"[{binding}] {ACTION_LABELS[action]}")
    if any(
        action.startswith("steal") or action == "double_steal"
        for action in actions
    ):
        commands.append("[T] Steal")
    if session is not None:
        offense = _team(state, _offense_side(state))[0]
        defense = _team(state, _defense_side(state))[0]
        if offense.bench:
            commands.append("[P] Pinch hit")
            if any(state.bases):
                commands.append("[R] Pinch run")
        if defense.bullpen and pitcher_may_be_replaced(
            state, _defense_side(state)
        ):
            commands.append("[M] Mound change")
        if defense.bench:
            commands.append("[D] Defensive sub")
        commands.append("[X] Position switch")
    commands.extend(
        (
            "[L] Lineups",
            "[V] Pitchers",
            "[Y] History",
            "[?] Rule",
            "[U] Undo",
            f"[K] {'Save a copy' if session and session.autosave_path else 'Save as'}",
            f"[Q] {'Quit' if session and session.is_saved else 'Save & quit'}",
        )
    )
    return commands


def _resolve_action(
    state: GameState, action: str, dice: RandomDice
) -> ActionResult:
    if action == "swing":
        return resolve_swing(state, dice)
    if action == "bunt":
        return resolve_bunt(state, dice)
    if action == "hit_and_run":
        return resolve_hit_and_run(state, dice)
    if action in {
        "steal_second",
        "steal_third",
        "double_steal",
        "steal_home",
    }:
        return resolve_steal(state, action, dice)
    raise ValueError(f"unsupported manager action {action!r}")


def _team(state: GameState, side: str):
    if side == "away":
        return state.away, state.source.teams.away
    if side == "home":
        return state.home, state.source.teams.home
    raise ValueError(f"unknown side {side!r}")


def _offense_side(state: GameState) -> str:
    return "away" if state.half == "top" else "home"


def _defense_side(state: GameState) -> str:
    return "home" if state.half == "top" else "away"


def _active_position(team_state, player_id: str, positions: tuple[str, ...]) -> str:
    for assignment in team_state.active_defense:
        if assignment.player_id == player_id:
            return assignment.position
    return positions[0] if positions else "-"


def _player_summary(player) -> str:
    if player.role in {"starter", "reliever"}:
        return f"{player.name} - {player.throws}HP - {player.pitch_die}"
    return (
        f"{player.name} - {'/'.join(player.positions)} - {player.bats} - "
        f"BT {player.bt} OBT {player.obt}{_traits(player.traits)}"
    )


def _traits(traits: tuple[str, ...]) -> str:
    return "" if not traits else "   " + " ".join(traits)


def _bases_text(state: GameState) -> str:
    occupied = []
    offense_data = _team(state, _offense_side(state))[1]
    for base, player_id in zip(("1B", "2B", "3B"), state.bases):
        if player_id is not None:
            occupied.append(f"{base} {offense_data.player(player_id).name}")
    return ", ".join(occupied) if occupied else "empty"


def _half_label(state: GameState) -> str:
    return f"{state.half.title()} {_ordinal(state.inning)}"


def _wrap(text: str) -> str:
    return textwrap.fill(text, width=88, break_long_words=False)


def _narration_box(text: str, *, width: int) -> list[str]:
    """Render one narration line with one row and three columns of padding."""
    text_width = max(1, width - 8)
    narration = textwrap.shorten(text, width=text_width, placeholder="…")
    box_width = min(width, len(narration) + 8)
    horizontal_padding = " " * 3
    return [
        "┌" + "─" * (box_width - 2) + "┐",
        "│" + " " * (box_width - 2) + "│",
        "│" + horizontal_padding + narration + horizontal_padding + "│",
        "│" + " " * (box_width - 2) + "│",
        "└" + "─" * (box_width - 2) + "┘",
    ]


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _filename_part(value: str) -> str:
    cleaned = "".join(character for character in value if character.isalnum())
    return cleaned or "Team"


def _header_cluster(left: str, center: str, right: str, width: int) -> str:
    """Keep all scoreboard groups together around the center of the screen."""
    left_width = 18
    center_width = 11
    right_width = max(45, len(right))
    group = (
        left[:left_width].ljust(left_width)
        + " " * 5
        + center[:center_width].center(center_width)
        + " " * 5
        + right[:right_width].rjust(right_width)
    )
    group = group[:width]
    available = max(0, width - len(group))
    left_padding = min(available, available // 2 + 2)
    return " " * left_padding + group + " " * (available - left_padding)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deadball-play",
        description="Play a generated Deadball game in the terminal.",
    )
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--game", type=Path, help="generated game JSON")
    source.add_argument("--resume", type=Path, help="saved session JSON")
    source.add_argument(
        "--demo", action="store_true", help="start a built-in fictional game"
    )
    source.add_argument(
        "--cached-game",
        metavar="MLB_GAME_ID",
        help="start a generated game from the local Deadball Web database",
    )
    source.add_argument(
        "--generate-game",
        metavar="MLB_GAME_ID",
        help="generate through the shared service and start the game",
    )
    source.add_argument(
        "--generate-only",
        metavar="MLB_GAME_ID",
        help="generate JSON and PDF through the shared service without starting a game",
    )
    parser.add_argument(
        "--web-base-url",
        default="http://127.0.0.1:8000/api",
        help="optional remote Deadball Web API used instead of the local service",
    )
    parser.add_argument(
        "--trait-mode",
        choices=("standard", "sabr", "adaptive"),
        default="standard",
        help="Deadball Web trait calculation mode",
    )
    parser.add_argument(
        "--force-generate",
        action="store_true",
        help="refresh cached statistics before generating",
    )
    parser.add_argument(
        "--scorecard-side",
        choices=("both", "home", "away"),
        default="both",
        help="team side(s) used for generated PDF score sheets (default: both)",
    )
    parser.add_argument(
        "--return-to-menu",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--away-team-label", default="Away team", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--home-team-label", default="Home team", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("backend/deadball_dev.db"),
        help="Deadball Web SQLite database used by --cached-game",
    )
    parser.add_argument(
        "--export-game",
        type=Path,
        help="also write the canonical schema-v1 game JSON",
    )
    parser.add_argument("--save", type=Path, help="autosave path for a new game")
    parser.add_argument("--seed", type=int, help="optional reproducible new-game seed")
    parser.add_argument(
        "--away-control", choices=("human", "computer"), default="human"
    )
    parser.add_argument(
        "--home-control", choices=("human", "computer"), default="human"
    )
    parser.add_argument("--away-daring", type=int)
    parser.add_argument("--home-daring", type=int)
    parser.add_argument(
        "--oddities",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable the Second Edition Oddities table for a new game",
    )
    parser.add_argument(
        "--three-batter-minimum",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable MLB-style minimum pitcher usage for a new game",
    )
    parser.add_argument(
        "--line-mode",
        action="store_true",
        help="use the scrolling compatibility interface instead of three columns",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="deprecated alias for --line-mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            arguments = ["--help"]
        else:
            from .startup import startup_arguments

            selected = startup_arguments()
            if selected is None:
                return 0
            arguments = selected
    parser = _parser()
    args = parser.parse_args(arguments)
    if not any(
        (
            args.game,
            args.resume,
            args.demo,
            args.cached_game,
            args.generate_game,
            args.generate_only,
        )
    ):
        parser.error("choose a game source or run without arguments for the start screen")

    def return_to_start_screen(message: str | None = None) -> int:
        if message:
            print(f"\nCould not continue: {message}")
        input("\nPress Enter to return to the main menu.")
        from .startup import startup_arguments

        selected = startup_arguments()
        return 0 if selected is None else main(selected)

    try:
        if args.generate_only:
            from .startup import generate_web_artifacts

            artifacts = generate_web_artifacts(
                args.generate_only,
                base_url=args.web_base_url,
                trait_mode=args.trait_mode,
                force=args.force_generate,
                scorecard_side=args.scorecard_side,
                progress_func=print,
                away_team_label=args.away_team_label,
                home_team_label=args.home_team_label,
            )
            print(f"Game JSON: {artifacts.game_path}")
            for scorecard_path in artifacts.scorecard_paths:
                print(f"Score sheet: {scorecard_path}")
            if args.return_to_menu:
                return return_to_start_screen()
            return 0
        session = None
        if args.resume:
            session = GameSession.load(args.resume)
        else:
            if args.demo:
                game = load_demo_game()
            elif args.cached_game:
                game = load_cached_game(args.cached_game, args.database)
            elif args.generate_game:
                from .startup import generate_web_artifacts

                artifacts = generate_web_artifacts(
                    args.generate_game,
                    base_url=args.web_base_url,
                    trait_mode=args.trait_mode,
                    force=args.force_generate,
                    scorecard_side=args.scorecard_side,
                    progress_func=print,
                    away_team_label=args.away_team_label,
                    home_team_label=args.home_team_label,
                )
                game = load_generated_game(
                    artifacts.game_path.read_text(encoding="utf-8")
                )
                if args.save is None:
                    args.save = artifacts.save_path
                print(f"Game JSON: {artifacts.game_path}")
                for scorecard_path in artifacts.scorecard_paths:
                    print(f"Score sheet: {scorecard_path}")
            else:
                try:
                    game_text = args.game.read_text(encoding="utf-8")
                except FileNotFoundError as exc:
                    raise ValueError(
                        f"game file not found: {args.game}. "
                        "Use --demo to start without a generated file."
                    ) from exc
                document = json.loads(game_text)
                if isinstance(document, dict) and "save_format_version" in document:
                    session = GameSession.load(args.game)
                else:
                    game = load_generated_game(document)
            if session is None:
                if (
                    args.oddities is not None
                    or args.three_batter_minimum is not None
                ):
                    game = replace(
                        game,
                        rules=replace(
                            game.rules,
                            oddities=(
                                game.rules.oddities
                                if args.oddities is None
                                else args.oddities
                            ),
                            three_batter_minimum=(
                                game.rules.three_batter_minimum
                                if args.three_batter_minimum is None
                                else args.three_batter_minimum
                            ),
                        ),
                    )
                if args.export_game:
                    args.export_game.parent.mkdir(parents=True, exist_ok=True)
                    args.export_game.write_text(
                        game.to_json(indent=2) + "\n", encoding="utf-8"
                    )
                config = SessionConfig(
                    away_control=args.away_control,
                    home_control=args.home_control,
                    away_daring=args.away_daring,
                    home_daring=args.home_daring,
                )
                rng = (
                    RandomDice(random.Random(args.seed))
                    if args.seed is not None
                    else None
                )
                session = GameSession(
                    initialize_game(game),
                    rng=rng,
                    config=config,
                    autosave_path=args.save,
                )
                if args.save:
                    session.save()
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        if args.return_to_menu:
            return return_to_start_screen(str(exc))
        parser.error(str(exc))
    use_fullscreen = (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not args.line_mode
        and not args.no_clear
    )
    if use_fullscreen:
        from .fullscreen import run_fullscreen

        return run_fullscreen(session)
    return TerminalApp(session, clear_screen=False).run()


if __name__ == "__main__":
    raise SystemExit(main())
