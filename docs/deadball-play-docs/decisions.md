# Deadball Play — Architectural Decision Log

## ADR — Keep Deadball Play in the Existing `deadball-play` Repository

**Status:** Accepted

### Decision

Develop Deadball Play inside the existing `deadball-play` repository rather than creating a separate repository.

Treat the codebase as a small monorepo with clean conceptual boundaries for:

- `deadball_generator`
- `deadball_core`
- `deadball_play`
- the existing web application

### Rationale

Deadball Play reuses the current generator and generated game data. A separate repository would immediately create avoidable questions around internal package publication, dependency versioning, schema synchronization, shared integration tests, and duplicated code.

### Consequences

The exact directory layout will be chosen after inspecting the existing repository.

Shared repository placement does not justify tight coupling. `deadball_core` should remain independent of TUI, web frontend, and MLB API concerns.

Extraction into separate repositories or published packages remains possible later if there is a concrete benefit.

## ADR — Place New Python Packages Beside the Existing Generator

**Status:** Accepted

### Decision

Add `backend/deadball_core` and `backend/deadball_play` as sibling `src`-layout
Python packages beside `backend/deadball_generator`. Leave the existing FastAPI,
React, Tauri, and generator paths intact.

### Rationale

The repository already isolates the generator as a Python package under
`backend/`. Sibling packages establish the required boundaries without moving
working code into a speculative top-level monorepo layout.

### Consequences

- `deadball_core` has no dependency on application or generator code.
- `deadball_play` may depend on `deadball_core`.
- Generator integration uses a versioned data contract rather than internal
  imports from the core.
- The current web application does not need to change during Phase 0.

## Purpose

This document records important design decisions already made for **Deadball Play**.

The goal is to preserve project intent and avoid repeatedly revisiting settled architectural questions during implementation.

Each decision should include:

- decision
- rationale
- consequences
- status

---

## ADR-001 — Version 1 Implements Deadball Second Edition

**Status:** Accepted

### Decision

Version 1 should implement the Modern Era rules of *Deadball: Baseball With Dice, Second Edition* as written.

### Rationale

The project exists to facilitate Deadball, not to create a new baseball simulation inspired by it.

### Consequences

- Do not add more detailed baseball mechanics merely because software can support them.
- Optional rules must be explicit.
- Future expanded rules should remain separable from core Deadball.

---

## ADR-002 — The Paper Score Sheet Remains Central

**Status:** Accepted

### Decision

The software will not initially replace the handwritten score sheet with a full electronic scorebook.

### Rationale

Keeping score by hand is an important part of the intended experience. The player should still be able to look back over the score sheet and see the game unfold.

### Consequences

- The application pauses after completed plays.
- The TUI provides clear scoring notation.
- The software tracks enough internal state to conduct the game correctly.
- The paper score sheet remains the player's primary visible game artifact.

---

## ADR-003 — Existing Team Generation Is Reused

**Status:** Accepted

### Decision

Deadball Play consumes game/team data from the existing `deadball-play` generator rather than duplicating player-rating generation.

### Rationale

The existing project already converts MLB data into Deadball ratings.

### Consequences

- The new application needs a clear game-data contract.
- Once generated, game data should be playable offline.
- The active rules engine does not fetch or recalculate MLB ratings.

---

## ADR-004 — The Rules Engine Is Standalone

**Status:** Accepted

### Decision

Deadball mechanics should live in a standalone rules layer with no dependency on the TUI, narration, save files, or MLB APIs.

### Rationale

This improves fidelity, testing, portability, and future reuse.

### Consequences

The same engine could later support:

- TUI
- graphical frontend
- replay
- simulation
- voice/radio output
- automated tests

---

## ADR-005 — Structured Events Are the Interface Between Rules and Presentation

**Status:** Accepted

### Decision

The rules engine emits structured play events rather than prewritten narration strings.

### Rationale

Game facts should remain separate from wording.

### Consequences

Structured events can independently drive:

- terminal narration
- scoring guidance
- history
- replay
- future spoken play-by-play

---

## ADR-006 — Narration Has No Mechanical Authority

**Status:** Accepted

### Decision

Narration may describe the result but must never determine or alter it.

### Rationale

Language variation must not create rule drift.

### Consequences

Narration cannot invent:

- runner advancement
- defensive outcomes
- scoring changes
- unsupported fielding details
- pitch types
- player behavior not represented in the event

