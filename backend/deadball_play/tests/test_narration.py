from dataclasses import replace
from pathlib import Path
import random
import sys

import pytest

from deadball_core import (
    GameResult,
    PlayEvent,
    RunnerMove,
    StealEvent,
    SubstitutionEvent,
    initialize_game,
    load_generated_game,
)
from deadball_play.narration import NarrationError, Narrator


CORE_TESTS = Path(__file__).parents[2] / "deadball_core" / "tests"
if str(CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(CORE_TESTS))

from test_game_data import canonical_game


def initial_state():
    return initialize_game(load_generated_game(canonical_game()))


def play(event_type, **changes):
    values = {
        "classification": "out",
        "batter_id": "away-h1",
        "pitcher_id": "home-sp",
        "resolved": True,
    }
    values.update(changes)
    return PlayEvent(event_type, **values)


@pytest.mark.parametrize(
    ("event", "family"),
    [
        (play("strikeout", scoring_notation="K", outs_added=1), "strikeout"),
        (play("walk", classification="walk", scoring_notation="BB"), "walk"),
        (play("single", classification="ordinary_hit", scoring_notation="1B"), "single"),
        (play("double", classification="ordinary_hit", scoring_notation="2B"), "double"),
        (play("triple", classification="ordinary_hit", scoring_notation="3B"), "triple"),
        (play("home_run", classification="ordinary_hit", scoring_notation="HR"), "home_run"),
        (play("groundout", fielded_by="SS", scoring_notation="6-3", outs_added=1), "groundout"),
        (play("flyout", fielded_by="CF", scoring_notation="F-8", outs_added=1), "flyout"),
        (play("fielders_choice", fielded_by="SS", scoring_notation="FC", outs_added=1), "fielders_choice"),
        (play("double_play", fielded_by="2B", scoring_notation="DP", outs_added=2), "double_play"),
        (
            play(
                "double_play",
                classification="hit_and_run",
                fielded_by="2B",
                scoring_notation="DP",
                outs_added=2,
            ),
            "hit_and_run_double_play",
        ),
        (play("triple_play", fielded_by="3B", scoring_notation="TP", outs_added=3), "triple_play"),
        (play("error", fielded_by="3B", scoring_notation="E-3B"), "error"),
        (play("out", hit_type="single", fielded_by="1B", defense_outcome="out", outs_added=1), "defensive_out"),
        (play("bunt_fielders_choice", classification="bunt", scoring_notation="FC", outs_added=1), "bunt_fielders_choice"),
        (play("bunt_out", classification="bunt", fielded_by="3B", outs_added=1), "bunt_out"),
        (play("sacrifice_bunt", classification="bunt", scoring_notation="SAC", outs_added=1), "sacrifice_bunt"),
        (play("hit_and_run_hit", classification="hit_and_run", scoring_notation="1B"), "hit_and_run_hit"),
        (play("hit_and_run_out", classification="hit_and_run", scoring_notation="G", outs_added=1), "hit_and_run_out"),
        (
            play(
                "oddity_rain_delay",
                classification="oddity",
                resolved=False,
                oddity_name="Rain Delay",
                details=("Rain delays the game for 100 minutes; the at-bat continues.",),
            ),
            "oddity",
        ),
        (StealEvent("stolen_base", "steal_second", True, (RunnerMove("away-h2", "1B", "2B"),), scoring_notation="SB"), "stolen_base"),
        (StealEvent("caught_stealing", "steal_second", True, (RunnerMove("away-h2", "1B", out=True),), outs_added=1, scoring_notation="CS"), "caught_stealing"),
        (StealEvent("double_steal", "double_steal", True, (RunnerMove("away-h2", "2B", "3B"), RunnerMove("away-h3", "1B", "2B")), scoring_notation="SB"), "double_steal"),
        (SubstitutionEvent("pinch_hit", "team-away", "away-bench", "away-h1", 1), "pinch_hit"),
        (SubstitutionEvent("pinch_run", "team-away", "away-bench", "away-h2", 2, base="1B"), "pinch_run"),
        (SubstitutionEvent("pitching_change", "team-home", "home-rp", "home-sp", position="P"), "pitching_change"),
        (SubstitutionEvent("defensive_substitution", "team-home", "home-bench", "home-h6", 6, "LF"), "defensive_substitution"),
        (SubstitutionEvent("position_change", "team-home", details=("home-h3: 2B to LF", "home-h6: LF to 2B")), "position_change"),
    ],
)
def test_every_supported_event_family_renders_from_structured_facts(event, family):
    state = initial_state()
    rendered = Narrator(random.Random(4)).render(event, state, state)

    assert rendered.family == family
    assert rendered.play_text
    assert rendered.spoken_text


