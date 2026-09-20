# Prediction UI verification

The Current and Future pages share one form and one JavaScript controller. LISTA
and KARTA are presentations of the same inputs. Explicit view preferences use
`localStorage`; otherwise desktop starts in LISTA and mobile in KARTA.

Dirty card navigation awaits the existing `/typy/` save handler. A `card_match` request
merges the other slots from persisted state, retains whole-round chip and GOLE
validation, and writes only the requested slot within the atomic save. The
response contains authoritative saved values. Enhanced list saves use the same
handler. Saving a Future list returns to the Future tab. The new UI preserves
locked GOLE values when CHANGE_MIND edits an outcome after kickoff.

Chip eligibility metadata comes from `ChipAssignment.clean()`. The client uses
backend-provided outcome limits and allowed outcomes; backend validation remains
authoritative. Existing scoring, Draft/lifecycle services, effective-match
resolution, result cards, models, migrations, and existing tests were not edited
for this task. Tests use an isolated database, not `db.sqlite3`.

## Files changed for this task

- `matches/views.py`: card request adapter, JSON responses and presentation context.
- `matches/services/prediction_ui.py`: eligibility metadata and saved state.
- `matches/templates/matches/obstaw.html`: shared controls, toolbar and dialogs.
- `matches/static/matches/predictions.js`: shared state, navigation and feedback.
- `matches/static/matches/predictions.css`: responsive list/card presentation.
- `matches/test_prediction_ui.py`: 17 backend/presentation regression tests.
- `matches/test_prediction_browser.py`: 12 opt-in real-browser integration tests.
- `PREDICTION_UI.md`: this verification guide.

The workspace contained other changes before this task; they were preserved.

## Tests

Initial implementation baseline: 52 tests; initial implementation final: 69 tests.
UX polish baseline: all 69 tests passed with browser checks enabled (31.535 seconds).
UX polish final: all 73 tests passed with browser checks enabled, including all
existing backend tests and six real-browser tests. Django system checks reported
no issues. Four browser scenarios were added and two were updated for the new UX.

Normal suite:

```powershell
.\.venv\Scripts\python.exe manage.py test
```

Include browser tests (Playwright must be installed; they use installed Edge):

```powershell
$env:MATCH50_BROWSER_TESTS='1'
.\.venv\Scripts\python.exe manage.py test
```

The backend tests cover Current/Future persistence, partial availability and
Future-to-Current attachment, unknown/stale cards, untouched slots, chip reuse,
DOUBLE PICK, GOOOOOOOAL!, SWAP replacement/removal, GOLE limits and confirmation,
atomic rollback, kickoff locks, CHANGE_MIND, and persisted response state.
Browser tests cover deferred navigation, failed saves, retained selections,
shared views, remembered preference, goal selection, chip disabling, last-card
completion, tab navigation and viewport widths from 320 to 1440 pixels.

## Focused UX polish

This pass changes only `predictions.js`, `predictions.css`,
`test_prediction_browser.py`, and this guide. Backend services, templates,
result cards, scoring, persistence, lifecycle and data are unchanged.

Each editable row has a snapshot of its loaded/saved outcomes, GOLE and chip.
Current values are compared with that snapshot, so reverting an edit is clean.
Successful responses replace the saved snapshot. Clean navigation skips both
validation and POST requests; dirty navigation still awaits successful persistence.

Dirty rows with GOLE/chip but no standard outcome show an inline message and an
outlined outcome control. Incomplete DOUBLE PICK receives its own inline message.
Neither state sends a request or loses selections. Missing optional GOLE is valid.
DOUBLE PICK's unselected third option becomes disabled and neutral after two picks.

Finishing the final available card returns to LISTA without changing the round URL
or overwriting the user's explicitly selected default view preference. A failed
save remains on the card. Clean final cards go straight to the review list.

Desktop editable rows share fixed league, outcome, GOLE, CHIP and status columns
with a flexible, truncated team-name column. Rows are 54 pixels tall. Narrower
screens use a controlled multiline layout. Status distinguishes saved (check),
untouched (circle), unsaved edits (dot) and incomplete (exclamation mark).

GOLE uses the same dialog and input in both views, anchored to its list button on
desktop and displayed as a bottom sheet on mobile. Selected LISTA GOLE labels show
only the number. The CHIP popup reuses backend-provided eligibility and used-chip
explanations. Clicking a chip now immediately submits a per-match request through
the existing save handler. The row, summary and availability remain unchanged
while pending or on failure, then reconcile from the successful server response.
DOUBLE PICK prepares its two outcomes only in the main row before committing the
chip; it never invents a second outcome or submits an invalid one-pick assignment.
Chip clicks explicitly commit the partial Round using the existing confirmation
flag; the backend still enforces GOLE limits and all chip validation. Other rows'
unsaved edits are not submitted. No new domain rules were introduced.
Browser tests check every control column's horizontal alignment,
at least eight visible desktop rows below the summary, SWAP display changes, and
unchanged result-card markup when switching views.

