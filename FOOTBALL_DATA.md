# Football data foundation

## Team Form / Last 5 (KARTA)

Round activation → database history request → Round + alternative Teams →
deduplicate provider IDs → team fixture sync → canonical Match records →
local last-five calculation → KARTA W/D/L.

Activation records `team_history_requested_at` without HTTP calls. Run the
following command immediately after activation, and schedule it externally
once each night (no scheduler is installed by this feature):

```powershell
.\.venv\Scripts\python.exe manage.py refresh_active_round_team_history
```

This services the currently active Round, including alternatives added after
activation. Successful runs record `team_history_synced_at`; a newer request
timestamp indicates an outstanding activation refresh. Initial form may be
empty until the command runs. Existing active Rounds are picked up even without
a request timestamp. Direct `QuerySet.update(is_active=True)` bypasses the model
activation hook; the command still discovers that active Round.

Each run refreshes the full previous 365 days through today's Warsaw date for
every distinct provider team, so retries, late results and rescheduling need no
separate incremental state. It requests each calendar year intersecting that
window plus the preceding starting-season year for cross-year club seasons
(normally three requests per team), with identical from/to date bounds. Each
returned fixture retains its own provider competition/season identity. Metadata
is fetched once per distinct competition-season per run. Teams without provider
IDs are skipped; their locally recorded completed matches can still supply form.
For a small diagnostic run use `--team 1118` (provider Team ID).

Existing importer upserts preserve game results, predictions, KW overrides and
Round membership. Partial errors are reported with a failing command exit; valid
history stays stored and rerunning is safe. No separate history fixture model is
used. **Prediction page never calls API-Football.**

One batched local history query supplies all normal and alternative fixtures.
Last five means completed, result-bearing matches strictly before the displayed
fixture's kickoff, shown oldest to newest. FT/AET/PEN read provider football scores;
penalty shootout tallies are ignored, so a tied PEN fixture is D. Manual finished
fixtures use their game scores. Cancelled/postponed/unfinished fixtures are omitted.
KARTA follows the effective SWAP fixture and exposes score-only hover/focus
tooltips. LISTA contains no form dots. No global scoring policy is changed.

The existing Competition → CompetitionSeason → Match and Team models remain
canonical. Nullable unique `api_football_id` fields distinguish provider-backed
records without invalidating manual/dev data. CompetitionSeason carries the
provider's starting season year, independent of its display label.

`api_football.py` owns HTTP, envelope checks and provider normalization.
`football_sync.py` owns transactional upserts and an explicit fact whitelist.
Credentials come from API_FOOTBALL_KEY in the process environment, falling back
to the project's ignored `.env`. No credentials or response bodies are logged.
The client uses a timeout and fails cleanly on HTTP, quota, provider-envelope or
JSON errors. Fixtures/leagues are nonpaginated for these requests; an unexpected
multi-page response is rejected rather than silently imported incompletely.
Provider reference: https://www.api-football.com/documentation-v3

## Import

Configured provider competitions include:

