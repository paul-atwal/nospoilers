# Chunk C browser harness

The mock is deterministic and uses only Node's standard library. Start it and
Vite from the repository root:

```bash
node tests/browser/mockReadApi.mjs
VITE_DEV_API_PROXY=http://127.0.0.1:8011 npm run dev -- --host 127.0.0.1
```

Select/reset a scenario without restarting either process:

```bash
curl 'http://127.0.0.1:8011/__set?name=lifecycle'
curl 'http://127.0.0.1:8011/__set?name=postponed'
curl 'http://127.0.0.1:8011/__set?name=unavailable'
curl 'http://127.0.0.1:8011/__set?name=unsupported'
curl 'http://127.0.0.1:8011/__set?name=missing'
curl 'http://127.0.0.1:8011/__set?name=race'
curl 'http://127.0.0.1:8011/__set?name=bootstrap-fail'
curl 'http://127.0.0.1:8011/__log'
```

Reload `http://127.0.0.1:3000` after selecting a scenario. `lifecycle` advances
through scheduled, 304 retention, 429 and 503 with Retry-After, delayed without
a score, two live score snapshots, provisional, and confirmed. The automated
pass also injects one connection reset. The season envelope polls once even
though its visible top ten is already rated; `unsupported` does not poll. The
request log is the network evidence for endpoint counts and paths.

For the repeatable automated Chrome pass, install the pinned runner without
changing application dependencies, then run:

```bash
npm install --no-save --package-lock=false playwright@1.55.0
node tests/browser/runChecks.mjs
```

For the real local integration, start the pinned DynamoDB Local container, then
export the complete disposable API environment before seeding or starting
uvicorn:

```bash
docker run --rm --name nospoil-dynamodb-chunk-c --publish 127.0.0.1:8000:8000 amazon/dynamodb-local:2.6.1 -jar DynamoDBLocal.jar -inMemory -sharedDb
export NOSPOIL_GAMES_TABLE=nospoil-games
export NOSPOIL_DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:8000
export AWS_DEFAULT_REGION=us-west-2
export AWS_ACCESS_KEY_ID=local
export AWS_SECRET_ACCESS_KEY=local
export NOSPOIL_FRONTEND_ORIGINS=http://127.0.0.1:3001
python -m tests.browser.seedLocalApi
python -m uvicorn backend.nospoil_nfl.api.local:app --host 127.0.0.1 --port 8001
```

In another shell, start Vite with
`VITE_DEV_API_PROXY=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1 --port 3001`,
then run `BROWSER_APP_URL=http://127.0.0.1:3001 node tests/browser/runLocalApiCheck.mjs`.
The seed is idempotent and creates one scheduled and one confirmed-final game
in 2026 regular-season week 1.
