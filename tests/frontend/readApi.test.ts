import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ReadApiClient,
  ReadApiError,
  decodeBootstrap,
  normalizeApiBaseUrl,
  parseRetryAfter,
} from '../../services/readApi';

const week = { season: 2026, phase: 'regular_season' as const, week: 1 };
const bootstrap = {
  activeSeason: 2026,
  currentWeek: week,
  knownWeeks: [week],
  calendarVersion: 'test',
  pollAfterSeconds: 300,
};

const response = (body: unknown, status = 200, headers: Record<string, string> = {}) => new Response(
  status === 304 ? null : JSON.stringify(body),
  { status, headers: { 'Content-Type': 'application/json', ...headers } },
);

afterEach(() => vi.restoreAllMocks());

describe('read API URL and decoding contract', () => {
  it('uses an origin/base prefix and never duplicates API segments', () => {
    expect(normalizeApiBaseUrl('http://127.0.0.1:8001///')).toBe('http://127.0.0.1:8001');
    expect(normalizeApiBaseUrl('')).toBe('');
    expect(() => normalizeApiBaseUrl('http://localhost/api')).toThrow();
    expect(() => normalizeApiBaseUrl('http://localhost/api/v1/')).toThrow();
    expect(() => decodeBootstrap({ ...bootstrap, knownWeeks: [{ ...week, phase: 'nope' }] })).toThrow(ReadApiError);
  });

  it('parses bounded delta and HTTP-date Retry-After values', () => {
    expect(parseRetryAfter('2')).toBe(2000);
    expect(parseRetryAfter('9999')).toBe(30_000);
    expect(parseRetryAfter(new Date(10_000).toUTCString(), 0)).toBe(10_000);
    expect(parseRetryAfter('not-a-date')).toBeNull();
  });
});

describe('ReadApiClient endpoint and body ownership', () => {
  it('requests exact paths and retains a cached body on 304', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(bootstrap, 200, { ETag: '"bootstrap-v1"' }))
      .mockResolvedValueOnce(response(null, 304, { ETag: '"bootstrap-v1"' }));
    const client = new ReadApiClient({ baseUrl: 'http://127.0.0.1:8001/', fetcher });

    const first = await client.fetchBootstrap();
    const second = await client.fetchBootstrap();
    expect(fetcher.mock.calls[0][0]).toBe('http://127.0.0.1:8001/api/v1/bootstrap');
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({ 'If-None-Match': '"bootstrap-v1"' });
    expect(second.body).toEqual(first.body);
    expect(second.notModified).toBe(true);
    expect(second.pollAfterSeconds).toBe(300);
  });

  it('recovers cacheless 304 with one unconditional request', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(null, 304, { ETag: '"orphan"' }))
      .mockResolvedValueOnce(response(bootstrap, 200, { ETag: '"fresh"' }));
    const client = new ReadApiClient({ baseUrl: 'http://api.test', fetcher });
    await expect(client.fetchBootstrap()).resolves.toMatchObject({ body: bootstrap });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1]?.headers).toBeUndefined();
  });

  it('isolates validators and bodies by exact endpoint', async () => {
    const weekBody = { season: 2026, week, snapshotAsOf: null, pollAfterSeconds: null, games: [] };
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(bootstrap, 200, { ETag: '"bootstrap"' }))
      .mockResolvedValueOnce(response(weekBody, 200, { ETag: '"week"' }));
    const client = new ReadApiClient({ baseUrl: 'http://api.test', fetcher });
    await client.fetchBootstrap();
    await client.fetchWeekSnapshot(week);
    expect(fetcher.mock.calls[0][0]).toContain('/api/v1/bootstrap');
    expect(fetcher.mock.calls[1][0]).toContain('/api/v1/weeks/2026/regular_season/1');
    expect(fetcher.mock.calls[1][1]?.headers).toBeUndefined();
  });

  it('hydrates only the exact cached week or season endpoint on re-entry', async () => {
    const weekBody = { season: 2026, week, snapshotAsOf: null, pollAfterSeconds: null, games: [] };
    const seasonBody = { season: 2026, snapshotAsOf: null, pollAfterSeconds: null, games: [] };
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(weekBody, 200, { ETag: '"week"' }))
      .mockResolvedValueOnce(response(seasonBody, 200, { ETag: '"season"' }));
    const client = new ReadApiClient({ baseUrl: 'http://api.test', fetcher });
    await client.fetchWeekSnapshot(week);
    await client.fetchSeasonSnapshot(2026);
    expect(client.getCachedWeekSnapshot(week)?.body).toEqual(weekBody);
    expect(client.getCachedSeasonSnapshot(2026)?.body).toEqual(seasonBody);
    expect(client.getCachedWeekSnapshot({ ...week, week: 2 })).toBeNull();
  });

  it('exposes bounded retry metadata for 429 and validates successful bodies', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ detail: 'busy' }, 429, { 'Retry-After': '4' }));
    const client = new ReadApiClient({ baseUrl: 'http://api.test', fetcher });
    await expect(client.fetchBootstrap()).rejects.toMatchObject({ status: 429, retryAfterMs: 4000, retryable: true });
    const malformed = new ReadApiClient({ baseUrl: 'http://api.test', fetcher: vi.fn().mockResolvedValue(response({ games: [] })) });
    await expect(malformed.fetchBootstrap()).rejects.toBeInstanceOf(ReadApiError);
  });
});
