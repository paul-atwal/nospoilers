import type { EspnEvent } from '../../../services/espnTypes';

export const finalPostseasonEvent: EspnEvent = {
  id: '401772510',
  date: '2026-01-18T21:30:00Z',
  status: {
    type: {
      state: 'post',
      shortDetail: 'Final',
    },
  },
  competitions: [{
    broadcasts: [{ names: ['FOX'] }],
    competitors: [
      {
        id: 'home',
        homeAway: 'home',
        score: '24',
        team: {
          abbreviation: 'HME',
          shortDisplayName: 'Home Team',
        },
        records: [{ summary: '13-6' }, { summary: '12-5' }],
      },
      {
        id: 'away',
        homeAway: 'away',
        score: '17',
        team: {
          abbreviation: 'AWY',
          shortDisplayName: 'Away Team',
        },
        records: [{ summary: '12-7' }, { summary: '11-6' }],
      },
    ],
  }],
};
