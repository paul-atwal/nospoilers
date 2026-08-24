
export interface GameSpoilerData {
  homeScore: string | number;
  awayScore: string | number;
  summary: string;
}

export interface Game {
  id: string;
  homeTeam: string;
  awayTeam: string;
  homeTeamLogo?: string;
  awayTeamLogo?: string;
  homeScore: number;
  awayScore: number;
  homeRecord: string; // e.g. "8-2"
  awayRecord: string; // e.g. "7-3"
  status: string; // e.g., "Final", "Upcoming"
  kickoffTime: string;
  dayOfWeek: string; // e.g. "Sun", "Mon"
  dateLabel: string; // e.g. "11/23"
  weekLabel: string; // e.g. "Week 12" or "Wild Card"
  seasonType: number; // ESPN: 2 for regular season, 3 for postseason
  excitementScore: number | null; // Null while loading
  isEstimated?: boolean; // True if calculated using fallback logic
  spoilerData: GameSpoilerData;
  broadcaster?: string;
  isUpcoming?: boolean;
  isLive?: boolean;
  odds?: string;
}

export interface WeekInfo {
  seasonType: number; // 2 for Reg, 3 for Post
  scheduleWeek: number; // 1-18 regular season, 19-23 postseason
  label: string;
}
