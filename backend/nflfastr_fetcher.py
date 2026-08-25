"""
Fetches NFL play-by-play data from nflfastR and processes win probability history.
"""
import nflreadpy as nfl
import pandas as pd
import json
import os
import redis
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .nflverse_normalizer import normalize_nflverse_game_data
    from .rating_input import GameRatingInput
except ImportError:
    from nflverse_normalizer import normalize_nflverse_game_data
    from rating_input import GameRatingInput


class NFLFastRFetcher:
    def __init__(self, cache_dir: str = "data"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "wp_cache.json"

        # Try to connect to Redis, fall back to file-based cache
        redis_url = os.environ.get("REDIS_URL")
        self.redis_client = None
        if redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
                print("DEBUG: Connected to Redis cache")
            except Exception as e:
                print(f"DEBUG: Redis connection failed ({e}), using file cache")
                self.redis_client = None

        # In-memory cache (loaded from Redis or file)
        self.cache = self._load_cache()
        print(f"DEBUG: Loaded {len(self.cache)} games from cache")
    
    def _load_cache(self) -> Dict:
        """Load existing cache from Redis or JSON file."""
        # Try Redis first
        if self.redis_client:
            try:
                keys = self.redis_client.keys("game:*")
                cache = {}
                for key in keys:
                    game_id = key.decode().replace("game:", "")
                    data = self.redis_client.get(key)
                    if data:
                        cache[game_id] = json.loads(data)
                if cache:
                    return cache
            except Exception as e:
                print(f"Error loading from Redis: {e}")

        # Fall back to file
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading cache: {e}")
                return {}
        return {}

    def _save_cache(self):
        """Save cache to Redis and JSON file."""
        # Save to Redis if available
        if self.redis_client:
            try:
                for game_id, data in self.cache.items():
                    self.redis_client.set(f"game:{game_id}", json.dumps(data))
            except Exception as e:
                print(f"Error saving to Redis: {e}")

        # Always save to file as backup
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Error saving cache: {e}")

    def _load_season_pbp(self, season: int) -> pd.DataFrame | None:
        try:
            pbp = nfl.load_pbp([season])
            if not isinstance(pbp, pd.DataFrame):
                pbp = pbp.to_pandas()
            return pbp
        except Exception as e:
            print(f"Error loading play-by-play data for season {season}: {e}")
            return None

    def fetch_game_wp(
        self,
        game_id: str,
        season: int = 2025,
    ) -> dict[str, Any] | None:
        """
        Fetch win probability history for a specific game.
        
        Args:
            game_id: Identifier matched directly against nflverse game_id rows
            season: NFL season year
        
        Returns:
            Dict with wp_history, home_score, away_score, is_overtime
        """
        # Check cache first
        if game_id in self.cache:
            return self.cache[game_id]
        
        try:
            print(f"Fetching play-by-play data for season {season}...")
            pbp = self._load_season_pbp(season)
            if pbp is None or 'game_id' not in pbp.columns:
                return None

            game_pbp = pbp[pbp['game_id'] == game_id]
            result = normalize_nflverse_game_data(game_id, game_pbp)
            if result is None:
                print(f"No usable data found for game {game_id}")
                return None

            self.cache[game_id] = result
            self._save_cache()
            return result

        except Exception as e:
            print(f"Error fetching game {game_id}: {e}")
            return None

    def fetch_week_games(
        self,
        week: int,
        season: int = 2024,
        season_type: str = 'REG',
    ) -> dict[str, GameRatingInput]:
        """
        Fetch WP data for all games in a given week.
        
        Args:
            week: Week number
            season: Season year
            season_type: 'REG' or 'POST'
        
        Returns:
            Dict mapping game_id to WP data
        """
        try:
            print(f"Fetching play-by-play data for {season} {season_type} Week {week}...")
            pbp = self._load_season_pbp(season)
            selection_columns = {'game_id', 'week', 'season_type'}
            if pbp is None or not selection_columns.issubset(pbp.columns):
                return {}

            week_pbp = pbp[(pbp['week'] == week) & (pbp['season_type'] == season_type)]
            if week_pbp.empty:
                print(f"No data found for Week {week}")
                return {}

            results: dict[str, GameRatingInput] = {}
            for game_id, game_pbp in week_pbp.groupby('game_id', sort=False):
                result = normalize_nflverse_game_data(game_id, game_pbp)
                if result is None:
                    continue
                results[game_id] = result
                self.cache[game_id] = result

            if results:
                self._save_cache()
            return results

        except Exception as e:
            print(f"Error fetching week {week}: {e}")
            return {}
    
    def get_cached_game(self, game_id: str) -> Optional[Dict]:
        """Get game data from cache without fetching."""
        return self.cache.get(game_id)
