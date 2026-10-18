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
The repository-level variable `NOSPOIL_RECONCILIATION_SCHEDULE_ENABLED`
defaults to off; set it to `true` only after production verification.
Each environment's import role is `NOSPOIL_IMPORT_ROLE_ARN` and may write only
that environment's games table. Optionally set the bounded
`NOSPOIL_ESPN_TIMEOUT_SECONDS` (at most 8) and
`NOSPOIL_NFLVERSE_TIMEOUT_SECONDS` (at most 60). Never copy staging table or
role values into the production GitHub environment.

## Deployment

Agents deploy end to end by following the release sequence in
[AGENTS.md](../AGENTS.md): staging from the PR branch, verify, merge, then
production from `main`. Run deployments from a checkout with AWS CLI
credentials for account `945461162255`. Locally that means `aws login --remote`
with the region set to `us-west-2`. AWS CloudShell in `us-west-2` also works.
Don't put access keys in files or logs.

```bash
bash infra/deploy.sh staging
```

When an environment variable is unset, `deploy.sh` keeps what the deployed
stacks already use: `NOSPOIL_FRONTEND_ORIGIN` and `NOSPOIL_SCHEDULE_STATE` from
`nospoil-<environment>`, and `NOSPOIL_ALERT_EMAIL` from `nospoil-foundation`.
So a code deploy never changes configuration. Production's sync schedules stay
`ENABLED` and staging's stay `DISABLED`. A stack created for the first time
starts with schedules disabled. Set a variable only to change that
configuration on purpose. For example, a rollback sets
`NOSPOIL_SCHEDULE_STATE=DISABLED`.

Verify each deployment as described under "Verifying a deployment" in
[AGENTS.md](../AGENTS.md).

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

The `nospoil-<environment>-read-warm` schedule pings the read Lambda every five
minutes so first page loads avoid a cold start. It is always enabled and
independent of `NOSPOIL_SCHEDULE_STATE`: it only initializes the read API, never
reads sources or writes DynamoDB, and the "keep schedules disabled" guidance
above refers to the sync schedules.

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

### Production inventory import

After staging verification and explicit production approval, dispatch the
manual-only `import-production.yml` workflow. It is fixed to the protected
`production` GitHub environment and validates `nospoil-production-games`; it
cannot select staging or another table. Confirm that the production environment
variables contain the CloudFormation `ImportRoleArn`, table name, region, and
`season-schedule-index` before dispatching. The workflow attempts all seven
seasons (2020–2026), then reconciles each season's captured supported IDs, and
fails with a complete import/reconciliation summary if any step needs
attention. Successful earlier writes are retained and safe to rerun.

Keep AWS schedules and the repository reconciliation gate disabled during the
import and release checks. If any source, envelope, AWS, or reconciliation step
fails, leave schedules disabled, retain successful imports, correct the cause,
and rerun the production workflow. Roll back by redeploying the last reviewed
revision with `NOSPOIL_SCHEDULE_STATE=DISABLED`; do not delete or manually
rewrite the retained production table.

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

Use the repair workflow when a durable final score or postgame record is stale. The command reads
the durable identity, fetches one ESPN scoreboard week, validates season/week,
game ID, both team IDs, and final status for the complete selected set, then
updates the final schedule status/score and any changed postgame records in one
conditional write. Pregame records remain frozen. ESPN postgame records remain
authoritative after they advance; if ESPN still returns the frozen pregame
record for a final regular-season game, the command derives the postgame record
from that final result. Logos, kickoff, broadcaster, odds, nflverse mapping,
rating, and retry metadata remain owned by their existing writers. A mismatch,
timeout, missing game, or non-final source
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
retained games table and current Render frontend remain the data and traffic
rollback points. Re-enable schedules only through a reviewed stack
deployment after staging passes and production approval is explicit.
