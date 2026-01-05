"""
FastAPI backend for NFL excitement scores using nflfastR data.

Provides endpoints to:
- Get excitement scores for individual games or entire weeks
- Background task monitors ESPN API for game status changes
- Automatically fetches nflfastR data when games finish
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import requests
from datetime import datetime
from typing import Dict, Optional
import json

from nflfastr_fetcher import NFLFastRFetcher
from excitement_calculator import calculate_excitement_score
from espn_fetcher import fetch_espn_game_data

from game_scheduler import GameScheduler

app = FastAPI(title="NFL Excitement API")

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
fetcher = NFLFastRFetcher()
scheduler = GameScheduler()
game_status_cache = {}  # Track which games we've seen as Final


ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


async def check_game_statuses():
    """
    Smart background task that only checks for finished games during likely ending windows.
    - Fetches schedule once and calculates exact check times
    - Sleeps until next check window (no polling)
    - Stops monitoring when all games in a slot finish
    """
    while True:
        try:
            # Check if we should be monitoring right now
            should_check, game_ids_to_monitor, sleep_seconds = scheduler.should_check_now()
            
            if not should_check:
                # Not in a check window - sleep until next window or 6 hours
                await asyncio.sleep(sleep_seconds)
                continue
            
            # We're in a check window - fetch current scoreboard
            response = requests.get(ESPN_API_BASE, timeout=10)
            if response.status_code != 200:
                # Error fetching - wait and try again
                await asyncio.sleep(sleep_seconds)
                continue
            
            data = response.json()
            events = data.get('events', [])
            
            # Only check games from the current time slot
            games_to_check = [e for e in events if game_ids_to_monitor and e['id'] in game_ids_to_monitor]
            
            if not games_to_check:
                # Use all events if filtering didn't work
                games_to_check = events
            
            for event in games_to_check:
                game_id = event['id']
                status_state = event['status']['type']['state']
                status_detail = event['status']['type']['shortDetail']
                
                # Check if game just finished
                is_final = status_state == 'post' or 'Final' in status_detail
                
                if is_final and game_id not in game_status_cache:
                    # New completed game! Fetch nflfastR data
                    print(f"Game {game_id} finished. Fetching nflfastR data...")
                    
                    try:
                        wp_data = fetcher.fetch_game_wp(game_id)
                        if wp_data:
                            # Calculate excitement
                            excitement = calculate_excitement_score(
                                wp_data['wp_history'],
                                wp_data['home_score'],
                                wp_data['away_score'],
                                wp_data['is_overtime']
                            )
                            print(f"Game {game_id} excitement score: {excitement:.1f}")
                            # Mark as processed
                            game_status_cache[game_id] = {
                                'excitement': excitement,
                                'processed_at': datetime.now().isoformat()
                            }
                            # Remove from scheduler tracking
                            scheduler.mark_game_finished(game_id)
                    except Exception as e:
                        print(f"Error processing game {game_id}: {e}")
                elif is_final and game_id in game_ids_to_monitor:
                    # Game is final but we already processed it - remove from tracking
                    scheduler.mark_game_finished(game_id)
            
        except Exception as e:
            print(f"Error in background task: {e}")
            await asyncio.sleep(300)  # Wait 5 min on error
            continue
        
        # In check window - sleep_seconds will be 5 minutes
        await asyncio.sleep(sleep_seconds)


@app.on_event("startup")
async def startup_event():
    """Start background task on server startup."""
    asyncio.create_task(check_game_statuses())
    print("Background game monitor started")


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "NFL Excitement API",
        "cached_games": len(fetcher.cache)
    }


@app.get("/api/excitement/{game_id}")
def get_game_excitement(game_id: str):
    """
    Get excitement score for a specific game.
    
    Returns cached score if available, otherwise fetches from nflfastR.
    """
    # Check if we have it cached
    cached = fetcher.get_cached_game(game_id)
    
    if game_id == "401772779":
        print(f"DEBUG MAIN: Request for 401772779")
        if cached:
            print(f"DEBUG MAIN: Cached entry found. Score: {cached.get('excitement_score')}")
        else:
            print(f"DEBUG MAIN: No cache entry found.")

    if cached and 'excitement_score' in cached:
        return {
            "game_id": game_id,
            "excitement_score": cached['excitement_score'],
            "cached": True
        }
    elif cached:
        # Fallback for old cache format (shouldn't happen after backfill)
        excitement = calculate_excitement_score(
            cached['wp_history'],
            cached['home_score'],
            cached['away_score'],
            cached['is_overtime'],
            cached.get('score_history')
        )
        return {
            "game_id": game_id,
            "excitement_score": round(excitement, 1),
            "cached": True
        }
    
    # Fetch fresh data - try nflfastR first
    wp_data = fetcher.fetch_game_wp(game_id)
    
    # If nflfastR doesn't have it, try ESPN API (for recent games)
    if not wp_data:
        print(f"nflfastR data not available for {game_id}, trying ESPN fallback...")
        wp_data = fetch_espn_game_data(game_id)
        
    if not wp_data:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found or no WP data available")
    
    excitement = calculate_excitement_score(
        wp_data['wp_history'],
        wp_data['home_score'],
        wp_data['away_score'],
        wp_data['is_overtime'],
        wp_data.get('score_history')
    )
    
    if game_id == "401772779":
        print(f"DEBUG MAIN: Calculated fresh score: {excitement}")
    
    # Cache the result (even if from ESPN)
    fetcher.cache[game_id] = {
        'excitement_score': round(excitement, 1),
        'home_score': wp_data['home_score'],
        'away_score': wp_data['away_score'],
        'is_overtime': wp_data['is_overtime'],
        'wp_history': wp_data['wp_history'],
        'score_history': wp_data.get('score_history', []),
        'source': wp_data.get('source', 'nflfastR')
    }
    fetcher._save_cache()
    
    return {
        "game_id": game_id,
        "excitement_score": round(excitement, 1),
        "cached": False,
        "source": wp_data.get('source', 'nflfastR')
    }


@app.get("/api/excitement/week/{week}")
def get_week_excitement(week: int, season: int = 2024, season_type: str = 'REG'):
    """
    Get excitement scores for all games in a week.
    
    Args:
        week: Week number (1-18 for regular season)
        season: Season year (default: 2024)
        season_type: 'REG' or 'POST'
    """
    # Fetch all games for the week
    games_data = fetcher.fetch_week_games(week, season, season_type)
    
    if not games_data:
        return {
            "week": week,
            "season": season,
            "season_type": season_type,
            "games": []
        }
    
    # Calculate excitement for each game
    results = []
    for game_id, wp_data in games_data.items():
        score = calculate_excitement_score(
            wp_data['wp_history'],
            wp_data['home_score'],
            wp_data['away_score'],
            wp_data['is_overtime'],
            wp_data.get('score_history')
        )
        results.append({
            "game_id": game_id,
            "excitement_score": round(score, 1),
            "home_score": wp_data['home_score'],
            "away_score": wp_data['away_score'],
            "is_overtime": wp_data['is_overtime']
        })
    
    # Sort by excitement (highest first)
    results.sort(key=lambda x: x['excitement_score'], reverse=True)
    
    return {
        "week": week,
        "season": season,
        "season_type": season_type,
        "games": results
    }


@app.post("/api/refresh-game/{game_id}")
def refresh_game(game_id: str):
    """
    Manually trigger a refresh for a specific game.
    Useful for testing or forcing an update.
    """
    wp_data = fetcher.fetch_game_wp(game_id)
    if not wp_data:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")
    
    excitement = calculate_excitement_score(
        wp_data['wp_history'],
        wp_data['home_score'],
        wp_data['away_score'],
        wp_data['is_overtime']
    )
    
    return {
        "game_id": game_id,
        "excitement_score": round(excitement, 1),
        "refreshed": True
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
