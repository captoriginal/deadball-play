from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import random
import sqlite3
import sys
from unittest.mock import Mock, patch

from deadball_core import (
    RandomDice,
    initialize_game,
    legal_actions,
    load_generated_game,
    resolve_swing,
)
from deadball_play import (
    GameSession,
    Narrator,
    SessionConfig,
    load_cached_game,
    load_demo_game,
)
from deadball_play.fullscreen import FullscreenApp
from deadball_play.layout import (
    DashboardView,
    column_widths,
    compose_modal,
    field_panel,
    lineups_panel,
    narration_panel,
    mlb_stats_panel,
)
from deadball_play.tui import (
    TerminalApp,
    _narration_box,
    main,
    render_bullpen,
    render_dice,
    render_game_screen,
    render_lineup,
)
from deadball_play.summary import build_batting_lines, build_pitching_lines


CORE_TESTS = Path(__file__).parents[2] / "deadball_core" / "tests"
if str(CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(CORE_TESTS))

from test_game_data import canonical_game, generator_rows


class StatefulFixedDice:
    def __init__(self, results):
        self.results = tuple(results)
        self.index = 0

    def roll(self, sides):
        result = self.results[self.index]
        self.index += 1
        assert 1 <= result <= sides
        return result

    def getstate(self):
        return self.index

    def setstate(self, state):
        self.index = state


def initial_state():
    return initialize_game(load_generated_game(canonical_game()))


def scripted_input(*responses):
    answers = iter(responses)
    return lambda prompt="": next(answers)


def test_main_screen_prioritizes_situation_and_only_legal_tactics():
    state = initial_state()
    screen = render_game_screen(state, GameSession(state))

    assert "VIS 0" in screen and "TOP 1st" in screen and "HST 0" in screen
    assert "Outs: 0    Runners: empty" in screen
    assert "Visitors Hitter 1" in screen
    assert "BT 30   OBT 39" in screen
    assert "Hosts Starter" in screen and "Pitch Die d8" in screen
    assert "[S/Enter] Swing" in screen
    assert "[B] Bunt" not in screen
    assert "[H] Hit & Run" not in screen
    assert "[T] Steal" not in screen


def test_runner_state_adds_only_tactics_returned_by_core():
    state = replace(initial_state(), bases=("away-h2", None, None))
    screen = render_game_screen(state, GameSession(state))

    assert set(legal_actions(state)) == {
        "swing",
        "bunt",
        "hit_and_run",
        "steal_second",
    }
    assert "[B] Bunt" in screen
    assert "[H] Hit & Run" in screen
    assert "[T] Steal" in screen
    assert "[R] Pinch run" in screen
    assert "1B Visitors Hitter 2" in screen


def test_three_column_dashboard_separates_state_vertical_options_and_field():
    state = replace(initial_state(), bases=("away-h2", None, None))
    session = GameSession(state)
    app = TerminalApp(session)
    view = DashboardView()

    screen = app.dashboard_screen(view, width=160, height=47)
    lines = screen.splitlines()
    left_width, middle_width, _ = column_widths(160)
    first_separator = left_width + 1
    second_separator = first_separator + middle_width + 1

    assert len(lines) == 47
    assert all(len(line) == 160 for line in lines)
    assert any("CURRENT STATE" in line[:first_separator] for line in lines)
    assert any(
        first_separator < line.index("[S/Enter] Swing") < second_separator
        for line in lines
        if "[S/Enter] Swing" in line
    )
    option_rows = {
        next(index for index, line in enumerate(lines) if option in line)
        for option in ("[S/Enter] Swing", "[B] Bunt", "[H] Hit & Run", "[T] Steal")
    }
    assert len(option_rows) == 4
    assert "FIELD" in screen
    assert "DEFENSE: Hosts" in screen
    assert all(f"Hosts Hitter {slot}" in screen for slot in range(1, 9))
    assert "Hosts Starter" in screen
    assert "[1B]" in screen
    assert "Runner: V. 2" in screen
    assert "OUTCOME" not in screen
    assert "Waiting for the next play." in screen
    assert "1  2  3  4  5  6  7  8  9" in screen
    assert all(line.endswith(("|", "+")) for line in lines)