def test_render_does_not_mutate_event_or_game_state():
    before = initial_state()
    after = replace(before, outs=1)
    event = play("groundout", fielded_by="SS", scoring_notation="6-3", outs_added=1)
    before_snapshot, after_snapshot, event_snapshot = before, after, event

    Narrator(random.Random(3)).render(event, before, after)

    assert before == before_snapshot
    assert after == after_snapshot
    assert event == event_snapshot


def test_repetition_avoidance_rotates_accurate_templates():
    state = initial_state()
    event = play("single", classification="ordinary_hit", scoring_notation="1B")
    narrator = Narrator(random.Random(8), recent_window=2)
    outputs = [narrator.render(event, state, state).play_text for _ in range(8)]

    assert len(set(outputs)) == 5
    assert all(first != second for first, second in zip(outputs, outputs[1:]))


def test_missing_required_fact_raises_instead_of_inventing_fielder():
    state = initial_state()
    event = play("groundout", scoring_notation="G", outs_added=1)

    with pytest.raises(NarrationError, match="lacks fields"):
        Narrator().render(event, state, replace(state, outs=1))


def test_unknown_player_raises_instead_of_fabricating_a_name():
    state = initial_state()
    event = replace(play("strikeout", outs_added=1), batter_id="unknown-player")

    with pytest.raises(NarrationError, match="unknown player"):
        Narrator().render(event, state, replace(state, outs=1))


def test_inconsistent_score_transition_is_rejected():
    state = initial_state()
    event = play("single", scoring_notation="1B", runs_scored=1)

    with pytest.raises(NarrationError, match="runs do not match"):
        Narrator().render(event, state, state)


def test_scoring_guidance_is_stable_and_separate_from_varied_prose():
    before = replace(initial_state(), bases=("away-h2", None, None))
    after = replace(before, bases=("away-h1", None, "away-h2"))
    event = play(
        "single",
        classification="ordinary_hit",
        scoring_notation="1B",
        runner_moves=(RunnerMove("away-h2", "1B", "3B"),),
    )
    narrator = Narrator(random.Random(2))
    first = narrator.render(event, before, after)
    second = narrator.render(event, before, after)

    assert first.play_text != second.play_text
    assert first.scoring_guidance == second.scoring_guidance == (
        "Score: 1B",
        "Runner: Visitors Hitter 2 -> 3B",
    )
    assert "Score:" not in first.spoken_text


def test_double_play_guidance_identifies_the_play_without_inventing_a_relay():
    before = replace(initial_state(), bases=("away-h2", None, None))
    after = replace(before, outs=2, bases=(None, None, None))
    event = play(
        "double_play",
        fielded_by="1B",
        scoring_notation="G-3",
        outs_added=2,
        runner_moves=(
            RunnerMove("away-h2", "1B", out=True),
            RunnerMove("away-h1", "BATTER", out=True),
        ),
    )

    rendered = Narrator(random.Random(2)).render(event, before, after)

    assert rendered.scoring_guidance[0] == (
        "Score: DP (ground ball initiated by first base)"
    )
    assert "3-6-3" not in " ".join(rendered.scoring_guidance)


def test_flyout_names_the_actual_fielder_and_reports_out_count():
    before = initial_state()
    after = replace(before, outs=1)
    event = play("flyout", fielded_by="LF", scoring_notation="F-7", outs_added=1)

    rendered = Narrator(random.Random(3)).render(event, before, after)

    assert "Hosts Hitter 6" in rendered.play_text
    assert "left fielder" in rendered.play_text.lower()
    assert "There is one out." in rendered.play_text


