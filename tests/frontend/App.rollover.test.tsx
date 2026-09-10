import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../../App';
import type { SeasonWeek } from '../../types';

const week = (weekNumber: number): SeasonWeek => ({ season: 2026, phase: 'regular_season', week: weekNumber });
const bootstrap = (currentWeek: SeasonWeek, knownWeeks: readonly SeasonWeek[]) => ({
  activeSeason: 2026,
  currentWeek,
  knownWeeks,
  calendarVersion: 'test',
  pollAfterSeconds: null,
});
const weekSnapshot = (currentWeek: SeasonWeek) => ({
  season: 2026,
  week: currentWeek,
  snapshotAsOf: null,
  pollAfterSeconds: null,
  games: [],
});
const jsonResponse = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderWithBootstrapRollover(initial: SeasonWeek, refreshed: SeasonWeek) {
  const knownWeeks = [week(1), week(2), week(3)];
  let bootstrapCalls = 0;
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith('/api/v1/bootstrap')) {
      const body = bootstrapCalls++ === 0
        ? bootstrap(initial, knownWeeks)
        : bootstrap(refreshed, knownWeeks);
      return Promise.resolve(jsonResponse(body));
    }
    const matchedWeek = url.match(/\/weeks\/2026\/regular_season\/(\d+)$/);
    return Promise.resolve(jsonResponse(weekSnapshot(week(Number(matchedWeek?.[1] ?? 1)))));
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<App />);
  await screen.findByText('Week 1');
  return fetchMock;
}

describe('App bootstrap rollover selection ownership', () => {
  it('advances an untouched selection when source current week rolls over', async () => {
    const fetchMock = await renderWithBootstrapRollover(week(1), week(2));

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });

    expect(await screen.findByText('Week 2')).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/bootstrap'))).toHaveLength(2);
  });

  it('keeps a deliberately selected week stable across source rollover', async () => {
    await renderWithBootstrapRollover(week(1), week(3));

    fireEvent.click(screen.getByRole('button', { name: 'Next week' }));
    expect(await screen.findByText('Week 2')).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });

    expect(screen.getByText('Week 2')).toBeTruthy();
    expect(screen.queryByText('Week 3')).toBeNull();
  });
});