def test_expanded_field_keeps_every_position_and_runner_at_minimum_width():
    state = replace(
        initial_state(),
        bases=("away-h2", "away-h3", "away-h4"),
    )
    _, _, right_width = column_widths(120)

    field = field_panel(state, right_width)
    rendered = "\n".join(field)

    assert len(field) == 25
    assert all(
        position in rendered
        for position in (
            "[LF]",
            "[CF]",
            "[RF]",
            "[SS]",
            "--RHP--",
            "[2B]",
            "[3B]",
            "[1B]",
            "[C]",
        )
    )
    assert all(f"Runner: V. {slot}" in rendered for slot in (2, 3, 4))
    assert "OUTFIELD" not in rendered and "INFIELD" not in rendered
    assert "Runner: empty" not in rendered

    outfielder_row = next(
        index for index, line in enumerate(field) if "[LF]" in line
    )
    second_base_row = next(
        index for index, line in enumerate(field) if "[2B]" in line
    )
    assert field[outfielder_row + 1:outfielder_row + 4] == ["", "", ""]
    assert field[second_base_row + 2:second_base_row + 5] == ["", "", ""]

    corner_names = next(line for line in field if "Hosts Hitter 4" in line)
    corner_bases = next(line for line in field if "[3B]" in line)
    corner_runners = next(line for line in field if "Runner: V. 4" in line)
    name_center = corner_names.index("Hosts Hitter 4") + len("Hosts Hitter 4") // 2
    base_center = corner_bases.index("[3B]") + len("[3B]") // 2
    runner_center = corner_runners.index("Runner: V. 4") + len("Runner: V. 4") // 2
    assert abs(name_center - base_center) <= 1
    assert abs(base_center - runner_center) <= 1

    defense_row = next(
        index for index, line in enumerate(field) if "DEFENSE: Hosts" in line
    )
    corner_runner_row = next(
        index for index, line in enumerate(field) if "Runner: V. 4" in line
    )
    assert field[defense_row + 1:defense_row + 3] == ["", ""]
    assert field[corner_runner_row + 1:corner_runner_row + 3] == ["", ""]


def test_field_hides_empty_runners_and_inactive_batter_box():
    field = field_panel(initial_state(), column_widths(120)[2])
    rendered = "\n".join(field)

    assert "Runner: empty" not in rendered
    assert "RH BATTER" not in rendered
    assert "LH BATTER" in rendered
    assert "BASE" not in rendered


def test_field_places_position_labels_below_names_and_batters_near_plate():
    field_width = 74
    field = field_panel(initial_state(), field_width)

    left_fielder = next(i for i, line in enumerate(field) if "Hosts Hitter 6" in line)
    outfield_labels = next(i for i, line in enumerate(field) if "[LF]" in line)
    shortstop = next(i for i, line in enumerate(field) if "Hosts Hitter 5" in line)
    second_baseman = next(
        i for i, line in enumerate(field) if "Hosts Hitter 3" in line
    )
    shortstop_label = next(i for i, line in enumerate(field) if "[SS]" in line)
    catcher = next(i for i, line in enumerate(field) if "Hosts Hitter 1" in line)
    catcher_label = next(i for i, line in enumerate(field) if "[C]" in line)
    batter_line = next(line for line in field if "LH BATTER" in line)
    plate_line = next(line for line in field if "( )" in line)

    assert left_fielder < outfield_labels
    assert shortstop == second_baseman + 1
    assert shortstop < shortstop_label
    first_cell_width = (field_width - 2) // 3
    expected_centered_start = (first_cell_width - len("Hosts Hitter 5")) // 2
    assert field[shortstop].index("Hosts Hitter 5") == expected_centered_start + 9
    assert catcher < catcher_label
    assert abs(batter_line.index("LH BATTER") - plate_line.index("( )")) < len(plate_line) // 3


def test_scoreboard_clusters_groups_and_places_half_arrows_around_inning():
    top = TerminalApp(GameSession(initial_state()))._scoreboard_lines(160)
    bottom_state = replace(initial_state(), half="bottom")
    bottom = TerminalApp(GameSession(bottom_state))._scoreboard_lines(160)

    assert "▲" in top[0] and "▲" not in top[2]
    assert "▼" in bottom[2] and "▼" not in bottom[0]
    assert top[0].index("▲") == top[1].index("1")
    assert bottom[2].index("▼") == bottom[1].index("1")
    assert top[0].index("B:") == top[1].index("S:") == top[2].index("O:")
    assert top[0].index("B:") - top[0].index("▲") == 4
    assert "Visitors" in top[1] and "Hosts" in top[2]
    assert top[0].index("▲") == 38
    assert top[0].index("B:") > 20
    assert len(top[0]) - len(top[0].rstrip()) > 20


