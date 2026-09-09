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

For the real local integration, start the pinned DynamoDB Local container, run
`python -m tests.browser.seedLocalApi`, then start
`backend.nospoil_nfl.api.local:app` with the environment documented in the
root README. Start Vite with `VITE_DEV_API_PROXY=http://127.0.0.1:8001` and
run `BROWSER_APP_URL=http://127.0.0.1:3001 node tests/browser/runLocalApiCheck.mjs`.
The seed is idempotent and creates one scheduled and one confirmed-final game
in 2026 regular-season week 1.
