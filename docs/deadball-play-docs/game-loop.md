# Deadball Play — Game Loop

## Purpose

This document defines the gameplay loop for **Deadball Play**, a terminal-based facilitator for *Deadball: Baseball With Dice, Second Edition*.

The goal is to turn the published Deadball rules into a clear sequence of game states and transitions that can later be implemented in a rules engine and TUI.

This document is intentionally limited to the **Modern Era core game loop**. It should remain faithful to the Second Edition rulebook. Optional systems and expanded house rules should be added separately rather than silently folded into the core loop.

Primary rulebook references are Chapter 2, especially:

- The Players — pp. 22–24
- The At-Bat — pp. 26–30
- Baserunning — pp. 31–33
- Pitching — pp. 34–37
- Miscellany / Managerial Daring — pp. 42–47

---

## High-Level Loop

A Deadball game consists of repeated plate appearances inside half-innings.

At the highest level:

```text
START GAME
    |
    v
TOP OF INNING
    |
    v
PRE-AT-BAT STATE
    |
    v
MANAGERIAL DECISION
    |
    v
RESOLVE ACTION / AT-BAT
    |
    v
UPDATE RUNNERS, OUTS, SCORE, PITCHER
    |
    v
PRESENT RESULT + SCORING GUIDANCE
    |
    v
PLAYER CONFIRMS RESULT IS SCORED
    |
    +---- outs < 3 ----> NEXT BATTER
    |
    +---- outs = 3 ----> CHANGE SIDES
                              |
                              v
                         NEXT HALF-INNING
                              |
                              v
                         CHECK GAME END
```

The system should advance only after the player confirms that the completed play has been recorded on the paper score sheet.

---

# 1. Game Initialization

Before the first plate appearance, the system loads the game and establishes the initial game state.

## Required Team Data

For each active player, the rules engine needs at least:

- name
- position
- batting handedness
- throwing hand for pitchers
- Batter Target (BT)
- On Base Target (OBT)
- Pitch Die for pitchers
- Bonus Traits
- batting-order position
- roster status

The existing Deadball generator should supply this information.

## Initial Managerial Setup

Before play begins, the player may configure the starting lineup and defensive positions.

The rulebook allows lineups to be freely arranged before the game. Once play begins, substitutions follow normal Deadball substitution rules. A player who leaves the game cannot return.

The system should establish:

- away lineup
- home lineup
- starting pitchers
- defensive positions
- DH usage, if applicable
- which team or teams are human-managed
- which team, if any, is computer-managed

## Initial Game State

```text
inning = 1
half = top
outs = 0
bases = empty
away_score = 0
home_score = 0
away_batter_index = 0
home_batter_index = 0
current_pitcher = home starting pitcher
```

Additional pitcher and substitution state should also be initialized.

---

# 2. Begin Half-Inning

At the beginning of each half-inning:

1. Set outs to zero.
2. Clear all bases in regulation innings and postseason extra innings.
3. In a regular-season extra inning, place the player immediately preceding
   the scheduled leadoff hitter in the current batting order on second base.
4. Identify the batting team.
5. Identify the defensive team.
6. Confirm the active pitcher.
7. Identify the next batter from the team's persistent batting-order position.

The batting order does **not** reset at the beginning of an inning.

The TUI should present the inning transition naturally, for example:

```text
TOP OF THE 4TH
Dodgers 2 — Giants 1

Mookie Betts leads off.
```

No rules decision occurs simply because a new inning begins, although pitching substitutions may be considered before the first batter.

---

# 3. Pre-At-Bat State

Before every plate appearance, the system determines the current baseball situation.

## Displayed State

The TUI should normally show:

- inning and half
- score
- outs
- occupied bases
- current batter
- batter position and handedness
- BT
- OBT
- relevant batter traits
- current pitcher
- pitcher handedness
- current Pitch Die
- relevant pitcher traits

The system should also know:

- whether the pitcher is fatigued
- whether the pitcher receives a handedness adjustment
- runners and their traits
- catcher defensive trait, if relevant to stealing
- bench and bullpen availability

