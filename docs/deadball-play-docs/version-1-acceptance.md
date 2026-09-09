# Deadball Play - Version 1 Acceptance

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
- the installed command and a real full-screen pseudo-terminal session; and
- the frontend production build.

The unified release run passed 709 tests: 257 core tests, 283 generator tests,
105 play/session/TUI tests, and 64 backend tests. The Vite
production build also completed successfully. The complete-game dashboard test
rendered 148 intermediate screens plus the final state during its 74-action
seeded game.

Run the complete gate from the repository root with:

```console
./scripts/check-deadball-v1
```

The script reports the optional Tauri desktop compile separately and runs it
when Cargo is installed. The Python and Web release paths do not require Rust.

The local licensed Deadball rulebook remains ignored and is not part of the
release artifact or any GitHub-bound change.

## Legacy Exclusions Closed

The generator's older scorecard-fill tests and the backend's older games API
tests are now part of the same green release gate. Simple one-table scorecard
templates remain compatible, double-encoded trait lists normalize correctly,
and games API tests use deterministic offline MLB responses instead of relying
on an implicit network or stub.
