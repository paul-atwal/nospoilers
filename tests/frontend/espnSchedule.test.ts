import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchCurrentWeek,
  fetchSchedule,
} from '../../services/espnSchedule';


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
});
