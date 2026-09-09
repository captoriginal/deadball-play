from dataclasses import replace

import pytest

from deadball_core import (
    FixedDice,
    SubstitutionError,
    initialize_game,
    load_generated_game,
    pitcher_may_be_replaced,
    pitching_change,
    pitching_opportunity,
    resolve_hit_and_run,
    resolve_swing,
)

from test_game_data import canonical_game


ODDITY_CASES = {
    2: [1, 1],
    3: [1, 2, 1],
    4: [2, 2, 50],
    5: [2, 3],
    6: [3, 3, 80, 1],
    7: [3, 4],
    8: [4, 4],
    9: [4, 5],
    10: [5, 5],
    11: [5, 6],
    12: [6, 6],
    13: [6, 7],
    14: [7, 7, 4],
    15: [7, 8],
    16: [8, 8, 80, 1],
    17: [8, 9, 80, 1],
    18: [9, 9],
    19: [9, 10],
    20: [10, 10],
}


def optional_state(*, oddities=False, three_batter_minimum=False):
    data = canonical_game()
    data["rules"]["oddities"] = oddities
    data["rules"]["three_batter_minimum"] = three_batter_minimum
    return initialize_game(load_generated_game(data))


@pytest.mark.parametrize(("total", "oddity_dice"), ODDITY_CASES.items())
def test_every_oddities_table_total_has_a_mechanical_path(total, oddity_dice):
    result = resolve_swing(
        optional_state(oddities=True),
        FixedDice([93, 6, *oddity_dice]),
    )

    assert result.dice.oddity_total == total
    assert result.event.oddity_name
    assert result.event.details


def test_three_batter_minimum_blocks_early_change_and_allows_third_batter():
    state = optional_state(three_batter_minimum=True)

    assert pitcher_may_be_replaced(state, "home") is False
    with pytest.raises(SubstitutionError, match="3 more batters"):
        pitching_change(state, "home", "home-rp")

    progress = replace(state.home.pitcher_state, batters_faced_since_entry=2)
    state = replace(state, home=replace(state.home, pitcher_state=progress))
    with pytest.raises(SubstitutionError, match="1 more batter"):
        pitching_change(state, "home", "home-rp")

    progress = replace(progress, batters_faced_since_entry=3)
    state = replace(state, home=replace(state.home, pitcher_state=progress))
    assert pitcher_may_be_replaced(state, "home") is True
    assert pitching_change(state, "home", "home-rp").new_state.home.active_pitcher_id == "home-rp"


def test_ending_the_half_inning_satisfies_the_minimum():
    state = replace(optional_state(three_batter_minimum=True), outs=2)
    result = resolve_swing(state, FixedDice([90, 2]))

    assert result.new_state.half == "bottom"
    assert result.new_state.home.pitcher_state.inning_end_removal_window is True
    assert pitcher_may_be_replaced(result.new_state, "home") is True

    continued = replace(result.new_state, half="top", inning=2)
    continued = resolve_swing(continued, FixedDice([90, 2])).new_state
    assert continued.home.pitcher_state.batters_faced_since_entry == 2
    assert continued.home.pitcher_state.inning_end_removal_window is False
    assert pitcher_may_be_replaced(continued, "home") is False


def test_disabled_three_batter_minimum_preserves_deadball_baseline():
    state = optional_state()
    assert pitching_change(state, "home", "home-rp").event.event_type == "pitching_change"


def test_computer_manager_does_not_offer_an_illegal_early_hook():
    state = optional_state(three_batter_minimum=True)
    progress = replace(state.home.pitcher_state, runs_allowed=4)
    state = replace(state, home=replace(state.home, pitcher_state=progress))

    assert pitching_opportunity(state, "home") is None


def test_hit_by_pitch_oddity_resolves_plate_appearance():
    state = optional_state(oddities=True)
    result = resolve_swing(state, FixedDice([93, 6, 5, 6]))

    assert result.event.oddity_name == "Hit by Pitch"
    assert result.event.resolved is True
    assert result.event.scoring_notation == "HBP"
    assert result.new_state.bases[0] == "away-h1"
    assert result.new_state.away.batting_order_index == 1
    assert result.new_state.home.pitcher_state.batters_faced_since_entry == 1


def test_hit_and_run_mss_also_routes_through_optional_oddities():
    state = replace(
        optional_state(oddities=True), bases=("away-h2", None, None)
    )
    result = resolve_hit_and_run(state, FixedDice([4, 93, 6, 5, 6]))

    assert result.event.oddity_name == "Hit by Pitch"
    assert result.event.resolved is True
    assert result.dice.swing.oddity_total == 11


def test_balk_advances_runners_without_consuming_plate_appearance():
    state = replace(optional_state(oddities=True), bases=(None, None, "away-h2"))
    result = resolve_swing(state, FixedDice([93, 6, 9, 10]))

    assert result.event.oddity_name == "Balk"
    assert result.event.resolved is False
    assert result.event.runs_scored == 1
    assert result.new_state.away_score == 1
    assert result.new_state.away.batting_order_index == 0
    assert result.new_state.home.pitcher_state.batters_faced_since_entry == 0


def test_pitcher_injury_is_a_three_batter_removal_exception():
    state = optional_state(oddities=True, three_batter_minimum=True)
    # MSS 99; Oddities 3+3; major injury 5, shoulder 2; 2d20 duration 4+5.
    result = resolve_swing(state, FixedDice([93, 6, 3, 3, 5, 2, 4, 5]))

    assert result.event.oddity_name == "Pitcher Appears Injured"
    assert result.new_state.home.pitcher_state.removal_exception == "injury"
    assert pitcher_may_be_replaced(result.new_state, "home") is True
    assert pitching_change(result.new_state, "home", "home-rp").new_state.home.active_pitcher_id == "home-rp"


def test_pitcher_distracted_bonus_applies_until_plate_appearance_ends():
    state = optional_state(oddities=True)
    result = resolve_swing(state, FixedDice([93, 6, 6, 7]))

    assert result.event.oddity_name == "Pitcher Distracted"
    assert result.new_state.oddity_state.steal_bonus_batter_id == "away-h1"


def test_video_replay_disregards_blown_call_at_first():
    state = optional_state(oddities=True)
    result = resolve_swing(state, FixedDice([93, 6, 4, 5]))

    assert result.event.oddity_name == "Call Blown at First"
    assert result.event.resolved is False
    assert result.new_state.away.batting_order_index == 0
    assert "Video replay" in result.event.details[0]
