# Deadball Play - Version 1 Acceptance

## Release Candidate

The repository is designated **Version 1 RC1**. Python packages identify as
`1.0.0rc1`; Web and desktop packages identify as `1.0.0-rc.1`. Promotion to the
final `1.0.0` release requires no unresolved release-gate or manual-playtest
regressions.

## Accepted Release Path

Version 1 now has a repeatable repository-local installation and play path:

```console
./scripts/install-deadball-play
./scripts/deadball-play --demo --save saves/demo-game.json
```

The installer creates or reuses `.venv`, installs the generator, rules core,
and play application as editable local packages, then verifies the installed
`deadball-play` entry point. Python 3.10 or newer is required.

## Acceptance Coverage

The release checks cover:

- deterministic games through a regulation ending, extra innings, and a walk-off;
- complete computer-manager games and the human scorekeeping confirmation loop;
- save, resume, autosave, undo, history, and random-state continuity;
- generated-game validation, DH and non-DH lineups, substitutions, and fatigue;
- narration and scoring guidance across a complete game;
- the three-column dashboard in every ready and pending game state;
- the installed command and a real full-screen pseudo-terminal session;
- recent-save discovery, postgame CSV/Markdown exports, and save-path protection;
- the frontend production build; and
- the Tauri desktop compile.

The unified release run passes 761 tests: 288 core tests, 285 generator tests,
124 play/session/TUI tests, and 64 backend tests. The Vite
production build also completed successfully. The complete-game dashboard test
rendered 148 intermediate screens plus the final state during its 74-action
seeded game.

Run the complete gate from the repository root with:

```console
./scripts/check-deadball-v1
```

The script also passes the Tauri desktop compile with the locked Rust
dependencies. The Python and Web release paths do not otherwise require Rust.

## RC1 Manual Smoke Test

The installed command was exercised interactively in line mode from introduction
through an unfinished-game exit. The walkthrough covered lineup, pitcher, and
history views; initial autosave creation; visible save status; creation of a save
copy without moving the autosave path; state-aware `Save a copy` and `Quit`
labels; and protected-path quit confirmation. Deterministic full-game tests cover
regulation, walk-off, and extra-inning completion in both line and fullscreen
presentation paths.

A second end-to-end run used the installed command and two computer managers to
play from first pitch through the final out. It completed 7-6 in nine innings,
displayed the final box score, and wrote the session archive, CSV box score, and
Markdown recap. The temporary smoke-test artifacts were removed afterward.

The local licensed Deadball rulebook remains ignored and is not part of the
release artifact or any GitHub-bound change.

## Legacy Exclusions Closed

The generator's older scorecard-fill tests and the backend's older games API
tests are now part of the same green release gate. Simple one-table scorecard
templates remain compatible, double-encoded trait lists normalize correctly,
and games API tests use deterministic offline MLB responses instead of relying
on an implicit network or stub.
