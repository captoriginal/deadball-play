# Deadball Play

## Project Overview

## Repository Context

Deadball Play will be built **inside the existing `deadball-play` repository**.

The repository should evolve toward a monorepo-style organization with clear responsibilities:

```text
deadball_generator -> generated game data -> deadball_core -> deadball_play
                                              ^
                                              |
                                      existing web app
                                      (possible future consumer)
```

Conceptually:

- `deadball_generator` handles MLB-to-Deadball conversion and game generation.
- `deadball_core` handles game state, Deadball rules, legal actions, and structured events.
- `deadball_play` handles the TUI, narration, scoring guidance, session persistence, and user interaction.
- the current web application remains separate.

The exact directory layout should be chosen after inspecting the existing repository rather than imposed in advance. Shared-repository placement must not create unnecessary coupling.

A separate repository may become useful later, but extraction is intentionally deferred until there is a concrete need.

**Deadball Play** is a terminal-based digital facilitator for playing *Deadball: Baseball With Dice, Second Edition*.

Its purpose is not to replace Deadball with a computer baseball simulation. Instead, it removes the mechanical friction involved in tabletop play while preserving the parts that make the game enjoyable: managerial decisions, watching a baseball game unfold one plate appearance at a time, and keeping score by hand.

The intended minimal setup is:

**computer + printed score sheet + pen**

The program replaces the need to keep the rulebook, reference tables, and physical dice continuously in use. It presents the current game situation, offers the managerial choices allowed by the rules, rolls and interprets the required dice, explains the result in baseball language, and tells the player how to record the play on the score sheet.

The physical score sheet remains an important part of the experience rather than being made obsolete by the software.

## Design Goals

### Faithful to Deadball

The initial version should implement **Deadball Second Edition as written**.

The rules engine should not invent additional baseball mechanics simply because they might make a more detailed simulation. Deadball deliberately represents players and baseball situations with a relatively small number of ratings, tables, and decisions. The software should preserve that character.

Any future expanded rules should be clearly separated from the standard Deadball rules.

### Reduce Procedural Work

The player should not normally need to:

- consult the Swing Result Table
- consult the Hit Table
- consult the Defense Table
- remember MSS thresholds for productive outs, fielder's choices, or double plays
- calculate handedness Pitch Die changes
- track pitcher fatigue manually
- remember which modifiers apply to steals, bunts, or hit-and-run plays

The program handles those procedures automatically.

The player should primarily make **baseball decisions**, not perform rule lookups.

### Preserve Scorekeeping

Deadball Play should not initially aim to replace the handwritten score sheet with a complete electronic scorebook.

The software must maintain enough internal game state to apply the rules correctly, but the player's score sheet remains the primary visible record of the game.

After resolving a play, the program should clearly state what happened and how it can be scored, then allow the player time to record it before continuing.

For example:

```text
Freeman grounds to short.
Betts advances to second.

Score: 6-3

Press Enter when scored.
```

### Let the Game Unfold

The program should feel like watching a baseball game develop rather than operating a statistical simulator.

Each plate appearance should occur individually. Runs, outs, baserunners, pitching changes, and innings should accumulate naturally.

The program should not rush from outcome to outcome.

### Varied but Accurate Narration

Play descriptions should have enough linguistic variety that repeated outcomes do not become monotonous.

For example, a single might appear as:

```text
Ohtani singles to center.
```

or:

```text
Ohtani lines a base hit into center.
```

or:

```text
A single to center for Ohtani.
```

Narration must never alter the underlying result.

The rules engine should produce a structured event describing exactly what happened. A separate narration layer chooses how that event is expressed.

This separation may eventually allow the same event stream to drive spoken play-by-play or a radio-style presentation.

## Existing Team Generation

Deadball Play should not duplicate the existing player-generation system.

The current `deadball-play` project already converts real MLB information into Deadball players and game-specific data.

The intended relationship is:

```text
Deadball Generator
        |
        v
Generated game/team data
        |
        v
Deadball Play
        |
        v
Rules + decisions + narration
        |
        v
Player records game on paper
```

The existing generator determines **who is playing and what their Deadball ratings are**.

Deadball Play determines **what happens once the game begins**.

## Architecture

The application should be divided into four major responsibilities.

### 1. Team / Data Layer

Responsible for supplying:

- players
- lineups
- positions
- handedness
- Batter Target
- On Base Target
- Pitch Die
- traits
- other game inputs

Much of this already exists in the current generator.

### 2. Rules Engine

Responsible exclusively for determining legal choices and resolving Deadball mechanics.

Examples include:

- at-bat resolution
- Hit Table
- DEF checks
- runner advancement
- steals
- bunts
- hit-and-run
- handedness adjustments
- pitcher fatigue
- substitutions
- Managerial Daring
- inning and game transitions

The rules engine should have no knowledge of terminal formatting, narration wording, save files, or MLB APIs.

### 3. Presentation Layer

Responsible for:

- TUI screen layout
- current game-state display
- legal managerial choices
- dice and result presentation
- scoring instructions
- varied narration
- future voice/TTS output

The presentation layer must never determine a baseball result.

### 4. Session / System Layer

Responsible for:

- save and resume
- autosave
- undo
- event history
- configuration
- logging
- game initialization
- recovery after interruption

This layer should make it possible to stop a game and later continue from exactly the same state.

## Core Data Flow

```text
TEAM / DATA
    |
    v
GAME STATE
    |
    v
RULES ENGINE
    |
    v
STRUCTURED EVENTS
    |
    v
PRESENTATION
```

The session/system layer wraps around game state to provide persistence, undo, and history.

## Initial Playing Experience

A typical plate appearance might look like:

```text
TOP 5TH                         LAD 2 - SF 1
1 OUT                             RUNNER ON 1ST

Freddie Freeman     1B   L
BT 30   OBT 39   P+

Logan Webb          RHP
Pitch Die: d8

[S] Swing
[T] Steal
[B] Bunt
[H] Hit & Run
[P] Pinch Hit
```

After the player's decision:

```text
Swing

d100: 41
Pitch Die: 6
MSS: 47

Freeman grounds to second.
Betts advances to second.

Score: 4-3

Press Enter when scored.
```

The program then advances to the next batter.

## Version 1 Scope

The first version should focus on allowing one person to play a complete regulation game correctly from first pitch to final out.

A reasonable initial scope includes:

- Modern Era Deadball Second Edition rules
- one human-managed team
- one computer-managed opponent using Managerial Daring
- automatic dice rolling
- legal decision prompts
- at-bat resolution
- baserunning
- bunts
- steals
- hit-and-run
- handedness
- pitcher fatigue
- substitutions
- score / inning / outs / bases tracking
- varied but accurate narration
- scoring guidance
- save/resume
- autosave
- undo
- play history

## Version 1 Non-Goals

Version 1 is not intended to be:

- a general-purpose baseball simulation
- a replacement for the Deadball score sheet
- a season-management application
- a graphical baseball game
- a card-based adaptation
- an expanded house-rule version of Deadball
- a full electronic scorebook

The initial objective is narrower:

> **Allow one person to sit down with a score sheet and play a complete, faithful game of Deadball Second Edition with the computer handling the dice, tables, procedural rules, opposing-manager decisions, and presentation.**

## Long-Term Possibilities

Possible future expansions include:

- richer manager tendencies
- optional expanded Deadball rules
- Ancient Era support
- season play
- alternate interfaces
- manual dice entry
- speech synthesis
- radio-style spoken play-by-play
- a graphical frontend using the same rules engine

These should remain separate from the core requirement that the standard game stay faithful to Deadball Second Edition.
