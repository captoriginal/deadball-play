# Deadball Play — Rules Engine

## Purpose

## Repository Placement

The rules engine should live in a reusable shared module/package inside the existing `deadball-play` repository, conceptually `deadball_core`.

Preferred dependency direction:

```text
deadball_generator -> game-data contract
deadball_play      -> deadball_core
web app            -> deadball_core   # optional/future
```

Avoid dependencies in the opposite direction:

```text
deadball_core -X-> deadball_play
deadball_core -X-> web frontend
deadball_core -X-> MLB API client
```

The core should consume generated Deadball game data and remain independently testable. Although it starts inside the monorepo, its boundary should be clean enough to extract or publish later if that becomes useful.

The rules engine is the authoritative implementation of *Deadball: Baseball With Dice, Second Edition* mechanics.

It should be usable independently of the TUI, narration system, save system, MLB data retrieval, or voice output.

The engine answers questions such as:

- What actions are legal now?
- What dice must be rolled?
- What does this roll mean under Deadball?
- Where do the runners end up?
- How many outs are recorded?
- Did a run score?
- How does the pitcher's Pitch Die change?
- Is the half-inning over?

It does not decide how these facts are worded for the player.

---

## Core Boundary

Conceptually:

```text
Game State + Player Action + RNG
              |
              v
         Rules Engine
              |
              v
Structured Result + New Game State
```

The engine must not depend on:

- terminal rendering
- prose templates
- speech synthesis
- save-file paths
- web APIs
- MLB data fetching
- user-interface state

---

## Inputs

A rules-engine action should receive explicit inputs.

Typical inputs include:

- current immutable or copyable game state
- requested action
- relevant player IDs
- ruleset configuration
- random-number source or explicit dice values

Example conceptual request:

```text
action: swing
batter_id: LAD_05
pitcher_id: SF_41
state_id: ...
```

For tests, dice should be injectable.

Example:

```text
d100 = 41
pitch_die_roll = 6
```

---

## Outputs

Every resolved action should return structured data.

At minimum:

```text
result
new_state
events
dice
rule_trace
```

Example:

```text
result:
  type: groundout
  scoring: "4-3"

runner_moves:
  runner_17:
    from: 1B
    to: 2B

outs_added: 1
runs_added: 0
```

Narration should be generated later.

---

## Game State Requirements

The state model should contain all information required to apply the rules without reading prior prose.

At minimum:

### Game

- inning number
- half-inning
- score
- outs
- bases
- ruleset configuration

### Teams

- lineup
- batting-order index
- active defensive alignment
- bench availability
- bullpen availability
- removed players

### Pitcher

- player ID
- starter or reliever
- base Pitch Die
- current Pitch Die
- handedness
- traits
- outs recorded
- runs allowed
- inning-level run state
- scoreless-inning streak
- fatigue/improvement state required by the rulebook

### Runners

Each occupied base should reference the actual player.

Do not store only "occupied = true."

---

## Legal Actions

The engine should expose legal actions for the current state.

Example:

```text
[
  "swing",
  "steal_second",
  "bunt",
  "hit_and_run",
  "pinch_hit",
  "pinch_run"
]
```

The TUI displays this list.

The TUI must not independently decide that an action is legal.

---

## Dice

Deadball Play should default to automatic dice.

The rules engine should nevertheless support an abstract random-number interface so that:

- tests can supply fixed results
- future manual dice entry is possible
- saved RNG state can support deterministic undo/replay

Supported dice include at least:

- d4
- d6
- d8
- d12
- d20
- d100

Negative Pitch Dice use the same die roll and subtract it from the Swing Score.

---

## RNG and Undo

Undo should restore the RNG state along with game state.

This prevents undo from becoming a reroll feature.

Given the same restored state and the same selected action, the same random sequence should normally reproduce the same result.

---

## Rule Resolution Pipeline

A normal at-bat should follow an explicit pipeline.

```text
validate action
      |
determine effective Pitch Die
      |
roll d100 and Pitch Die
      |
calculate MSS
      |
classify MSS
      |
resolve hit / walk / error / out
      |
apply Hit Table / DEF / Out Table as required
      |
resolve runner movement
      |
update score and outs
      |
update pitcher state
      |
check half-inning/game transition
      |
emit structured events
```

Each stage should be independently testable where practical.

---

## Rule Trace

For transparency and testing, resolved actions should be able to produce a rule trace.

Example:

```text
Swing Score: 50
Pitch Die: +6
MSS: 56
OBT: 39
MSS > OBT+5
Out Table last digit: 6 -> SS groundball
Runner on first
MSS 50-69 -> Fielder's Choice
```

This trace can power the TUI's `?` explanation.

It should not be the narration shown by default.

---

## Tables as Data

Where practical, published Deadball tables should be represented as explicit data or narrowly scoped functions.

Examples:

- Hit Table
- DEF table
- Out Table
- Bunting Table
- Hit & Run Table
- double-steal table
- Pitch Die ladder

Avoid scattering unexplained numeric constants throughout the code.

---

## Pitch Die Ladder

The Modern Era ladder should be represented in a form that supports stepping up or down:

```text
d20
d12
d8
d4
-d4
-d8
-d12
-d20
```

Pitcher role and rule-specific ceilings should be applied when changing levels.

---

## Traits

Traits should be explicit rule modifiers.

Do not encode them as vague player "abilities."

Examples:

```text
P+ -> Hit Table +1
P++ -> Hit Table +2
```

Trait handling should be centralized enough that tests can verify every trait effect.

---

## State Transactions

Every action should resolve as one transaction.

Conceptually:

```text
before_state
   |
resolve action
   |
after_state
```

The state should not be partially committed if resolution fails.

This supports:

- undo
- autosave
- debugging
- deterministic tests

---

## Game Transitions

The engine should determine:

- third out
- half-inning end
- inning increment
- home-team no-bat ending
- extra innings
- final game state

These should be rules-engine outcomes rather than TUI guesses.

---

## Application Procedure vs Deadball Rule

A small number of software decisions may be necessary even when the rulebook leaves judgment to the player.

These must be labeled in code/documentation.

Example:

```text
source: "deadball_rule"
```

versus:

```text
source: "deadball_play_procedure"
```

This distinction is especially important for computer-managed tactical decisions.

---

## Error Handling

The engine should reject impossible actions explicitly.

Examples:

- steal second with second base occupied
- pinch hit with no selected replacement
- use removed player
- re-enter substituted player
- bunt after plate appearance has already resolved

Do not silently repair invalid requests.

---

## No Generalized Baseball Engine

Deadball Play should not first build a generic baseball simulation and then attempt to configure it to resemble Deadball.

Encode Deadball directly.

This keeps:

- implementation smaller
- fidelity clearer
- testing easier
- future optional expansions separable

---

## Core Acceptance Test

The rules engine is successful if:

> Given a valid game state, a legal action, and deterministic dice, it always produces the same state transition that a careful reading of Deadball Second Edition would produce.