---

# 4. Determine Available Managerial Decisions

The system should present only choices that make sense in the current Deadball situation.

The purpose is not to invent additional baseball strategy. Choices should come from the published Deadball rules.

## Always-Possible Offensive Choices

Depending on roster availability and context:

- Swing away / normal at-bat
- Pinch hit
- Manage lineup / substitutions

## Situational Choices

Possible choices may include:

- attempt to steal second
- attempt to steal third
- attempt to steal home, if permitted by S+
- attempt a double steal
- bunt
- hit and run
- pinch run

The system should determine availability from:

- occupied bases
- player traits
- current batter
- roster availability
- the published rules

For example:

```text
Runner on first, no outs

[S] Swing
[T] Steal second
[B] Bunt
[H] Hit & Run
[P] Pinch Hit
[R] Pinch Run
```

The program should not present impossible or unsupported actions.

---

# 5. Computer-Managed Decisions

If one team is computer-managed, managerial choices should initially use **Deadball's Managerial Daring rule** rather than an invented AI system.

The rulebook assigns a manager a Daring rating from 1–19. When a decision arises:

1. Identify the risky choice.
2. Identify the conservative choice.
3. Roll d20.
4. If the result is equal to or below Daring, choose the risky action.
5. Otherwise choose the conservative action.

The rulebook identifies examples including:

- attempt steal vs. do not steal
- hit and run vs. no hit and run
- decline bunt vs. bunt
- pull starter early vs. leave starter in
- leave starter in past the sixth vs. pull pitcher
- leave reliever in for another inning vs. remove reliever

The system may later support more sophisticated manager personalities, but Version 1 should stay with the published Daring mechanism.

---

# 6. Resolve Pre-At-Bat Tactical Actions

Some managerial actions replace or modify the normal at-bat.

## 6.1 Steal

A steal is attempted before the normal MSS roll.

### Steal Second

Roll:

```text
d8
```

Base result:

```text
1–3  OUT
4–8  SAFE
```

Apply published modifiers such as:

- S+ runner: +1
- S− runner: -2
- D+ catcher: -1 to opposing stolen-base rolls
- D− catcher: +1 to opposing stolen-base rolls

After resolution:

- update runner position or record an out
- if the attempt creates the third out, end the half-inning
- otherwise return to the same batter for the plate appearance

The steal does not itself consume the batter's plate appearance.

### Steal Third

Roll:

```text
d8 - 1
```

Then apply other appropriate modifiers.

### Steal Home

Only an S+ runner may attempt to steal home under the core rule.

Roll d8.

On an 8, the runner steals home.

### Double Steal

Use the published Double Steal table and relevant lead-runner speed modifier.

---

## 6.2 Bunt

A bunt replaces the normal MSS at-bat.

Roll d6 and resolve the result using the Bunting Table.

The result depends on:

- die roll
- location of the lead runner
- batter traits such as C+, C−, or S+

Possible results include:

- lead runner out, batter safe
- lead runner advances, batter out
- on qualifying results, a single followed by a DEF check

After resolution:

1. update bases
2. update outs
3. advance batting order
4. update pitcher/game state as required
5. present scoring guidance
6. wait for scorekeeping confirmation

---

## 6.3 Hit and Run

The hit and run modifies the normal at-bat and includes a simultaneous stolen-base roll.

The system should:

1. roll the stolen-base attempt with normal modifiers
2. modify the batter's BT/OBT:
   - +5 normally
   - +10 for a C+ hitter
3. roll and resolve the MSS
4. classify the batting result as:
   - Hit
   - Pop Up / Strikeout
   - Groundball
5. combine it with the steal result using the published Hit & Run Table

Possible outcomes include:

- runners at first and third
- runners at first and second
- batter out with runner staying at first
- batter out with runner reaching second
- double play

The Hit & Run Table governs runner behavior; the program should not add extra advancement logic beyond the rulebook.

---

# 7. Normal At-Bat Resolution

If the manager chooses to swing away, the normal Deadball at-bat begins.

## 7.1 Determine Effective Pitch Die

