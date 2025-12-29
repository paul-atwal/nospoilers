"""
Excitement Score Calculator v2.0
Simplified 2-factor model based on data analysis.
"""
from typing import List, Optional


def calculate_wp_volatility_normalized(wp_history: List[float]) -> float:
    """
    Calculate normalized WP volatility (sum of absolute WP changes per play).
    
    This captures:
    - Overall drama and tension
    - Lead changes (natural byproduct)
    - Late-game stakes (WP changes larger near end)
    - OT intensity (plays have high WPA)
    
    Returns:
        Volatility as a percentage (0-100 scale)
    """
    if len(wp_history) < 2:
        return 0.0
    
    # Sum of absolute WP changes
    total_volatility = sum(
        abs(wp_history[i] - wp_history[i-1]) 
        for i in range(1, len(wp_history))
    )
    
    # Normalize by number of plays
    num_plays = len(wp_history) - 1
    normalized = (total_volatility / num_plays) * 100  # Convert to percentage
    
    return normalized


def calculate_comeback_factor(wp_history: List[float], home_score: int, away_score: int) -> float:
    """
    Calculate comeback factor (largest deficit overcome).
    
    Captures narrative arc and psychological drama of overcoming adversity.
    """
    if len(wp_history) < 2:
        return 0.0
    
    # Find the minimum WP for the eventual winner
    home_won = home_score > away_score
    
    if home_won:
        # Home won, find their lowest WP
        min_wp = min(wp_history)
        deficit_overcome = 0.5 - min_wp  # How far below 50% they went
    else:
        # Away won, find their lowest WP (which is home's highest)
        max_wp = max(wp_history)
        deficit_overcome = max_wp - 0.5  # How far below 50% away team went
    
    # Scale to a meaningful range (0-3 points)
    # A team that was at 10% WP and came back gets max points
    comeback_score = deficit_overcome * 7.5  # 0.4 deficit -> 3.0 points
    
    return max(0.0, comeback_score)


def calculate_excitement_score_v2(
    wp_history: List[float],
    home_score: int,
    away_score: int,
    score_history: Optional[List[tuple]] = None
) -> float:
    """
    Calculate excitement score using simplified 2-factor model.
    
    Factors:
    1. WP Volatility (normalized) - PRIMARY
    2. Comeback Factor - SECONDARY
    
    Target distribution: Normal, Mean ~6.0, Range 1-10
    
    Args:
        wp_history: List of home team win probability for each play
        home_score: Final home team score
        away_score: Final away team score
        score_history: Optional list of (home, away) score tuples (not used in v2)
    
    Returns:
        Excitement score from 1.0 to 10.0
    """
    # Factor 1: WP Volatility (normalized per play)
    volatility = calculate_wp_volatility_normalized(wp_history)
    
    # Factor 2: Comeback
    comeback = calculate_comeback_factor(wp_history, home_score, away_score)
    
    # Final tuned weights (binary search, 3 iterations)
    # Distribution: Mean=6.045, StdDev=2.46, Perfect target!
    volatility_weight = 2.2607
    comeback_weight = 0.7701
    
    score = (volatility * volatility_weight) + (comeback * comeback_weight)
    
    # Clamp to 1.0 - 10.0 range
    final_score = max(1.0, min(10.0, score))
    
    return final_score
