import { describe, expect, it } from 'vitest';
import { mapEspnEventToGame } from '../../services/espnGameMapper';
import { finalPostseasonEvent } from './fixtures/espn';

describe('mapEspnEventToGame', () => {
  it('prepares regular-season snapshots from a final source record', () => {
    const event = structuredClone(finalPostseasonEvent);
    event.competitions[0].competitors[0].records = [{ summary: '9-2' }];
    event.competitions[0].competitors[1].records = [{ summary: '7-4' }];

    const game = mapEspnEventToGame(event, {
      oddsByGameId: {},
      seasonWeek: {
        season: 2026,
        phase: 'regular_season',
        week: 12,
      },
    });

    expect(game.homeRecord).toEqual({
      pregame: {
        record: { wins: 8, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
      postgame: {
        record: { wins: 9, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
    });
    expect(game.awayRecord).toEqual({
      pregame: {
        record: { wins: 7, losses: 3, ties: 0 },
        scope: 'regular_season',
      },
      postgame: {
        record: { wins: 7, losses: 4, ties: 0 },
        scope: 'regular_season',
      },
    });
  });

  it('maps a final postseason event to cumulative pregame and postgame records', () => {
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
      homeRecord: {
        pregame: {
          record: { wins: 12, losses: 6, ties: 0 },
          scope: 'season_to_date',
        },
        postgame: {
          record: { wins: 13, losses: 6, ties: 0 },
          scope: 'season_to_date',
        },
      },
      awayRecord: {
        pregame: {
          record: { wins: 12, losses: 6, ties: 0 },
          scope: 'season_to_date',
        },
        postgame: {
          record: { wins: 12, losses: 7, ties: 0 },
          scope: 'season_to_date',
        },
      },
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

  it('marks malformed source records as unavailable', () => {
    const event = structuredClone(finalPostseasonEvent);
    for (const competitor of event.competitions[0].competitors) {
      competitor.records = [{ summary: 'not-a-record' }];
    }

    const game = mapEspnEventToGame(event, {
      oddsByGameId: {},
      seasonWeek: {
        season: 2026,
        phase: 'postseason',
        week: 1,
      },
    });

    expect(game.homeRecord).toBeNull();
    expect(game.awayRecord).toBeNull();
  });
});
