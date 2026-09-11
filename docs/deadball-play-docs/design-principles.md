# Deadball Play — Design Principles

## Purpose

## Repository and Package Boundaries

Deadball Play begins inside the existing `deadball-play` repository.

Treat the repository as a small monorepo:

- generator code remains responsible for MLB-to-Deadball conversion
- a shared core owns rules/state/events
- the Deadball Play app owns terminal interaction/session/presentation
- the existing web app stays logically separate

Keep dependency direction clean even though the components share one repository. Design for possible future extraction, but do not incur separate-repository or package-publishing overhead before it is needed.

This document defines the project-wide principles that should guide design and implementation decisions for **Deadball Play**.

These principles are intentionally short. When a later implementation choice is ambiguous, prefer the option that best preserves these constraints.

## 1. Deadball Fidelity Comes First

Version 1 should implement *Deadball: Baseball With Dice, Second Edition* as written for the Modern Era.

Do not replace a Deadball rule with a more detailed or more realistic baseball mechanic simply because the software could support one.

If the rulebook leaves a gap, document the application procedure used to bridge that gap. Do not silently present application procedure as a published Deadball rule.

## 2. Automate Procedure, Not Strategy

The computer should handle:

- dice
- arithmetic
- table lookups
- modifiers
- runner movement required by the rules
- pitcher-state bookkeeping
- legal-action filtering
- state transitions

The human player should still make the meaningful managerial decisions that Deadball gives them.

For a computer-managed opponent, use Deadball's Managerial Daring system rather than an unrelated baseball AI.

## 3. Keep the Paper Score Sheet Central

Deadball Play is not intended to replace the physical score sheet.

The software should:

- tell the player what happened
- provide clear scoring notation
- pause after completed plays
- wait for the player to record the result

The ideal minimum setup remains:

**computer + printed score sheet + pen**

## 4. Rules and Presentation Must Be Separate

The rules engine produces structured facts.

The presentation layer turns those facts into terminal text, scoring guidance, and later possibly voice.

Narration must never alter:

- outs
- runs
- runner advancement
- hit type
- defensive result
- substitutions
- pitcher state
- game state

## 5. Prefer Explicit State Over Inference

If a rule depends on something, store that fact in game state.

Examples include:

- current pitcher role
- consecutive scoreless innings
- active lineup
- removed players
- runners on base
- pitcher fatigue changes
- current batting-order index

Do not reconstruct important rules state from presentation text.

## 6. Every Important State Change Should Be Testable

Deadball's tables and numeric boundaries make it well suited to deterministic tests.

Rules behavior should be provable through tests rather than trusted because the output "looks like baseball."

## 7. Optional Rules Must Be Explicit

Optional systems should be represented as named ruleset options.

They should never quietly become part of standard Deadball behavior.

A saved game should record which optional rules were active.

## 8. Expanded Deadball Must Remain Separate From Core Deadball

Future additions may include:

- richer manager tendencies
- alternate baserunning systems
- additional commentary
- expanded defensive logic
- historical modes
- house rules

These should be clearly labeled and switchable.

The core Second Edition ruleset should remain available unchanged.

## 9. The Computer Should Reduce Friction

The project exists to make Deadball easier to play in settings where handling the rulebook, dice, tables, and other materials is inconvenient.

Features should generally remove procedural burden rather than add setup or menu complexity.

## 10. Preserve the Feeling of a Game Unfolding

Deadball Play should not feel like a batch simulator.

Resolve one decision and one play at a time.

Show the result.

Allow the player to score it.

Then continue.

The player's score sheet should still tell the story of the game when it is over.
