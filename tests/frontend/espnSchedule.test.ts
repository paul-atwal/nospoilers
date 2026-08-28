import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchCurrentWeek,
  fetchSchedule,
} from '../../services/espnSchedule';
import type { EspnEvent } from '../../services/espnTypes';


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  localStorage.clear();
});

const makeResponse = (payload: object) => ({
  ok: true,
  json: async () => payload,
});

const makeEvent = ({
  id,
  state,
  homeScore,
  awayScore,
  homeRecord,
  awayRecord,
}: {
  id: string;
  state: 'pre' | 'post';
  homeScore: string;
  awayScore: string;
  homeRecord: string;
  awayRecord: string;
}): EspnEvent => ({
  id,
  date: '2026-11-29T21:05:00Z',
  status: {
    type: {
      state,
      shortDetail: state === 'post' ? 'Final' : 'Scheduled',
    },
  },
  competitions: [{
    competitors: [
      {
        id: `${id}-home`,
        homeAway: 'home',
        score: homeScore,
        records: [{ summary: homeRecord }],
        team: {
          abbreviation: 'HME',
          shortDisplayName: 'Home Team',
        },
      },
      {
        id: `${id}-away`,
        homeAway: 'away',
        score: awayScore,
        records: [{ summary: awayRecord }],
        team: {
          abbreviation: 'AWY',
          shortDisplayName: 'Away Team',
        },
      },
    ],
  }],
});

describe('ESPN schedule season-week handling', () => {
  it('keeps ESPN preseason identity instead of replacing it with Week 1', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeResponse({
      events: [],
      season: { year: 2026, type: 1 },
      week: { number: 3 },
    })));

    await expect(fetchCurrentWeek()).resolves.toEqual({
      seasonWeek: {
        season: 2026,
        phase: 'preseason',
        week: 3,
      },
      title: 'Preseason',
      label: 'Week 3',
      seasonLabel: '2026-27',
    });
  });

  it('includes the requested season in a weekly ESPN query', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeResponse({
      events: [],
      season: { year: 2026, type: 2 },
      week: { number: 1 },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchSchedule({
      season: 2025,
      phase: 'postseason',
      week: 1,
    });

    expect(fetchMock.mock.calls[0][0]).toContain(
      '?dates=2025&week=1&seasontype=3&limit=100',
    );
  });

  it('uses an explicit typed fallback for an unsupported source phase', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2027, 0, 15));
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeResponse({
      events: [],
      season: { year: 2026, type: 99 },
      week: { number: 7 },
    })));

    await expect(fetchCurrentWeek()).resolves.toEqual({
      seasonWeek: {
        season: 2026,
        phase: 'regular_season',
        week: 1,
      },
      title: 'Regular Season',
      label: 'Week 1',
      seasonLabel: '2026-27',
    });
  });

  it('removes current-week results from a future week pregame snapshot', async () => {
    const futureEvent = makeEvent({
      id: 'future-game',
      state: 'pre',
      homeScore: '0',
      awayScore: '0',
      homeRecord: '9-2',
      awayRecord: '7-4',
    });
    const currentEvent = makeEvent({
      id: 'current-game',
      state: 'post',
      homeScore: '24',
      awayScore: '17',
      homeRecord: '9-2',
      awayRecord: '7-4',
    });
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('week=12')) {
        return makeResponse({ events: [futureEvent] });
      }
      if (url.includes('week=11')) {
        return makeResponse({ events: [currentEvent] });
      }
      return makeResponse({
        events: [],
        season: { year: 2026, type: 2 },
        week: { number: 11 },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const games = await fetchSchedule({
      season: 2026,
      phase: 'regular_season',
      week: 12,
    });

    expect(games[0].homeRecord).toEqual({
      pregame: {
        record: { wins: 8, losses: 2, ties: 0 },
        scope: 'regular_season',
      },
    });
    expect(games[0].awayRecord).toEqual({
      pregame: {
        record: { wins: 7, losses: 3, ties: 0 },
        scope: 'regular_season',
      },
    });
  });
});
