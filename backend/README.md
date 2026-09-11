# NoSpoil NFL backend

## Provider-free read API foundation

The read API foundation in `nospoil_nfl/api/` owns the spoiler-free snapshot
projection, polling advice, ETag material, and the checked-in 2026 season
calendar plus a source-verified 2020–2026 readable catalogue. It is safe for
the HTTP read process to import: it performs no
provider requests, downloads, calculations, writes, or scheduler startup. The
later HTTP adapter exposes these operations at
`GET /api/v1/bootstrap`, `GET /api/v1/weeks/{season}/{phase}/{week}`, and
`GET /api/v1/seasons/{season}`.

See [API.md](API.md) for the durable transport contract, response semantics,
ETag/cache behavior, CORS, Lambda environment, and calendar rollover procedure.

The active calendar is the 2026 schedule observed from ESPN's normalized
scoreboard calendar. The readable catalogue preserves the existing 2020–2025
backfill range and the active 2026 season. Each year was verified 2026-09-07 at
`https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={season}`.
The active boundaries, ordered catalogue, and
`espn-2020-2026-verified-2026-09-07` version are checked in at
`nospoil_nfl/api/calendar.py`. Rollover appends the new season without removing
historical identities.

The active backend is a FastAPI read Lambda and a separate sync Lambda. Both use
the `backend.nospoil_nfl` package. The old always-on service, Redis cache, JSON
cache, and background monitor were removed after the AWS cutover.

Dependency installs are resolved from one Python 3.11/Linux lock graph. Use
the bounded set that matches the process you are running:

```bash
# Development and CI
python -m pip install -r backend/requirements-dev.txt
# Read Lambda
python -m pip install -r backend/requirements-read.txt
# ESPN sync Lambda
python -m pip install -r backend/requirements-sync.txt
# Scheduled nflverse reconciliation
python -m pip install -r backend/requirements-reconcile.txt
```

To run the read API locally with DynamoDB Local, use the development dependency
set from the repository root:

```bash
python -m pip install -r backend/requirements-dev.txt
```

In terminal 1, run the pinned DynamoDB Local image on port 8000:

```bash
docker run --rm --name nospoil-dynamodb \
  --publish 127.0.0.1:8000:8000 \
  amazon/dynamodb-local:2.6.1 \
  -jar DynamoDBLocal.jar -inMemory -sharedDb
```

In terminal 2, configure the local endpoint and create the table/index once:

```bash
export NOSPOIL_GAMES_TABLE=nospoil-games
export NOSPOIL_DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:8000
export AWS_DEFAULT_REGION=us-west-2
export AWS_ACCESS_KEY_ID=local
export AWS_SECRET_ACCESS_KEY=local
export NOSPOIL_FRONTEND_ORIGINS=http://localhost:3000
python - <<'PY'
import boto3

resource = boto3.resource(
    "dynamodb",
    endpoint_url="http://127.0.0.1:8000",
    region_name="us-west-2",
    aws_access_key_id="local",
    aws_secret_access_key="local",
)
table = resource.create_table(
    TableName="nospoil-games",
    KeySchema=[{"AttributeName": "game_id", "KeyType": "HASH"}],
    AttributeDefinitions=[
        {"AttributeName": "game_id", "AttributeType": "S"},
        {"AttributeName": "season_key", "AttributeType": "S"},
        {"AttributeName": "schedule_key", "AttributeType": "S"},
    ],
    GlobalSecondaryIndexes=[{
        "IndexName": "season-schedule-index",
        "KeySchema": [
            {"AttributeName": "season_key", "KeyType": "HASH"},
            {"AttributeName": "schedule_key", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
        "ProvisionedThroughput": {
            "ReadCapacityUnits": 5,
            "WriteCapacityUnits": 5,
        },
    }],
    ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
)
table.wait_until_exists()
PY
python -m uvicorn backend.nospoil_nfl.api.local:app --host 127.0.0.1 --port 8001
```

In terminal 3, make a real GSI-backed historical read. A new local table is a
known-empty `200` response with `games: []` and `pollAfterSeconds: null`:

```bash
curl --fail-with-body --include \
  http://127.0.0.1:8001/api/v1/weeks/2020/preseason/1
```

The deployed read Lambda handler is `backend.nospoil_nfl.api.handler.lambda_handler`.

To update the shared resolved graph deterministically, use Python 3.11 on
Linux with the pinned pip-tools version and review the resulting diff:

```bash
python3.11 -m pip install 'pip-tools==7.5.2'
cd backend
python3.11 -m piptools compile --resolver=backtracking --strip-extras \
  --output-file constraints.txt constraints.in
```

All install files apply `constraints.txt`; an incompatible dependency fails
the install rather than silently resolving outside the shared graph. The
constraints file intentionally has no hashes so Linux deployment resolution
remains portable across supported architectures.

## Tests

Install the development dependencies and run the backend tests from the repository root:

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

## API

The active HTTP API is documented in [API.md](API.md). It exposes only the
read-only versioned routes under `/api/v1/`.

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
- `NOSPOIL_OPERATIONS_ROLE_ARN`: required AWS role ARN for schedule repair;
  this is the same generic environment-bound role/output alias and is limited
  to GetItem/UpdateItem on the exact table and Query on the exact schedule
  index.
- `NOSPOIL_IMPORT_ROLE_ARN`: staging-only role ARN for reviewed inventory
  import and targeted correction; it writes only `nospoil-staging-games`.
- `NOSPOIL_AWS_REGION`: required AWS region for `due` and `correction`.
- `NOSPOIL_NFLVERSE_TIMEOUT_SECONDS`: optional source timeout greater than 0
  and no more than 60 seconds; the default is 20 seconds.

The workflow uses GitHub OIDC and short-lived AWS credentials. It does not use
long-lived access keys or repository secrets. The eventual least-privilege
generic reconcile/operations role needs only `dynamodb:Query` on this table's
season-schedule index and `dynamodb:GetItem` and `dynamodb:UpdateItem` on the
games table. The separate staging-only import role additionally grants
`dynamodb:PutItem` on the exact `nospoil-staging-games` table. The import role's
trust policy must restrict the OIDC audience to `sts.amazonaws.com` and the
subject to this repository's fixed `staging` environment
(`repo:<OWNER>/<REPO>:environment:staging`; replace the placeholders during
NS-013 provisioning). NS-013 provisions the roles, trust policies, table
permissions, variables, and alarms; NS-009 creates no infrastructure.

GitHub can automatically disable scheduled workflows in public repositories
after 60 days without repository activity. Re-enable the workflow in GitHub
and run manual `verify` before relying on the next scheduled run. See the
[GitHub scheduled workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
and [workflow enable/disable documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
