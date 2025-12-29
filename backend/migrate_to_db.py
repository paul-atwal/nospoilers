#!/usr/bin/env python3
"""
Migrate JSON cache to PostgreSQL database.
Run once during initial deployment.
"""
import json
import os
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values

def migrate_cache_to_db():
    """Migrate wp_cache.json to PostgreSQL."""
    
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    
    # Load JSON cache
    cache_file = Path('data/wp_cache.json')
    if not cache_file.exists():
        print("No cache file found, skipping migration")
        return
    
    with open(cache_file, 'r') as f:
        cache = json.load(f)
    
    print(f"Migrating {len(cache)} games to database...")
    
    # Connect to database
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    
    # Prepare data for bulk insert
    values = []
    for game_id, data in cache.items():
        values.append((
            game_id,
            data.get('excitement_score'),
            data.get('home_score', 0),
            data.get('away_score', 0),
            data.get('is_overtime', False),
            json.dumps(data.get('wp_history', [])),
            json.dumps(data.get('score_history', [])),
            data.get('nfl_game_id'),
            data.get('season'),
            data.get('week')
        ))
    
    # Bulk insert with conflict handling
    insert_query = """
        INSERT INTO excitement_cache 
        (game_id, excitement_score, home_score, away_score, is_overtime, 
         wp_history, score_history, nfl_game_id, season, week)
        VALUES %s
        ON CONFLICT (game_id) DO UPDATE SET
            excitement_score = EXCLUDED.excitement_score,
            home_score = EXCLUDED.home_score,
            away_score = EXCLUDED.away_score,
            is_overtime = EXCLUDED.is_overtime,
            wp_history = EXCLUDED.wp_history,
            score_history = EXCLUDED.score_history,
            updated_at = NOW()
    """
    
    execute_values(cur, insert_query, values)
    conn.commit()
    
    print(f"✅ Migrated {len(values)} games successfully")
    
    # Verify
    cur.execute("SELECT COUNT(*) FROM excitement_cache")
    count = cur.fetchone()[0]
    print(f"Database now contains {count} games")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    migrate_cache_to_db()
