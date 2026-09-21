Round match order
=================

Future presentation uses `ordered_matches`: kickoff, NFKC-normalized,
trimmed, case-folded home name, then away name and ID for exact duplicates.
Draft membership remains unchanged. No result-arrival timestamp is used.

`promote_round` atomically freezes the complete slot ID list and activates
the Round. Model activation also freezes complete rounds; constructing an
already-active Round freezes when its final slot is saved. Bulk activation
must use `promote_round`, not QuerySet.update (which bypasses model hooks).
Consumers use the snapshot even after kickoff/name edits. SWAP replaces the
effective fixture inside the original slot, never its position.

Round chronology follows existing ranking_date ascending, with ID only as
the deterministic tie-breaker for rounds sharing a date. Scoring's normal
TYP success drives SERIA, including effective VAR and DOUBLE PICK selections;
GOLE and bonus points do not. Unresolved, cancelled and absent predictions are skipped, preserving
existing eligibility. Later results trigger a full deterministic rebuild.

Migration 0022 snapshots existing active and settled rounds from currently
available kickoff/name data. Original historical kickoff values are not
available: rescheduling before migration cannot be reconstructed. Future
rounds remain dynamic. Run `manage.py rebuild_streaks` after migration to
refresh existing profile/achievement SERIA progress and context. Previously
unlocked achievements are retained; no scoring or trophies are recalculated.
