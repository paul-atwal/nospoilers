# NoSpoil NFL backend

This FastAPI service calculates spoiler-free excitement scores for completed NFL games.

## Data flow

1. The background task checks the ESPN scoreboard near expected game end times.
2. The service tries to load play-by-play win probability from nflverse.
3. If recent nflverse data is not available, the service uses ESPN win probability.
4. The excitement calculator uses win-probability volatility and a comeback bonus.
5. The result is saved in Redis or a local JSON file.

## Local setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The service runs at `http://localhost:8000`.

You can also use the helper script:

```bash
./run.sh
```

## Tests

Install the development dependencies and run the backend tests from the repository root:

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

## API

### `GET /`

Returns service health and the number of cached games.

### `GET /api/excitement/{game_id}`

Returns the excitement score for one ESPN game ID.

Example response:

```json
{
  "game_id": "401671755",
  "excitement_score": 8.5,
  "cached": true
}
```

### `GET /api/excitement/week/{week}`

Returns excitement scores for a week.

Query parameters:

- `season`: NFL season year. The current default in the code is 2024.
- `season_type`: `REG` or `POST`.

### `POST /api/refresh-game/{game_id}`

Forces a new lookup for one game. This endpoint is intended for maintenance and testing.

## Score storage

The backend uses three storage layers:

1. Process memory for fast reads.
2. Redis when `REDIS_URL` is set.
3. `data/wp_cache.json` as a local fallback.

The local file does not survive a restart or deployment on hosts with a temporary filesystem. Use Redis with persistence when saved scores must survive those events.

The cache currently includes raw win-probability and score history. This makes the file larger than the live API needs, but it allows scores to be recalculated later.

## Background checks

The scheduler groups games by kickoff time. It checks ESPN every five minutes during expected game-ending windows and refreshes the schedule at intervals outside those windows.

## Data sources

- ESPN scoreboard and game summary endpoints
- nflverse play-by-play data

These upstream formats can change. Test schedule, identifier mapping, and win-probability parsing before each new season.

## ESPN sync application entry point

NS-007 provides `backend.nospoil_nfl.sync.handler.lambda_handler`. It accepts
only these event shapes:

```json
{"mode":"manual_preseason","scheduledTime":"2026-08-01T17:00:00Z","season":2026}
{"mode":"daily_near_term","scheduledTime":"<aws.scheduler.scheduled-time>"}
{"mode":"weekly_remaining","scheduledTime":"<aws.scheduler.scheduled-time>"}
{"mode":"live_tick","scheduledTime":"<aws.scheduler.scheduled-time>","season":2026}
```

The manual season is checked against ESPN metadata. Daily and weekly work gets
the season, phase, current week, and remaining week list from ESPN metadata.
The live season selects saved games before any source call; if work is due, the
returned ESPN metadata must match it. Update that EventBridge input only after
the preseason import verifies the new source season.

Required environment:

- `NOSPOIL_GAMES_TABLE`: DynamoDB games table name.
- `NOSPOIL_SCHEDULE_INDEX`: season/schedule index name. It defaults to
  `season-schedule-index`.
- `NOSPOIL_ESPN_TIMEOUT_SECONDS`: bounded timeout for each ESPN request. It
  defaults to 8 seconds and cannot exceed 8 seconds.
- `NOSPOIL_ESPN_SUMMARY_TIMEOUT_SECONDS`: bounded timeout for each provisional
  ESPN summary request. It defaults to 5 seconds and cannot exceed 5 seconds.

A live tick passes due final-game IDs directly from `ScheduleSyncService` to
the provisional-rating service in the same Lambda invocation. At most two
games are attempted per tick, in effective due-time order. The sync service
returns ordered IDs; the rating service strongly reads at most four of them to
find eligible games. The rating service stops before revalidation or another
summary request when the Lambda has less than 12 seconds remaining. Expected
ESPN failures retry after 1, 3, and 10 minutes. A fourth expected failure marks
the ESPN provisional path unavailable. Schedule-import modes do not make
summary requests; the next live tick finds their durable due work.

NS-013 must deploy these settings as one unit:

- One sync Lambda with handler
  `backend.nospoil_nfl.sync.handler.lambda_handler`, a 45-second function
  timeout, reserved concurrency `1`, no provisioned concurrency, and one
  published version exposed through `live` and `schedule` aliases.
- Direct EventBridge Scheduler Lambda targets with flexible windows disabled.
  Use UTC expressions `rate(1 minute)` for live work,
  `cron(15 10 * * ? *)` for the daily near-term refresh, and
  `cron(45 10 ? * TUE *)` for the weekly remaining-season refresh. Daily and
  weekly runs are separated by 30 minutes. Each scheduled input uses the
  `<aws.scheduler.scheduled-time>` context attribute.
