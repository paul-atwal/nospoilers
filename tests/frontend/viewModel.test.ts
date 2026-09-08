import { describe, expect, it } from 'vitest';
import { formatKickoff, toViewGame } from '../../services/gameViewModel';
import { getTeamLogoUrl } from '../../services/teamAssets';
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

  it('keeps rating states explicit and maps only confirmed/provisional scores', () => {
    const game = makeApiGame('final', { home: 3, away: 0 });
    expect(toViewGame(game).rating.label).toBe('Rating pending');
    expect(toViewGame({ ...game, rating: { ...game.rating, state: 'unavailable' } }).rating.score).toBeNull();
    expect(toViewGame({ ...game, rating: { ...game.rating, state: 'provisional', score: 7.2 } }).rating.score).toBe(7.2);
  });

  it('formats kickoff in the requested zone, including DST and an explicit zone label', () => {
    const kickoff = formatKickoff('2026-03-08T10:30:00Z', 'America/Los_Angeles');
    expect(kickoff.time).toMatch(/3:30/);
    expect(kickoff.zone).toBe('PDT');
    expect(formatKickoff('2026-03-08T10:30:00Z', 'America/New_York').zone).toBe('EDT');
  });

  it('uses a deterministic unknown kickoff and local logo fallback', () => {
    expect(formatKickoff(null).time).toBe('Kickoff time TBD');
    expect(getTeamLogoUrl({ id: 'unknown', logoKey: 'historical' })).toBeNull();
    expect(getTeamLogoUrl({ id: '26', logoKey: 'sea' })).toBe('/team-logos/sea.svg');
  });
});
