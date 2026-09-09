# Deadball Play — Game Data Schema

## Purpose

## Repository Contract

Because the generator and gameplay code share the `deadball-web` repository, the generated-game schema should still be treated as a formal boundary rather than allowing Deadball Play to import generator internals indiscriminately.

Conceptually:

```text
deadball_generator
        |
        v
versioned generated-game contract
        |
        v
deadball_core
        |
        v
deadball_play
```

The shared repository is an implementation convenience, not permission to blur responsibilities.

This document defines the data handoff between the existing **deadball-web** generator and **Deadball Play**.

## Implemented Contract

Schema version 1 is implemented by `deadball_core.game_data`. It uses immutable
models and strict validation before initial state can be created.

Required implementation details beyond the conceptual examples below:

- each roster player has a canonical `player_id`, `name`, `role`, `positions`,
  and `traits`
- roles are `position_player`, `starter`, or `reliever`
- BT/OBT are JSON integers from 0 through 100
- a DH game has one `DH` and no `P` in each nine-player lineup
- a non-DH game has one `P` and no `DH` in each lineup
- each initial defense covers `P`, `C`, `1B`, `2B`, `3B`, `SS`, `LF`, `CF`,
  `RF` exactly once
- the normal Modern roster count is not enforced because the rulebook permits
  roster composition changes and generated MLB exports may vary

The current flat generator payload is accepted only through
`adapt_generator_game`; generator-oriented column names are not part of the
canonical schema.

The generator answers:

> Who is playing, and what are their Deadball ratings?

Deadball Play answers:

> What happens once the game begins?

The TUI should not need to fetch MLB statistics or regenerate player ratings during a game.

---

## Design Goals

The game-data format should be:

- self-contained
- human-readable where practical
- versioned
- independent of the TUI
- sufficient for offline play after generation
- stable enough to support saved games
- explicit about missing data

JSON is a natural initial interchange format.

---

## Top-Level Structure

Conceptual example:

```json
{
  "schema_version": 1,
  "game": {},
  "rules": {},
  "teams": {
    "away": {},
    "home": {}
  }
}
```

The optional-rules object currently includes:

```json
{
  "edition": "second",
  "era": "modern",
  "designated_hitter": true,
  "oddities": true,
  "three_batter_minimum": true
}
```

Both `oddities` and `three_batter_minimum` default to `true` when absent.
Explicit values in existing schema-v1 game files and saves remain authoritative.

---

## Game Metadata

Recommended fields:

```text
game_id
game_date
source
source_game_id
season
game_type
venue
generated_at
```

Only fields needed by Deadball Play should be required.

MLB metadata may be retained for display or provenance without affecting mechanics.

Example:

```json
{
  "game_id": "mlb-2026-08-15-lad-sf",
  "game_date": "2026-08-15",
  "source": "deadball-web",
  "source_game_id": "123456",
  "season": 2026,
  "game_type": "R"
}
```

`game_type` uses MLB's schedule code. `R` enables the regular-season automatic
runner in extra innings. Explicit postseason (`F`, `D`, `L`, `W`, or `C`),
spring (`S`), and exhibition (`E`) games begin extra innings with empty bases.
Legacy game files without this field are treated as regular season.

---

## Rules Metadata

The generated game should identify mechanically relevant configuration.

Example:

```json
{
  "edition": "second",
  "era": "modern",
  "designated_hitter": true
}
```

Optional Deadball Play settings may be added when the session starts rather than during generation.

---

## Team Structure

Each team should contain at least:

```text
team_id
name
short_name
lineup
roster
starting_pitcher
```

Optional display fields may include:

- city
- abbreviation
- league
- logo reference

These should have no mechanical effect unless explicitly used by a future rule.

---

## Player Identity

Each player requires a stable ID.

Recommended:

```text
player_id
source_player_id
name
```

Example:

```json
{
  "player_id": "mlb-660271",
  "source_player_id": 660271,
  "name": "Shohei Ohtani"
}
```

The game should reference players by ID internally rather than by name.

---

## Position Player Fields

Required Deadball fields:

```text
player_id
name
position
bats
BT
OBT
traits
```

Example:

```json
{
  "player_id": "mlb-660271",
  "name": "Shohei Ohtani",
  "position": "DH",
  "bats": "L",
  "BT": 29,
  "OBT": 38,
  "traits": ["P++", "S+"]
}
```

### Position

Use a normalized vocabulary such as:

```text
C
1B
2B
3B
SS
LF
CF
RF
DH
UT
```

If the generator supports multiple natural positions, retain them separately from the active starting position.

### MLB Display Statistics