---

## ADR-007 — Narration Is Template-Driven by Default

**Status:** Accepted

### Decision

The initial narration system should use curated contextual templates rather than requiring an LLM for every play.

### Rationale

This provides:

- offline operation
- deterministic factual accuracy
- lower complexity
- lower cost
- controllable tone

### Consequences

Future LLM-assisted or TTS features can be layered on top without changing the rules engine.

---

## ADR-008 — Computer Opponent Uses Managerial Daring

**Status:** Accepted

### Decision

The computer-managed opponent should use Deadball's published Managerial Daring mechanic for risky/conservative decisions.

### Rationale

This preserves the game's intended solo-management mechanism.

### Consequences

Deadball Play still needs transparent application procedures for deciding when a steal, bunt, hit-and-run, pitching change, or similar decision should be considered.

Those triggers must be labeled as **Deadball Play application procedure**, not published Deadball rules.

---

## ADR-009 — Automate Procedure, Not Human Strategy

**Status:** Accepted

### Decision

The application should handle rules procedure while leaving meaningful human managerial choices to the player.

### Rationale

The goal is to reduce lookup and bookkeeping burden without turning the user into a passive observer.

### Consequences

The TUI should:

- present legal choices
- resolve rules
- avoid inventing extra strategy systems
- not make human-side decisions automatically unless explicitly configured

---

## ADR-010 — Save Frequently and Treat Interruption as Normal

**Status:** Accepted

### Decision

Autosave should occur after completed play transactions and other important state changes.

### Rationale

The application is intended for relaxed, portable use where interruption is normal.

### Consequences

A player should be able to:

- close the terminal
- sleep the laptop
- stop when a flight lands
- recover after a crash

without losing the game.

---

## ADR-011 — Undo Restores Exact Prior State

**Status:** Accepted

### Decision

Undo should restore the complete pre-action game state, including RNG state.

### Rationale

Trying to reverse individual mutations is fragile, and rerolling after Undo would create an unintended exploit.

### Consequences

Replaying the same action from the restored state should normally reproduce the same dice result unless another choice changes the sequence.

---

## ADR-012 — Use Structured History, Not Prose as the Record

**Status:** Accepted

### Decision

Game history should store structured events.

### Rationale

Presentation wording may vary. The factual event stream must remain stable.

### Consequences

History can support:

- recent plays
- undo
- debugging
- replay
- voice
- future exports

---

## ADR-013 — Hybrid Persistence

**Status:** Accepted

### Decision

Use current-state snapshots plus structured event history.

### Rationale

This is simpler than pure event sourcing while retaining auditability and future replay potential.

### Consequences

A session save should contain:

- current state
- structured events
- ruleset configuration
- enough prior state for supported Undo behavior

---

## ADR-014 — Legal Actions Come From the Rules Engine

**Status:** Accepted

### Decision

The rules engine determines which actions are legal in the current state.

### Rationale

The TUI should not independently implement baseball legality.

### Consequences

The interface renders the action list supplied by the engine.

---

## ADR-015 — Rule Explanations Are Available On Demand

**Status:** Accepted

### Decision

There will not initially be separate Guided and Compact game modes.

### Rationale

The normal screen should remain concise while still making every ruling inspectable.

### Consequences

A command such as `?` can show:

- dice
- MSS
- table lookup
- applicable traits
- exact rule path

---

## ADR-016 — Offline Play After Generation

**Status:** Accepted

### Decision

Once a game has been generated, conducting it should not require network access.

### Rationale

A key use case is portable play, including airplanes and travel.

### Consequences

The game-data package must include everything mechanically required for play.

---

## ADR-017 — Application Procedure Must Be Distinguished From Published Rules

**Status:** Accepted

### Decision

Whenever software requires a procedural choice that the rulebook does not fully specify, that behavior must be documented separately.

### Rationale

This preserves clarity about what is Deadball and what is Deadball Play.

### Consequences

This distinction is especially important for:

- computer-manager trigger logic
- bullpen-selection procedure
- any future convenience behavior

---

## ADR-018 — Future Expanded Rules Must Be Switchable

**Status:** Accepted

### Decision

House rules, alternate eras, or richer simulation systems must not silently replace the baseline ruleset.

### Rationale

Users should always be able to play faithful Second Edition Deadball.

### Consequences

Ruleset configuration should be explicit and stored with saved games.
