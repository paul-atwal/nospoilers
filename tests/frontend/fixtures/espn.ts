import type { EspnScoreboard } from '../../../services/espnTypes';

// Sanitized from ESPN's 2024 postseason scoreboards. These fields establish
// that overall/total is distinct from the home and road record splits.
export const wildCardScoreboard: EspnScoreboard = {
  season: { year: 2024, type: 3 },
  week: { number: 1 },
  events: [{
    id: '401671878',
    date: '2025-01-11T21:30:00Z',
    status: { type: { state: 'post', shortDetail: 'Final' } },
    competitions: [{
      competitors: [
        {
          id: '34',
          homeAway: 'home',
          score: '32',
          team: { abbreviation: 'HOU', displayName: 'Houston Texans' },
          records: [
            { name: 'overall', type: 'total', summary: '10-7' },
            { name: 'Home', type: 'home', summary: '5-3' },
            { name: 'Road', type: 'road', summary: '5-4' },
          ],
        },
        {
          id: '24',
          homeAway: 'away',
          score: '12',
          team: { abbreviation: 'LAC', displayName: 'Los Angeles Chargers' },
          records: [
            { name: 'overall', type: 'total', summary: '11-6' },
            { name: 'Home', type: 'home', summary: '5-3' },
            { name: 'Road', type: 'road', summary: '6-3' },
          ],
        },
      ],
    }],
  }],
};

export const superBowlScoreboard: EspnScoreboard = {
  season: { year: 2024, type: 3 },
  week: { number: 5 },
  events: [{
    id: '401671889',
    date: '2025-02-10T00:30:00Z',
    status: { type: { state: 'post', shortDetail: 'Final' } },
    competitions: [{
      competitors: [
        {
          id: '21',
          homeAway: 'home',
          score: '40',
          team: { abbreviation: 'PHI', displayName: 'Philadelphia Eagles' },
          records: [
            { name: 'overall', type: 'total', summary: '14-3' },
            { name: 'Home', type: 'home', summary: '8-1' },
            { name: 'Road', type: 'road', summary: '6-2' },
          ],
        },
        {
          id: '12',
          homeAway: 'away',
          score: '22',
          team: { abbreviation: 'KC', displayName: 'Kansas City Chiefs' },
          records: [
            { name: 'overall', type: 'total', summary: '15-2' },
            { name: 'Home', type: 'home', summary: '8-0' },
            { name: 'Road', type: 'road', summary: '7-2' },
          ],
        },
      ],
    }],
  }],
};
