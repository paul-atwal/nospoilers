"""
Excitement Score Calculator v3.0
Z-score normalized 2-factor model based on 2,622 games (2016-2025).

Factors:
1. WP Volatility (z-score normalized) - captures drama, tension, lead changes, late stakes
2. Comeback Factor - captures narrative arc of overcoming adversity

Target distribution: Normal, Mean=5.0, Range 0-10
"""
from typing import List, Optional


# Constants derived from 2,622 games (2016-2025 regular season)
VOLATILITY_MEAN = 2.1804
VOLATILITY_STD = 0.8237
COMEBACK_MEAN = 1.2812
K_FACTOR = 1.8
COMEBACK_MULTIPLIER = 0.3


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


def calculate_excitement_score(
    wp_history: List[float],
    home_score: int,
    away_score: int,
    is_overtime: bool,
    score_history: Optional[List[tuple]] = None
) -> float:
    """
    Calculate excitement score using z-score normalized volatility.

    Factors:
    1. WP Volatility (z-score normalized) - PRIMARY
    2. Comeback Factor - SECONDARY bonus

    Target distribution: Normal, Mean=5.0, Range 0-10

    Args:
        wp_history: List of home team win probability for each play
        home_score: Final home team score
        away_score: Final away team score
        is_overtime: Whether game went to overtime (captured implicitly in volatility)
        score_history: Optional list of (home, away) score tuples (not used)

    Returns:
        Excitement score from 0.0 to 10.0
    """
    # Factor 1: WP Volatility (normalized per play)
    volatility = calculate_wp_volatility_normalized(wp_history)

    # Factor 2: Comeback
    comeback = calculate_comeback_factor(wp_history, home_score, away_score)

    # Z-score normalize volatility
    z_volatility = (volatility - VOLATILITY_MEAN) / VOLATILITY_STD

    # Center adjusted to keep mean at 5.0 after adding comeback bonus
    center = 5.0 - (COMEBACK_MEAN * COMEBACK_MULTIPLIER)

    # Calculate score
    raw_score = center + (z_volatility * K_FACTOR) + (comeback * COMEBACK_MULTIPLIER)

    # Clamp to 0.0 - 10.0 range
    final_score = max(0.0, min(10.0, raw_score))

    return round(final_score, 1)
