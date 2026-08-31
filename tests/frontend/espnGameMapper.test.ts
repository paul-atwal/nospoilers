import { describe, expect, it } from 'vitest';
import { mapEspnEventToGame } from '../../services/espnGameMapper';
import {
  superBowlScoreboard,
  wildCardScoreboard,
} from './fixtures/espn';

const postseasonContext = (week: number) => ({
  oddsByGameId: {},
  seasonWeek: {
    season: 2024,
    phase: 'postseason' as const,
    week,
  },
});

describe('mapEspnEventToGame', () => {
  it('prepares regular-season snapshots from a final source record', () => {
    const event = structuredClone(wildCardScoreboard.events![0]);
    event.competitions[0].competitors[0].records = [
      { name: 'overall', type: 'total', summary: '9-2' },
    ];
    event.competitions[0].competitors[1].records = [
      { name: 'overall', type: 'total', summary: '7-4' },
    ];

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

  it('keeps Wild Card overall records static during the playoffs', () => {
    const game = mapEspnEventToGame(
      wildCardScoreboard.events![0],
      postseasonContext(1),
    );

    expect(game).toMatchObject({
      id: '401671878',
      homeTeam: 'Houston Texans',
      awayTeam: 'Los Angeles Chargers',
      homeScore: 32,
      awayScore: 12,
      homeRecord: {
        pregame: {
          record: { wins: 10, losses: 7, ties: 0 },
          scope: 'regular_season',
        },
      },
      awayRecord: {
        pregame: {
          record: { wins: 11, losses: 6, ties: 0 },
          scope: 'regular_season',
        },
      },
      status: 'Final',
      seasonWeek: {
        season: 2024,
        phase: 'postseason',
        week: 1,
      },
      excitementScore: null,
      isUpcoming: false,
      isLive: false,
    });
  });

  it('uses the same overall records for the later Super Bowl round', () => {
    const game = mapEspnEventToGame(
      superBowlScoreboard.events![0],
      postseasonContext(5),
    );

    expect(game.homeRecord).toEqual({
      pregame: {
        record: { wins: 14, losses: 3, ties: 0 },
        scope: 'regular_season',
      },
    });
    expect(game.awayRecord).toEqual({
      pregame: {
        record: { wins: 15, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
    });
  });

  it('marks malformed source records as unavailable', () => {
    const event = structuredClone(wildCardScoreboard.events![0]);
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
