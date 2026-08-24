# NoSpoil NFL backend

This FastAPI service calculates spoiler-free excitement scores for completed NFL games.

## Data flow

1. The background task checks the ESPN scoreboard near expected game end times.
2. The service tries to load play-by-play win probability from nflverse.
3. If recent nflverse data is not available, the service uses ESPN win probability.
4. The excitement calculator uses win-probability volatility and a comeback bonus.
5. The result is saved in Redis or a local JSON file.

## Local setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The service runs at `http://localhost:8000`.

You can also use the helper script:

```bash
./run.sh
```

## Tests

Install the development dependencies and run the backend tests from the repository root:

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

## API

### `GET /`

Returns service health and the number of cached games.

### `GET /api/excitement/{game_id}`

Returns the excitement score for one ESPN game ID.

Example response:

```json
{
  "game_id": "401671755",
  "excitement_score": 8.5,
  "cached": true
}
```

### `GET /api/excitement/week/{week}`

Returns excitement scores for a week.

Query parameters:

- `season`: NFL season year. The current default in the code is 2024.
- `season_type`: `REG` or `POST`.

### `POST /api/refresh-game/{game_id}`

Forces a new lookup for one game. This endpoint is intended for maintenance and testing.

## Score storage

The backend uses three storage layers:

1. Process memory for fast reads.
2. Redis when `REDIS_URL` is set.
3. `data/wp_cache.json` as a local fallback.

The local file does not survive a restart or deployment on hosts with a temporary filesystem. Use Redis with persistence when saved scores must survive those events.

The cache currently includes raw win-probability and score history. This makes the file larger than the live API needs, but it allows scores to be recalculated later.

## Background checks

The scheduler groups games by kickoff time. It checks ESPN every five minutes during expected game-ending windows and refreshes the schedule at intervals outside those windows.

## Data sources

- ESPN scoreboard and game summary endpoints
- nflverse play-by-play data

These upstream formats can change. Test schedule, identifier mapping, and win-probability parsing before each new season.
