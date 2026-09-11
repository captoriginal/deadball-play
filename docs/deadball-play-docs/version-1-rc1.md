# Deadball Play Version 1 RC1

Version 1 RC1 is the first release candidate for the complete Modern Era
Deadball Second Edition play experience.

## Included

- standalone schedule browsing and MLB game generation through the shared local service
- canonical Play JSON and home/away score-sheet bundles
- complete regulation, walk-off, and extra-inning games
- human or Daring-managed teams, substitutions, fatigue, Oddities, and pitcher minimums
- autosave, resume, undo, scorekeeping confirmation, and protected-path quitting
- recent-save browsing with matchup, inning, score, and modification metadata
- automatic final-session, CSV box-score, and Markdown recap exports
- line-mode and three-column fullscreen terminal interfaces
- Web production build and Tauri desktop compile verification

## Verification

Run the complete release gate from the repository root:

```console
./scripts/check-deadball-v1
```

The ignored Tauri backend archive must be generated before a clean desktop
compile or bundle:

```console
bash scripts/package-backend.sh
```

RC1 is ready for final hands-on playtesting and packaging. Any release-blocking
finding should be fixed without changing the save or generated-game schemas;
otherwise the candidate can be promoted to Version 1.0.0.
