#!/usr/bin/env python3
"""
Backfill excitement scores for 2020-2025 seasons using nflfastR data.
"""
import nflreadpy as nfl
import pandas as pd
from excitement_calculator import calculate_excitement_score
import json
from pathlib import Path

def backfill_multi_season(start_year=2020, end_year=2025):
    """
    Backfill all games from start_year to end_year using nflfastR.
    """
    
    print("=" * 60)
    print(f"Backfilling NFL Seasons {start_year}-{end_year}")
    print("=" * 60)
    print()
    
    # Load cache
    cache_file = Path('data/wp_cache.json')
    if cache_file.exists():
        with open(cache_file, 'r') as f:
            cache = json.load(f)
    else:
        cache = {}
    
    print(f"Existing cache: {len(cache)} games")
    print()
    
    total_processed = 0
    
    for season in range(start_year, end_year + 1):
        print(f"\n{'='*60}")
        print(f"Processing Season {season}")
        print(f"{'='*60}")
        
        # Load schedule for ESPN ID mapping
        print(f"Loading {season} schedule...")
        schedules = nfl.load_schedules([season])
        sched_df = schedules.to_pandas() if not isinstance(schedules, pd.DataFrame) else schedules
        
        # Filter completed regular season games
        completed = sched_df[
            (sched_df['game_type'] == 'REG') & 
            (sched_df['away_score'].notna())
        ]
        
        print(f"Found {len(completed)} completed games")
        
        if len(completed) == 0:
            print(f"No completed games for {season}, skipping...")
            continue
        
        # Load play-by-play data for this season
        print(f"Loading play-by-play data for {season}...")
        pbp = nfl.load_pbp([season])
        pbp_df = pbp.to_pandas() if not isinstance(pbp, pd.DataFrame) else pbp
        
        # Ensure proper pandas conversion and fillna
        pbp_df = pbp_df.ffill().fillna(0)
        
        # Process each game
        for idx, game_row in completed.iterrows():
            nfl_game_id = game_row['game_id']
            espn_id = str(game_row['espn'])
            
            # Skip if already cached
            if espn_id in cache:
                continue
            
            # Get play-by-play for this game
            game_pbp = pbp_df[pbp_df['game_id'] == nfl_game_id]
            
            if len(game_pbp) == 0:
                print(f"  ⚠ No PBP data for {nfl_game_id} (ESPN: {espn_id})")
                continue
            
            # Extract WP history
            wp_history = game_pbp['home_wp'].tolist()
            if not wp_history or len(wp_history) < 2:
                print(f"  ⚠ Insufficient WP data for {nfl_game_id}")
                continue
            
            # Build score history from pbp
            score_history = []
            for _, play in game_pbp.iterrows():
                home_score = int(play.get('total_home_score', 0))
                away_score = int(play.get('total_away_score', 0))
                score_history.append((home_score, away_score))
            
            # Get final scores
            home_score = int(game_row['home_score'])
            away_score = int(game_row['away_score'])
            
            # Check for OT
            is_overtime = game_pbp['qtr'].max() > 4
            
            # Calculate excitement score
            excitement = calculate_excitement_score(
                wp_history,
                home_score,
                away_score,
                is_overtime,
                score_history
            )
            
            # Cache the result
            cache[espn_id] = {
                'excitement_score': round(excitement, 1),
                'home_score': home_score,
                'away_score': away_score,
                'is_overtime': bool(is_overtime),
                'wp_history': wp_history,
                'score_history': score_history,
                'nfl_game_id': nfl_game_id,
                'season': season
            }
            
            total_processed += 1
            if total_processed % 50 == 0:
                print(f"  Processed {total_processed} games... (Score: {excitement:.1f})")
    
    # Save cache
    print(f"\n{'='*60}")
    print("Saving cache...")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'w') as f:
        json.dump(cache, f, indent=2)
    
    print()
    print("=" * 60)
    print("Backfill Complete!")
    print(f"{len(cache)} total games in cache")
    print(f"{total_processed} new games processed")
    print(f"Cache saved to: {cache_file}")
    print("=" * 60)
    print()

if __name__ == "__main__":
    backfill_multi_season(2020, 2025)
