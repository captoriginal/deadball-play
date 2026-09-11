# Deadball Play — Phase 0 Repository Review

## Outcome

Deadball Play remains in `deadball-play`. The least disruptive layout is to keep
the existing generator intact and add two sibling Python packages under
`backend/`:

```text
backend/
├── app/                 # existing FastAPI application
├── deadball_generator/  # existing MLB-to-Deadball package
├── deadball_core/       # reusable data, state, rules, and event boundaries
└── deadball_play/       # session, narration, and TUI boundaries
```

The React frontend and Tauri shell remain unchanged. Moving existing code into a
new top-level `packages/` or `apps/` tree would add churn without improving the
Phase 0 dependency boundaries.

## Established Dependency Direction

```text
deadball_generator -> versioned generated-game data -> deadball_core
                                                     -> deadball_play

deadball_play -> deadball_core
FastAPI/web   -> deadball_generator
```

`deadball_core` has no runtime dependencies and must not import
`deadball_play`, the FastAPI `app`, or `deadball_generator`. The generator-to-core
arrow above describes a data handoff, not a required Python import.

## Existing Generator Entry Points

The FastAPI path is:

1. `POST /api/games/{game_id}/generate`
2. `deadball_generator.generator.generate_game_from_raw`
3. `deadball_generator.deadball_api.convert_game`
4. `deadball_generator.cli.game.build_deadball_for_game`

The package also exposes the `deadball-game` and `deadball` command-line entry
points. Its CLI writes generated games beneath
`backend/deadball_generator/data/generated/games/` by default. The web flow
stores generated JSON and CSV strings in the `GameGenerated` database table.

## Current Generated-Game Output

`convert_game` currently returns a Python mapping with two string values:

- `stats`: JSON containing flat `players`, team labels in `teams`, and rating
  metadata in `meta`
- `game_text`: the same player rows as CSV

Player rows use generator-oriented columns such as `IDmlb`, `Type`, `Team`,
`BatOrder`, `Name`, `Pos`, `Positions`, `Hand`, `Throws`, `BT`, `OBT`, `PD`, and
`Traits`.

This output is sufficient for scorecard rendering but is not yet the documented
Deadball Play contract. Phase 1 should add an explicit adapter/export rather than
make the core depend on the generator's dataframe or internal modules.

## Conflicts and Gaps Found

- `game-data-schema.md` describes nested teams, ordered lineups, rosters,
  `schema_version`, rules configuration, and canonical trait arrays. The current
  output is a flat row collection with traits encoded as a string.
- MLB IDs survive as `IDmlb`, but the current payload does not expose the
  documented canonical `player_id` field.
- The API returns JSON inside a JSON string and persists it with a parallel CSV
  artifact. That transport shape should not become the core contract.
- `phases.md` combines the generated-game contract and initial game state in
  Phase 1, while `implementation-plan.md` splits them into Phases 1 and 2. For
  implementation sessions, `phases.md` is the controlling phase sequence named
  by the originating directive.
- The existing generator module named `rules.py` owns player-rating conversion
  rules. It is distinct from `deadball_core.rules`, which will own gameplay rules;
  imports should always use their fully qualified package names.

## Phase 0 Scaffolding

`deadball_core` contains empty boundaries for:

- generated game data
- game state
- rules and legal actions
- structured events

`deadball_play` contains empty boundaries for:

- session/system behavior
- narration
- TUI interaction

The tests enforce that core imports do not pull in application or generator
packages. No data model or gameplay behavior is implemented in Phase 0.

## Phase 1 Needs

Phase 1 should define a versioned generated-game model and a one-way adapter from
the current flat generator output. It should validate identities, lineups,
positions, handedness, BT/OBT, traits, Pitch Dice, pitcher roles, and starting
pitchers, then create initial state without network access.