Before rolling, determine the pitcher's effective Pitch Die.

### Handedness

When pitcher and batter have the same handedness:

- increase the Pitch Die one level

The normal ladder is:

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

For a starting pitcher, the same-handed advantage cannot raise the pitcher above d12.

For a relief pitcher, it may raise the Pitch Die as high as d20.

Other active pitcher adjustments, such as fatigue or traits, must already be reflected in current pitcher state.

---

## 7.2 Roll Swing Score and Pitch Die

Roll:

```text
d100 + Pitch Die
```

The result is the **Modified Swing Score (MSS)**.

For a negative Pitch Die, subtract the die roll.

The interface should show the actual dice because seeing the mechanism is part of the intended experience.

Example:

```text
d100: 31
Pitch Die d8: 6
MSS: 37
```

---

# 8. Classify MSS

The MSS is evaluated against the batter's BT and OBT using the Swing Result Table.

The engine should determine one of the following broad outcomes.

## 8.1 Oddity

If optional Oddities are enabled:

- MSS 1 → Oddity
- MSS 99 → Oddity

Resolve using the Oddities system before continuing.

If Oddities are disabled, the engine should follow the rulebook's non-Oddity interpretation.

Oddities are an optional rules module. When enabled, the engine rolls 2d10,
applies the result from the complete table, records every follow-up die, and
either advances the batting order or preserves the at-bat as directed by the
result.

Persistent effects (injuries, rating changes, temporary Pitch Die changes,
and steal modifiers) are explicit save-state data.

---

## 8.2 Critical Hit

For qualifying low MSS results, resolve a hit and increase its level by one:

```text
single -> double
double -> triple
triple -> home run
```

Relevant Bonus Traits are applied before increasing the hit level.

Critical hits cannot be taken away by defense.

---

## 8.3 Ordinary Hit

If:

```text
MSS <= BT
```

the batter records a hit.

Proceed to the Hit Table.

---

## 8.4 Walk

If:

```text
BT < MSS <= OBT
```

the batter walks.

Update runners according to normal forced advancement.

No Hit Table roll is required.

---

## 8.5 Possible Error

If:

```text
OBT < MSS <= OBT + 5
```

resolve the corresponding fielder and make a DEF roll.

For normally strikeout-coded endings of 0–2, use the rulebook's specified infield locations for possible-error purposes.

If the DEF roll results in an error:

- batter reaches first
- runners advance one base

Otherwise the normal out is recorded.

---

## 8.6 Productive Out / Fielder's Choice Range

The rulebook uses MSS bands to control runner advancement and double-play behavior.

Broadly:

### OBT+6 through 49

- appropriate runners at second or third may advance
- on an infield ball with a runner at first, the runner advances to second and the batter is out

### 50–69

- appropriate runners at second or third may advance
- on an infield ball with a runner at first, the runner is out and the batter reaches first on a fielder's choice

### 70+

- runners at second and third cannot advance on fly balls
- on an infield ball with a runner at first, both runner and batter are out on a double play

### 100+

With the appropriate infield/baserunner situation, a triple play may occur.

The rules engine should derive these results directly from the published ranges.

---

# 9. Hit Table Resolution

When the batter records a hit, roll d20 on the Hit Table.

Before final resolution, apply hitter traits that modify the Hit Table roll or specific low-roll outcomes.

Examples include:

- P+ adds 1
- P++ adds 2
- P− subtracts 1
- P−− subtracts 2
- C+ changes specified low results
- S+ changes specified low results

The Modern Hit Table can produce:

- single
- single with DEF check
- single with specified runner advancement
- double with DEF check
- double with specified runner advancement
- home run

The system should retain the exact Hit Table result as structured event data.

---

# 10. Defense Check

Some Hit Table results require a DEF check for a specific fielder.

Roll d12.

Base results:

```text
0–2   Error
3–9   No change
10–11 Hit reduced one level
12+   Hit turned into an out
```

Apply:

```text
D+  +1
D−  -1
```

The engine should then:

1. determine the final hit/out result
2. update runners
3. update score
4. retain the responsible fielder for scoring/narration

