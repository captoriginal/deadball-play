# Deadball Play — Implementation Plan

## Purpose

## Repository Strategy

All Version 1 work should occur inside the existing `deadball-play` repository.

Phase 0 should inspect the actual tree and establish the least disruptive monorepo-style boundaries, conceptually similar to:

```text
packages/
  deadball_generator/
  deadball_core/

apps/
  web/
  deadball_play/
```

Do not force those literal paths if the current repository already has a cleaner equivalent.

The important constraints are:

- reuse existing generator code
- introduce a reusable core for rules/state/events
- keep Deadball Play as a separate application layer
- preserve the existing web application
- avoid unnecessary cross-dependencies
- defer separate-repository extraction unless a future need justifies it

This document turns the current design into a concrete development sequence.

> **Phase numbering:** `phases.md` is the canonical execution sequence. This
> document contains a more granular architectural breakdown, so its phase
> numbers after Phase 0 do not map one-to-one to `phases.md`.

The plan assumes:

- the existing `deadball-play` generator remains the source of team/player ratings
- Version 1 targets Modern Era Deadball Second Edition
- the rules engine remains independent of the TUI
- the paper score sheet remains central
- computer-managed opposition uses Managerial Daring plus documented application procedures

The order is deliberately rules-first.

---

# Phase 0 — Repository and Package Structure

## Goal

Create the basic project/module structure without implementing game behavior.

## Tasks

- decide whether Deadball Play lives inside the existing repository or as a separate package
- create module boundaries for:
  - game data
  - state
  - rules
  - events
  - session
  - narration
  - TUI
  - tests
- add documentation
- define coding/test conventions

## Done When

- modules exist
- tests can run
- no UI code is required to import the rules engine
- documentation is linked from `README.md`

---

# Phase 1 — Game Data Contract

## Goal

Make the handoff from the existing generator explicit and validated.

## Tasks

Implement the schema described in `game-data-schema.md`.

Support:

- game metadata
- teams
- players
- lineup
- positions
- handedness
- BT
- OBT
- traits
- Pitch Dice
- pitcher roles
- starting pitchers

Add validation for:

- unique player IDs
- valid lineup slots
- legal positions
- handedness
- ratings
- traits
- Pitch Dice

## Dependencies

None beyond existing generator output.

## Done When

A generated game file can be loaded and validated without any network access.

---

# Phase 2 — Core Game State Model

## Goal

Represent a live game explicitly.

## Tasks

Create state objects for:

- inning
- half
- score
- outs
- bases
- batting-order index
- active pitcher
- active defense
- bench
- bullpen
- substitutions
- removed players
- pitcher counters
- ruleset configuration

Add safe copy/snapshot support.

## Dependencies

Phase 1.

## Done When

A game can be initialized from generated data and the complete current state can be serialized and restored.

---

# Phase 3 — Dice and Rules Infrastructure

## Goal

Create deterministic foundations for all rule resolution.

## Tasks

Implement:

- d4
- d6
- d8
- d12
- d20
- d100
- negative Pitch Dice
- injectable RNG
- RNG state capture/restore
- Pitch Die ladder
- trait identifiers
- rule-trace structure
- structured event base types

## Dependencies

Phase 2.

## Done When

Tests can inject exact dice and reproduce exact results.

---

# Phase 4 — Basic At-Bat Engine

## Goal

Resolve the simplest complete plate appearances.

## Tasks

Implement:

- handedness Pitch Die adjustment
- effective Pitch Die
- d100 + Pitch Die
- MSS
- BT hit threshold
- OBT walk threshold
- ordinary Out Table lookup
- strikeouts
- basic groundouts
- basic flyouts
- walks
- batting-order advancement

Do not yet implement all runner interactions.

## Dependencies

Phases 2–3.

## Done When

With empty bases, a normal at-bat can resolve deterministically from dice through structured event and state update.

---

# Phase 5 — Hit Table and Defense

## Goal

Implement the full hit-resolution path.

## Tasks

Implement:

- Modern Hit Table
- P+
- P++
- P-
- P--
- C+ special results
- S+ special results
- DEF checks
- D+
- D-
- errors
- hit reduction
- hit-to-out conversion
- critical hits

## Dependencies

Phase 4.

## Done When

Every Hit Table result and DEF boundary has automated tests.

---

# Phase 6 — Runner Advancement and Productive Outs

## Goal

Make ordinary plate appearances work with all base states.

## Tasks

Implement:

- runner advancement from Hit Table
- walks and forced movement
- error advancement
- productive outs
- sacrifice-fly behavior
- fielder's choices
- double plays
- triple-play conditions
- scoring
- third-out handling

Build runner-state matrix tests.

## Dependencies

Phases 4–5.

## Done When

Normal swing-away plate appearances work from all relevant base configurations and inning transitions are correct.

---

# Phase 7 — Tactical Offense

## Goal

Implement Deadball's active offensive decisions.

## Tasks

Implement:

- steal second
- steal third
- steal home
- double steal
- S+
- S-
- catcher D+
- catcher D-
- bunting
- C+/C- bunt modifiers
- hit-and-run
- hit-and-run BT/OBT modifiers
- Hit & Run Table

## Dependencies

Phase 6.

## Done When

The rules engine can expose and resolve all core tactical offensive choices.

---

# Phase 8 — Pitcher State and Fatigue

## Goal

Implement persistent pitching rules across innings.

## Tasks

Implement starter improvement:

- three consecutive scoreless innings
- strike out every batter in inning
- bases-loaded/no-out escape

Implement starter degradation:

