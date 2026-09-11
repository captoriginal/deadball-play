# Deadball Play — Repository Directive

You are working in the existing **`deadball-play` repository**.

**Do not create a new repository for Deadball Play.**

Treat `deadball-play` as a small monorepo with clean conceptual boundaries:

- `deadball_generator` — existing MLB-to-Deadball conversion and generated game data
- `deadball_core` — reusable game state, rules, legal actions, structured events, and rule traces
- `deadball_play` — TUI, narration, scoring guidance, saves, undo, history, and interaction
- existing web application — separate consumer/application

A conceptual layout such as `packages/deadball_core` and `apps/deadball_play` is acceptable, but **do not force a directory layout before inspecting the actual repository**. Prefer the least disruptive structure that preserves current behavior.

Keep dependency direction clean. In particular, `deadball_core` must not depend on the TUI, web frontend, or MLB API implementation. Deadball Play should consume the generator through a clear generated-game data contract rather than reaching arbitrarily into generator internals.

Separate repositories or published packages may be considered later if there is a concrete benefit, but they are not part of Version 1.

---

# Deadball Play — Originating Codex Prompt

You are working on **Deadball Play**, a terminal-based digital facilitator for *Deadball: Baseball With Dice, Second Edition*.

The project already has substantial planning documentation and an existing MLB-to-Deadball team/game generator.

Before making changes, read the project documentation and inspect the existing repository.

## Source Priority

Use sources in this order:

1. **Deadball Second Edition rulebook** — authoritative for game rules
2. **Project documentation** — authoritative for architecture, scope, and application behavior
3. **Existing repository code** — authoritative for the current generator and project structure

If the rulebook and project documentation disagree about a game rule:

- follow the rulebook
- note the discrepancy
- update the documentation if appropriate

Do not silently "correct" Deadball using general baseball knowledge.

## Core Project Goal

Deadball Play should allow one person to sit down with:

**computer + printed score sheet + pen**

and play a complete, faithful Modern Era game of Deadball Second Edition.

The computer handles procedural work:

- dice
- arithmetic
- table lookups
- rule modifiers
- required runner movement
- pitcher-state bookkeeping
- legal-action filtering
- computer-manager Daring decisions
- scoring guidance

The human player keeps score on paper and makes the meaningful managerial choices that Deadball gives them.

The program should pause after completed plays so the player has time to score them.

## Critical Design Constraint

This is **Deadball**, not a generalized baseball simulator.

Do not add mechanics merely because they seem more realistic.

Do not add:

- pitch-by-pitch simulation
- discretionary send/hold mechanics not in Deadball
- hidden sabermetric strategy
- extra defensive mechanics
- pitch types
- Statcast-style modeling
- other baseball rules not supported by the Deadball ruleset

Any future expanded rules must remain explicitly separate from the core Second Edition rules.

## Architecture

Keep these responsibilities separate.

### Team / Data Layer

The existing `deadball-play` generator supplies:

- game/team data
- players
- lineups
- positions
- handedness
- BT
- OBT
- Pitch Dice
- traits

Do not duplicate MLB data retrieval or player-rating generation inside the rules engine.

### Rules Engine

The rules engine is the authoritative implementation of Deadball mechanics.

It should be usable without:

- TUI
- narration
- save files
- MLB APIs
- voice output

Conceptually:

```text
Game State + Legal Action + RNG
              |
              v
         Rules Engine
              |
              v
Structured Result + New State
```

The rules engine should determine legal actions.

The TUI should display those actions, not independently decide legality.

### Structured Events

Rules results should be emitted as structured events.

Example conceptually:

```text
event_type: groundout
batter: Freddie Freeman
fielded_by: SS
putout_by: 1B
runner_advances:
  Mookie Betts: 1B -> 2B
outs_added: 1
runs_scored: 0
score_notation: "6-3"
```

Do not make prose strings the authoritative game record.

### Presentation

Narration may vary, but it has **zero mechanical authority**.

The presentation layer may turn the structured event into:

```text
Freeman grounds to short.
Betts moves up to second.

Score: 6-3
```

A future TTS/radio layer should be able to consume the same structured events.

### Session/System

The system layer handles:

- autosave
- resume
- Undo
- history
- configuration
- crash recovery

Undo should restore complete pre-action state, including RNG state, so Undo does not become a reroll mechanism.

## Paper Score Sheet

Do not turn Version 1 into a full electronic scorebook.

The application tracks enough state to conduct the game correctly, but the physical score sheet remains central.

After a resolved play, the expected flow is:

```text
d100: 41
Pitch Die: 6
MSS: 47

Freeman grounds to second.
Betts advances to second.

Score: 4-3

Press Enter when scored.
```

The program should not advance until the player confirms.

## Solo Opponent

The computer opponent should use Deadball's published **Managerial Daring** rule.

Important distinction:

- the Daring roll is a Deadball rule
- deciding *when* the computer should consider a steal, bunt, hit-and-run, pitching change, etc. is Deadball Play application procedure

Keep those concepts separate in code, tests, and documentation.

Do not build a complex baseball AI for Version 1.

## Testing Requirement

Deadball is table-driven and should be tested deterministically.

For every rules phase:

- inject exact dice
- test numeric boundaries
- test state transitions
- test rule tables
- reference the rulebook section/page where useful
- add regression tests for any discovered bug

Do not accept "looks plausible" as proof of correctness.

## Development Method

Work in small phases.

Read `phases.md` and implement **only the current phase** unless explicitly asked to continue.

A phase should normally contain:

1. relevant rulebook review
2. implementation
3. deterministic tests
4. boundary tests
5. state-transition tests
6. documentation corrections if needed

Do not attempt to implement the entire rules engine in one pass.

## Starting Task

Begin with **Phase 0** from `phases.md`.

For Phase 0:

1. inspect the repository structure
2. inspect the current `deadball-play` generator and its output path
3. read the project documentation
4. identify where the new Deadball Play modules should live
5. propose the smallest clean package/module structure
6. identify any conflicts between the existing codebase and the documented architecture
7. implement only the minimal scaffolding needed for Phase 0
8. do not implement gameplay rules yet

Before editing, provide a concise implementation plan for Phase 0 based on the actual repository.

When Phase 0 is complete, stop and summarize:

- files added or changed
- architecture established
- tests added
- issues discovered
- anything that should be updated in the docs
- what Phase 1 will need

Do not continue into Phase 1 unless explicitly requested.
