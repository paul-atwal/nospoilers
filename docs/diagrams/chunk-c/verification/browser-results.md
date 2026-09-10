# Chunk C browser and local integration results

Verified 2026-09-08 with the actual React application in installed Google
Chrome through Playwright 1.55.0. Playwright was installed with `--no-save` and
did not change `package.json` or `package-lock.json`.

## Deterministic browser pass

Commands:

```bash
node tests/browser/mockReadApi.mjs
VITE_DEV_API_PROXY=http://127.0.0.1:8011 npm run dev -- --host 127.0.0.1
node tests/browser/runChecks.mjs
```

Result: pass. The run covered bootstrap failure/retry; a connection reset; 304,
429, and 503 handling; retained stale data; scheduled/delayed/live/final and
postponed/resumed states; pending/provisional/confirmed/unavailable ratings;
unsupported confirmation stopping; page-return refresh coalescing; a known-empty
week; navigation races; same-game revealed-score updates; active-season top 10;
missing kickoff/record/logo fallback; local assets; Los Angeles and New York
time zones; keyboard reveal; desktop and 390px layouts. It asserted that there
were no ESPN or per-game rating requests and no unexpected page/console errors.
The only warning was the repository's existing Tailwind CDN production warning.

## FastAPI plus DynamoDB Local pass

Commands and topology:

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
VITE_DEV_API_PROXY=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1 --port 3001
BROWSER_APP_URL=http://127.0.0.1:3001 node tests/browser/runLocalApiCheck.mjs
```

Result: pass. Chrome rendered both GSI-backed week games, read the confirmed
rating and scheduled odds, kept the final score and postgame records hidden
until reveal, loaded four local logo images, and reduced the season view to the
eligible final game. A real historical read returned `200`, `games: []`, and
`pollAfterSeconds: null`; its ETag then produced a `304` response. There were no
page or console errors and no ESPN or per-game rating requests.

## Visual evidence

- `screenshots/scheduled-desktop.png`
- `screenshots/live-revealed-desktop.png`
- `screenshots/missing-narrow.png`
- `screenshots/season-desktop.png`
- `screenshots/season-narrow.png`
- `screenshots/bootstrap-retry.png`
- `screenshots/actual-local-api-week.png`
- `screenshots/actual-local-api-season.png`

All screenshots were visually inspected in the app's dark theme. The application
canvas is intentionally dark; the C1/C2 interaction diagrams use an opaque white
canvas with dark text and high-contrast connectors, so their meaning remains
theme-independent.