Players may carry an optional `mlb_stats` object used only for presentation.
The current generator preserves G, AVG, OBP, HR, 2B, and SB for hitters, and
GS, IP, ERA, K/9, BB/9, and GB% for pitchers. Canonical keys use descriptive
snake case, for example `on_base_percentage`, `innings_pitched`, and
`strikeouts_per_nine`.

`appeared_in_source_game` records whether the player participated in the MLB
game used to construct the roster. Deadball Play uses this provenance flag to
asterisk source-game bench substitutions; it has no mechanical effect.

---

## Pitcher Fields

Required fields:

```text
player_id
name
throws
pitch_die
traits
role
```

Example:

```json
{
  "player_id": "mlb-123",
  "name": "Example Pitcher",
  "throws": "R",
  "pitch_die": "d8",
  "traits": ["K+", "GB+"],
  "role": "starter"
}
```

Valid Pitch Die values should be normalized:

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

Do not store arbitrary strings such as `"D8+"`.

---

## Traits

Traits should be an array of canonical identifiers.

Example:

```json
"traits": ["P+", "S+", "D+"]
```

Do not encode traits only as prose.

The consumer should validate traits against the selected Deadball ruleset.

---

## Lineup

The starting lineup should be ordered.

Example:

```json
"lineup": [
  {
    "slot": 1,
    "player_id": "mlb-1",
    "position": "SS"
  },
  {
    "slot": 2,
    "player_id": "mlb-2",
    "position": "DH"
  }
]
```

The lineup is the authoritative starting batting order.

Deadball Play may allow pre-game edits before the game state becomes active.

---

## Roster

The roster should contain every player available to the game.

Players should be classifiable as:

- starting position player
- bench
- starting pitcher
- reliever

The schema should not require the exact fictional-roster counts from the rulebook when using a real MLB game roster, as long as all generated players needed for the game are represented.

---

## Starting Pitcher

The team should explicitly identify the starting pitcher.

Example:

```json
"starting_pitcher_id": "mlb-55"
```

Do not infer the starter from roster ordering.

The current generator marks the actual game starter with `GameStarted`. The
legacy adapter requires exactly one such pitcher per team unless the caller
provides the starter ID explicitly in trusted game context.

---

## Defensive Alignment

The lineup's position fields provide the initial defense.

The game state may later diverge due to substitutions and position changes.

The source data should therefore remain immutable after game initialization.

---

## Handedness

Normalize:

### Batters

```text
R
L
S
```

### Pitchers

```text
R
L
```

Missing handedness should be resolved by the generator before game play whenever possible.

Deadball Play should not guess handedness during an at-bat.

---

## Optional Informational Statistics

The generator may include real-world statistics for display.

Examples:

- AVG
- OBP
- HR
- doubles
- stolen bases
- ERA
- K/9
- BB/9

These are informational only once BT, OBT, Pitch Die, and traits have been generated.

Deadball Play must not recalculate game mechanics from these fields unless explicitly performing a regeneration step outside the active game.

---

## Provenance

It may be useful to retain the source used to generate each rating.

Example:

```json
{
  "ratings_source": {
    "season": 2026,
    "mode": "standard",
    "generated_by": "deadball-web"
  }
}
```

This is especially useful if future generator modes include Standard, SABR, or Adaptive trait calculations.

---

## Missing Data Policy

The generator should ideally resolve missing required mechanical data before export.

### Required Mechanical Fields Missing

Examples:

- no BT
- no OBT
- no Pitch Die
- unknown pitcher handedness

Recommended behavior:

**Reject game initialization with a clear validation error.**

Do not invent ratings silently.

### Optional Display Fields Missing

Examples:

- venue
- player real-world stats
- source metadata

Recommended behavior:

Allow the game to load.

---

## Validation

Before a game starts, validate:

- unique player IDs
- valid lineup order
- every lineup player exists on roster
- starting pitcher exists
- valid positions
- valid handedness
- BT and OBT numeric validity
- valid Pitch Die value
- known trait identifiers
- no duplicate active lineup players

Validation errors should identify the exact field and player.

---

## Immutability

Once imported into an active game, the generated source record should remain unchanged.

Game state should separately track:

- substitutions
- active positions
- current pitcher
- batting-order progress

This preserves the distinction between:

**original generated roster data**

and

**current game state**

---

## Offline Use

Once the game file has been generated, Deadball Play should be able to conduct the game without network access.

This supports use on:

- airplanes
- travel
- unreliable connections
- archived historical games

---

## Schema Versioning

Include:

```text
schema_version
```

Deadball Play should reject unsupported future schemas cleanly or migrate them explicitly.

Do not reinterpret fields based only on application version.

---

## Core Acceptance Test

The schema is sufficient if:

> Deadball Play can load the file, initialize both teams, display the complete starting matchup, and conduct the entire game without retrieving any additional player-rating information.
