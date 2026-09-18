import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import App from '../../App';
import type { SeasonWeek } from '../../types';

const week = (weekNumber: number): SeasonWeek => ({ season: 2026, phase: 'regular_season', week: weekNumber });
const bootstrap = (currentWeek: SeasonWeek, knownWeeks: readonly SeasonWeek[], activeSeason = 2026) => ({
  activeSeason,
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
  it('restores detailed rating explanations and score bands', async () => {
    await renderWithBootstrapRollover(week(1), week(1));

    fireEvent.click(screen.getByRole('button', { name: 'Rating Info' }));

    expect(screen.getByText(/play-by-play data from 2,600\+ games/)).toBeTruthy();
    expect(screen.getByText(/Game Volatility \(Primary\)/)).toBeTruthy();
    expect(screen.getByText(/Comeback Factor \(Bonus\)/)).toBeTruthy();
    expect(screen.getByText('Score Guide')).toBeTruthy();
    expect(screen.getByText(/Must Watch \(Top 5%\)/)).toBeTruthy();
    expect(screen.getByText(/Skip It/)).toBeTruthy();
  });

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

  it('loads Best of Season from the deliberately selected historical season', async () => {
    const historicalWeek: SeasonWeek = { season: 2024, phase: 'regular_season', week: 1 };
    const knownWeeks = [historicalWeek, week(1), week(2)];
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/api/v1/bootstrap')) {
        return Promise.resolve(jsonResponse(bootstrap(week(1), knownWeeks)));
      }
      if (url.endsWith('/api/v1/seasons/2024')) {
        return Promise.resolve(jsonResponse({ season: 2024, snapshotAsOf: null, pollAfterSeconds: null, games: [] }));
      }
      const matchedWeek = url.match(/\/weeks\/(\d+)\/([^/]+)\/(\d+)$/);
      const selected = matchedWeek
        ? { season: Number(matchedWeek[1]), phase: matchedWeek[2] as SeasonWeek['phase'], week: Number(matchedWeek[3]) }
        : week(1);
      return Promise.resolve(jsonResponse(weekSnapshot(selected)));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await screen.findByText('Week 1');

    fireEvent.click(screen.getByRole('button', { name: 'Select season, currently 2026' }));
    fireEvent.click(screen.getByRole('option', { name: '2024' }));
    await screen.findByText('No games found.');
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/weeks/2024/regular_season/1'))).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Show Best of Season' }));
    await screen.findByText('No eligible rated games yet.');
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/api/v1/seasons/2024'))).toBe(true);
  });

  it('follows an untouched selection into a new active season on rollover', async () => {
    const currentWeek: SeasonWeek = { season: 2026, phase: 'regular_season', week: 18 };
    const nextSeasonWeek: SeasonWeek = { season: 2027, phase: 'preseason', week: 1 };
    const knownWeeks = [currentWeek, nextSeasonWeek];
    let bootstrapCalls = 0;
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/api/v1/bootstrap')) {
        bootstrapCalls += 1;
        return Promise.resolve(jsonResponse(
          bootstrapCalls === 1
            ? bootstrap(currentWeek, knownWeeks)
            : bootstrap(nextSeasonWeek, knownWeeks, 2027),
        ));
      }
      const match = url.match(/\/weeks\/(\d+)\/([^/]+)\/(\d+)$/);
      const selected = match
        ? { season: Number(match[1]), phase: match[2] as SeasonWeek['phase'], week: Number(match[3]) }
        : currentWeek;
      return Promise.resolve(jsonResponse(weekSnapshot(selected)));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await screen.findByText('Week 18');

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });

    expect(await screen.findByRole('button', { name: 'Select season, currently 2027' })).toBeTruthy();
    expect(screen.getByText('Week 1')).toBeTruthy();
  });
});
