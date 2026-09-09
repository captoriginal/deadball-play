# Phase 20.5 Review — Generator-Aware Start Screen

## Outcome

Deadball Play's no-argument start screen now fronts the existing Deadball Web
pipeline instead of requiring users to switch interfaces or re-enter a known
file path. The generator remains responsible for MLB retrieval, ratings, trait
assignment, canonical Play JSON, and the PDF score sheet.

## Launch Flow

The start screen can:

- browse scheduled MLB games by date;
- select a game or accept a directly entered MLB game ID;
- choose Standard, SABR, or Adaptive traits;
- optionally refresh cached statistics;
- generate both home and away PDF score sheets by default, or select one side;
- generate and start playing, or generate files only;
- browse generated JSON files and load one as a new game; and
- retain the existing resume and demo paths.

All launch menus use the standard outlined box. Before a generated or loaded
new game starts, each club can be assigned to a human or computer manager. The
older cached-database shortcut has been removed from the menu because the
browse/generate/load workflow supersedes it; its CLI flag remains compatible.
The schedule field is prefilled with today's local date but can be edited in
place. Setup and generation errors originating from this menu now show a concise
message and return to the menu after Enter instead of printing argparse usage
and exiting. Direct CLI failures continue to return as CLI errors.

Every generation produces an artifact bundle. The shell-safe Play JSON and both
team PDFs are stored together in `saves/` by default. In-progress sessions share
that directory and use the `.save.json` suffix. The generated-game picker lists
only `*DeadballPlay.json` artifacts. The progress indicator reports away- and
home-team ratings separately, then JSON, each PDF, and local saving. Generate
files only returns to the main menu. Directory separation is intentionally
deferred.

## Roster Statistics

Canonical Play JSON now retains the complete season-stat set displayed by the
generator. The MLB Stats tab lists G, AVG, OBP, HR, 2B, and SB for every hitter
and GS, IP, ERA, K/9, BB/9, and GB% for every pitcher. The Box Score / Lineups
tab continues to show live game statistics and includes the complete bench and
pitching staff. A bench player used as a substitute in the source MLB game has
an asterisk after the name.

## Command-Line Equivalent

The same noninteractive pipeline is available directly:

```text
./scripts/deadball-play --generate-only MLB_GAME_ID \
  --trait-mode adaptive --force-generate --scorecard-side away
```

Replace `--generate-only` with `--generate-game` to launch the new game after
the JSON/PDF pair is written.

## Verification

Focused tests cover scheduled-game selection, generator-option propagation,
paired artifact placement, filtering session saves from the game picker, and
the generate-only exit path.
