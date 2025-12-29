"""
Smart scheduler that only checks for finished games during likely ending windows.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import requests
from zoneinfo import ZoneInfo


ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


class GameScheduler:
    """
    Intelligently schedules game status checks to minimize API calls.
    Only checks during windows when games are likely to be finishing.
    """
    
    def __init__(self):
        self.game_slots: List[Dict] = []  # List of time slots with games
        self.last_schedule_fetch = None
        self.pst = ZoneInfo("America/Los_Angeles")
    
    def fetch_weekly_schedule(self) -> List[Dict]:
        """
        Fetch this week's games from ESPN to build the schedule.
        Returns list of games with their kickoff times.
        """
        try:
            response = requests.get(ESPN_API_BASE, timeout=10)
            if response.status_code != 200:
                return []
            
            data = response.json()
            events = data.get('events', [])
            
            games = []
            for event in events:
                game_id = event['id']
                game_date = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                status_state = event['status']['type']['state']
                
                games.append({
                    'id': game_id,
                    'kickoff': game_date,
                    'status': status_state
                })
            
            return games
            
        except Exception as e:
            print(f"Error fetching schedule: {e}")
            return []
    
    def group_games_into_slots(self, games: List[Dict]) -> List[Dict]:
        """
        Group games by kickoff time slots (within 30 minutes = same slot).
        Returns list of slots with start time and list of game IDs.
        """
        if not games:
            return []
        
        # Only consider upcoming or in-progress games
        relevant_games = [g for g in games if g['status'] in ['pre', 'in']]
        
        if not relevant_games:
            return []
        
        # Sort by kickoff time
        sorted_games = sorted(relevant_games, key=lambda x: x['kickoff'])
        
        slots = []
        current_slot = None
        
        for game in sorted_games:
            if current_slot is None:
                # Start new slot
                current_slot = {
                    'slot_start': game['kickoff'],
                    'games': [game['id']]
                }
            else:
                # Check if this game is within 30 min of slot start
                time_diff = abs((game['kickoff'] - current_slot['slot_start']).total_seconds() / 60)
                
                if time_diff <= 30:
                    # Same slot
                    current_slot['games'].append(game['id'])
                else:
                    # New slot - save current and start new
                    slots.append(current_slot)
                    current_slot = {
                        'slot_start': game['kickoff'],
                        'games': [game['id']]
                    }
        
        # Don't forget the last slot
        if current_slot:
            slots.append(current_slot)
        
        return slots
    
    def calculate_check_windows(self, slots: List[Dict]) -> List[Tuple[datetime, datetime]]:
        """
        For each time slot, calculate when games are likely to finish.
        Typical NFL game: 3 hours 15 minutes
        We'll check from 3 hours to 4 hours after slot start.
        
        Returns list of (window_start, window_end) tuples.
        """
        windows = []
        
        for slot in slots:
            # Games typically end 3-3.5 hours after kickoff
            # We'll check from 3 hours to 4 hours after kickoff
            window_start = slot['slot_start'] + timedelta(hours=3)
            window_end = slot['slot_start'] + timedelta(hours=4)
            
            windows.append((window_start, window_end, slot['games']))
        
        return windows
    
    def should_check_now(self) -> Tuple[bool, Optional[List[str]], Optional[int]]:
        """
        Determine if we should check for finished games right now.
        
        Returns:
            (should_check, game_ids_to_check, seconds_to_sleep)
            - should_check: True if we're in a check window now
            - game_ids_to_check: List of game IDs to monitor
            - seconds_to_sleep: How long to sleep before next check (None if checking now)
        """
        # Refresh schedule if it's been more than 6 hours or first time
        now = datetime.now(tz=self.pst)
        
        if (self.last_schedule_fetch is None or 
            (now - self.last_schedule_fetch).total_seconds() > 6 * 3600):
            
            print("Refreshing weekly schedule...")
            games = self.fetch_weekly_schedule()
            self.game_slots = self.group_games_into_slots(games)
            self.last_schedule_fetch = now
            
            if not self.game_slots:
                print("No upcoming games found")
                # Check again in 6 hours
                return False, None, 21600
        
        # Calculate check windows
        windows = self.calculate_check_windows(self.game_slots)
        
        if not windows:
            print("No check windows scheduled")
            # Check again in 6 hours
            return False, None, 21600
        
        # Check if we're in any window RIGHT NOW
        for window_start, window_end, game_ids in windows:
            if window_start <= now <= window_end:
                print(f"In check window: {window_start.strftime('%H:%M')} - {window_end.strftime('%H:%M')} PST")
                print(f"Monitoring {len(game_ids)} games from this slot")
                # We're checking now, sleep 5 minutes until next check
                return True, game_ids, 300
        
        # Not in any window - calculate sleep time until next window
        next_window = None
        for window_start, window_end, game_ids in windows:
            if now < window_start:
                next_window = (window_start, game_ids)
                break
        
        if next_window:
            sleep_seconds = int((next_window[0] - now).total_seconds())
            print(f"Next check window: {next_window[0].strftime('%I:%M %p %Z')} ({len(next_window[1])} games)")
            print(f"Sleeping for {sleep_seconds // 60} minutes until then")
            return False, None, sleep_seconds
        else:
            # All windows have passed - sleep until schedule refresh (6 hours)
            print("All games for this schedule have finished")
            return False, None, 21600
    
    def mark_game_finished(self, game_id: str):
        """
        Mark a game as finished and remove it from active monitoring.
        """
        for slot in self.game_slots:
            if game_id in slot.get('games', []):
                slot['games'].remove(game_id)
                
                # If this slot is now empty, remove it
                if not slot['games']:
                    print(f"All games from {slot['slot_start'].strftime('%I:%M %p')} slot are finished")
                    self.game_slots.remove(slot)
                return True
        return False
    
    def get_next_check_time(self) -> Optional[datetime]:
        """
        Returns the next time we should check for finished games.
        Useful for logging/debugging.
        """
        if not self.game_slots:
            return None
        
        now = datetime.now(tz=self.pst)
        windows = self.calculate_check_windows(self.game_slots)
        
        for window_start, window_end, _ in windows:
            if now < window_start:
                return window_start
            elif window_start <= now <= window_end:
                # We're in a window now
                return now
        
        return None
