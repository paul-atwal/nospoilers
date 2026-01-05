"""
Fetches NFL play-by-play data from nflfastR and processes win probability history.
"""
import nfl_data_py as nfl
import pandas as pd
import json
from pathlib import Path
from typing import Dict, List, Optional


class NFLFastRFetcher:
    def __init__(self, cache_dir: str = "data"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_file = self.cache_dir / "wp_cache.json"
        print(f"DEBUG: Loading cache from {self.cache_file.absolute()}")
        self.cache = self._load_cache()
        if "401772779" in self.cache:
            print(f"DEBUG: Cache has 401772779: {self.cache['401772779'].get('excitement_score')}")
        else:
            print("DEBUG: Cache MISSING 401772779")
    
    def _load_cache(self) -> Dict:
        """Load existing cache from JSON file."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading cache: {e}")
                return {}
        return {}
    
    def _save_cache(self):
        """Save cache to JSON file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Error saving cache: {e}")
    
    def fetch_game_wp(self, game_id: str, season: int = 2025) -> Optional[Dict]:
        """
        Fetch win probability history for a specific game.
        
        Args:
            game_id: ESPN game ID (e.g., "401671755")
            season: NFL season year
        
        Returns:
            Dict with wp_history, home_score, away_score, is_overtime
        """
        # Check cache first
        if game_id in self.cache:
            return self.cache[game_id]
        
        try:
            # Fetch play-by-play for the entire season (returns Polars DataFrame)
            print(f"Fetching play-by-play data for season {season}...")
            pbp = nfl.load_pbp([season])
            
            # Convert to pandas for easier processing
            if not isinstance(pbp, pd.DataFrame):
                pbp = pbp.to_pandas()
            
            # Filter for this specific game
            game_pbp = pbp[pbp['game_id'] == game_id]
            
            if game_pbp.empty:
                print(f"No data found for game {game_id}")
                return None
            
            # Extract WP history (filter out None values)
            # Also get score history for lead change calculation
            wp_data = game_pbp[['home_wp', 'total_home_score', 'total_away_score']].dropna(subset=['home_wp'])
            wp_history = wp_data['home_wp'].tolist()
            
            # Get score history as list of tuples (home, away)
            # Fill NaNs with 0 (start of game) or forward fill
            score_data = game_pbp[['total_home_score', 'total_away_score']].ffill().fillna(0)
            # Align with WP history length/index
            score_data = score_data.loc[wp_data.index]
            score_history = list(zip(score_data['total_home_score'].tolist(), score_data['total_away_score'].tolist()))
            
            # Get final scores
            final_play = game_pbp.iloc[-1]
            home_score = int(final_play['total_home_score']) if not game_pbp['total_home_score'].isna().all() else 0
            away_score = int(final_play['total_away_score']) if not game_pbp['total_away_score'].isna().all() else 0
            
            # Detect overtime
            is_overtime = game_pbp['qtr'].max() > 4
            
            result = {
                'wp_history': wp_history,
                'score_history': score_history,
                'home_score': home_score,
                'away_score': away_score,
                'is_overtime': is_overtime,
                'game_id': game_id
            }
            
            # Cache the result
            self.cache[game_id] = result
            self._save_cache()
            
            return result
            
        except Exception as e:
            print(f"Error fetching game {game_id}: {e}")
            return None
    
    def fetch_week_games(self, week: int, season: int = 2024, season_type: str = 'REG') -> Dict[str, Dict]:
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
            pbp = nfl.load_pbp([season])
            
            # Ensure pandas
            if not isinstance(pbp, pd.DataFrame):
                pbp = pbp.to_pandas()
            
            # Filter by week and season type
            week_pbp = pbp[(pbp['week'] == week) & (pbp['season_type'] == season_type)]
            
            if week_pbp.empty:
                print(f"No data found for Week {week}")
                return {}
            
            # Get unique games
            game_ids = week_pbp['game_id'].unique()
            
            results = {}
            for game_id in game_ids:
                game_pbp = week_pbp[week_pbp['game_id'] == game_id]
                
                # Extract WP history
                wp_data = game_pbp[['home_wp', 'total_home_score', 'total_away_score']].dropna(subset=['home_wp'])
                wp_history = wp_data['home_wp'].tolist()
                
                # Get score history
                # Use ffill() instead of fillna(method='ffill')
                score_data = game_pbp[['total_home_score', 'total_away_score']].ffill().fillna(0)
                score_data = score_data.loc[wp_data.index]
                score_history = list(zip(score_data['total_home_score'].tolist(), score_data['total_away_score'].tolist()))
                
                if game_id == "401772779": # Debug Colts @ Chiefs
                    print(f"DEBUG WEEK 401772779 Score History Length: {len(score_history)}")
                    print(f"DEBUG WEEK 401772779 First 5: {score_history[:5]}")
                    print(f"DEBUG WEEK 401772779 Last 5: {score_history[-5:]}")
                    print(f"DEBUG WEEK 401772779 Score Data NaNs: {score_data.isna().sum()}")
                
                if game_id == "401772779": # Debug Colts @ Chiefs
                    print(f"DEBUG 401772779 Score History Length: {len(score_history)}")
                    print(f"DEBUG 401772779 First 5: {score_history[:5]}")
                    print(f"DEBUG 401772779 Last 5: {score_history[-5:]}")
                    print(f"DEBUG 401772779 Score Data NaNs: {score_data.isna().sum()}")
                
                # Get final scores
                final_play = game_pbp.iloc[-1]
                home_score = int(final_play['total_home_score']) if pd.notna(final_play['total_home_score']) else 0
                away_score = int(final_play['total_away_score']) if pd.notna(final_play['total_away_score']) else 0
                
                # Detect overtime
                is_overtime = 'OT' in str(final_play.get('game_half', '')) or game_pbp['qtr'].max() > 4
                
                result = {
                    'wp_history': wp_history,
                    'score_history': score_history,
                    'home_score': home_score,
                    'away_score': away_score,
                    'is_overtime': is_overtime,
                    'game_id': game_id
                }
                
                results[game_id] = result
                self.cache[game_id] = result
            
            # Save cache after batch fetch
            self._save_cache()
            
            return results
            
        except Exception as e:
            print(f"Error fetching week {week}: {e}")
            return {}
    
    def get_cached_game(self, game_id: str) -> Optional[Dict]:
        """Get game data from cache without fetching."""
        return self.cache.get(game_id)