def test_inning_transition_pauses_on_final_frame_for_enter():
    before = replace(initial_state(), inning=3, half="top")
    after = replace(initial_state(), inning=3, half="bottom")
    fake_screen = Mock()
    app = FullscreenApp(GameSession(before), fake_screen)
    app._show_centered_message = Mock()

    with patch("deadball_play.fullscreen.curses.napms") as napms:
        app._show_inning_transition(before, after)

    assert napms.call_count == 2
    final_call = app._show_centered_message.call_args_list[-1]
    assert "BOTTOM 3rd" in final_call.args[0]
    assert "Press Enter to continue." in final_call.args[0]
    assert final_call.kwargs == {"wait": True}


def test_narration_column_toggles_scrolls_and_never_changes_game_state():
    session = GameSession(initial_state())
    before = session.state
    fake_screen = type("Screen", (), {"getmaxyx": lambda self: (32, 160)})()
    controller = FullscreenApp(session, fake_screen)

    assert controller._handle_view_key("\t") is True
    assert controller.view.context_mode == "narration"
    import curses

    assert controller._handle_view_key(curses.KEY_UP) is True
    assert controller.view.narration_offset == 1
    assert controller._handle_view_key(curses.KEY_NPAGE) is True
    assert controller.view.narration_offset == 0
    assert session.state == before
    assert session.history == ()


def test_third_column_cycles_field_narration_and_lineups():
    session = GameSession(initialize_game(load_demo_game()))
    app = TerminalApp(session)
    view = DashboardView()

    assert "FIELD" in app.dashboard_screen(view, width=160, height=47)
    view.toggle()
    assert "NARRATION" in app.dashboard_screen(view, width=160, height=47)
    view.toggle()
    lineups = app.dashboard_screen(view, width=160, height=47)
    assert "LINEUPS" in lineups
    assert "Milo Hayes" in lineups and "Silas Reed" in lineups
    assert "BENCH / REMOVED" in lineups
    assert "PITCHERS" in lineups
    assert "RBI" in lineups and "IP" in lineups
    assert "Wesley Quinn   " in lineups
    assert "Arthur Vaughn   " in lineups
    view.toggle()
    assert "MLB STATS" in app.dashboard_screen(view, width=160, height=47)
    view.toggle()
    assert view.context_mode == "field"


def test_mlb_stats_panel_lists_full_roster_and_marks_used_source_bench():
    document = load_demo_game().to_dict()
    away = document["teams"]["away"]
    lineup_ids = {slot["player_id"] for slot in away["lineup"]}
    bench = next(
        player
        for player in away["roster"]
        if player["role"] == "position_player"
        and player["player_id"] not in lineup_ids
    )
    bench["appeared_in_source_game"] = True
    bench["mlb_stats"] = {
        "games": 82,
        "average": 0.275,
        "on_base_percentage": 0.351,
        "home_runs": 9,
        "doubles": 14,
        "stolen_bases": 3,
    }
    state = initialize_game(load_generated_game(document))

    panel = "\n".join(mlb_stats_panel(state, 100))
    lineups = "\n".join(lineups_panel(state, 100))

    assert bench["name"] + "*" in panel
    assert bench["name"] + "*" in lineups
    assert ".275" in panel and ".351" in panel
    assert "MLB PITCHER STATS" in panel


def test_final_modal_is_centered_and_keeps_complete_right_border():
    modal = compose_modal(
        ["FINAL BOX SCORE", "RD 3   HM 2", "Winning pitcher: Owen Mercer"],
        width=120,
        height=36,
    )

    lines = modal.splitlines()
    assert len(lines) == 36
    assert all(len(line) == 120 for line in lines)
    assert "FINAL BOX SCORE" in lines[16]