- run-based reductions
- two-inning thresholds
- innings after six
- late-run rule

Implement reliever degradation:

- runs
- every three outs

Implement same-handed role ceilings.

## Dependencies

Phases 4–7.

## Done When

Scripted multi-inning tests reproduce every published Pitch Die change condition.

---

# Phase 9 — Substitutions

## Goal

Support complete roster management.

## Tasks

Implement:

- pinch hitters
- pinch runners
- pitching changes
- defensive substitutions
- position changes
- fixed batting-order slots
- no re-entry
- out-of-position D-
- UT exception

## Dependencies

Phases 2 and 6–8.

## Done When

A game can use bench and bullpen players without corrupting lineup or defensive state.

---

# Phase 10 — Full Game Structure

## Goal

Complete rules-engine support from first batter to final out.

## Tasks

Implement:

- half-inning transitions
- inning increment
- batting-order persistence
- home team no-bat ending
- extra innings
- walk-off completion
- game finalization

## Dependencies

Phases 4–9.

## Done When

Predetermined complete games can be played entirely through the rules API with no TUI.

---

# Phase 11 — Managerial Daring Opponent

## Goal

Allow one human to play against a computer-managed opponent.

## Tasks

Implement:

- Managerial Daring roll
- offensive trigger procedures
- steal decisions
- hit-and-run decisions
- bunt decisions
- starter-hook decisions
- post-sixth starter decisions
- reliever continuation decisions
- simple bullpen selection procedure

Keep published rules separate from Deadball Play trigger procedures.

## Dependencies

Phases 7–10.

## Done When

A scripted computer-managed team can complete a game without human intervention for its managerial decisions.

---

# Phase 12 — Session Layer

## Goal

Make games safe to stop and resume.

## Tasks

Implement:

- autosave
- manual save
- resume
- save-and-quit
- schema versioning
- crash-safe writes
- scorekeeping-confirmation state
- event history
- previous-state snapshot
- Undo
- RNG restoration

## Dependencies

Stable game state from Phases 2–10.

## Done When

A game can be interrupted at representative states and resumed exactly.

Undo restores the exact previous state.

---

# Phase 13 — Narration Layer

## Goal

Convert structured events into varied, accurate baseball language.

## Tasks

Implement template families for:

- strikeout
- walk
- single
- double
- triple
- home run
- groundout
- flyout
- productive out
- fielder's choice
- double play
- error
- stolen base
- caught stealing
- bunt
- hit-and-run
- pitching change
- inning transition
- game end

Add:

- context-sensitive variants
- recent-template avoidance
- stable scoring guidance
- no unsupported factual invention

## Dependencies

Structured events from earlier phases.

## Done When

Repeated games produce varied phrasing while structured results and scoring text remain exact.

---

# Phase 14 — TUI Prototype

## Goal

Create the playable terminal conductor.

## Tasks

Implement:

- score/inning/outs/bases display
- batter display
- pitcher display
- traits
- legal-action menu
- dice display
- narration
- scoring guidance
- scorekeeping pause
- recent-play history
- rule explanation
- lineup view
- bullpen view
- substitution flow
- save/quit
- undo

## Dependencies

Phases 10–13.

## Done When

A player can complete a full game with:

**computer + printed score sheet + pen**

without consulting the rulebook during ordinary play.

---

# Phase 15 — Full-Game Regression Suite

## Goal

Prove that all systems work together.

## Tasks

Create predetermined complete-game scripts covering:

- ordinary nine-inning game
- high-scoring game
- starter fatigue
- multiple relievers
- substitutions
- steals
- bunts
- hit-and-run
- DEF checks
- errors
- double plays
- extra innings
- walk-off ending

Compare:

- final score
- event sequence
- game state
- pitcher state
- batting-order state

## Dependencies

All core phases.

## Done When

Regression games run deterministically and all expected outcomes match.

---

# Phase 16 — Playtesting and Application-Procedure Tuning

## Goal

Evaluate how the program feels rather than changing published mechanics.

## Focus Areas

- frequency of computer steal attempts
- bunt trigger behavior
- hit-and-run trigger behavior
- bullpen decisions
- TUI information density
- scoring-pause pacing
- narration repetition
- keyboard ergonomics

## Important Constraint

If playtesting reveals a problem, determine whether it is:

1. a Deadball rules bug
2. a Deadball Play application-procedure problem
3. a presentation problem

Do not "fix" an awkward computer-manager behavior by quietly changing Deadball rules.

## Done When

The solo game feels coherent and the distinction between Deadball mechanics and application procedure remains intact.

---

# Phase 17 — Version 1 Release Criteria

Version 1 is ready when:

- generated game data loads offline
- all core Modern Era rules are implemented
- rules tests cover numeric boundaries and tables
- a complete game can run from first pitch to final out
- one human-managed side works
- one Daring-managed opponent works
- save/resume works
- Undo works
- narration varies without changing facts
- TUI provides scoring guidance
- paper scorekeeping remains comfortable
- several deterministic full-game regression tests pass

The final acceptance question is:

> Can a player take a generated score sheet, a pen, and a computer and comfortably play a complete faithful game of Deadball Second Edition without needing the rulebook or dice?

If yes, Version 1 has met its purpose.

---

# Deferred Until After Version 1

Do not block the first release on:

- Ancient Era
- season/campaign management
- graphical interface
- cloud sync
- advanced manager tendencies
- manual physical-dice mode
- full digital scorebook
- voice synthesis
- radio-style broadcast
- expanded house rules

These should build outward from the stable core.
