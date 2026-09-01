# NoSpoil NFL

NoSpoil NFL helps you choose which NFL games to watch without showing the final score.

Weekly games are intended to stay in schedule order, but the current frontend still groups them by status. The **Best of Season** view sorts completed games by excitement score.

## How it works

- The React frontend gets schedules, team details, game status, and odds from ESPN.
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

Set `VITE_API_URL` if the backend is not available at `http://localhost:8000/api`:

```text
VITE_API_URL=https://your-api.example.com/api
```

### Backend

Requirements:

- Python 3.11

Install packages and start the backend:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The backend runs at `http://localhost:8000`.

## Environment variables

| Name | Service | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | Frontend | Full backend API URL, including `/api` |
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
- `utils/scheduleWeek.ts`: structured season-week labels and navigation
- `services/espnWeekMapper.ts`: ESPN season-type translation
- `utils/records.ts`: frontend pregame and postgame record calculations
- `backend/main.py`: FastAPI endpoints and background game checks
- `backend/nflfastr_fetcher.py`: play-by-play loading and cache access
- `backend/nospoil_nfl/rating/`: primary excitement score calculation
- `backend/nospoil_nfl/game/`: canonical game models, rules, typed updates, and the new DynamoDB repository
- `backend/excitement_calculator.py`: compatibility calculation wrapper for legacy scripts

During the playoffs, the app shows regular-season records unchanged by score reveal. Cumulative playoff records remain planned for later design and implementation.