A DEF check is automatic when called for. It is not a managerial choice.

---

# 11. Resolve Ordinary Outs

For an out, the final digit of the MSS determines the normal fielding result.

Modern Out Table:

```text
0  Strikeout
1  Strikeout
2  Strikeout
3  Groundball to 1B
4  Groundball to 2B
5  Groundball to 3B
6  Groundball to SS
7  Fly/pop to LF
8  Fly/pop to CF
9  Fly/pop to RF
```

The MSS band then determines runner advancement, sacrifice-fly eligibility, fielder's choices, and double plays.

The structured event should include:

- out type
- responsible fielder(s)
- runners advanced
- runners retired
- batter outcome
- scoring notation

---

# 12. Runner Advancement

Runner movement should be determined by Deadball's tables and rules, not by additional send/hold decisions invented by the TUI.

Examples include:

- Hit Table instructions such as runners advance two or three bases
- critical-hit extra advancement
- productive-outs based on MSS
- sacrifice flies
- fielder's choices
- double plays
- errors
- walks and forced advancement

Version 1 should **not** add Strat-like discretionary send/hold mechanics.

---

# 13. Update Game State

After the play is completely resolved, the system updates authoritative game state.

At minimum:

- score
- outs
- base occupants
- batter's completed plate appearance, if applicable
- batting-order index
- active pitcher
- pitcher performance counters
- substitutions
- inning state
- event history

The update should occur as a single completed transaction so that Undo can later restore the state exactly.

---

# 14. Pitcher State and Fatigue

After relevant plays and innings, evaluate pitcher performance under the published pitching rules.

## Starting Pitchers Gain a Pitch Die Level When They

- pitch three consecutive scoreless innings
- strike out every batter faced in an inning
- escape a bases-loaded, no-out jam without allowing a run

These bonuses stack.

## Starting Pitchers Lose Pitch Die Levels When They

- allow 3+ runs in an inning
- allow 4+ runs over two innings
- allow runs beyond the specified total threshold
- pitch beyond six innings

If a starter allows a run in the seventh inning or later, their Pitch Die is automatically reduced to d4 before normal fatigue effects are subsequently applied.

The exact rulebook wording and stacking behavior should be encoded directly in tests.

## Relief Pitchers

Relievers lose a Pitch Die level:

- for every run allowed
- for every three outs recorded

Relievers therefore require separate fatigue tracking from starters.

---

# 15. Post-Play Presentation

Once the result is finalized, the rules engine returns a **structured play event**.

Example conceptually:

```text
event_type: groundout
batter: Freddie Freeman
fielded_by: SS
putout_by: 1B
batter_out: true
runner_advances:
  Mookie Betts: 1B -> 2B
outs_added: 1
runs_scored: 0
score_notation: "6-3"
```

The presentation layer then chooses natural wording.

Example:

```text
Freeman bounces one to short.
Betts moves up to second as Freeman is retired at first.

Score: 6-3
```

The narration layer may vary the phrasing, but it must never change any mechanical fact in the structured event.

---

# 16. Scorekeeping Pause

After every completed scoring event, the interface pauses.

Example:

```text
Score: 6-3
Betts -> 2B

Press Enter when scored.
```

This pause is a core design feature.

Deadball Play is intended to facilitate a physical scorekeeping experience rather than race through a simulation.

During this pause the player should be able to:

- inspect the result
- request rule details
- undo the play
- view history
- save/quit

The game should not advance to the next batter until the player confirms.

---

# 17. Advance Batter or End Half-Inning

After confirmation:

## If Outs < 3

1. advance the batting-order index if the plate appearance was completed
2. identify the next batter
3. return to Pre-At-Bat State

## If Outs = 3

1. end the half-inning
2. clear the bases
3. reset outs
4. change batting/fielding teams
5. advance inning number when appropriate
6. determine the pitcher for the next half-inning
7. check whether the game has ended

---

# 18. Game-End Check

After the top and bottom halves as appropriate, apply normal baseball game-ending logic.

At minimum:

- after nine innings, if the home team leads after the top of the ninth, no bottom half is played
- after the bottom of the ninth, if one team leads, the game ends
- if tied, continue into extra innings
- every regular-season extra half-inning starts with MLB's automatic runner on
  second: the active player immediately preceding the scheduled leadoff hitter
- postseason extra innings begin with empty bases
- in extra innings, continue until a complete game-ending condition is reached

Deadball Play should initially follow ordinary baseball inning logic unless a specific Deadball rule modifies it.

The final screen should show the score and indicate that the handwritten score sheet is the permanent record.

---

# 19. Substitutions Within the Loop

Substitutions may occur at relevant pre-at-bat or pitching decision points.

The Second Edition rules allow:

- position players to switch positions
- bench players to replace position players
- pinch hitters
- pinch runners
- pitching changes

A substituted player takes the replaced player's batting-order position.

Once removed, a player cannot return.

A player moved from the infield to the outfield or vice versa is treated as D− when making DEF checks unless they are a UT player.

The system must enforce:

- roster availability
- batting-order continuity
- defensive position state
- no re-entry

---

# 20. Rules Explanation on Demand

There is no separate Guided/Compact mode planned at this stage.

Instead, the normal presentation should be concise and pleasant, while the player can request the mechanical explanation when desired.

For example:

```text
MSS 56
Groundball to short
Runner on first
MSS 50–69 -> Fielder's Choice
```

This keeps the ordinary game uncluttered while making every ruling inspectable.

---

# 21. Event History

Every completed action should create an event in chronological history.

History should include enough structured information to:

- redraw recent play-by-play
- undo the latest action
- reconstruct game state
- debug rules behavior
- eventually support replay or spoken broadcast

Possible event categories include:

```text
game_start
half_inning_start
plate_appearance_start
steal_attempt
bunt
hit_and_run
pitching_change
pinch_hit
pinch_run
hit
walk
strikeout
groundout
flyout
fielder_choice
double_play
triple_play
error
run_scored
half_inning_end
game_end
```

Presentation text should not be the authoritative event record. Structured event data should be.

---

# 22. Rule-Engine Boundary

The rules engine is responsible for:

- determining which Deadball actions are legal
- rolling or accepting dice
- applying modifiers
- resolving tables
- moving runners
- recording outs
- scoring runs
- adjusting pitchers
- determining inning/game transitions
- producing structured events

The rules engine is **not** responsible for:

- prose variation
- terminal layout
- colors
- voice
- saving files
- user preferences
- MLB data retrieval

This boundary is essential for both rule fidelity and testing.

---

# 23. Initial Implementation Milestones

A practical order for implementing the loop later:

## Milestone 1 — State Model

Represent:

- inning
- half
- score
- outs
- bases
- batting orders
- active pitchers
- player availability

## Milestone 2 — Basic At-Bat

Implement:

- handedness
- Pitch Die
- MSS
- hit
- walk
- ordinary out

## Milestone 3 — Ball-in-Play Resolution

Implement:

- Hit Table
- traits
- DEF checks
- runner advancement
- productive outs
- sacrifice flies
- fielder's choices
- double plays
- triple plays

## Milestone 4 — Managerial Offense

Implement:

- steals
- bunts
- hit and run
- pinch hitting
- pinch running

## Milestone 5 — Pitching

Implement:

- pitcher substitutions
- starting-pitcher changes
- relief-pitcher fatigue
- performance-based Pitch Die adjustments

## Milestone 6 — Solo Manager

Implement Deadball Managerial Daring.

## Milestone 7 — Session Features

Implement:

- autosave
- resume
- undo
- history

## Milestone 8 — Presentation

Implement:

- TUI game screen
- scoring prompts
- varied narration
- rule explanation on demand

---

# 24. Core Design Test

The fundamental acceptance test for the game loop is:

> Can a player with a generated Deadball score sheet and a pen play a complete game from first pitch to final out without opening the rulebook or consulting a table, while still making every managerial decision that Deadball Second Edition gives them?

If the answer is yes, the core loop is doing its job.
