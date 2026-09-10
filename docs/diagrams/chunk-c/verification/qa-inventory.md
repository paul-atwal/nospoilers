# Chunk C closeout QA inventory

| Claim / control / state | Functional browser check | Visual state and evidence |
| --- | --- | --- |
| Initial bootstrap failure is recoverable | `bootstrap-fail`: observe error, activate Retry, reach weekly page | Error/action at desktop width; no endless loader |
| Catalogue navigation is bounded and stale responses cannot win | `race`: navigate from delayed week 1 response to fast week 2; verify week 2 remains | Header label and disabled end controls |
| ETag body/poll retention and bounded transport recovery | `lifecycle` plus an intercepted connection reset: inspect request log for `If-None-Match`, 304, 429, 503, Retry-After, and network recovery; data remains usable | Scheduled card retained; stale alert during retry |
| Schedule lifecycle keeps refreshing | `lifecycle` and `postponed`: scheduled/delayed/postponed advance to live/resumed without reload | Stable card layout at each status |
| Ratings are explicit and polling follows the full envelope | `lifecycle`, `unavailable`, `unsupported`, and season view: observe pending/provisional/unavailable/confirmed; unsupported stops; season polls despite completed visible top 10 | Distinct non-spinner rating labels |
| Spoilers stay hidden and same-game revealed scores update | Reveal during live snapshot; wait for next score/final; confirm score changes while still revealed | Hidden versus revealed cards; no result in other text |
| Reveal never crosses identities and missing scores stay absent | `race`/`missing`: navigate or replace identity; verify no score/reveal for missing result | TBD/unknown state with no fake 0-0 |
| Records use prepared snapshots | Hidden regular game shows pregame; reveal shows postgame; missing pregame stays `--` | Record text before/after reveal |
| Best of Season uses active season and API order | Open Best of Season from another selected week; verify ranked 1–10 only, no preseason/upcoming/pending entries | Dense ten-card desktop and narrow layout |
| Team assets are local with text fallback | Inspect network for `/team-logos/{id}.png`; `missing` uses abbreviation fallback | Current logos and historical fallback both visible |
| Kickoff is explicit in viewer timezone | Run browser contexts in `America/Los_Angeles` and `America/New_York`; verify zone label; `missing` shows TBD | Kickoff labels in both contexts |
| Keyboard core flow works | Tab through brand/view/navigation/reveal/info; activate view, navigation, reveal with Enter/Space; inspect focus ring | Desktop and 390×844 narrow focus states |
| No source/per-game browser calls | Inspect browser/mock request log and performance entries | Only Vite, local assets, `/bootstrap`, `/weeks/...`, `/seasons/...` |
| No unexpected console/runtime errors or layout clipping | Assert no page errors and no console errors beyond the deliberately injected 500/429/503/reset diagnostics; inspect scroll/layout at 1440×900 and 390×844 | Saved viewport screenshots |

Exploratory scenarios: toggle views while a retry is pending; navigate during a
slow response; return focus after polling has stopped; force one local image
failure and confirm the abbreviation remains readable.
