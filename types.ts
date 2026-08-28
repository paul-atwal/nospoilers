
export interface GameSpoilerData {
  homeScore: string | number;
  awayScore: string | number;
  summary: string;
}

export type SeasonPhase = 'preseason' | 'regular_season' | 'postseason';

export interface SeasonWeek {
  readonly season: number;
  readonly phase: SeasonPhase;
  readonly week: number;
}

export interface WeekInfo {
  readonly seasonWeek: SeasonWeek;
  readonly title: string;
  readonly label: string;
  readonly seasonLabel: string;
}

export type RecordScope = 'preseason' | 'regular_season' | 'season_to_date';

export interface TeamRecord {
  readonly wins: number;
  readonly losses: number;
  readonly ties: number;
}

export interface RecordSnapshot {
  readonly record: TeamRecord;
  readonly scope: RecordScope;
}

export interface GameRecordSnapshots {
  readonly pregame: RecordSnapshot;
  readonly postgame?: RecordSnapshot;
}

export interface Game {
  id: string;
  homeTeam: string;
  awayTeam: string;
  homeTeamLogo?: string;
  awayTeamLogo?: string;
  homeScore: number;
  awayScore: number;
  homeRecord: GameRecordSnapshots | null;
  awayRecord: GameRecordSnapshots | null;
  status: string; // e.g., "Final", "Upcoming"
  kickoffTime: string;
  dayOfWeek: string; // e.g. "Sun", "Mon"
  dateLabel: string; // e.g. "11/23"
  seasonWeek: SeasonWeek;
  excitementScore: number | null; // Null while loading
  isEstimated?: boolean; // True if calculated using fallback logic
  spoilerData: GameSpoilerData;
  broadcaster?: string;
  isUpcoming?: boolean;
  isLive?: boolean;
  odds?: string;
}
