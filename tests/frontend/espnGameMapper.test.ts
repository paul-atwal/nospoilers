import { describe, expect, it } from 'vitest';
import { mapEspnEventToGame } from '../../services/espnGameMapper';
import { finalPostseasonEvent } from './fixtures/espn';

describe('mapEspnEventToGame', () => {
  it('maps a final postseason event without changing regular-season records', () => {
    const game = mapEspnEventToGame(finalPostseasonEvent, {
      oddsByGameId: {},
      seasonWeek: {
        season: 2026,
        phase: 'postseason',
        week: 1,
      },
    });

    expect(game).toMatchObject({
      id: '401772510',
      homeTeam: 'Home Team',
      awayTeam: 'Away Team',
      homeScore: 24,
      awayScore: 17,
      homeRecord: '12-5',
      awayRecord: '11-6',
      status: 'Final',
      seasonWeek: {
        season: 2026,
        phase: 'postseason',
        week: 1,
      },
      excitementScore: null,
      isUpcoming: false,
      isLive: false,
      broadcaster: 'FOX',
    });
  });
});
