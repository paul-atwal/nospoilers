# Agent guide

Instructions for coding agents (Claude Code, Codex, and others) working in this
repository. [README.md](README.md) covers the product and local setup, and
[docs/OPERATIONS.md](docs/OPERATIONS.md) covers operations in depth.

## You own the release

The owner wants agents to ship their changes end to end. Don't hand the owner
commands to run, and don't stop to ask them to validate staging or approve
production for a change they asked for. Run the whole sequence below yourself
and report the result.

Stop and ask the owner only for:

- **AWS sign-in**, when `aws sts get-caller-identity` fails. Ask them to run
  `aws login --remote` in their terminal. They must copy the sign-in URL from
  the terminal pane itself, not from a chat transcript: transcripts can render
  `&` as `&amp;`, and that URL returns 400 Bad Request. Each run makes a new URL
  and code, so they should use only the newest one.
- **Changing what production does, beyond the code.** That covers flipping sync
  schedules (`NOSPOIL_SCHEDULE_STATE`), running the import, repair, or
  reconciliation workflows against production, and deleting stacks, tables, or
  data.

## Layout

| Part | Hosting | Deploys |
| --- | --- | --- |
| React frontend (`App.tsx`, `components/`, `services/`) | Render static site (`render.yaml`) | Automatically when `main` changes |
| Read API Lambda (`backend/nospoil_nfl/api/`) | AWS, CloudFormation stack `nospoil-<env>` | `infra/deploy.sh <env>` |
| Sync Lambda and schedules (`backend/nospoil_nfl/sync/`, `rating/`) | Same stack | `infra/deploy.sh <env>` |

There are two AWS environments, `staging` and `production`, in account
`945461162255`, region `us-west-2`. Both serve the Render origin
`https://nospoilers-web.onrender.com`. The production read URL is in the CSP in
`render.yaml`. Get either environment's URL from its stack's `ReadUrl` output.

## Checks

```bash
npm ci && npm test && npm run typecheck && npm run build
python3.11 -m venv venv && venv/bin/pip install -r backend/requirements-dev.txt
venv/bin/python -m pytest backend/tests -q
```

CI runs the same checks on every PR. On macOS, one test,
`test_season_import_and_reconciliation_failures_do_not_stop_later_seasons[malformed]`,
fails because macOS ships bash 3.2. It passes in CI on Linux.

## Release sequence

1. **Branch and PR.** Work on a branch, push, and open a PR against `main`.
   Wait for CI to pass.
2. **Staging, if the backend or `infra/` changed.** From the PR branch, run:

   ```bash
   bash infra/deploy.sh staging
   ```

   No environment variables are needed. The region comes from the AWS CLI
   config, and the frontend origin, alert email, and schedule state are read
   from the deployed stacks, so a code deploy never changes configuration.
   The script prints the values it chose; check them. A deploy takes about
   3–5 minutes, so run it in the background.
3. **Verify staging** (see below). If it fails, fix it on the branch and repeat
   step 2.
4. **Merge** the PR into `main` once CI passes and staging verifies. Render
   deploys the frontend from `main` automatically.
5. **Production.** From an up-to-date `main`, run:

   ```bash
   bash infra/deploy.sh production
   ```

6. **Verify production** the same way, and confirm that its sync schedules are
   still `ENABLED`.

Frontend-only changes skip steps 2, 3, and 5. After the merge, confirm that
Render published the change at the site URL.

## Verifying a deployment

- The stack reached `UPDATE_COMPLETE`:
  `aws cloudformation describe-stacks --stack-name nospoil-<env> --query 'Stacks[0].StackStatus'`.
- The read API answers with HTTP 200 for `GET <ReadUrl>api/v1/bootstrap` and
  for a week, for example `api/v1/weeks/2026/regular_season/1`.
- Schedules match what they were before the deploy:
  `aws scheduler get-schedule --name nospoil-<env>-<live|daily|weekly|read-warm> --query State`.
  `read-warm` is always `ENABLED`.
- Cold start stays fast. A deploy replaces the execution environments, so the
  next request starts cold. Check the `Init Duration` in the newest `REPORT`
  lines of the `/aws/lambda/nospoil-<env>-read` log group, and compare it with
  the previous deploy.
- Check the `read-errors`, `sync-errors`, and throttle alarms.

## Rollback

Check out the last good `main` commit and run `infra/deploy.sh <env>` again.
Never edit CloudFormation-owned resources, such as Lambda aliases, function
configuration, or schedules, by hand. If you change one temporarily for a
diagnosis, put it back right away. [docs/OPERATIONS.md](docs/OPERATIONS.md) has
the outage and data-repair procedures.
