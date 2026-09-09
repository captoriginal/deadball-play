# Deadball Play — Session and System Design

## Purpose

This document defines the session-management and persistence behavior for **Deadball Play**.

The session layer is responsible for keeping the game safe and recoverable without becoming part of the baseball rules.

Its responsibilities include:

- save
- resume
- autosave
- undo
- event history
- configuration
- crash recovery
- game initialization
- session lifecycle

The rules engine remains the authority for Deadball mechanics.

---

## Design Principles

### Playing Should Feel Low-Risk

The user should not worry about losing a game because:

- the terminal closed
- the laptop slept
- a flight ended
- the program crashed
- the user pressed the wrong choice

The system should save frequently and make recovery simple.

### Do Not Replace the Paper Score Sheet

The application must track enough state to continue the game correctly.

However, the physical score sheet remains the user's primary scorekeeping artifact.

The program's internal state is authoritative for:

- current inning
- outs
- runners
- batting-order position
- score
- active players
- substitutions
- pitcher state
- rules-required history

The paper score sheet is authoritative for the user's handwritten record and enjoyment of the game.

If the two disagree, the application should allow correction rather than assuming the paper sheet is wrong.

---

## Session State

A saved game should contain everything required to resume play exactly.

At minimum:

### Game Identity

- game ID
- date
- teams
- source/generated roster identifiers
- ruleset version
- application version where useful

### Score State

- inning
- half
- away score
- home score
- outs
- base occupants

### Batting State

- batting order for each team
- current batting-order index
- active substitutions
- removed players
- pinch hitters/runners where relevant

### Defensive State

- current defensive positions
- active pitcher
- fielding substitutions
- out-of-position status where relevant

### Pitcher State

- base Pitch Die
- current Pitch Die
- starter/reliever role
- innings/outs recorded
- runs allowed
- scoreless-inning streaks
- fatigue-related counters
- other rule-required pitching state

### Manager State

- human/computer control
- Managerial Daring
- temporary Daring adjustments if implemented according to the rules
- any other rule-required manager state

### Event History

- chronological structured events
- completed plate appearances
- substitutions
- steals
- pitching changes
- inning transitions

---

## Autosave

Autosave should be the default behavior.

### Primary Autosave Point

Save after every **completed play transaction**.

A completed play means:

1. the rules engine has finalized the outcome
2. game state has been updated
3. the structured event has been recorded

The scorekeeping confirmation may be recorded separately so the application can know whether the user has acknowledged the play.

### Additional Autosave Points

Autosave should also occur after:

- lineup confirmation
- substitutions
- pitching changes
- inning transitions
- configuration changes that affect the active game
- explicit quit

---

## Scorekeeping Confirmation State

Because the player records results on paper, the application should distinguish:

```text
play_resolved = true
scorekeeping_confirmed = false
```

from:

```text
play_resolved = true
scorekeeping_confirmed = true
```

This helps with interrupted sessions.

If the computer closes while the TUI is showing:

```text
Score: 6-3
Press Enter when scored.
```

then resume should return to that same screen rather than advancing to the next batter.

---

## Resume

When Deadball Play starts and an unfinished game exists, it should offer a simple resume path.

Example:

```text
Unfinished game found

Dodgers 3 — Giants 2
Top 6th
1 out
Runner on 2nd

[R] Resume
[N] New game
```

If a play had been resolved but not acknowledged, resume should display that play and its scoring guidance first.

---

## Manual Save

Manual save may be available, but autosave should make it rarely necessary.

A command such as:

```text
S
```

may save immediately and briefly display:

```text
Game saved.
```

Manual saving should not create multiple confusing save files by default.

---

## Save and Quit

A quit command should:

1. save the current session
2. verify the save completed
3. exit cleanly

Example:

```text
Game saved.

Resume later from:
Top 6th, 1 out, runner on second.
```

Do not require confirmation for every normal quit if autosave is reliable, though an accidental-quit guard may be useful.

---

## Undo

Undo is important because Deadball Play is interactive and keyboard-driven.

### Core Rule

Undo should restore the complete game state to immediately before the most recent completed action.

An action may be:

- plate appearance
- steal attempt
- bunt
- hit-and-run
- substitution
- pitching change

### Transactional State

Before each action begins, capture a restorable state.

Conceptually:

```text
STATE BEFORE ACTION
        |
        v
PLAYER DECISION
        |
        v
RULE RESOLUTION
        |
        v
STATE AFTER ACTION
```

Undo restores `STATE BEFORE ACTION`.

This is safer than trying to reverse every individual state mutation.

---

## Undo After Scorekeeping Confirmation

Undo should still be possible after the player presses Enter to confirm scoring.

The application should show what will be reversed.

Example:

```text
Undo last play?

Freeman grounded 6-3.
Betts advanced to second.

[Y] Yes
[N] No
```

After undo, the player is responsible for correcting the paper score sheet.

The application should remind them:

```text
Play undone.
Please correct the paper score sheet.
```

---

## Undo Before Scorekeeping Confirmation

If the play has resolved but the user has not yet confirmed scoring, Undo should simply restore the previous state.