The missing-persistence bug was reproduced before this correction: removing a
persisted BANKER in LISTA produced zero POST requests, changed only the local row,
and left both the database assignment and sticky used status intact. Previously
the popup only invoked the local card edit handler. The regression now starts
with a persisted BANKER and verifies removal and addition without Save/navigation,
including availability on another match. The same path is exercised for all five
chip types, delayed requests, failed additions/removals and unrelated dirty rows.
After this correction, the full suite passed: 76 tests, including nine browser
tests, in 45.008 seconds. Django system checks reported no issues.

## League identity, kickoff and exact-card navigation polish

Baseline for this pass: 76 tests passed before changes (38.010 seconds).
Final: all 81 tests passed, including 12 browser tests, in 46.732 seconds. Django
system checks reported no issues.

- `matches/services/league_flags.py` is the single competition-to-football-country
  mapping. Small inline SVGs render consistently on Windows, including England's
  St George's Cross for Premier League, never the Union Jack. Unknown and
  international competitions receive no flag. Stored league names are unchanged.
- `matches/templatetags/prediction_labels.py` and its package initializer expose
  that mapping to existing league labels in `obstaw.html` and `round_readonly.html`.
  Result-card structure, scores and interactions are unchanged.
- `matches/services/prediction_ui.py` supplies the same flags to JavaScript and
  formats kickoff using Django's active timezone. `predictions.js` and
  `predictions.css` place kickoff under the league and add a fixed shortcut column
  while retaining the 54-pixel desktop rows and fixed control widths.
- The main view toggle and row shortcut explicitly identify a navigation save.
  They use the existing partial-round confirmation flag without displaying a
  completion dialog. Explicit submit/final-card flows retain their confirmation.
  The shortcut selects the clicked element's index in the existing available-card
  array only after a successful save, so partial Future rounds use their actual
  sequence and keep their URL/tab.
- The chip popup has no prediction inputs. Because the unchanged backend requires
  exactly two outcomes before DOUBLE PICK can persist, choosing it temporarily
  prepares the main row. Its label explicitly says to choose two; the sticky
  summary remains persisted-only. The second outcome calls the same chip save
  path and unchanged response reconciliation. Failed preparation restores the
  previous outcomes/chip; selecting the pending chip again cancels preparation.
  Already-persisted DOUBLE PICK outcomes are edited only in the main row.

Tests cover country identity/fallback/escaping, timezone formatting, history labels,
flags in both views and progress badges, exact Current/Future navigation, failed
navigation, no completion popup on dirty view switches, row-only DOUBLE PICK,
failed DOUBLE PICK persistence, and unchanged chip synchronization regressions.
The existing alignment/density test now includes the shortcut column. Screenshots
were inspected at desktop and mobile widths.

Manual checks for this pass: inspect league flags and kickoff, open a later Current
or Future row directly, switch views with unsaved 1/X/2 edits but no GOLE, choose
DOUBLE PICK then its second outcome in the row, and verify a genuine final save
still offers partial-round confirmation. Check existing result cards for unchanged
layout and behavior. No scoring, domain, lifecycle, data or migrations changed.

## Manual smoke test

1. Open `/typy/` on desktop and mobile with no saved view preference. Check LISTA
   and KARTA defaults, then select a preference and reload.
2. Select an outcome and GOLE. Save forward and backward; switch views and reload.
   Confirm the saved selections and league/chip progress agree.
3. Activate DOUBLE PICK, select two outcomes, then remove it. Check that one
   outcome remains. Confirm GOOOOOOOAL! disables X and used chips explain where
   they were used. Try SWAP on a slot with a resolved Draft loser.
4. Open Future with partially populated matches. Verify available/predicted
   counts, save the last available card, then revisit it. No missing slots should
   be fabricated.
5. Simulate an offline save or use an incomplete DOUBLE PICK. Try Next, Previous,
   LISTA and another round tab. The page must stay on the card with selections
   intact. Restore connectivity/correct the selection and retry.
6. Inspect finished Current cards and Previous Round against the existing design.
   Check keyboard focus and GOLE dialog opening/closing on a phone.
7. Without editing, navigate both views, Next/Previous and round tabs. There should
   be no save requests or warnings. Select then undo an outcome/GOLE/chip and repeat.
8. Enter only GOLE 9 and attempt navigation. Check inline feedback, retained GOLE
   and no request; then choose an outcome and retry. Finish the final card and
   verify LISTA appears in the same round.
9. Review short/long team names and empty/selected GOLE and chips in LISTA. Check
   aligned columns, picker positioning, row status, and persisted corrections.

## Deliberately unchanged / limits

- Newly arriving Draft matches require a page refresh; no live polling was added.
- Browser close, refresh and browser-history navigation cannot reliably await a
  save. Unsaved changes trigger the browser's native leave-page protection;
  in-page links, round tabs, view switches, card buttons and logout await saving.
- Existing backend GOLE-only fallback is unchanged; the enhanced UI now catches
  dirty rows without a standard outcome before submitting them.
- No cross-tab conflict-resolution system was introduced. Browser automation was
  run in Edge; Safari/iOS should also receive a manual smoke test.