def test_narration_viewport_follows_bottom_until_user_scrolls():
    narration = [f"Play {index}" for index in range(20)]

    latest, maximum = narration_panel(
        narration, width=40, height=8, offset=0
    )
    older, _ = narration_panel(
        narration, width=40, height=8, offset=5
    )

    assert maximum > 0
    assert any("Play 19" in line for line in latest)
    assert not any("Play 19" in line for line in older)
    assert older[2].startswith("[scrolled up")


def test_pending_play_shows_dice_narration_scoring_and_pause():
    state = initial_state()
    session = GameSession(state, rng=StatefulFixedDice([50, 1]))
    session.perform(resolve_swing)
    app = TerminalApp(session, narrator=Narrator(random.Random(1)))

    screen = app.pending_screen()

    assert "PLAY" in screen
    assert "d100: 50" in screen
    assert "Pitch value: +1" in screen
    assert "Score:" in screen
    assert "Press Enter when scored." in screen
    assert not session.scorekeeping_confirmed


def test_outcome_narration_box_has_one_row_and_three_columns_of_padding():
    narration = "A sharply hit ground ball ends the inning."
    box = _narration_box(
        narration,
        width=60,
    )

    assert len(box) == 5
    assert box[0].startswith("┌") and box[0].endswith("┐")
    assert box[4].startswith("└") and box[4].endswith("┘")
    assert len(box[0]) == len(narration) + 8
    assert box[1] == "│" + " " * (len(narration) + 6) + "│"
    assert box[3] == box[1]
    assert box[2] == f"│   {narration}   │"


def test_box_score_derives_standard_batter_and_pitcher_stats():
    session = GameSession(initial_state(), rng=StatefulFixedDice([18, 2, 9]))
    batter_id = session.state.away.lineup[0]
    pitcher_id = session.state.home.active_pitcher_id

    session.perform(resolve_swing)
    batting = build_batting_lines(session.history)
    pitching = build_pitching_lines(session.history)

    assert batting[batter_id].at_bats == 1
    assert batting[batter_id].hits == 1
    assert batting[batter_id].rbi == 0
    assert pitching[pitcher_id].hits == 1
    assert pitching[pitcher_id].innings_pitched == "0.0"


def test_pending_dashboard_keeps_previous_batter_until_score_is_confirmed():
    session = GameSession(initial_state(), rng=StatefulFixedDice([50, 1]))
    first_batter = session.state.away.lineup[0]
    second_batter = session.state.away.lineup[1]
    first_name = session.state.source.teams.away.player(first_batter).name
    second_name = session.state.source.teams.away.player(second_batter).name
    app = TerminalApp(session)

    session.perform(resolve_swing)
    pending = app.dashboard_screen(DashboardView(), width=160, height=47)

    assert first_name in pending
    boxed_narration = next(
        line[line.index("│"):line.rindex("│") + 1]
        for line in pending.splitlines()
        if line.count("│") == 2
        and line[line.index("│") + 1:line.rindex("│")].strip()
    )
    assert boxed_narration.startswith("│   ")
    assert boxed_narration.endswith("   │")
    assert "Review the outcome above." not in pending
    assert second_name not in "\n".join(
        line.split("|")[1]
        for line in pending.splitlines()[11:-1]
        if line.startswith("|")
    )
    assert "Press Enter when scored." in "\n".join(pending.splitlines()[4:17])

    session.confirm_scorekeeping()
    confirmed = app.dashboard_screen(DashboardView(), width=160, height=47)
    assert second_name in confirmed


def test_line_mode_enter_swings_after_intro(tmp_path):
    session = GameSession(
        initial_state(),
        rng=StatefulFixedDice([50, 1]),
        autosave_path=tmp_path / "enter-swing.json",
    )
    app = TerminalApp(
        session,
        input_func=scripted_input("", "", "", "Q"),
        output=StringIO(),
    )

    assert app.run() == 0
    assert len(session.history) == 1


def test_computer_offense_pauses_for_intro_and_continue_command(tmp_path):
    session = GameSession(
        initial_state(),
        rng=StatefulFixedDice([50, 1]),
        config=SessionConfig(away_control="computer"),
        autosave_path=tmp_path / "computer.json",
    )
    prompts = []
    responses = iter(("", "", "", "Q"))
    app = TerminalApp(
        session,
        input_func=lambda prompt="": (prompts.append(prompt), next(responses))[1],
        output=StringIO(),
    )

    assert app.run() == 0
    assert len(session.history) == 1
    assert prompts[0] == "Press Enter to begin. "
    assert "continue computer" in prompts[1]


