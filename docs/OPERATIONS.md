# NoSpoil NFL operations runbook

The public read URL is not an operator surface. Schedule repair is manual-only
through `.github/workflows/repair-schedule.yml`; choose `staging` first and use
exactly one game ID or one season/phase/week. The GitHub environment supplies a
short-lived OIDC session and an isolated table. Keep schedules disabled until a
staging run is verified.

Configure each GitHub environment from the matching CloudFormation outputs:
`NOSPOIL_GAMES_TABLE`, `NOSPOIL_OPERATIONS_ROLE_ARN`,
`NOSPOIL_RECONCILE_ROLE_ARN`, `NOSPOIL_AWS_REGION`, and
`NOSPOIL_SCHEDULE_INDEX` (`season-schedule-index`). Optionally set the bounded
`NOSPOIL_ESPN_TIMEOUT_SECONDS` (at most 8) and
`NOSPOIL_NFLVERSE_TIMEOUT_SECONDS` (at most 60). Never copy staging table or
role values into the production GitHub environment.

## Health and freshness

Check the read URL for the expected season/week envelope and complete game list.
Inspect the sync Lambda logs and CloudWatch alarms for errors or throttles. Each
game exposes `scheduleCheckedAt`, `liveSourceCheckedAt`, and rating retry data.
A schedule check older than one day during the active season is stale; a live
game without a recent source observation needs operator attention.

## Reconciliation and overdue work

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
their existing writers. Any mismatch, timeout, missing game, non-final source,
or conditional conflict fails closed; inspect the current item and rerun
explicitly. Rating correction starts only after repair succeeds.

## Outage, alarms, and rollback

For an ESPN or nflverse outage, leave the last valid durable data in place and
rerun after the provider recovers. Do not repeatedly broaden the repair scope.
Investigate `read-errors`, `sync-errors`, `read-throttles`, and
`sync-throttles` alarms and the finite-retention Lambda logs. If a deployment or
alias is unhealthy, disable the three schedules, keep the current Render
frontend/backend live, and roll the AWS environment stack or Lambda alias back
to the last verified artifact. Re-enable schedules only after a staging check
and an explicit production review.
