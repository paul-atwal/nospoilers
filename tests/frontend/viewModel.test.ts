import { describe, expect, it } from 'vitest';
import { selectBestSeasonGames } from '../../App';
import { formatKickoff, toViewGame } from '../../services/gameViewModel';
import { getKnownTeamAssets, getTeamLogoUrl } from '../../services/teamAssets';
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
    expect(game.isLive).toBe(false);
    expect(game.isDelayed).toBe(true);
  });

  it('only marks an in-progress game as live', () => {
    const game = toViewGame(makeApiGame('in_progress', { home: 7, away: 3 }));
    expect(game.isLive).toBe(true);
    expect(game.isDelayed).toBe(false);
  });

  it('keeps a missing final-game score nullable too', () => {
    const game = toViewGame(makeApiGame('final', null));
    expect(game.homeScore).toBeNull();
    expect(game.awayScore).toBeNull();
  });

  it('distinguishes scheduled games from paused upcoming games', () => {
    expect(toViewGame(makeApiGame('scheduled', null)).isScheduled).toBe(true);
    expect(toViewGame(makeApiGame('postponed', null)).isScheduled).toBe(false);
    expect(toViewGame(makeApiGame('postponed', null)).isUpcoming).toBe(true);
  });

  it('keeps rating states explicit and maps only confirmed/provisional scores', () => {
    const game = makeApiGame('final', { home: 3, away: 0 });
    expect(toViewGame(game).rating.label).toBe('Rating pending');
    expect(toViewGame({ ...game, rating: { ...game.rating, state: 'unavailable' } }).rating.score).toBeNull();
    expect(toViewGame({ ...game, rating: { ...game.rating, state: 'provisional', score: 7.2 } }).rating.score).toBe(7.2);
  });

  it('maps API team display names to nicknames without city prefixes', () => {
    const game = toViewGame({
      ...makeApiGame('final', { home: 3, away: 0 }),
      home: { ...makeApiGame('final', { home: 0, away: 0 }).home, id: '26', displayName: 'Seattle Seahawks', abbreviation: 'SEA' },
      away: { ...makeApiGame('final', { home: 0, away: 0 }).away, id: '17', displayName: 'New England Patriots', abbreviation: 'NE' },
    });

    expect(game.home?.name).toBe('Seahawks');
    expect(game.away?.name).toBe('Patriots');
    expect(game.homeTeam).toBe('Seahawks');
    expect(game.awayTeam).toBe('Patriots');
  });

  it('formats kickoff in the requested zone with a stable regional label', () => {
    const kickoff = formatKickoff('2026-03-08T10:30:00Z', 'America/Los_Angeles');
    expect(kickoff.time).toMatch(/3:30/);
    expect(kickoff.zone).toBe('PT');
    expect(formatKickoff('2026-03-08T10:30:00Z', 'America/New_York').zone).toBe('ET');
  });

  it('preserves the requested display locale for visible kickoff fields', () => {
    const kickoff = formatKickoff('2026-03-09T00:30:00Z', 'America/Los_Angeles', 'de-DE');
    expect(kickoff.time).toBe('17:30');
    expect(kickoff.date).toBe('8.3.');
    expect(kickoff.zone).toBe('PT');
  });

  it('uses a deterministic unknown kickoff and local logo fallback', () => {
    expect(formatKickoff(null).time).toBe('Kickoff time TBD');
    expect(getTeamLogoUrl({ id: 'unknown', logoKey: 'historical' })).toBeNull();
    expect(getTeamLogoUrl({ id: '26', logoKey: 'sea' })).toBe('/team-logos/26.png');
    expect(getKnownTeamAssets()).toHaveLength(32);
    expect(getTeamLogoUrl({ id: '17', logoKey: '17' })).toBe('/team-logos/17.png');
  });

  it('keeps API ranking order while excluding preseason/upcoming/unrated games and capping at ten', () => {
    const eligible = Array.from({ length: 12 }, (_, index) => ({ ...makeApiGame('final', { home: index, away: 0 }), id: `eligible-${index}`, seasonWeek: { season: 2026, phase: 'regular_season' as const, week: index + 1 }, rating: { ...makeApiGame('final', { home: 0, away: 0 }).rating, state: 'confirmed' as const, score: 9 - index / 10 } }));
    const result = selectBestSeasonGames([{ ...eligible[0], id: 'preseason', seasonWeek: { season: 2026, phase: 'preseason', week: 1 } }, ...eligible, { ...eligible[0], id: 'upcoming', status: { ...eligible[0].status, state: 'scheduled' } }]);
    expect(result).toHaveLength(10);
    expect(result[0].id).toBe('eligible-0');
    expect(result.at(-1)?.id).toBe('eligible-9');
  });
});
