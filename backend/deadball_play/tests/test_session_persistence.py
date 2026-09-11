from dataclasses import replace
import json
from pathlib import Path
import random
import sys

import pytest

from deadball_core import (
    InjuryRecord,
    OddityState,
    PitchDieAdjustment,
    PitcherOddityModifier,
    RandomDice,
    initialize_game,
    load_generated_game,
    pinch_hit,
    resolve_bunt,
    resolve_hit_and_run,
    resolve_steal,
    resolve_swing,
)
from deadball_play.session import (
    GameSession,
    RULESET_ID,
    SAVE_FORMAT_VERSION,
    SessionConfig,
    SessionError,
    SessionLoadError,
)


CORE_TESTS = Path(__file__).parents[2] / "deadball_core" / "tests"
if str(CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(CORE_TESTS))

from test_game_data import canonical_game


def initial_state():
    return initialize_game(load_generated_game(canonical_game()))


def seeded_session(*, path=None, state=None, seed=8675309):
    return GameSession(
        state or initial_state(),
        rng=RandomDice(random.Random(seed)),
        autosave_path=path,
    )


def swing(state, dice):
    return resolve_swing(state, dice)


def test_new_game_round_trips_before_first_pitch(tmp_path):
    path = tmp_path / "saves" / "active-game.json"
    session = seeded_session()
    session.save(path)
    restored = GameSession.load(path)

    assert restored.state == session.state
    assert restored.history == ()
    assert restored.rng.getstate() == session.rng.getstate()
    assert restored.autosave_path == path
    document = json.loads(path.read_text())
    assert document["save_format_version"] == SAVE_FORMAT_VERSION
    assert document["ruleset"] == RULESET_ID
    assert not list(path.parent.glob("*.tmp"))


def test_save_status_and_copy_preserve_active_autosave_path(tmp_path):
    autosave = tmp_path / "active.json"
    copy = tmp_path / "copy.json"
    session = seeded_session(path=autosave)

    assert not session.is_saved
    assert str(autosave) in session.autosave_status
    session.save()
    assert session.is_saved
    assert "Autosave: saved" in session.autosave_status

    session.save_copy(copy)

    assert copy.exists()
    assert session.autosave_path == autosave
    assert session.last_save_path == autosave
    assert session.is_saved


def test_active_oddities_state_round_trips(tmp_path):
    state = initial_state()
    state = replace(
        state,
        oddity_state=OddityState(
            last_out_fielder_id="home-h6",
            poor_defenders=("home-h6",),
            home_batting_penalty_inning=2,
            steal_bonus_batter_id="away-h1",
            catcher_steal_bonus_team_id=state.home.team_id,
            pitcher_modifiers=(
                PitcherOddityModifier("home-sp", -1, 1, "top"),
            ),
            injuries=(
                InjuryRecord(
                    "away-h1",
                    "superficial",
                    "wrist",
                    batting_penalty=5,
                    traits_nullified=True,
                ),
            ),
        ),
    )
    path = tmp_path / "oddities.json"
    seeded_session(state=state).save(path)

    assert GameSession.load(path).state == state


def test_completed_action_autosaves_structured_history_and_pending_confirmation(
    tmp_path,
):
    path = tmp_path / "active-game.json"
    session = seeded_session(path=path)
    result = session.perform(swing)

    assert path.exists()
    assert len(session.history) == 1
    assert session.history[0].event == result.event
    assert session.history[0].dice == result.dice
    assert session.history[0].rule_trace == result.rule_trace
    assert not session.scorekeeping_confirmed
    assert session.pending_event == result.event

    restored = GameSession.load(path)
    assert restored.state == session.state
    assert restored.history == session.history
    assert restored.pending_event == result.event
    with pytest.raises(SessionError, match="confirmation is pending"):
        restored.perform(swing)


def test_scorekeeping_confirmation_autosaves_and_allows_next_action(tmp_path):
    path = tmp_path / "active-game.json"
    session = seeded_session(path=path)
    session.perform(swing)
    session.confirm_scorekeeping()

    assert session.scorekeeping_confirmed
    assert session.pending_event is None
    assert GameSession.load(path).scorekeeping_confirmed
    session.perform(swing)
    assert len(session.history) == 2


def test_undo_restores_exact_state_and_rng_then_replays_same_result(tmp_path):
    path = tmp_path / "active-game.json"
    session = seeded_session(path=path)
    before = session.state
    first = session.perform(swing)
    session.confirm_scorekeeping()

    undone = session.undo()
    assert undone.event == first.event
    assert session.state == before
    assert session.history == ()
    assert GameSession.load(path).state == before

    replay = session.perform(swing)
    assert replay.event == first.event
    assert replay.dice == first.dice
    assert replay.new_state == first.new_state


def test_undo_before_confirmation_restores_previous_state():
    session = seeded_session()
    before = session.state
    session.perform(swing)

    session.undo()
    assert session.state == before
    assert session.scorekeeping_confirmed


