CORE progression v2
===================

Six persisted families reuse UserAchievement, _tiers, _unlock and the
AchievementOccurrence/Notification ledgers. No schema migration is needed.
TYPY/GOLE use the best fully settled Round score. SERIA uses frozen match
order and the same TYP-success function as scoring.

DO IT AGAIN qualification events are chronological settled Round IDs or
independent streak start slots. The first event discovers the family with
zero completions; later events each add one. Crossing 8 is recorded once
per continuous streak, even across Rounds. Discovery is stored separately
from tier unlocking in the family's context_data. Public profiles only
read their owner's persisted state and redact undiscovered families.

Run `python manage.py rebuild_core` after deploying these rules. It rebuilds
only CORE from authoritative histories, including zero-state users. It
reconciles active tier flags under the new thresholds, so obsolete legacy
tiers no longer contribute points. Occurrences and notifications, including
consumption timestamps, are retained. Re-running does not duplicate them.
SECRET, Mastery, repeatable star families, trophies and ranking scores are
not rebuilt. No reset or reseed is needed.

`core_achievement_points(user)` counts exactly 2 per active CORE tier;
discovery and family aggregate rows award no extra points. There was no
existing achievement-points ranking service in this repository; match
rankings remain unchanged. CORE points are exposed separately for reuse.
