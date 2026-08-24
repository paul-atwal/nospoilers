import type { RecordSummary } from '../utils/records';

export interface EspnTeam {
  abbreviation?: string;
  displayName?: string;
  logo?: string;
  shortDisplayName?: string;
}

export interface EspnCompetitor {
  homeAway: 'home' | 'away';
  id: string;
  records?: RecordSummary[];
  score?: string;
  team: EspnTeam;
}

export interface EspnCompetition {
  broadcasts?: Array<{ names?: string[] }>;
  competitors: EspnCompetitor[];
  odds?: Array<{ details?: string }>;
}

export interface EspnEvent {
  competitions: EspnCompetition[];
  date: string;
  id: string;
  status: {
    type: {
      shortDetail: string;
      state: string;
    };
  };
}

export interface EspnScoreboard {
  events?: EspnEvent[];
  season?: { type?: number };
  week?: { number?: number };
}
