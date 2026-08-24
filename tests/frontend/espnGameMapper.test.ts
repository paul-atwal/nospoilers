import { describe, expect, it } from 'vitest';
import { mapEspnEventToGame } from '../../services/espnGameMapper';
import { finalPostseasonEvent } from './fixtures/espn';

describe('mapEspnEventToGame', () => {
  it('maps a final postseason event without changing regular-season records', () => {
    const game = mapEspnEventToGame(finalPostseasonEvent, {
      oddsByGameId: {},
      seasonType: 3,
      weekLabel: 'Wild Card',
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
      weekLabel: 'Wild Card',
      seasonType: 3,
      excitementScore: null,
      isUpcoming: false,
      isLive: false,
      broadcaster: 'FOX',
    });
  });
});