def test_grand_slam_is_called_a_grand_slam():
    before = replace(
        initial_state(),
        bases=("away-h2", "away-h3", "away-h4"),
    )
    after = replace(before, away_score=4, bases=(None, None, None))
    event = play(
        "home_run",
        classification="ordinary_hit",
        scoring_notation="HR",
        hit_type="home_run",
        runs_scored=4,
        runner_moves=(
            RunnerMove("away-h2", "1B", "HOME", scored=True),
            RunnerMove("away-h3", "2B", "HOME", scored=True),
            RunnerMove("away-h4", "3B", "HOME", scored=True),
            RunnerMove("away-h1", "BATTER", "HOME", scored=True),
        ),
    )

    rendered = Narrator(random.Random(2)).render(event, before, after)

    assert rendered.family == "grand_slam"
    assert "grand slam" in rendered.play_text.lower()


def test_half_inning_transition_reports_runners_left_on_base():
    before = replace(initial_state(), outs=2, bases=(None, "away-h2", None))
    after = replace(before, inning=1, half="bottom", outs=0, bases=(None, None, None))
    event = play(
        "flyout",
        fielded_by="LF",
        scoring_notation="F-7",
        outs_added=1,
        runner_moves=(RunnerMove("away-h1", "BATTER", out=True),),
    )

    rendered = Narrator(random.Random(4)).render(event, before, after)

    assert "That is the third out." in rendered.play_text
    assert "VIS leaves 1 runner on base." in rendered.transition_text


def test_extra_inning_transition_names_automatic_runner():
    before = replace(
        initial_state(),
        inning=9,
        half="bottom",
        outs=2,
        away_score=2,
        home_score=2,
    )
    after = replace(
        before,
        inning=10,
        half="top",
        outs=0,
        bases=(None, "away-h9", None),
    )
    event = play(
        "strikeout",
        batter_id="home-h1",
        pitcher_id="away-sp",
        scoring_notation="K",
        outs_added=1,
    )

    rendered = Narrator(random.Random(4)).render(event, before, after)

    assert (
        "Visitors Hitter 9 starts the top of the 10th on second as the "
        "automatic runner."
    ) in rendered.transition_text


def test_run_scoring_context_only_describes_verified_tie_or_lead():
    before = replace(initial_state(), away_score=1, home_score=2)
    tied = replace(before, away_score=2)
    event = play(
        "single",
        classification="ordinary_hit",
        scoring_notation="1B",
        runs_scored=1,
        runner_moves=(RunnerMove("away-h2", "2B", "HOME", scored=True),),
    )
    rendered = Narrator(random.Random(1)).render(event, before, tied)

    assert "ties the game at 2" in rendered.play_text
    assert "Runner: Visitors Hitter 2 -> HOME" in rendered.scoring_guidance
    assert "Runs: 1" in rendered.scoring_guidance
    assert "Scoreboard: VIS 2, HST 2" in rendered.scoring_guidance


def test_inning_transition_and_final_text_come_from_state_transition():
    before = replace(initial_state(), inning=5, half="top", outs=2, away_score=3, home_score=1)
    after_inning = replace(before, half="bottom", outs=0)
    event = play("strikeout", scoring_notation="K", outs_added=1)
    inning = Narrator(random.Random(1)).render(event, before, after_inning)

    assert inning.transition_text == "That ends the top of the 5th. VIS 3, HST 1."

    final_before = replace(before, inning=9, away_score=3, home_score=4)
    final_state = replace(
        final_before,
        inning=9,
        bases=(None, None, None),
        result=GameResult("team-home", "regulation", 9, "top"),
    )
    final = Narrator(random.Random(1)).render(event, final_before, final_state)
    assert final.transition_text == "Final: VIS 3, HST 4. HST wins."


def test_walk_off_transition_is_factual_and_tts_ready():
    before = replace(initial_state(), inning=9, half="bottom", away_score=2, home_score=2)
    after = replace(
        before,
        home_score=3,
        result=GameResult("team-home", "walk_off", 9, "bottom"),
    )
    event = play(
        "home_run",
        scoring_notation="HR",
        runs_scored=1,
        batter_id="home-h1",
        pitcher_id="away-sp",
    )
    rendered = Narrator(random.Random(7)).render(event, before, after)

    assert rendered.transition_text == "HST wins on a walk-off. Final: VIS 2, HST 3."
    assert "HR" not in rendered.spoken_text
