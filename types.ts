
export interface GameSpoilerData {
  homeScore: string | number | null;
  awayScore: string | number | null;
  summary: string;
}

export type SeasonPhase = 'preseason' | 'regular_season' | 'postseason';

export interface SeasonWeek {
  readonly season: number;
  readonly phase: SeasonPhase;
  readonly week: number;
}

/** The typed projection returned by the provider-free read API. */
export interface ApiRecord {
  readonly wins: number;
  readonly losses: number;
  readonly ties: number;
  readonly scope: RecordScope;
  readonly snapshotAt?: string | null;
}

export interface ApiTeam {
  readonly id: string;
  readonly displayName: string;
  readonly abbreviation: string;
  readonly logoKey: string | null;
  readonly pregameRecord: ApiRecord | null;
  readonly postgameRecord: ApiRecord | null;
}

export type ApiGameState =
  | 'scheduled'
  | 'in_progress'
  | 'final'
  | 'delayed'
  | 'postponed'
  | 'cancelled';

export interface ApiGameStatus {
  readonly state: ApiGameState;
  readonly detail: string | null;
  readonly period: number | null;
  readonly clock: string | null;
  readonly score: { readonly home: number; readonly away: number } | null;
}

export type ApiRatingState = 'pending' | 'provisional' | 'confirmed' | 'unavailable';

export interface ApiRating {
  readonly state: ApiRatingState;
  readonly score: number | null;
  readonly source: string | null;
  readonly modelVersion: string | null;
  readonly calculatedAt: string | null;
  readonly confirmedAt: string | null;
  readonly confirmationSupported: boolean;
  readonly confirmationWorkRemains: boolean;
}

export interface ApiFreshness {
  readonly scheduleCheckedAt: string | null;
  readonly scheduleUpdatedAt: string | null;
  readonly liveSourceCheckedAt: string | null;
  readonly liveStateUpdatedAt: string | null;
}

export interface ApiGame {
  readonly id: string;
  readonly espnId: string;
  readonly seasonWeek: SeasonWeek;
  readonly kickoffAt: string | null;
  readonly home: ApiTeam;
  readonly away: ApiTeam;
  readonly status: ApiGameStatus;
  readonly broadcaster: string | null;
  readonly odds: { readonly details: string | null; readonly updatedAt: string | null } | null;
  readonly rating: ApiRating;
  readonly freshness: ApiFreshness;
}

export interface BootstrapResponse {
  readonly activeSeason: number;
  readonly currentWeek: SeasonWeek;
  readonly knownWeeks: readonly SeasonWeek[];
  readonly calendarVersion: string;
  readonly pollAfterSeconds: number | null;
}

export interface WeekSnapshotResponse {
  readonly season: number;
  readonly week: SeasonWeek;
  readonly snapshotAsOf: string | null;
  readonly pollAfterSeconds: number | null;
  readonly games: readonly ApiGame[];
}

export interface SeasonSnapshotResponse {
  readonly season: number;
  readonly snapshotAsOf: string | null;
  readonly pollAfterSeconds: number | null;
  readonly games: readonly ApiGame[];
}

export interface WeekInfo {
  readonly seasonWeek: SeasonWeek;
  readonly title: string;
  readonly label: string;
  readonly seasonLabel: string;
}

export type RecordScope = 'preseason' | 'regular_season';

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

export interface KickoffView {
  time: string;
  day: string;
  date: string;
  zone: string;
}

export interface TeamView {
  id: string;
  name: string;
  abbreviation: string;
  logoUrl: string | null;
  records: GameRecordSnapshots | null;
}

export interface RatingPresentation {
  state: ApiRatingState;
  label: string;
  score: number | null;
  confirmationSupported?: boolean;
}

export interface Game {
  id: string;
  homeTeam: string;
  awayTeam: string;
  homeTeamLogo?: string;
  awayTeamLogo?: string;
  homeScore: number | null;
  awayScore: number | null;
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
  isScheduled?: boolean;
  isLive?: boolean;
  isDelayed?: boolean;
  odds?: string;
  kickoff?: KickoffView;
  home?: TeamView;
  away?: TeamView;
  rating?: RatingPresentation;
}