This may be the most common correction case.

---

## Undo Depth

Version 1 may support only **one-step undo** if that simplifies implementation.

However, the internal event/snapshot design should avoid making deeper undo impossible later.

If full history snapshots are inexpensive, multiple-step undo may be reasonable.

The UI should never imply more undo depth than is actually supported.

---

## Event History

Every meaningful game action should create a structured history event.

Examples:

```text
game_start
lineup_confirmed
half_inning_start
plate_appearance
steal_attempt
bunt
hit_and_run
pinch_hit
pinch_run
pitching_change
defensive_substitution
half_inning_end
game_end
```

The event record should contain facts, not narration strings.

Example:

```text
event_type: groundout
batter_id: player_123
fielder: SS
putout: 1B
runner_moves:
  runner_456: 1B -> 2B
outs_added: 1
score_delta: 0
```

Narration may be regenerated later from this event.

---

## Event History Uses

Structured history supports:

- recent play display
- full play history
- undo
- debugging
- rule verification
- crash recovery
- future replay
- future spoken broadcast
- possible game export

---

## Snapshot vs Event Sourcing

Two broad persistence strategies are possible.

### State Snapshots

Save the entire current game state after every completed action.

Advantages:

- simple resume
- simple implementation
- easy exact restoration

Disadvantages:

- history still needs separate storage
- deeper undo may require multiple snapshots

### Event Sourcing

Save the initial state plus every event and rebuild current state by replaying events.

Advantages:

- excellent audit trail
- natural history
- powerful replay/debugging

Disadvantages:

- more complex
- requires deterministic replay
- migrations can become harder

### Recommended Initial Approach

Use a **hybrid**:

- save the complete current game-state snapshot
- also append structured events to history
- optionally retain the previous snapshot for Undo

This is simpler and robust while keeping future possibilities open.

---

## Crash Recovery

The application should assume interruption is normal.

Recovery should work after:

- terminal closure
- application crash
- system sleep
- laptop power loss after the last completed autosave

Use safe write behavior where practical:

1. write new save data to a temporary file
2. flush/close
3. replace the prior save atomically

This reduces the chance of a partially written save corrupting the session.

---

## Save File Versioning

Saved games should include a schema version.

Example:

```text
save_format_version: 1
ruleset: deadball_second_edition_modern
```

This allows future versions of the program to migrate older saves deliberately.

Rules changes should never silently reinterpret an existing game.

---

## Ruleset Identity

The active game should record which rules are enabled.

For example:

```text
rules:
  edition: second
  era: modern
  oddities: true
  injuries: false
  managerial_daring: true
```

This becomes more important if optional rules are added later.

---

## Configuration

Configuration should be separate from saved game state where possible.

Possible user preferences include:

- default human-controlled side
- automatic dice vs future manual dice
- enabled optional rules
- terminal color settings
- narration preferences
- autosave behavior

A saved game should retain any setting that changes game mechanics.

Pure presentation preferences may remain global.

---

## Randomness and Reproducibility

If practical, the random-number generator state or seed should be stored.

Benefits include:

- exact debugging
- deterministic test reproduction
- possible replay tools

Undo should not accidentally reroll a different result unless the design explicitly intends that behavior.

A useful policy is:

> Undo restores the pre-action state, including RNG state.

Then replaying the same action without changing anything produces the same dice result.

This prevents Undo from becoming a way to reroll unfavorable outcomes.

---

## History and Narration

History should store structured events, not only the text originally shown.

This allows:

- different narration templates on replay
- future spoken narration
- improved rendering in later application versions

If desired, the exact originally displayed narration may also be stored as metadata, but it should not be the authoritative game record.

---

## Completed Games

When the final out is recorded:

1. mark the game complete
2. save final state
3. retain the event history
4. prevent accidental continuation
5. display the final score

The paper score sheet remains the player's primary permanent artifact.

A completed game may later be archived or exported, but that is outside the initial scope.

---

## Starting a New Game While One Is Active

The application should protect unfinished games.

If the player chooses New Game while a game is active:

```text
An unfinished game is saved.

Dodgers 3 — Giants 2
Top 6th

Start a new game anyway? [y/N]
```

The existing game should remain resumable unless the user explicitly discards it.

---

## Error Handling

If game state cannot be loaded safely:

- do not silently discard it
- preserve the save file
- show a useful error
- offer recovery from the previous snapshot if available

The application should favor preserving data over attempting aggressive automatic repair.

---

## Logging

Diagnostic logging may record:

- application errors
- failed saves
- invalid state transitions
- rules-engine exceptions

Logs should remain separate from game history.

Normal play-by-play does not need to be duplicated into technical logs.

---

## Non-Goals

The initial session layer is not intended to provide:

- cloud synchronization
- multiplayer networking
- accounts
- season databases
- elaborate save-slot management
- automatic reconciliation with handwritten score sheets

These may be considered later if there is a clear need.

---

## Core Acceptance Test

The session system is successful if:

> The player can stop at essentially any point, close the computer, resume later at the same point, and recover from an accidental action without losing confidence in the game's state.
