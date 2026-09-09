from dataclasses import replace

import pytest

from deadball_core.dice import DiceError, FixedDice
from deadball_core.game_data import load_generated_game
from deadball_core.rules import (
    AtBatClassification,
    classify_mss,
    effective_pitch_die,
    out_table_result,
    resolve_swing,
)
from deadball_core.state import initialize_game
from test_game_data import canonical_game


@pytest.mark.parametrize(
    ("mss", "expected"),
    [
        (5, AtBatClassification.CRITICAL_HIT),
        (6, AtBatClassification.ORDINARY_HIT),
        (30, AtBatClassification.ORDINARY_HIT),
        (31, AtBatClassification.WALK),
        (39, AtBatClassification.WALK),
        (40, AtBatClassification.POSSIBLE_ERROR),
        (44, AtBatClassification.POSSIBLE_ERROR),
        (45, AtBatClassification.OUT),
        (70, AtBatClassification.OUT),
        (99, AtBatClassification.OUT),
        (100, AtBatClassification.OUT),
    ],
)
def test_swing_result_boundaries(mss, expected):
    assert classify_mss(mss, bt=30, obt=39) == expected


def test_optional_oddities_do_not_replace_default_results():
    assert classify_mss(1, 30, 39) == AtBatClassification.CRITICAL_HIT
    assert classify_mss(99, 30, 39) == AtBatClassification.OUT
    assert classify_mss(1, 30, 39, oddities=True) == AtBatClassification.ODDITY
    assert classify_mss(99, 30, 39, oddities=True) == AtBatClassification.ODDITY


@pytest.mark.parametrize(
    ("base", "role", "throws", "bats", "expected"),
    [
        ("-d4", "starter", "R", "R", "d4"),
        ("d8", "starter", "R", "R", "d12"),
        ("d12", "starter", "L", "L", "d12"),
        ("d20", "starter", "L", "L", "d20"),
        ("d12", "reliever", "R", "R", "d20"),
        ("d8", "starter", "R", "L", "d8"),
        ("d8", "starter", "R", "S", "d8"),
    ],
)
def test_same_handed_pitch_die_ladder_and_ceilings(base, role, throws, bats, expected):
    assert effective_pitch_die(base, role, throws, bats) == expected


@pytest.mark.parametrize(
    ("digit", "event_type", "fielder", "notation"),
    [
        (0, "strikeout", None, "K"),
        (1, "strikeout", None, "K"),
        (2, "strikeout", None, "K"),
        (3, "groundout", "1B", "G-3"),
        (4, "groundout", "2B", "4-3"),
        (5, "groundout", "3B", "5-3"),
        (6, "groundout", "SS", "6-3"),
        (7, "flyout", "LF", "F-7"),
        (8, "flyout", "CF", "F-8"),
        (9, "flyout", "RF", "F-9"),
    ],
)
def test_every_out_table_digit(digit, event_type, fielder, notation):
    result = out_table_result(70 + digit)
    assert (result.event_type, result.fielded_by, result.scoring_notation) == (
        event_type, fielder, notation,
    )


def test_possible_error_strikeout_digits_use_infield_locations():
    assert out_table_result(40, possible_error=True).fielded_by == "SS"
    assert out_table_result(41, possible_error=True).fielded_by == "SS"
    assert out_table_result(42, possible_error=True).fielded_by == "2B"


def test_walk_is_a_complete_state_transaction():
    state = initial_state()
    result = resolve_swing(state, FixedDice([25, 6]))  # MSS 31: BT+1

    assert result.event.event_type == "walk"
    assert result.event.resolved is True
    assert result.new_state.bases == ("away-h1", None, None)
    assert result.new_state.away.batting_order_index == 1
    assert result.dice.mss == 31


def test_out_is_structured_and_advances_state():
    state = initial_state()
    result = resolve_swing(state, FixedDice([69, 6]))  # MSS 75

    assert result.event.event_type == "groundout"
    assert result.event.fielded_by == "3B"
    assert result.event.scoring_notation == "5-3"
    assert result.event.outs_added == 1
    assert result.new_state.outs == 1
    assert result.new_state.away.batting_order_index == 1
    assert result.rule_trace[-1].stage == "out_table"


def test_third_out_changes_half_inning_and_preserves_order_progress():
    state = replace(initial_state(), outs=2)
    result = resolve_swing(state, FixedDice([64, 6]))  # MSS 70

    assert result.new_state.inning == 1
    assert result.new_state.half == "bottom"
    assert result.new_state.outs == 0
    assert result.new_state.away.batting_order_index == 1


def test_bottom_third_out_starts_next_inning_and_lineup_wraps():
    state = initial_state()
    state = replace(
        state,
        inning=3,
        half="bottom",
        outs=2,
        home=replace(state.home, batting_order_index=8),
    )
    result = resolve_swing(state, FixedDice([64, 6]))

    assert (result.new_state.inning, result.new_state.half, result.new_state.outs) == (4, "top", 0)
    assert result.new_state.home.batting_order_index == 0


def test_possible_error_event_identifies_special_zero_digit_fielder():
    result = resolve_swing(initial_state(), FixedDice([34, 6, 3]))  # MSS 40

    assert result.event.event_type == "groundout"
    assert result.event.fielded_by == "SS"


def test_resolver_applies_same_handed_adjustment_before_rolling():
    state = initial_state()
    state = replace(state, away=replace(state.away, batting_order_index=1))
    result = resolve_swing(state, FixedDice([30, 12, 3]))

    assert result.dice.pitch_die == "d12"
    assert result.dice.mss == 42


def test_enabled_oddity_rolls_table_and_can_leave_at_bat_pending():
    state = initial_state()
    source = replace(state.source, rules=replace(state.source.rules, oddities=True))
    state = replace(state, source=source)
    result = resolve_swing(state, FixedDice([93, 6, 2, 2, 50]))

    assert result.event.event_type == "oddity_rain_delay"
    assert result.event.oddity_name == "Rain Delay"
    assert result.event.resolved is False
    assert result.new_state == state
    assert result.dice.oddity_rolls == (2, 2)
    assert result.dice.oddity_detail_rolls == (("rain_delay_d100", 50),)


def test_negative_pitch_die_is_subtracted():
    state = initial_state()
    state = replace(state, home=replace(state.home, active_pitch_die="-d8"))
    result = resolve_swing(state, FixedDice([50, 6, 3]))

    assert result.dice.signed_pitch_value == -6
    assert result.dice.mss == 44


def test_fixed_dice_rejects_impossible_results():
    with pytest.raises(DiceError, match="between 1 and 8"):
        FixedDice([9]).roll(8)


def initial_state():
    return initialize_game(load_generated_game(canonical_game()))
