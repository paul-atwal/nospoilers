# NoSpoil NFL

NoSpoil NFL helps you choose which NFL games to watch without showing the final score.

Weekly games are intended to stay in schedule order, but the current frontend still groups them by status. The **Best of Season** view sorts completed games by excitement score.

## How it works

- The React frontend reads schedules, teams, status, odds, and ratings from the versioned read API.
- The FastAPI backend calculates excitement scores from play-by-play win probability.
- Redis stores scores when `REDIS_URL` is set.
- A local JSON file is used when Redis is not available.

## Local setup

### Frontend

Requirements:

- Node.js
- npm

Install packages and start the frontend:

```bash
npm install
npm run dev
```

The frontend runs at `http://localhost:3000`.

Set `VITE_API_URL` to the API origin or base prefix (for local development use `http://127.0.0.1:8001`):

```text
VITE_API_URL=https://your-api.example.com
```

### Backend

Requirements:

- Python 3.11

Install packages and start the backend:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
export NOSPOIL_GAMES_TABLE=nospoil-games
export NOSPOIL_DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:8000
export NOSPOIL_FRONTEND_ORIGINS=http://localhost:3000
python -m uvicorn backend.nospoil_nfl.api.local:app --host 127.0.0.1 --port 8001
```

The read API runs at `http://127.0.0.1:8001` and serves `/api/v1/bootstrap`, `/api/v1/weeks/...`, and `/api/v1/seasons/...`. It requires the DynamoDB Local table and index to exist before startup.

See [backend/README.md](backend/README.md) for the complete DynamoDB Local setup and table-creation recipe. See [backend/API.md](backend/API.md) for the response contract.

## Environment variables

| Name | Service | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | Frontend | Optional API origin/base prefix; do not include `/api` or `/api/v1` |
| `REDIS_URL` | Backend | Optional Redis connection for shared score storage |
| `PORT` | Backend | Server port set by the hosting platform |

If `REDIS_URL` is not set, the backend writes to `backend/data/wp_cache.json`. This local file is useful for development. It is not reliable storage on a hosting service with a temporary filesystem.

## Deployment

[render.yaml](./render.yaml) defines a Render static site and FastAPI web service.

Before deployment:

1. Set the frontend `VITE_API_URL` during the frontend build.
2. Set `REDIS_URL` if scores must survive backend restarts and deployments.
3. Use persistent Redis storage if losing the cache is not acceptable.

## Main source files

- `App.tsx`: page state and weekly or season views
- `components/GameCard.tsx`: spoiler-safe game display
- `services/gameViewModel.ts`: display-ready API mapping and viewer-timezone kickoff labels
- `services/teamAssets.ts`: versioned local team logo map and text fallbacks
- `utils/scheduleWeek.ts`: structured season-week labels and navigation
- `utils/records.ts`: record formatting for display (records are supplied by the API)
- `backend/main.py`: FastAPI endpoints and background game checks
- `backend/nflfastr_fetcher.py`: play-by-play loading and cache access
- `backend/nospoil_nfl/rating/`: primary excitement score calculation
- `backend/nospoil_nfl/game/`: canonical game models, rules, typed updates, and the new DynamoDB repository
- `backend/excitement_calculator.py`: compatibility calculation wrapper for legacy scripts

During the playoffs, the app shows regular-season records unchanged by score reveal. Cumulative playoff records remain planned for later design and implementation.
