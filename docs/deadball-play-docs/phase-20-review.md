# Phase 20 Review — Oddities and MLB Pitcher Minimum

## Outcome

Deadball Play now resolves the optional Second Edition Oddities table instead
of stopping at a placeholder event. The optional pitcher-appearance rule also
matches MLB's three-batter/end-of-half-inning procedure.

Both rules are enabled by default. Explicit values in existing schema-v1 games
and saves remain authoritative, and either rule can be disabled for a new game.

## Oddities

When `rules.oddities` is true, an MSS of 1 or 99 rolls 2d10. Totals 2-20 cover:

- fan and animal interference
- rain delays and blown calls
- player injuries and their follow-up tables
- TOOTBLAN and pickoff outs
- hit by pitch, wild pitch, passed ball, balk, and catcher interference
- pitcher distraction, dropped third strike, and pitcher error

The event records the Oddity name, explanation, 2d10 result, and all follow-up
dice. Results that do not resolve the at-bat preserve the batter and do not
increment batters faced. Persistent effects live in immutable game state, so
undo and save/resume reproduce them exactly.

## Three-Batter Minimum

When `rules.three_batter_minimum` is true, starters and relievers cannot be
removed until one of these conditions is true:

- three batters have completed plate appearances against that pitcher;
- the offensive side has been retired and the pitcher is removed before
  returning to face another batter; or
- an incapacitating injury or illness creates a removal exception.

If a pitcher ends an inning below three and returns for the next inning, the
inning-end window closes after the next batter and the remaining minimum still
applies. Human mound-change options and computer-manager opportunities use the
same eligibility function.

## Enabling the Rules

Canonical game JSON may set:

```json
"rules": {
  "oddities": true,
  "three_batter_minimum": true
}
```

For a new terminal game, no enabling switches are required:

```text
./scripts/deadball-play --game saves/Game.json
```

Use `--no-oddities` or `--no-three-batter-minimum` to disable a default.
Rule-changing switches apply only to new games. Resumed games keep the rules
stored in their original generated-game source.

## Verification

The unified release gate covers the generator, rules core, terminal app, Web
API, and production frontend build. Focused tests cover disabled defaults,
Oddity continuation and resolution, baserunner-only effects, injury removal,
three-batter blocking, inning-end removal, return for a new inning, narration,
and save compatibility.