- Target the `live` alias from the live schedule and the `schedule` alias from
  the daily and weekly schedules. Apply both layers of asynchronous delivery
  policy because Scheduler invokes Lambda asynchronously:
  - Live Scheduler target and `live` alias Lambda event-invoke configuration:
    `MaximumEventAgeInSeconds=60`, `MaximumRetryAttempts=0`.
  - Daily and weekly Scheduler targets and `schedule` alias Lambda event-invoke
    configuration: `MaximumEventAgeInSeconds=900`,
    `MaximumRetryAttempts=2`.
- No queue or dead-letter queue in the normal sync path. The next current live
  tick replaces missed old work.
- A Scheduler execution role limited to `lambda:InvokeFunction` on the `live`
  and `schedule` alias ARNs. The Lambda role needs CloudWatch Logs writes and
  only the games-table `dynamodb:GetItem`, `dynamodb:PutItem`,
  `dynamodb:UpdateItem`, and `dynamodb:Query` actions, including query access
  to the schedule index.

The application also rejects live events older than two minutes and daily or
weekly events older than 15 minutes. It emits JSON logs without routine score
values at INFO level. Season-wide source reads accept at most 32 source-defined
weeks and use at most eight parallel requests. The ESPN request timeout settings
bound connect and read inactivity; they are not total HTTP wall-clock deadlines.
The Lambda timeout remains the hard execution stop and leaves 15 seconds
before the next one-minute live event. Lambda applies execution-error and
timeout retries from the invoked alias; Scheduler's separate policy only bounds
target delivery.
A missed live event is replaced by the next current tick. NS-013 owns the AWS
resources, versions, aliases, asynchronous invocation settings, IAM resources,
log retention, and alarms; this change does not create them.

## Scheduled nflverse reconciliation

`.github/workflows/reconcile-ratings.yml` runs every six hours at minute 37 UTC.
The offset is intentional: GitHub notes that scheduled workflows can be delayed
or dropped during periods of high load near the top of an hour. The workflow
also supports manual `due`, `correction`, and `verify` runs. Use `correction`
for all confirmed final games, or pass one final ESPN game ID for a targeted
repair. Use `verify` before a season starts to load and validate nflverse
schedule and play data without reading or writing DynamoDB.

Normal reconciliation selects due unconfirmed final games from the existing
`season-schedule-index` query before downloading a season. Initial eligibility
is six hours after the provisional calculation, or six hours after the durable
final observation when no provisional calculation exists. A later source
failure is retried after 6 hours, 12 hours, then 24 hours (capped at 24 hours).
A due item more than 18 hours beyond its initial eligibility raises a workflow
failure and a GitHub error annotation. Later retry times do not reset that
overdue clock. Routine not-ready retries are successful unless they are
overdue.
Confirmed ratings are never downgraded; correction runs preserve a confirmed
rating when source validation fails.

The staging environment uses these repository or environment variables:

- `NOSPOIL_GAMES_TABLE`: required for `due` and `correction`.
- `NOSPOIL_SCHEDULE_INDEX`: optional index name; defaults to
  `season-schedule-index`.
- `NOSPOIL_RECONCILE_ROLE_ARN`: required AWS role ARN for `due` and
  `correction`.
- `NOSPOIL_AWS_REGION`: required AWS region for `due` and `correction`.
- `NOSPOIL_NFLVERSE_TIMEOUT_SECONDS`: optional source timeout greater than 0
  and no more than 60 seconds; the default is 20 seconds.

The workflow uses GitHub OIDC and short-lived AWS credentials. It does not use
long-lived access keys or repository secrets. The eventual least-privilege
staging role needs only `dynamodb:Query` on this table's season-schedule index
and `dynamodb:GetItem` and `dynamodb:UpdateItem` on the games table. Its trust
policy must restrict the OIDC audience to `sts.amazonaws.com` and the subject
to this repository's `staging` environment (`repo:<OWNER>/<REPO>:environment:staging`;
replace the placeholders during NS-013 provisioning). NS-013 provisions the
role, trust policy, table permissions, variables, and alarms; NS-009 creates no
infrastructure.

GitHub can automatically disable scheduled workflows in public repositories
after 60 days without repository activity. Re-enable the workflow in GitHub
and run manual `verify` before relying on the next scheduled run. See the
[GitHub scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
and [workflow enable/disable documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
