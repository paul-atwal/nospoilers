# C2 contract: viewer migration and core access

## Scope

C2 finishes the frontend migration by replacing C1's compatibility mapping with display-ready view data and connecting `App.tsx`, `components/Header.tsx`, and `components/GameCard.tsx`. It adds a small versioned team-asset map plus bundled assets, focused component/view-model tests, and documentation for asset provenance and frontend API configuration. It does not change backend behavior, add features, redesign the page, or add a state framework.

## Target files and ownership

- `services/gameViewModel.ts` (or an equivalently narrow module) maps `ApiGame` into a presentation record. It owns status labels, explicit rating presentation, independent pregame/postgame records, kickoff formatting, optional score handling, and team display/logo lookup. It does not derive source records, rankings, or outcomes.
- `services/teamAssets.ts` owns a versioned map keyed by stable team ID/logo key and returns a local asset URL or `null`. Unknown and historical identities use API display name/abbreviation text fallbacks. `public/team-logos/` contains only bundled assets; a provenance file records retrieval source/date and identifiers. No runtime ESPN or remote image URL is allowed.
- `App.tsx` retains C1 request/selection ownership. It passes server-ordered weekly games through unchanged and, for active-season Best of Season, filters the API's deterministic order to the first ten eligible final regular/postseason games with explicit provisional/confirmed scores. It derives polling only from the full response envelope, never the visible top ten.
- `components/Header.tsx` remains presentation-only. It uses native buttons with descriptive accessible names, native disabled states at catalogue ends, and visible `:focus-visible` treatment.
- `components/GameCard.tsx` receives display-ready status, records, kickoff, rating, and optional scores. It owns only per-card reveal UI. Reveal is keyed to `game.id`, survives updates to that ID, and resets if the component receives another ID.

## Invariants

1. Hidden cards expose no score, winner/tie styling, result summary, or score-derived accessible text/title/live-region content.
2. A score is revealable only when both values exist. Missing optional score never becomes `0-0`, `NaN`, or an enabled misleading reveal action.
3. Hidden records use only `pregameRecord`; after reveal they may use `postgameRecord` when supplied. Missing pregame remains unknown even if postgame exists. The card performs no record arithmetic. Playoff record scope remains the prepared regular-season snapshot.
4. `pending`, `provisional`, `confirmed`, and `unavailable` ratings have distinct text/visual treatment. A provisional score is labeled provisional; pending/unavailable do not show a spinner or sentinel. Transport stale/error presentation remains separate.
5. Weekly order equals API order. Best of Season excludes preseason and non-final/upcoming games, keeps the API's rating/tie order, and displays at most ten. Empty/pending season data has a clear message.
6. Kickoff uses the viewer's timezone with an explicit zone label and deterministic unknown fallback (`Kickoff time TBD`). Invalid input is treated as unknown. Tests cover two named zones and a DST boundary.
7. Logo lookup uses stable identity plus local files only. Missing, unknown, or broken assets fall back to readable abbreviation/name text without an external fetch.
8. Every interactive control is a native button with a useful label and visible keyboard focus. Status/error announcements are useful but never contain hidden results.

## Failure and fallback branches

- Unknown logo key/team ID or failed local image load: hide the image and show the supplied abbreviation (or display-name initial as last resort).
- Missing pregame record: show `--`; do not substitute postgame.
- Missing kickoff or invalid timestamp: show `Kickoff time TBD` without constructing a date.
- Pending rating: show a stable pending label. Unavailable rating: show unavailable and no retry spinner; API refresh lifecycle remains responsible for later changes.
- Final game without both score values: do not render/enable reveal and do not announce a result.
- Temporary API failure with retained data: cards continue rendering; stale/error status stays outside domain rating labels.

## C2 checks

- View-model tests for all rating states, optional score, independent records, explicit timezone labels, DST behavior, unknown kickoff, local logo lookup, and unknown identity fallback.
- Component tests for hidden/revealed/updated scores, identity reset, no hidden accessible leak, missing score, postgame record reveal, missing pregame, rating labels, image failure fallback, and keyboard activation/focus classes.
- App-level tests for weekly order and active-season top-ten eligibility/order without altering envelope polling.
- Focused C2 tests, then frontend tests, typecheck, build, and `git diff --check`.

