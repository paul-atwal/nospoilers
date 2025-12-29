"""
ESPN API fallback for when nflfastR doesn't have data yet.
Fetches win probability and scores from ESPN's summary endpoint.
"""
import requests
from typing import Dict, Optional


def fetch_espn_game_data(game_id: str) -> Optional[Dict]:
    """
    Fetch game data from ESPN API as fallback when nflfastR doesn't have it yet.
    
    Args:
        game_id: ESPN game ID (e.g., "401772783")
    
    Returns:
        Dict with wp_history, score_history, home_score, away_score, is_overtime
        or None if data unavailable
    """
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        # Get win probability data
        wp_data = data.get('winprobability', [])
        if not wp_data or len(wp_data) == 0:
            return None
            
        # Extract home WP for each play
        wp_history = [entry.get('homeWinPercentage', 0.5) for entry in wp_data]
        
        # Get final scores from header
        header = data.get('header', {})
        competitions = header.get('competitions', [])
        
        if not competitions:
            return None
            
        competition = competitions[0]
        competitors = competition.get('competitors', [])
        
        home_score = 0
        away_score = 0
        
        for competitor in competitors:
            try:
                score = int(competitor.get('score', '0'))
                if competitor.get('homeAway') == 'home':
                    home_score = score
                else:
                    away_score = score
            except (ValueError, TypeError):
                continue
        
        # Check for overtime from header status
        status = competition.get('status', {})
        status_detail = status.get('type', {}).get('shortDetail', '')
        is_overtime = 'OT' in status_detail or 'Overtime' in status_detail
        
        # Build approximate score history based on final scores
        # We'll distribute scores across the WP history length
        # This is an approximation but good enough for the excitement calculator
        num_points = len(wp_history)
        score_history = []
        
        for i in range(num_points):
            # Simple approximation: gradually increase scores based on position in game
            progress = (i + 1) / num_points
            h_score = int(home_score * progress)
            a_score = int(away_score * progress)
            score_history.append((h_score, a_score))
        
        return {
            'wp_history': wp_history,
            'score_history': score_history,
            'home_score': home_score,
            'away_score': away_score,
            'is_overtime': is_overtime,
            'game_id': game_id,
            'source': 'ESPN'
        }
        
    except Exception as e:
        import traceback
        print(f"Error fetching ESPN data for {game_id}: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return None
