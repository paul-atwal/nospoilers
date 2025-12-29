# NFL Excitement Backend

FastAPI backend that fetches nflfastR play-by-play data and calculates excitement scores for NFL games.

## Features

- **Automatic Data Fetching**: Background task monitors ESPN API for game completions
- **Smart Scheduling**: Only checks for finished games during likely ending windows
  - Groups games by kickoff time slots (games within 30 min = same slot)
  - Calculates ending windows (3-4 hours after each slot's kickoff)
  - Avoids unnecessary API calls when no games are ending
  - Example: Sunday 10 AM games → check 1:00-2:00 PM, then stop until next slot
- **ESPN API Friendly**: Minimal API calls to avoid rate limiting
  - Only polls during ending windows (every 5 minutes)
  - Waits 15 minutes between checks when no games are ending
  - Refreshes schedule every 6 hours
- **Smart Caching**: Only fetches nflfastR data once per completed game
- **Excitement Algorithm**: Based on lead changes, late drama, comebacks, and close scores
- **REST API**: Simple endpoints for frontend integration

## Setup

1. Create virtual environment and install dependencies:
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Run the server:
   ```bash
   python main.py
   ```
   
   Or use the helper script:
   ```bash
   ./run.sh
   ```

The server will start on `http://localhost:8000`

## API Endpoints

### GET /
Health check endpoint

### GET /api/excitement/{game_id}
Get excitement score for a specific game (ESPN game ID)

**Response:**
```json
{
  "game_id": "401671755",
  "excitement_score": 8.5,
  "cached": true
}
```

### GET /api/excitement/week/{week}
Get excitement scores for all games in a week

**Query Parameters:**
- `season` (optional): Season year (default: 2024)
- `season_type` (optional): 'REG' or 'POST' (default: 'REG')

**Response:**
```json
{
  "week": 12,
  "season": 2024,
  "season_type": "REG",
  "games": [
    {
      "game_id": "401671755",
      "excitement_score": 8.5,
      "home_score": 28,
      "away_score": 27,
      "is_overtime": false
    }
  ]
}
```

### POST /api/refresh-game/{game_id}
Manually trigger a data refresh for a specific game

## How It Works

1. **Smart Scheduling**: On startup and every 6 hours:
   - Fetches this week's schedule from ESPN
   - Groups games by kickoff time slots (within 30 minutes)
   - Calculates likely ending windows (3-4 hours after each slot)

2. **Intelligent Monitoring**: 
   - Only checks ESPN API during ending windows (every 5 minutes)
   - When not in a window, waits 15 minutes before rechecking schedule
   - Example timeline for Sunday games:
     - 10:00 AM: Games kick off (Slot 1)
     - 1:00 PM: Start checking Slot 1 games every 5 min
     - 1:00 PM: New games kick off (Slot 2) 
     - 2:00 PM: Stop checking (Slot 1 complete)
     - 4:00 PM: Start checking Slot 2 games
     - 5:00 PM: Stop checking (Slot 2 complete)

3. **Auto-Fetch**: When a game finishes:
   - Automatically fetches nflfastR play-by-play data
   - Analyzes win probability history

4. **Excitement Calculation**: 
   - Lead changes (crossing 50% WP)
   - Late game drama (4th quarter volatility)
   - Comeback factor (largest deficit overcome)
   - Final score margin
   - Overtime bonus

5. **Caching**: Stores results in `data/wp_cache.json` to avoid re-fetching

## Data Storage

- Cache file: `data/wp_cache.json`
- Contains win probability history for all fetched games
- Persists across server restarts
