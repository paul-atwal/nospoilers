import type { Game } from '../../../types';

const baseGame: Game = {
  id: 'game-1',
  homeTeam: 'Home Team',
  awayTeam: 'Away Team',
  homeScore: 24,
  awayScore: 17,
  homeRecord: {
    pregame: {
      record: { wins: 8, losses: 2, ties: 0 },
      scope: 'regular_season',
    },
    postgame: {
      record: { wins: 9, losses: 2, ties: 0 },
      scope: 'regular_season',
    },
  },
  awayRecord: {
    pregame: {
      record: { wins: 7, losses: 3, ties: 0 },
      scope: 'regular_season',
    },
    postgame: {
      record: { wins: 7, losses: 4, ties: 0 },
      scope: 'regular_season',
    },
  },
  status: 'Final',
  kickoffTime: '1:00 PM PST',
  dayOfWeek: 'SUN',
  dateLabel: '11/23',
  seasonWeek: {
    season: 2026,
    phase: 'regular_season',
    week: 12,
  },
  excitementScore: 6.5,
  spoilerData: {
    homeScore: 24,
    awayScore: 17,
    summary: 'Away Team 17 @ Home Team 24',
  },
  isUpcoming: false,
  isLive: false,
};

export function makeGame(overrides: Partial<Game> = {}): Game {
  return {
    ...baseGame,
    ...overrides,
    spoilerData: {
      ...baseGame.spoilerData,
      ...overrides.spoilerData,
    },
  };
}