def test_computer_pause_message_appears_only_in_full_width_status_area():
    session = GameSession(
        initial_state(),
        config=SessionConfig(away_control="computer"),
    )
    app = TerminalApp(session)

    screen = app.dashboard_screen(DashboardView(), width=160, height=47)
    lines = screen.splitlines()
    message = "Computer offense is paused for your defensive decision."

    assert message in "\n".join(lines[4:17])
    assert message not in "\n".join(lines[18:])


def test_rule_history_lineup_and_bullpen_views_use_structured_state():
    state = initial_state()
    session = GameSession(state, rng=StatefulFixedDice([50, 1]))
    session.perform(resolve_swing)
    app = TerminalApp(session, narrator=Narrator(random.Random(2)))

    assert "mss:" in app.rule_screen()
    assert "Second Edition" in app.rule_screen()
    assert "awaiting scorecard" in app.history_screen()
    assert "VISITORS LINEUP" in app.lineup_screen()
    assert ">1 Visitors Hitter 1" in render_lineup(state, "away")
    assert "Visitors Reliever" in render_bullpen(state, "away")
    assert "Dice:" in render_dice(session.history[-1])
    assert "RECENT PLAYS" in app.game_screen()
    assert "Top 1st:" in app.game_screen()


def test_scripted_gameplay_confirms_scorecard_then_saves_and_quits(tmp_path):
    path = tmp_path / "game.json"
    session = GameSession(
        initial_state(),
        rng=RandomDice(random.Random(5)),
        autosave_path=path,
    )
    output = StringIO()
    app = TerminalApp(
        session,
        input_func=scripted_input("", "S", "?", "", "", "Q"),
        output=output,
    )

    assert app.run() == 0
    assert path.exists()
    assert session.scorekeeping_confirmed
    assert len(session.history) == 1
    assert "RULE EXPLANATION" in output.getvalue()
    assert "Press Enter when scored." in output.getvalue()


def test_unknown_command_never_advances_game(tmp_path):
    path = tmp_path / "game.json"
    state = initial_state()
    session = GameSession(state, autosave_path=path)
    app = TerminalApp(
        session,
        input_func=scripted_input("Z", "Q"),
        output=StringIO(),
    )

    assert app.run() == 0
    assert session.state == state
    assert session.history == ()


def test_guided_pinch_hit_requires_selection_and_confirmation():
    session = GameSession(initial_state())
    app = TerminalApp(
        session,
        input_func=scripted_input("1", ""),
        output=StringIO(),
    )

    app._handle_ready_command("P")

    assert session.pending_event is not None
    assert session.pending_event.event_type == "pinch_hit"
    assert session.state.away.lineup[0] == "away-bench"


def test_computer_offense_resolves_daring_and_play_in_one_undo_transaction():
    state = replace(initial_state(), bases=("away-h2", None, None))
    session = GameSession(
        state,
        rng=RandomDice(random.Random(4)),
        config=SessionConfig(
            away_control="computer",
            away_daring=12,
        ),
    )
    rng_before = session.rng.getstate()
    app = TerminalApp(session, output=StringIO())

    app._perform_computer_action()

    assert len(session.history) == 1
    assert "Daring 12" in app._notice
    session.undo()
    assert session.state == state
    assert session.rng.getstate() == rng_before


def test_computer_pitching_change_precedes_play_and_is_exactly_undoable():
    state = initial_state()
    tired = replace(state.home.pitcher_state, runs_allowed=4)
    state = replace(state, home=replace(state.home, pitcher_state=tired))
    session = GameSession(
        state,
        rng=RandomDice(random.Random(1)),
        config=SessionConfig(home_control="computer", home_daring=19),
    )
    rng_before = session.rng.getstate()
    app = TerminalApp(session, output=StringIO())

    app._perform_play_action("swing")

    assert session.pending_event.event_type == "pitching_change"
    assert session.state.home.active_pitcher_id == "home-rp"
    assert "pitching decision" in app._notice
    session.undo()
    assert session.state == state
    assert session.rng.getstate() == rng_before


def test_missing_defensive_pitcher_suppresses_play_actions():
    state = initial_state()
    state = replace(
        state,
        home=replace(
            state.home,
            active_pitcher_id=None,
            active_pitch_die=None,
            pitcher_state=None,
        ),
    )

    assert legal_actions(state) == ()


