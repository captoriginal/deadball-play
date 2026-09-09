# Deadball Play — Rules Scope

## Purpose

This document defines which parts of *Deadball: Baseball With Dice, Second Edition* belong in the initial Deadball Play ruleset.

The goal is to prevent accidental scope growth and to distinguish:

- **Version 1 core**
- **supported optional rules**
- **future rules**
- **out-of-scope systems**

The initial target is the **Modern Era** game described beginning on page 22 of the Second Edition rulebook.

---

## Version 1 Core Rules

These rules should be implemented before Deadball Play is considered capable of conducting a complete game.

### Player Attributes

Support:

- name
- position
- handedness
- Batter Target (BT)
- On Base Target (OBT)
- Pitch Die (PD)
- hitter traits
- pitcher traits

### Hitter Traits

Support the Modern Era effects of:

- P+
- P++
- C+
- S+
- D+
- T+
- P-
- P--
- C-
- S-
- D-

Trait effects must match the rulebook rather than inferred baseball behavior.

### Pitcher Traits

Support:

- K+
- GB+
- CN+
- ST+
- CN-

### Normal At-Bat

Support:

- d100 Swing Score
- positive and negative Pitch Dice
- Modified Swing Score (MSS)
- BT hit threshold
- OBT walk threshold
- possible-error range
- productive-out ranges
- Out Table
- critical hits

### Hit Resolution

Support:

- Modern Hit Table
- trait modifiers to the Hit Table
- runner advancement specified by the table
- DEF checks
- hits reduced by defense
- hits converted to outs by defense
- defensive errors

### Ordinary Outs

Support:

- strikeouts
- groundouts
- fly/pop outs
- productive outs
- runner advancement from second and third when permitted
- fielder's choices
- double plays
- triple-play conditions
- sacrifice-fly outcomes

### Baserunning

Support:

- steal second
- steal third
- steal home where permitted
- double steals
- S+ and S- modifiers
- catcher D+ and D- steal modifiers

### Tactical Offense

Support:

- bunting
- C+/C- bunt modifiers
- hit-and-run
- C+ hit-and-run bonus
- simultaneous steal component
- Hit & Run Table

### Pitching

Support:

- handedness Pitch Die adjustment
- starting-pitcher d12 handedness ceiling
- reliever handedness ceiling through d20
- starting-pitcher improvement conditions
- starting-pitcher fatigue conditions
- late-run rule
- reliever fatigue
- pitcher substitutions

### Substitutions

Support:

- pinch hitters
- pinch runners
- defensive substitutions
- position changes
- pitcher changes
- fixed batting-order positions
- no re-entry
- out-of-position D- treatment
- UT exception

### Game Structure

Support:

- batting order
- top/bottom half-innings
- three-out transitions
- nine innings
- extra innings
- home team not batting when already ahead after the top of the ninth or later
- walk-off endings where normal baseball game state makes the winner certain
- MLB regular-season automatic runners in extra innings, selected from the
  active batting order; postseason extra innings remain empty

### Solo Opponent

Support Deadball's **Managerial Daring** mechanism for computer-managed decisions.

The Daring roll itself is a published Deadball rule. The circumstances under which Deadball Play decides that a managerial question should be considered are application procedure and are documented separately in `manager-ai.md`.

---

## Supported Optional Rules

These are part of Second Edition but should be explicitly switchable rather than silently enabled.

Initial implementation may defer them until after the core game works.

### Oddities

The rulebook allows Oddity results associated with extreme MSS values.

Status:

**Supported optional rule**

It is enabled by default. Set `oddities: false` in generated-game rules or use
`--no-oddities` for a new terminal game to disable it deliberately. When
disabled, the engine uses the rulebook's non-Oddity treatment. When enabled,
MSS 1 and 99 roll the complete 2d10 table, including injuries and
persistent/at-bat modifiers.

### Designated Hitter

The Second Edition explicitly permits use of a DH.

Status:

**Supported configuration**

The generated game data should determine whether the game uses a DH when possible.

### Three-Batter Minimum

The rulebook notes that Deadball itself does not require pitchers to face a minimum number of hitters but allows players to employ the real-life rule if desired.

Status:

**Supported optional application rule**

It is enabled by default as Deadball Play's modern-MLB procedure. Set
`three_batter_minimum: false` in generated-game rules or use
`--no-three-batter-minimum` for a new terminal game to disable it. A pitcher
must face three batters in the appearance, but may be removed when the
offensive side is retired or for an incapacitating injury/illness. If retained
for the next inning before reaching three, the remaining batter count still
applies.

### Other Explicit Modern-Era Optional Systems

Any additional optional systems found in the Second Edition rulebook should be inventoried before implementation and assigned one of:

- supported
- future
- excluded

Do not assume an optional rule is enabled merely because it appears in the book.

---

## Future Rules

These may be added after a complete Modern Era game is stable.

### Ancient Era

The Ancient Era begins on page 50 and modifies:

- roster size
- Hit Table
- DEF table
- Out Table
- bunting
- darkness/game-ending behavior
- other era-specific mechanics

Status:

**Future ruleset**

Ancient Era should be implemented as a separate ruleset, not as conditionals scattered throughout Modern Era code.

### Campaign / Season Systems

Examples include:

- Nine Game Pennant
- campaign structures
- season-level roster changes
- aging
- trades
- longer-term player development

Status:

**Future / separate subsystem**

Deadball Play Version 1 conducts individual games.

### Expanded Manager Personalities

Real-world managerial tendencies may eventually supplement or modify Daring.

Status:

**Future optional expansion**

They must not replace the ability to use pure Second Edition Daring.

### Voice / Radio Presentation

Status:

**Future presentation feature**

No rules impact.

---

## Explicit Non-Goals for Version 1

Version 1 should not add:

- discretionary send/hold baserunning not present in Deadball
- pitch-by-pitch simulation
- pitch types
- weather mechanics
- park factors unless explicitly required by an enabled Deadball rule
- Statcast-style contact modeling
- player morale
- hidden baseball AI
- defensive positioning systems not present in the rules
- extra player ratings solely because MLB data exists for them

---

## Scope Rule

When implementation encounters an unclear situation:

1. Check the Second Edition rulebook.
2. If the rule is explicit, implement it directly.
3. If the rule is optional, expose it explicitly.
4. If the book deliberately leaves judgment to the player, preserve that judgment where practical.
5. If software requires a procedural decision the book does not define, document it as **Deadball Play application procedure**.
6. Do not silently invent additional baseball rules.

---

## Version 1 Definition of Complete

The rules scope is complete when a player can conduct a full Modern Era Deadball game from first batter to final out using generated teams, including:

- all ordinary at-bat outcomes
- runner movement
- tactical offense
- pitching changes and fatigue
- substitutions
- inning transitions
- solo Managerial Daring
- scoring guidance

without needing another baseball simulation layer to fill gaps.
