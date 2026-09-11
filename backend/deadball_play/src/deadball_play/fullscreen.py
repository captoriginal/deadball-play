"""Curses controller for the three-column Deadball Play dashboard."""

from __future__ import annotations

import curses
from pathlib import Path
import re
import textwrap

from .layout import DashboardView, MIN_COLUMNS, MIN_ROWS, compose_modal
from .narration import Narrator
from .session import GameSession, SessionError
from .tui import TerminalApp, _ordinal


class FullscreenApp(TerminalApp):
    """Immediate-key controller that keeps the three dashboard columns visible."""

    def __init__(
        self,
        session: GameSession,
        screen,
        *,
        narrator: Narrator | None = None,
    ) -> None:
        super().__init__(session, narrator=narrator, clear_screen=False)
        self.screen = screen
        self.view = DashboardView()
        self._dialog: list[str] | None = None
        self._team_colors: list[tuple[tuple[str, ...], int]] = []

    def run(self) -> int:
        self.screen.keypad(True)
        self._init_colors()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if self._intro_pending:
            try:
                self._show_centered_message(
                    self.intro_screen().splitlines(), wait=True
                )
            except KeyboardInterrupt:
                return 130
            self._intro_pending = False
        while True:
            try:
                if self.session.state.is_final and self.session.pending_event is None:
                    self._archive_completed_game()
                self._draw()
                key = self.screen.get_wch()
                if key == "\x03":
                    return 130
                if self._handle_view_key(key):
                    continue
                if self.session.state.is_final:
                    if _letter(key) == "Y":
                        self._pause(self.history_screen())
                    elif _letter(key) == "K":
                        self._save()
                    elif _letter(key) == "Q":
                        return 0
                    continue
                if self.session.pending_event is not None:
                    if key in ("\n", "\r", curses.KEY_ENTER):
                        before = self.session.history[-1].state_before
                        after = self.session.state
                        self.session.confirm_scorekeeping()
                        if (before.inning, before.half) != (
                            after.inning,
                            after.half,
                        ):
                            self._show_inning_transition(before, after)
                        self._archive_completed_game()
                        self._notice = None
                    elif _letter(key) == "?":
                        self._pause(self.rule_screen())
                    elif _letter(key) == "Y":
                        self._pause(self.history_screen())
                    elif _letter(key) == "U":
                        self._undo()
                    elif _letter(key) == "K":
                        self._save()
                    elif _letter(key) == "Q" and self._quit():
                        return 0
                    else:
                        self._notice = "The play is still awaiting scorecard confirmation."
                    continue
                letter = (
                    "S"
                    if key in ("\n", "\r", curses.KEY_ENTER)
                    else _letter(key)
                )
                if self._offense_is_computer() and letter == "S":
                    self._perform_computer_action()
                    continue
                if letter and self._handle_ready_command(letter):
                    return 0
            except KeyboardInterrupt:
                return 130
            except (SessionError, ValueError, OSError) as exc:
                self._notice = f"Could not complete that action: {exc}"

    def _handle_view_key(self, key) -> bool:
        if key == "\t":
            self.view.toggle()
            return True
        if self.view.context_mode != "narration":
            return False
        height, _ = self.screen.getmaxyx()
        page = max(1, height - 6)
        if key == curses.KEY_UP:
            self.view.scroll_up()
        elif key == curses.KEY_DOWN:
            self.view.scroll_down()
        elif key == curses.KEY_PPAGE:
            self.view.scroll_up(page)
        elif key == curses.KEY_NPAGE:
            self.view.scroll_down(page)
        elif key == curses.KEY_HOME:
            self.view.scroll_up(10_000)
        elif key == curses.KEY_END:
            self.view.narration_offset = 0
        else:
            return False
        return True

    def _draw(self) -> None:
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        if width < MIN_COLUMNS or height < MIN_ROWS:
            message = (
                f"Deadball Play needs at least {MIN_COLUMNS} columns by "
                f"{MIN_ROWS} rows; current terminal is {width} by {height}. "
                "Resize the window or press Ctrl-C to exit."
            )
            lines = textwrap.wrap(message, max(20, width - 2))
        else:
            if self.session.state.is_final and self.session.pending_event is None:
                lines = compose_modal(
                    self.final_summary_lines(), width=width, height=height
                ).splitlines()
            else:
                lines = self.dashboard_screen(
                    self.view,
                    width=width,
                    height=height,
                ).splitlines()
        for row, line in enumerate(lines[:height]):
            try:
                limit = width - 1 if row == height - 1 else width
                self.screen.addnstr(row, 0, line, max(0, limit))
                self._add_team_colors(row, line, limit)
                self._add_scoreboard_colors(row, line, limit)
                self._add_rating_colors(row, line, limit)
                self._add_narration_box_colors(row, line, limit)
                self._add_information_colors(row, line, limit)
            except curses.error:
                pass
        self.screen.refresh()

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_WHITE, -1)
            orange = 208 if getattr(curses, "COLORS", 0) >= 256 else curses.COLOR_YELLOW
            curses.init_pair(6, orange, -1)
        except curses.error:
            return
        source = self.session.state.source
        self._team_colors = [
            (_team_color_tokens(source.teams.away), curses.color_pair(1) | curses.A_BOLD),
            (_team_color_tokens(source.teams.home), curses.color_pair(2) | curses.A_BOLD),
        ]

    def _add_team_colors(self, row: int, line: str, limit: int) -> None:
        for tokens, attribute in self._team_colors:
            for token in tokens:
                start = 0
                while True:
                    start = line.find(token, start, limit)
                    if start < 0:
                        break
                    end = start + len(token)
                    before_ok = start == 0 or not line[start - 1].isalnum()
                    after_ok = end >= len(line) or not line[end].isalnum()
                    if before_ok and after_ok:
                        try:
                            self.screen.addnstr(
                                row, start, token, min(len(token), limit - start), attribute
                            )
                        except curses.error:
                            pass
                    start = end

    def _add_scoreboard_colors(self, row: int, line: str, limit: int) -> None:
        if not self._team_colors:
            return
        for label, pair in (("B:", 3), ("S:", 4), ("O:", 4)):
            start = line.find(label)
            if start < 0:
                continue
            separator = line.find("|", start)
            end = min(limit, separator if separator >= 0 else limit)
            for column in range(start + len(label), end):
                if line[column] in {"●", "○"}:
                    try:
                        self.screen.addstr(
                            row,
                            column,
                            line[column],
                            curses.color_pair(pair) | curses.A_BOLD,
                        )
                    except curses.error:
                        pass
        for column, character in enumerate(line[:limit]):
            if character == "◆":
                try:
                    self.screen.addstr(
                        row,
                        column,
                        character,
                        curses.color_pair(2) | curses.A_BOLD,
                    )
                except curses.error:
                    pass

    def _add_rating_colors(self, row: int, line: str, limit: int) -> None:
        for match in re.finditer(r"\b(BT|OBT)\s+(\d+)\b", line[:limit]):
            label, raw = match.groups()
            value = int(raw)
            if label == "BT":
                pair = 3 if value >= 30 else 2 if value >= 27 else 4
            else:
                pair = 3 if value >= 38 else 2 if value >= 35 else 4
            start = match.start(2)
            try:
                self.screen.addnstr(
                    row,
                    start,
                    raw,
                    len(raw),
                    curses.color_pair(pair) | curses.A_BOLD,
                )
            except curses.error:
                pass

    def _add_narration_box_colors(self, row: int, line: str, limit: int) -> None:
        if not self._team_colors:
            return
        attribute = curses.color_pair(5) | curses.A_DIM
        for column, character in enumerate(line[:limit]):
            if character not in {"┌", "─", "┐", "│", "└", "┘"}:
                continue
            try:
                self.screen.addstr(row, column, character, attribute)
            except curses.error:
                pass

    def _add_information_colors(self, row: int, line: str, limit: int) -> None:
        if not self._team_colors:
            return
        messages = (
            "Computer offense is paused for your defensive decision. "
            "Press Enter or S to continue.",
            "Press Enter when scored.",
            "Waiting for the next play.",
            "MANAGER NOTICE:",
        )
        attribute = curses.color_pair(6) | curses.A_BOLD
        for message in messages:
            start = line.find(message, 0, limit)
            if start < 0:
                continue
            length = len(message)
            if message == "MANAGER NOTICE:":
                length = len(line[:limit].rstrip()) - start
            try:
                self.screen.addnstr(
                    row,
                    start,
                    line[start:start + length],
                    min(length, limit - start),
                    attribute,
                )
            except curses.error:
                pass

    def _show_centered_message(self, lines: list[str], *, wait: bool = False) -> None:
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        modal = compose_modal(lines, width=width, height=height).splitlines()
        for row, line in enumerate(modal[:height]):
            try:
                self.screen.addnstr(row, 0, line, width)
            except curses.error:
                pass
        self.screen.refresh()
        if wait:
            while self.screen.get_wch() not in ("\n", "\r", curses.KEY_ENTER):
                pass

    def _show_inning_transition(self, before, after) -> None:
        ending = f"END {before.half.upper()} {_ordinal(before.inning)}"
        beginning = f"{after.half.upper()} {_ordinal(after.inning)}"
        for title in (ending, "◆"):
            self._show_centered_message(
                ["INNING TRANSITION", "", title],
            )
            curses.napms(220)
        self._show_centered_message(
            [
                "INNING TRANSITION",
                "",
                beginning,
                "",
                "Press Enter to continue.",
            ],
            wait=True,
        )

    def _dashboard_state_lines(self) -> list[str]:
        if self._dialog is not None:
            return ["CURRENT STATE / QUESTION", "", *self._dialog]
        return super()._dashboard_state_lines()

    def _choose_value(self, title, values, label):
        if not values:
            self._notice = f"{title.title()}: none available."
            return None
        selected = 0
        while True:
            self._dialog = [
                title,
                "",
                *(
                    f"{'>' if index == selected else ' '} {index + 1}. {label(value)}"
                    for index, value in enumerate(values)
                ),
                "",
                "Up/Down selects; Enter confirms; Esc cancels.",
            ]
            self._draw()
            key = self.screen.get_wch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(values)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(values)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                self._dialog = None
                return values[selected]
            elif key == "\x1b":
                self._dialog = None
                return None

    def _confirm(self, prompt: str) -> bool:
        self._dialog = [prompt, "", "[Y/Enter] Yes", "[N/Esc] No"]
        self._draw()
        while True:
            key = self.screen.get_wch()
            letter = _letter(key)
            if key in ("\n", "\r", curses.KEY_ENTER) or letter == "Y":
                self._dialog = None
                return True
            if key == "\x1b" or letter == "N":
                self._dialog = None
                return False

    def _pause(self, text: str) -> None:
        raw_lines = text.splitlines()
        offset = 0
        height, _ = self.screen.getmaxyx()
        page = max(1, height - 8)
        while True:
            self._dialog = [
                *raw_lines[offset:],
                "",
                "Up/Down or Page Up/Page Down scrolls; Esc/Enter returns.",
            ]
            self._draw()
            key = self.screen.get_wch()
            if key in ("\n", "\r", "\x1b", curses.KEY_ENTER):
                self._dialog = None
                return
            if key == curses.KEY_UP:
                offset = max(0, offset - 1)
            elif key == curses.KEY_DOWN:
                offset = min(max(0, len(raw_lines) - 1), offset + 1)
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - page)
            elif key == curses.KEY_NPAGE:
                offset = min(max(0, len(raw_lines) - 1), offset + page)

    def _save(self) -> bool:
        if self.session.autosave_path is None:
            name = self._read_text("Save as path (Esc cancels)")
            if not name:
                self._notice = "Save cancelled."
                return False
            path = self.session.save(Path(name).expanduser())
            self._notice = f"Autosave enabled at {path}."
        else:
            name = self._read_text("Save a copy path (Esc cancels)")
            if not name:
                self._notice = "Save copy cancelled."
                return False
            path = self.session.save_copy(Path(name).expanduser())
            self._notice = f"Saved a copy to {path}."
        return True

    def _quit(self) -> bool:
        if not self.session.is_saved:
            if self.session.autosave_path is None:
                name = self._read_text("Save as path before quitting (Esc cancels)")
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

    def _read_text(self, prompt: str) -> str | None:
        value = ""
        while True:
            self._dialog = [prompt, "", value + "_"]
            self._draw()
            key = self.screen.get_wch()
            if key == "\x1b":
                self._dialog = None
                return None
            if key in ("\n", "\r", curses.KEY_ENTER):
                self._dialog = None
                return value.strip()
            if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                value = value[:-1]
            elif isinstance(key, str) and key.isprintable():
                value += key

    def _write(self, text: str) -> None:
        self._notice = text.strip()


def run_fullscreen(session: GameSession, narrator: Narrator | None = None) -> int:
    """Run the curses UI and restore the terminal on every exit path."""
    return curses.wrapper(
        lambda screen: FullscreenApp(session, screen, narrator=narrator).run()
    )


def _letter(key) -> str:
    return key.upper() if isinstance(key, str) and len(key) == 1 else ""


def _team_color_tokens(team) -> tuple[str, ...]:
    values = {team.name, team.name.upper(), team.short_name}
    for player in team.roster:
        values.update((player.name, player.name.upper()))
        parts = player.name.split()
        if len(parts) > 1:
            short = f"{parts[0][0]}. {parts[-1]}"
            values.update((short, short.upper()))
    return tuple(sorted(values, key=len, reverse=True))