def test_builtin_demo_is_valid_and_ready_offline():
    state = initialize_game(load_demo_game())

    assert state.source.game.source == "deadball-play-demo"
    assert state.source.teams.away.short_name == "RD"
    assert state.source.teams.home.short_name == "HM"
    assert legal_actions(state) == ("swing",)
    assert state.source.teams.away.roster[0].name == "Milo Hayes"
    assert state.source.teams.home.roster[0].name == "Silas Reed"


def test_game_option_auto_resumes_a_saved_session(tmp_path, monkeypatch):
    save_path = tmp_path / "misnamed-generated-game.json"
    session = GameSession(initial_state(), autosave_path=save_path)
    session.save()
    observed = {}

    def fake_run(app):
        observed["game_id"] = app.session.state.source.game.game_id
        observed["save_path"] = app.session.autosave_path
        return 0

    monkeypatch.setattr(TerminalApp, "run", fake_run)

    assert main(["--game", str(save_path), "--line-mode"]) == 0
    assert observed == {
        "game_id": "mlb-123",
        "save_path": save_path,
    }


def test_local_web_cache_loads_as_canonical_game_without_writes(tmp_path):
    database = tmp_path / "web.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE game (
            id INTEGER PRIMARY KEY, game_id TEXT, game_date TEXT,
            away_team TEXT, home_team TEXT,
            away_team_short TEXT, home_team_short TEXT
        );
        CREATE TABLE gamegenerated (
            id INTEGER PRIMARY KEY, game_id INTEGER, stats TEXT
        );
        CREATE TABLE gamerawstats (
            id INTEGER PRIMARY KEY, game_id INTEGER, payload TEXT
        );
        """
    )
    stats = {
        "players": [
            *generator_rows("Visitors", 100),
            *generator_rows("Hosts", 200),
        ],
        "teams": {"away_abbr": "VIS", "home_abbr": "HST"},
    }
    connection.execute(
        "INSERT INTO game VALUES (1, '123', '2026-08-15', "
        "'Visitors', 'Hosts', 'Visitors', 'Hosts')"
    )
    connection.execute(
        "INSERT INTO gamegenerated VALUES (1, 1, ?)",
        (json.dumps(stats),),
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()

    game = load_cached_game("123", database)

    assert game.game.game_id == "mlb-123"
    assert game.teams.away.short_name == "VIS"
    assert database.read_bytes() == before


def test_old_cache_retries_without_unrated_reserves_and_never_writes_database(
    tmp_path, monkeypatch
):
    database = tmp_path / "web.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE game (
            id INTEGER PRIMARY KEY, game_id TEXT, game_date TEXT,
            away_team TEXT, home_team TEXT,
            away_team_short TEXT, home_team_short TEXT
        );
        CREATE TABLE gamegenerated (
            id INTEGER PRIMARY KEY, game_id INTEGER, stats TEXT
        );
        CREATE TABLE gamerawstats (
            id INTEGER PRIMARY KEY, game_id INTEGER, payload TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO game VALUES (1, '123', '2026-08-15', "
        "'Visitors', 'Hosts', 'VIS', 'HST')"
    )
    connection.execute("INSERT INTO gamegenerated VALUES (1, 1, '{}')")
    connection.execute("INSERT INTO gamerawstats VALUES (1, 1, '{}')")
    connection.commit()
    connection.close()
    before = database.read_bytes()
    valid_stats = {
        "players": [
            *generator_rows("Visitors", 100),
            *generator_rows("Hosts", 200),
        ],
        "teams": {"away_abbr": "VIS", "home_abbr": "HST"},
    }

    def regenerate(**kwargs):
        if kwargs["include_reserves"]:
            return {"stats": '{"players": []}'}
        return {"stats": json.dumps(valid_stats)}

    generate = Mock(side_effect=regenerate)
    monkeypatch.setattr(
        "deadball_generator.generator.generate_game_from_raw", generate
    )

    game = load_cached_game("123", database)

    assert game.teams.away.short_name == "VIS"
    assert [call.kwargs["include_reserves"] for call in generate.call_args_list] == [
        True,
        False,
    ]
    assert database.read_bytes() == before
