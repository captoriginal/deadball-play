from dataclasses import replace

import pytest

from deadball_core import (
    FixedDice,
    SubstitutionError,
    initialize_game,
    legal_actions,
    load_generated_game,
    pinch_hit,
    pinch_run,
    resolve_steal,
    resolve_swing,
)
from deadball_core.rules import RulesError

from test_game_data import canonical_game


def game_state(**changes):
    state = initialize_game(load_generated_game(canonical_game()))
    return replace(state, **changes)


def record_out(state):
    return resolve_swing(state, FixedDice([90, 1])).new_state


def hit_home_run(state):
    return resolve_swing(state, FixedDice([20, 1, 18])).new_state


def test_third_out_in_top_nine_ends_game_when_home_team_leads():
    state = game_state(
        inning=9,
        half="top",
        outs=2,
        away_score=2,
        home_score=3,
        bases=("away-h2", None, "away-h3"),
    )
    final = record_out(state)

    assert final.is_final
    assert final.result is not None
    assert final.result.winner_team_id == "team-home"
    assert final.result.ending == "regulation"
    assert (final.inning, final.half, final.outs) == (9, "top", 3)
    assert final.bases == (None, None, None)


def test_tied_top_nine_continues_to_bottom_half():
    state = game_state(inning=9, half="top", outs=2, away_score=3, home_score=3)
    continued = record_out(state)

    assert not continued.is_final
    assert (continued.inning, continued.half, continued.outs) == (9, "bottom", 0)


def test_away_team_wins_after_bottom_nine_final_out():
    state = game_state(
        inning=9,
        half="bottom",
        outs=2,
        away_score=4,
        home_score=3,
        bases=(None, "home-h2", None),
    )
    final = record_out(state)

    assert final.result is not None
    assert final.result.winner_team_id == "team-away"
    assert final.result.ending == "regulation"
    assert (final.inning, final.half, final.outs) == (9, "bottom", 3)
    assert final.bases == (None, None, None)


def test_tie_after_nine_starts_the_tenth():
    state = game_state(inning=9, half="bottom", outs=2, away_score=4, home_score=4)
    extra = record_out(state)

    assert not extra.is_final
    assert (extra.inning, extra.half, extra.outs) == (10, "top", 0)
    assert extra.bases == (None, "away-h9", None)
    substituted = pinch_run(extra, "2B", "away-bench").new_state
    assert substituted.bases == (None, "away-bench", None)
    assert substituted.away.lineup[8] == "away-bench"


def test_regular_season_extra_half_uses_player_before_due_batter():
    state = game_state(
        inning=10,
        half="top",
        outs=2,
        away_score=4,
        home_score=4,
        home=replace(game_state().home, batting_order_index=4),
    )

    bottom = record_out(state)

    assert (bottom.inning, bottom.half, bottom.outs) == (10, "bottom", 0)
    assert bottom.bases == (None, "home-h4", None)


@pytest.mark.parametrize("game_type", ["D", "L", "W", "C", "F", "S", "E"])
def test_non_regular_mlb_games_start_extra_innings_with_empty_bases(game_type):
    data = canonical_game()
    data["game"]["game_type"] = game_type
    state = initialize_game(load_generated_game(data))
    state = replace(
        state,
        inning=9,
        half="bottom",
        outs=2,
        away_score=4,
        home_score=4,
    )

    extra = record_out(state)

    assert (extra.inning, extra.half) == (10, "top")
    assert extra.bases == (None, None, None)


def test_extra_inning_final_out_records_extra_innings_ending():
    state = game_state(
        inning=11,
        half="bottom",
        outs=2,
        away_score=6,
        home_score=5,
    )
    final = record_out(state)

    assert final.result is not None
    assert final.result.winner_team_id == "team-away"
    assert final.result.ending == "extra_innings"
    assert final.result.inning == 11


@pytest.mark.parametrize("inning", [9, 12])
def test_home_run_that_breaks_bottom_half_tie_is_walk_off(inning):
    state = game_state(
        inning=inning,
        half="bottom",
        outs=1,
        away_score=5,
        home_score=5,
        bases=(None, None, None),
    )
    final = hit_home_run(state)

    assert final.result is not None
    assert final.result.winner_team_id == "team-home"
    assert final.result.ending == "walk_off"
    assert (final.inning, final.half, final.outs) == (inning, "bottom", 1)
    assert (final.away_score, final.home_score) == (5, 6)
    assert final.bases == (None, None, None)


def test_home_team_taking_lead_before_ninth_does_not_end_game():
    state = game_state(
        inning=8,
        half="bottom",
        away_score=5,
        home_score=5,
    )
    continued = hit_home_run(state)

    assert not continued.is_final
    assert (continued.inning, continued.half, continued.home_score) == (8, "bottom", 6)


def test_steal_home_can_end_game_without_advancing_batting_order():
    data = canonical_game()
    data["teams"]["home"]["roster"][1]["traits"] = ["S+"]
    state = initialize_game(load_generated_game(data))
    state = replace(
        state,
        inning=9,
        half="bottom",
        away_score=2,
        home_score=2,
        bases=(None, None, "home-h2"),
        home=replace(state.home, batting_order_index=4),
    )
    final = resolve_steal(state, "steal_home", FixedDice([7])).new_state

    assert final.result is not None
    assert final.result.ending == "walk_off"
    assert final.home.batting_order_index == 4
    assert final.home_score == 3


def test_batting_order_carries_across_both_half_inning_transitions():
    state = game_state(
        inning=5,
        half="top",
        outs=2,
        away=replace(game_state().away, batting_order_index=7),
        home=replace(game_state().home, batting_order_index=4),
    )
    bottom = record_out(state)
    assert bottom.away.batting_order_index == 8
    assert bottom.home.batting_order_index == 4

    next_inning = record_out(replace(bottom, outs=2))
    assert (next_inning.inning, next_inning.half) == (6, "top")
    assert next_inning.away.batting_order_index == 8
    assert next_inning.home.batting_order_index == 5


def test_final_game_exposes_no_legal_actions_or_roster_moves():
    final = record_out(
        game_state(
            inning=9,
            half="top",
            outs=2,
            away_score=0,
            home_score=1,
        )
    )

    assert legal_actions(final) == ()
    with pytest.raises(RulesError, match="game is final"):
        resolve_swing(final, FixedDice([90, 1]))
    with pytest.raises(SubstitutionError, match="game is final"):
        pinch_hit(final, "away-bench")


def test_final_third_out_completes_pitcher_inning_state():
    state = game_state(
        inning=9,
        half="top",
        outs=2,
        away_score=0,
        home_score=1,
    )
    pitcher = replace(state.home.pitcher_state, outs_recorded=2)
    state = replace(state, home=replace(state.home, pitcher_state=pitcher))
    final = record_out(state)

    assert final.home.pitcher_state is not None
    assert final.home.pitcher_state.outs_recorded == 3
    assert final.home.pitcher_state.completed_innings == 1
