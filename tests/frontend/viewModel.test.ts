import { describe, expect, it } from 'vitest';
import { toViewGame } from '../../App';
import type { ApiGame } from '../../types';

const makeApiGame = (state: ApiGame['status']['state'], score: ApiGame['status']['score']): ApiGame => ({
  id: 'game-1', espnId: 'game-1', seasonWeek: { season: 2026, phase: 'regular_season', week: 1 }, kickoffAt: null,
  home: { id: 'home', displayName: 'Home Team', abbreviation: 'HME', logoKey: null, pregameRecord: null, postgameRecord: null },
  away: { id: 'away', displayName: 'Away Team', abbreviation: 'AWY', logoKey: null, pregameRecord: null, postgameRecord: null },
  status: { state, detail: null, period: null, clock: null, score }, broadcaster: null, odds: null,
  rating: { state: 'pending', score: null, source: null, modelVersion: null, calculatedAt: null, confirmedAt: null, confirmationSupported: false, confirmationWorkRemains: false },
  freshness: { scheduleCheckedAt: null, scheduleUpdatedAt: null, liveSourceCheckedAt: null, liveStateUpdatedAt: null },
});

describe('read API compatibility view model', () => {
  it('keeps a missing delayed-game score nullable rather than inventing 0-0', () => {
    const game = toViewGame(makeApiGame('delayed', null));
    expect(game.homeScore).toBeNull();
    expect(game.awayScore).toBeNull();
    expect(game.spoilerData.homeScore).toBeNull();
    expect(game.spoilerData.awayScore).toBeNull();
  });

  it('keeps a missing final-game score nullable too', () => {
    const game = toViewGame(makeApiGame('final', null));
    expect(game.homeScore).toBeNull();
    expect(game.awayScore).toBeNull();
  });
});