def test_resumed_undo_restores_rng_for_identical_replay(tmp_path):
    path = tmp_path / "active-game.json"
    session = seeded_session(path=path)
    first = session.perform(swing)
    restored = GameSession.load(path)

    restored.undo()
    replay = restored.perform(swing)
    assert replay.dice == first.dice
    assert replay.new_state == first.new_state


def test_history_snapshots_support_multiple_undo_steps():
    session = seeded_session()
    initial = session.state
    session.perform(swing)
    after_first = session.state
    session.confirm_scorekeeping()
    session.perform(swing)
    session.confirm_scorekeeping()

    session.undo()
    assert session.state == after_first
    assert len(session.history) == 1
    session.undo()
    assert session.state == initial
    assert session.history == ()


def test_failed_action_restores_rng_and_does_not_create_history():
    session = seeded_session()
    rng_before = session.rng.getstate()

    def fail_after_roll(state, dice):
        dice.roll(20)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        session.perform(fail_after_roll)
    assert session.rng.getstate() == rng_before
    assert session.state == initial_state()
    assert session.history == ()


def test_substitution_and_manager_config_resume_exactly(tmp_path):
    path = tmp_path / "active-game.json"
    config = SessionConfig(
        away_control="computer",
        home_control="human",
        away_daring=16,
        home_daring=None,
    )
    session = seeded_session(path=path)
    session.update_config(config)
    session.perform(lambda state, dice: pinch_hit(state, "away-bench"))
    restored = GameSession.load(path)

    assert restored.config == config
    assert restored.state == session.state
    assert restored.history[0].event.event_type == "pinch_hit"
    assert restored.history[0].dice is None


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("steal", lambda state, dice: resolve_steal(state, "steal_second", dice)),
        ("bunt", lambda state, dice: resolve_bunt(state, dice)),
        ("hit-and-run", lambda state, dice: resolve_hit_and_run(state, dice)),
    ],
)
def test_all_tactical_event_and_dice_records_round_trip(tmp_path, name, action):
    state = replace(initial_state(), bases=("away-h2", None, None))
    path = tmp_path / f"{name}.json"
    session = seeded_session(path=path, state=state)
    session.perform(action)
    restored = GameSession.load(path)

    assert restored.state == session.state
    assert restored.history == session.history


def test_completed_game_is_saved_and_cannot_continue_after_resume(tmp_path):
    state = replace(
        initial_state(),
        inning=9,
        half="top",
        outs=2,
        away_score=0,
        home_score=1,
    )
    path = tmp_path / "final.json"
    session = seeded_session(path=path, state=state)
    session.perform(swing)
    restored = GameSession.load(path)

    assert restored.state.is_final
    with pytest.raises(SessionError, match="game is final"):
        restored.perform(swing)


def test_pitcher_counters_and_extra_inning_state_round_trip(tmp_path):
    state = initial_state()
    adjustment = PitchDieAdjustment(
        "starter_innings_fatigue", "d8", "d4", 10, "top"
    )
    pitcher = replace(
        state.home.pitcher_state,
        current_pitch_die="d4",
        outs_recorded=27,
        runs_allowed=3,
        completed_innings=9,
        adjustments=(adjustment,),
    )
    state = replace(
        state,
        inning=10,
        half="top",
        outs=1,
        away_score=3,
        home_score=3,
        bases=(None, "away-h2", None),
        home=replace(
            state.home,
            active_pitch_die="d4",
            pitcher_state=pitcher,
        ),
    )
    path = tmp_path / "extra-innings.json"
    seeded_session(state=state).save(path)

    assert GameSession.load(path).state == state


def test_save_rejects_obsolete_or_corrupt_data_without_modifying_file(tmp_path):
    path = tmp_path / "active-game.json"
    session = seeded_session()
    session.save(path)
    document = json.loads(path.read_text())
    document["save_format_version"] = 99
    path.write_text(json.dumps(document))
    obsolete = path.read_bytes()

    with pytest.raises(SessionLoadError, match="save_format_version"):
        GameSession.load(path)
    assert path.read_bytes() == obsolete

    path.write_text("{not json")
    corrupt = path.read_bytes()
    with pytest.raises(SessionLoadError, match="could not load"):
        GameSession.load(path)
    assert path.read_bytes() == corrupt


def test_load_rejects_state_that_would_corrupt_future_play(tmp_path):
    path = tmp_path / "active-game.json"
    seeded_session().save(path)
    document = json.loads(path.read_text())
    document["current_state"]["bases"] = ["away-h2", "away-h2", None]
    path.write_text(json.dumps(document))

    with pytest.raises(SessionLoadError, match="two bases"):
        GameSession.load(path)


@pytest.mark.parametrize(
    "config",
    [
        {"home_control": "robot"},
        {"home_daring": 20},
    ],
)
def test_session_config_validates_control_and_daring(config):
    with pytest.raises(SessionError):
        SessionConfig(**config)


def test_computer_control_uses_neutral_daring_when_omitted():
    config = SessionConfig(home_control="computer")

    assert config.home_daring == 10


def test_undo_and_confirmation_require_history():
    session = seeded_session()
    with pytest.raises(SessionError, match="no action"):
        session.undo()
    with pytest.raises(SessionError, match="no event"):
        session.confirm_scorekeeping()
