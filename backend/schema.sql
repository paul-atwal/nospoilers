-- Database schema for NFL excitement scores
-- Run this in your Railway PostgreSQL instance

CREATE TABLE IF NOT EXISTS excitement_cache (
    game_id VARCHAR(20) PRIMARY KEY,
    excitement_score FLOAT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    is_overtime BOOLEAN DEFAULT FALSE,
    wp_history JSONB,
    score_history JSONB,
    nfl_game_id VARCHAR(50),
    season INTEGER,
    week INTEGER,
    cached_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_season_week ON excitement_cache(season, week);
CREATE INDEX IF NOT EXISTS idx_excitement_score ON excitement_cache(excitement_score DESC);
CREATE INDEX IF NOT EXISTS idx_cached_at ON excitement_cache(cached_at);

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_excitement_cache_updated_at 
    BEFORE UPDATE ON excitement_cache 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();