- `5` → UEFA Nations League
- `10` → Friendlies. This is the requested international-friendly feed; the
  provider's current 2026 response also contains age-group/women's labels, so
  consumers must not treat league identity alone as a guaranteed senior-men
  classification.

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py sync_football_fixtures --competition 5 --season 2026
.\.venv\Scripts\python.exe manage.py sync_football_fixtures --competition 10 --season 2026
```

Optional paired `--from 2026-09-24 --to 2026-09-27` parameters limit the request.
The command makes one competition request and one fixtures request. Tests mock
both and never use real quota. Repeated imports reuse provider IDs. Individual
bad fixtures roll back their team/match changes; good rows remain, errors are
reported and the command exits unsuccessfully. Network errors occur before any
writes. Counters distinguish fetched, created, updated, unchanged and errors.

Exact unbound Team names and Competition name+country can bind an existing
canonical record. Conflicting identities are rejected, not silently merged.
For differently named legacy records, bind provider IDs explicitly in admin
before importing. Existing season labels such as `2026/27` can be bound using
`provider_season=2026`; otherwise the new provider season label is `2026`.
Provider fixture IDs never match existing manual fixtures merely by team names.

## Ownership and results

Sync updates canonical team/competition facts, kickoff, provider status and
`provider_score` (goals and period scores). It does NOT update game `status`,
`home_goals`, `away_goals`, `result`, `finished_at`, scoring or achievements.
These provider result fields intentionally stage future result synchronization:
FT/AET/PEN/awarded/postponed mapping and scoring policy belong to the next task.
No scheduler, polling or automatic scoring is installed.

Match.save already triggers scoring for manual game-result edits. Sync instead
validates and performs a narrow ORM update/bulk insert, bypassing that hook.
It never modifies Round membership, Drafts, alternatives, overrides, predictions,
chips or frozen order. In-game legacy display names stay stable because existing
goal-team chips compare these strings; canonical Team names still refresh.
Identity changes to the teams/competition of an existing fixture are rejected
for manual review. Rescheduling changes calendar classification only, never
moves a fixture between Drafts or Rounds.

## Calendar weeks and admin

`CalendarWeek(year, week)` validates ISO year/week and supplies `.shift(weeks)`.
`current_week()` and fixture classification use `FOOTBALL_TIME_ZONE=Europe/Warsaw`.
The general Django timezone is unchanged. ISO week-year may differ from calendar
year (2021-01-01 belongs to KW 53/2020). Current and suggested next weeks appear
above Match and Draft admin lists; the suggestion is not a domain restriction.

The Match list exposes competition/season, kickoff, automatic/effective KW,
override status, provider status and game Round. Set both `kw_override_year` and
`kw_override_week` in the edit form, or clear both to return to automatic KW.
The effective-week filter uses the override when present. New/edited/imported
kickoffs update indexed automatic columns. Direct bulk kickoff writes outside
the import service bypass this maintenance and should not be used.

To inspect the bootstrap pool, filter Matches by UEFA Nations League, provider
season 2026 and effective KW 39/2026. The actual import on 2026-09-21 produced
156 fixtures; exactly 34 belong to KW39 and September 24–27 (Warsaw time).
Local shortcut: `/admin/matches/match/?competition_season__competition__id__exact=5&competition_season__provider_season=2026&effective_kw=2026-39`.
The competition's local ID is installation-specific; use the filter controls
on other databases. The first import assigned none of these fixtures to a Round.

## One initial bootstrap, then normal Drafts

1. In the filtered Match list select the desired 30 fixtures, then run
   **Create initial inactive Round from exactly 30 selected fixtures**.
   The action rejects assigned/started fixtures and existing Draft candidates
   or alternatives. It does not choose the 30 for you.
2. Rename the new Round and set its ranking date/modifier in Rounds admin.
3. Add four **Match alternatives**, each mapping one selected slot to one of
   the four unused fixtures. Leave all four alternatives outside any Round.
   The remaining 26 slots have no relationship and cannot use SWAP.
4. In Rounds run **Promote selected complete Round and freeze match order**.
   This uses the existing promotion service, deactivates the old Current and
   freezes the selected Round. Do not activate it until selection is complete.
5. For subsequent Rounds use existing Draft/DraftPair voting. Every resolved
   pair now writes the same MatchAlternative record with source_pair provenance;
   progressive Future population, tie resolution and saved predictions are
   otherwise unchanged. `draft_candidates(week)` is the future builder's reusable
   eligible-pool query. No drag-and-drop builder has been implemented.

MatchAlternative is a general game-domain relation, not a bootstrap-only field
or a second fixture model. A synthetic four-pair Draft would misrepresent voting
and fail the existing thirty-pair Draft completion rules, so bootstrap uses this
same canonical relationship directly. Migration 0023 copies the exact loser
selected by the old lookup for every resolved pair, plus backfills automatic KW.
Deleting a source pair removes its relation as the old lookup did; alternatives
themselves are protected from fixture deletion. Admin does not allow deletion of
exposed relationships, and used/locked relationships cannot be changed.

SWAP backend validation requires the exact persisted alternative, outside all
playable Rounds, unstarted and with eligible provider status (blank/NS/TBD).
Missing, incorrect, started or cancelled alternatives are rejected. Existing
frontend eligibility uses this same backend validation. Historical saved SWAPs
still resolve their persisted replacement directly.

## Review before automatic results

- Define regulation-time versus extra-time/penalty treatment explicitly.
- Review provider identity conflicts instead of guessing canonical merges.
- Changing kickoff can affect existing time-based locking; no new locking policy
  or rescheduling automation is introduced here.
- Keep the single-provider IDs behind this adapter; a second provider should add
  an explicit identity map rather than repurpose these IDs.
- Migration/import preserve existing dev data. A local database backup was taken
  before migration; do not reseed to install this foundation.
