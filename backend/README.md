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
weeks and use at most eight parallel requests. The ESPN setting bounds connect
and read inactivity; it is not a total HTTP wall-clock deadline. The Lambda's
45-second timeout is the hard execution limit and leaves 15 seconds before the
next one-minute live event. Lambda applies execution-error and timeout retries
from the invoked alias; Scheduler's separate policy only bounds target delivery.
A missed live event is replaced by the next current tick. NS-013 owns the AWS
resources, versions, aliases, asynchronous invocation settings, IAM resources,
log retention, and alarms; this change does not create them.
