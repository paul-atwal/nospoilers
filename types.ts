
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
  excitementScore: number; // 0.0 to 10.0
  spoilerData: GameSpoilerData;
  broadcaster?: string;
  isUpcoming?: boolean;
  isLive?: boolean;
  odds?: string;
}

export interface WeekInfo {
  seasonType: number; // 2 for Reg, 3 for Post
  week: number; // Continuous week number (1-18 Reg, 19+ Post)
  label: string;
}