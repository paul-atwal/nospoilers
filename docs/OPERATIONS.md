# NoSpoil NFL operations runbook

The public read URL is not an operator surface. Schedule repair is manual-only
through `.github/workflows/repair-schedule.yml`; choose `staging` first and use
exactly one game ID or one season/phase/week. The GitHub environment supplies a
short-lived OIDC session and an isolated table. Keep schedules disabled until a
staging run is verified.

Configure each GitHub environment from the matching CloudFormation outputs:
`NOSPOIL_GAMES_TABLE`, `NOSPOIL_OPERATIONS_ROLE_ARN`,
`NOSPOIL_IMPORT_ROLE_ARN`, `NOSPOIL_RECONCILE_ROLE_ARN`,
`NOSPOIL_AWS_REGION`, and `NOSPOIL_SCHEDULE_INDEX` (`season-schedule-index`).
The staging import role is `NOSPOIL_IMPORT_ROLE_ARN` and may write only
`nospoil-staging-games`. Optionally set the bounded
`NOSPOIL_ESPN_TIMEOUT_SECONDS` (at most 8) and
`NOSPOIL_NFLVERSE_TIMEOUT_SECONDS` (at most 60). Never copy staging table or
role values into the production GitHub environment.

## Health and freshness

Check the read URL for the expected season/week envelope and complete game list.
Inspect the sync Lambda logs and CloudWatch alarms for errors or throttles. Each
game exposes `scheduleCheckedAt`, `liveSourceCheckedAt`, and
`confirmationWorkRemains`. The reconciliation workflow summary reports
aggregate retry and failure counts; its logs include retry attempt/error detail,
while the DynamoDB item is the source for the exact next-attempt time. A
schedule check older than one day during the active season is stale. During a
game window, a `liveSourceCheckedAt` value older than two minutes needs operator
attention.

## Reconciliation and overdue work

### Staging inventory import

The manual-only `import-staging.yml` workflow is fixed to the `staging`
environment and imports the reviewed 189-week catalogue (2020–2026) one
season at a time. The live legacy Render backend returned `cached_games: 0` on
2026-09-09 and there is no checked-in cache; no legacy data is copied. Each
season fetches exact ESPN week envelopes, treats verified empty weeks as valid,
and emits the exact nflverse-supported final IDs. A rerun is safe: conditional
writes preserve newer observations and confirmed ratings.

If a source, envelope, AWS, or reconciliation step fails, leave schedules
disabled, retain the successful season imports, fix the underlying issue, and
rerun that explicit staging workflow. An empty ID list is intentionally skipped.
Production import is not selectable here and remains NS-017-B.

Use the existing `reconcile-ratings.yml` workflow in `correction` mode for one
game or a named season, and `due` mode for routine work. A due game is overdue
more than 18 hours after its initial eligibility (six hours after provisional
calculation, or six hours after the durable final observation when no
provisional calculation exists). Later retry times do not reset that clock. Do
not clear a valid confirmed rating when the source is unavailable. GitHub can
disable scheduled workflows in a public repository after 60 days without
repository activity; if that happens, re-enable `reconcile-ratings.yml` and run
manual `verify` before relying on its schedule.

## Preseason and manual reconciliation

Before a season starts, import and verify every known preseason, regular-season,
and postseason week in staging. Confirm the source season/week envelope and the
frontend catalogue before enabling schedules. For a manual rating reconciliation,
select the smallest season or game scope and retain the workflow summary as the
operator record.

## One-game repair

Use the repair workflow when a durable final score is stale. The command reads
the durable identity, fetches one ESPN scoreboard week, validates season/week,
game ID, both team IDs, and final status for the complete selected set, then
updates only the final schedule status/score. Frozen records, logos, kickoff,
broadcaster, odds, nflverse mapping, rating, and retry metadata remain owned by
their existing writers. A mismatch, timeout, missing game, or non-final source
fails during the complete preflight before any mutation. A later conditional
conflict can occur after an earlier game in the week was already repaired; the
summary fails, but the applied writes are safe and an explicit rerun is
idempotent. Inspect the current items before rerunning. Rating correction starts
only after the complete repair succeeds, uses the returned durable season and
exact supported game IDs, and skips rating work for unsupported preseason or
Pro Bowl games.

## Outage, alarms, and rollback

For an ESPN or nflverse outage, leave the last valid durable data in place and
rerun after the provider recovers. Do not repeatedly broaden the repair scope.
Investigate `read-errors`, `sync-errors`, `read-throttles`, and
`sync-throttles` alarms and the finite-retention Lambda logs. If a deployment is
unhealthy, first redeploy the current revision with
`NOSPOIL_SCHEDULE_STATE=DISABLED`. Then check out or revert to the last reviewed
revision and run `infra/deploy.sh <environment>` with the same region, frontend
origin, alert address, and schedules still disabled. Do not move a
CloudFormation-owned alias manually. Confirm the environment stack reaches
`UPDATE_COMPLETE`, inspect its read URL and alias outputs, invoke the read URL,
and run a scoped staging repair/verification before any production retry. The
retained games table and current Render frontend/backend remain the data and
traffic rollback points. Re-enable schedules only through a reviewed stack
deployment after staging passes and production approval is explicit.
