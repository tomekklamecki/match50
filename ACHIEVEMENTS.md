# Achievement engine

Achievement and UserAchievement remain the definition and player-state tables.
Progress families use a stable family code (TYPY, GOLE, STREAK, MASTERY_EPL,
etc.) with persisted progress/current_tier. Existing tier-specific codes and
unlocks remain intact. Tier families never use stars.

AchievementOccurrence records the award context and earned timestamp.
The database enforces uniqueness on (user_achievement, event_key).
One-time/tier awards use "unlock"; repeatable awards use a stable domain key
such as "round:123". State, occurrence and notification updates share a
transaction with row locking. Twitter Expert retains all occurrences, with
displayed stars capped at five; further occurrences do not notify a sixth star.

AchievementNotification now permits one notification per occurrence key.
Existing notification consumption status is retained. TrophyFinish remains
separate and its ranking/tie sources have not changed.

## Entry points

Match.save already invokes recalculate_round_scores for affected rounds,
including SWAP owners. Scoring formulas are unchanged. evaluate_score dispatches
match evaluators for persisted score results and round evaluators only on
complete rounds. Per-user round awards additionally require a full settled
effective-match breakdown. Ranking trophies/champion awards run after the
whole round's score batch, not between players.

evaluate_month_trophies and evaluate_season_trophies are explicit finalization
entry points with period-end guards. The application/operator must call these
at period finalization; this change does not introduce a second scheduler.

EVALUATORS contains the match/round evaluator functions. To extend the engine,
add an event-specific evaluator and use _unlock with a stable code and context
key, or _tiers for progressive state. Failures roll back the evaluation and
surface to the caller; they are not silently discarded.

Lone Wolf uses the persisted kickoff population and the actual correct outcome,
including a second DOUBLE PICK outcome. Missing snapshots never produce awards.
This engine does not synthesize historical kickoff populations.

## Migration and backfill

Run migrate, then rebuild_achievement_state to fill current progress from
existing scores. The command is idempotent and does not reseed or alter
predictions, rounds, scores or trophies. It can create legitimate newly
discovered achievement notifications. Migration 0019 preserves old unlocks
as occurrences, including timestamps/context, and derives initial family state
from existing tier records. Backfill fills below-tier progress as well.

Profiles read saved achievement progress, tiers and streak state; they never
invoke achievement evaluators. History and general player statistics retain
their existing independent query behavior.

## Boundaries

Earned awards are monotonic: result corrections do not revoke historical
awards. A future audited revocation/reconciliation policy is needed if that
behavior is desired. Existing orphan TrophyFinish rows from development seed
runs are deliberately not deleted by these migrations.

Streak/mastery evaluators currently rebuild their aggregate at scoring events
to preserve chronology and existing semantics, not on HTTP requests. A large
installation can later add persisted per-match contributions without changing
occurrence identities or profile reads. Snapshot capture scheduling and
period-finalization scheduling remain responsibilities of the existing caller.
