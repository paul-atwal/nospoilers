# NoSpoil read API

The read-only API exposes exactly `GET`/`HEAD` for `/api/v1/bootstrap`,
`/api/v1/weeks/{season}/{phase}/{week}`, and `/api/v1/seasons/{season}`.
`OPTIONS` is available only as CORS preflight. The readable catalogue is
source-verified for 2020–2026: 2020 has preseason 1–5, regular 1–17,
postseason 1–5; 2021–2026 have preseason 1–4, regular 1–18, postseason 1–5
(189 ordered week identities). Seasons/weeks use positive
decimal 32-bit integer syntax and phase is exactly `preseason`,
`regular_season`, or `postseason` (`422` when malformed); unknown configured
calendar values are `404` before any read.

Bootstrap returns `activeSeason`, `currentWeek`, all source-known `knownWeeks`
in ascending season/phase/week order, `calendarVersion`, and
`pollAfterSeconds`. Active/current-week selection uses only the source-timed
2026 boundaries; the readable catalogue is a separate navigation and
validation concern. Week responses
contain `season`, `week`, `snapshotAsOf`, `pollAfterSeconds`, and `games`;
season responses omit `week`. A known empty week is a valid `200` with
`games: []` and `snapshotAsOf: null`. Nullable kickoff, scores, status details,
broadcaster, odds, records, and rating fields retain their domain nulls.
Timestamps are UTC RFC3339 `Z`. Week order is kickoff ascending, unknown last,
then game ID. Season order is rated first by score descending, then kickoff and
game ID; unrated games follow by kickoff and ID. One application/repository
operation consumes all results; a DynamoDB GSI query may internally use several
paginated AWS requests. Results are eventually consistent, not an atomic
multi-game view. Historical reads always return `pollAfterSeconds: null`;
known-empty historical weeks query once and return `200` with empty games,
while unsupported identities return `404` before any repository call. Rollover
appends a new active season without removing prior catalogue entries.

Each browser route is one client request and one application/repository
operation; the client does not make per-game requests.

Representative week/game JSON (all listed nullable fields are independently
nullable, including pregame versus postgame records):

```json
{"season":2026,"week":{"season":2026,"phase":"regular_season","week":1},"snapshotAsOf":"2026-09-11T04:02:00Z","pollAfterSeconds":30,"games":[{"id":"401872656","espnId":"401872656","seasonWeek":{"season":2026,"phase":"regular_season","week":1},"kickoffAt":"2026-09-10T00:20:00Z","home":{"id":"26","displayName":"Seattle Seahawks","abbreviation":"SEA","logoKey":"sea","pregameRecord":{"wins":0,"losses":0,"ties":0,"scope":"regular_season","snapshotAt":"2026-09-09T12:00:00Z"},"postgameRecord":null},"away":{"id":"17","displayName":"New England Patriots","abbreviation":"NE","logoKey":"ne","pregameRecord":null,"postgameRecord":null},"status":{"state":"in_progress","detail":"3rd Quarter","period":3,"clock":"08:14","score":{"home":17,"away":10}},"broadcaster":"NBC","odds":{"details":"SEA -3.5","updatedAt":"2026-09-09T12:00:00Z"},"rating":{"state":"pending","score":null,"source":null,"modelVersion":null,"calculatedAt":null,"confirmedAt":null,"confirmationSupported":true,"confirmationWorkRemains":false},"freshness":{"scheduleCheckedAt":"2026-09-10T00:15:00Z","scheduleUpdatedAt":"2026-09-09T12:00:00Z","liveSourceCheckedAt":"2026-09-11T04:02:00Z","liveStateUpdatedAt":"2026-09-11T04:02:00Z"}}]}
```

Season responses use the same envelope and game shape but omit `week` and
include all season games in Best-of-Season order. Bootstrap resembles
`{"activeSeason":2026,"currentWeek":{"season":2026,"phase":"regular_season","week":1},"knownWeeks":[{"season":2020,"phase":"preseason","week":1},...],"calendarVersion":"espn-2020-2026-verified-2026-09-07","pollAfterSeconds":300}`.

| Condition | Seconds |
|---|---:|
| Scheduled, more than 24h away | 3600 |
| Scheduled, 2–24h away | 900 |
| Scheduled, 15m–2h away | 300 |
| Scheduled, <=15m away or past due | 60 |
| In progress | 30 |
| Delayed after play started | 30 |
| Delayed before play started | 60 |
| Postponed | 900 |
| Cancelled | `null` |
| Final, pending ESPN rating | 60 |
| Final, provisional, supported confirmation | 3600 |
| Final, unavailable, supported confirmation | 3600 |
| Final, provisional or unavailable, unsupported confirmation | `null` |
| Final, confirmed | `null` |

Pending ESPN work takes precedence over confirmation. Empty current/future
weeks advise 300 seconds, past empty weeks advise `null`; bootstrap advises
300 seconds in-season and 86400 seconds after season end.

Success and 304 responses use `Cache-Control: public, max-age=0,
must-revalidate` and a quoted canonical SHA-256 ETag. `If-None-Match` supports
exact, weak, comma-list, and `*`; a match is an empty `304` with current ETag
and cache header. Clients retain their prior body and `pollAfterSeconds` after
304; ordinary clock ticks do not change the ETag, while stored changes,
polling-class threshold crossings, or calendar changes produce `200`.
Repository/data failures
are `503` with `{"detail":"snapshot temporarily unavailable"}`,
`Retry-After: 30`, and `Cache-Control: no-store`; all >=400 responses are
`no-store` and failures are never represented as empty success.

`NOSPOIL_FRONTEND_ORIGINS` is required: comma-separated exact, canonical
browser-serialized `http`/`https` origins without wildcard, path, query,
fragment, credentials, Unicode hostnames, or explicit default ports.
Only GET/HEAD/OPTIONS are permitted; `If-None-Match` is allowed and ETag,
Cache-Control, and Retry-After are exposed. Credentials are false. CORS is
browser policy, not authentication.

`NOSPOIL_GAMES_TABLE` is required. `NOSPOIL_SCHEDULE_INDEX` defaults to
`season-schedule-index`; AWS region is optional. If set,
`NOSPOIL_DYNAMODB_LOCAL_ENDPOINT` is passed to boto3 for local DynamoDB only.
Imports do not read env or contact AWS. Lambda handler is
`backend.nospoil_nfl.api.handler.lambda_handler`, a lazy Mangum adapter for
Function URL v2 events with lifespan off.

## Calendar rollover

Before preseason, fetch the ESPN scoreboard calendar using the existing
adapter, add the new source-timed active entries, and append its week identities
to the checked-in readable catalogue in a reviewed release. Never replace or
remove the already supported historical catalogue during rollover. Run focused
calendar/API retention tests, perform the manual preseason import, and then
activate the new season. Until then the final configured week remains selected
with slow polling; clients navigate only `knownWeeks`. The catalogue establishes
which identities may be read; populating historical data remains a separate
import operation.
