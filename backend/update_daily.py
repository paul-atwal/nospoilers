#!/usr/bin/env python3
"""
Daily update script - fetches and caches excitement scores for current week.
Run via GitHub Actions or Railway cron.
"""
import os
import sys
import nflreadpy as nfl
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
import json

sys.path.insert(0, os.path.dirname(__file__))
from excitement_calculator import calculate_excitement_score

def get_current_week():
    """Determine current NFL week."""
    # This is a simplified version - you'd want to use NFL API
    # For now, hardcode or fetch from ESPN
    return 2025, 12  # season, week

def update_current_week():
    """Fetch and update scores for current week's completed games."""
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL not set")
    
    season, week = get_current_week()
    print(f"Updating scores for {season} Week {week}...")
    
    # Load schedule
    schedules = nfl.load_schedules([season])
    sched_df = schedules.to_pandas()
    
    # Filter for current week completed games
    current_week_games = sched_df[
        (sched_df['season'] == season) &
        (sched_df['week'] == week) &
        (sched_df['game_type'] == 'REG') &
        (sched_df['away_score'].notna())
    ]
    
    if len(current_week_games) == 0:
        print("No completed games found")
        return
    
    print(f"Found {len(current_week_games)} completed games")
    
    # Load play-by-play
    pbp = nfl.load_pbp([season])
    pbp_df = pbp.to_pandas().ffill().fillna(0)
    
    # Connect to database
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    
    updates = []
    for _, game in current_week_games.iterrows():
        espn_id = str(int(game['espn']))
        nfl_game_id = game['game_id']
        
        # Get play-by-play for this game
        game_pbp = pbp_df[pbp_df['game_id'] == nfl_game_id]
        
        if len(game_pbp) == 0:
            continue
        
        wp_history = game_pbp['home_wp'].tolist()
        if len(wp_history) < 2:
            continue
        
        # Calculate excitement
        score = calculate_excitement_score(
            wp_history,
            int(game['home_score']),
            int(game['away_score']),
            bool(game_pbp['qtr'].max() > 4),
            None
        )
        
        updates.append((
            espn_id,
            round(score, 1),
            int(game['home_score']),
            int(game['away_score']),
            bool(game_pbp['qtr'].max() > 4),
            json.dumps(wp_history),
            json.dumps([]),  # score_history
            nfl_game_id,
            season,
            week
        ))
        
        print(f"  {espn_id}: {score:.1f}")
    
    # Bulk upsert
    upsert_query = """
        INSERT INTO excitement_cache 
        (game_id, excitement_score, home_score, away_score, is_overtime,
         wp_history, score_history, nfl_game_id, season, week)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (game_id) DO UPDATE SET
            excitement_score = EXCLUDED.excitement_score,
            updated_at = NOW()
    """
    
    execute_batch(cur, upsert_query, updates)
    conn.commit()
    
    print(f"✅ Updated {len(updates)} games")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    update_current_week()
