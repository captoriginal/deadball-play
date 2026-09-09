# Deadball Play — Phase 19 Review

## Outcome

Deadball Play now follows MLB's automatic-runner procedure. Every regular-season
half-inning beginning with the 10th places the active player immediately before
the scheduled leadoff hitter on second base. If the first lineup position is
due, the ninth-position player is selected. Because the live lineup is used,
earlier substitutions are respected and the usual pinch-runner action remains
available.

Explicit postseason, spring-training, and exhibition game types start extra
innings with empty bases. Legacy canonical files that predate `game_type` are
treated as regular-season games so existing generated games gain the expected
behavior.

## Metadata Path

MLB `gameType` now travels from the schedule response into the Web cache, API
schema, canonical Play JSON, and read-only cached-game loader. Web startup adds
the nullable column to an existing database without discarding cached games.
Old databases remain readable directly by the command-line cache adapter.

## Presentation and Scoring

The transition narration names the automatic runner and the upcoming half.
Deadball Play records pitcher runs allowed rather than earned runs, so no earned
run is charged or displayed for the automatic runner. No fielding error is
created.

No Deadball resolution table or numeric rule changed in this phase.
