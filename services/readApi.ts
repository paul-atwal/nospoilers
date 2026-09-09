import type {
  ApiFreshness,
  ApiGame,
  ApiGameState,
  ApiRating,
  ApiRatingState,
  ApiTeam,
  BootstrapResponse,
  SeasonPhase,
  SeasonSnapshotResponse,
  SeasonWeek,
  WeekSnapshotResponse,
} from '../types';

export type ReadApiBody = BootstrapResponse | WeekSnapshotResponse | SeasonSnapshotResponse;

export interface ReadApiResponse<T> {
  /** The retained body for both 200 and 304 responses. */
  readonly body: T;
  /** Alias useful to callers that use the Fetch API vocabulary. */
  readonly data: T;
  readonly etag: string | null;
  readonly notModified: boolean;
  readonly pollAfterSeconds: number | null;
}

export class ReadApiError extends Error {
  readonly status: number | null;
  readonly retryAfterMs: number | null;
  readonly retryable: boolean;

  constructor(
    message: string,
    options: { status?: number | null; retryAfterMs?: number | null; retryable?: boolean } = {},
  ) {
    super(message);
    this.name = 'ReadApiError';
    this.status = options.status ?? null;
    this.retryAfterMs = options.retryAfterMs ?? null;
    this.retryable = options.retryable ?? false;
  }
}

export const RETRY_MAX_DELAY_MS = 30_000;

export function normalizeApiBaseUrl(value: string | undefined | null): string {
  const configured = (value ?? '').trim().replace(/\/+$/, '');
  if (/\/api(?:\/v1)?$/i.test(configured)) {
    throw new Error('VITE_API_URL must be an origin/base prefix, not an /api or /api/v1 path');
  }
  return configured;
}

export function getConfiguredApiBaseUrl(): string {
  // Vite supplies import.meta.env in the browser. Keeping this function free of
  // a localhost fallback is important: production must use same-origin or an
  // explicitly configured read API.
  return normalizeApiBaseUrl(import.meta.env?.VITE_API_URL);
}

export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (!value) return null;
  const seconds = Number(value.trim());
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(RETRY_MAX_DELAY_MS, Math.round(seconds * 1000));
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return Math.min(RETRY_MAX_DELAY_MS, Math.max(0, timestamp - now));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0) throw new ReadApiError(`Invalid read API field: ${field}`);
  return value;
}

function nullableString(value: unknown, field: string): string | null {
  if (value !== null && typeof value !== 'string') throw new ReadApiError(`Invalid read API field: ${field}`);
  return value as string | null;
}

function integer(value: unknown, field: string): number {
  if (!Number.isInteger(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  return value as number;
}

function nullableNumber(value: unknown, field: string): number | null {
  if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
    throw new ReadApiError(`Invalid read API field: ${field}`);
  }
  return value as number | null;
}

function nullableBoolean(value: unknown, field: string): boolean {
  if (typeof value !== 'boolean') throw new ReadApiError(`Invalid read API field: ${field}`);
  return value;
}

function phase(value: unknown, field: string): SeasonPhase {
  if (value !== 'preseason' && value !== 'regular_season' && value !== 'postseason') {
    throw new ReadApiError(`Invalid read API field: ${field}`);
  }
  return value;
}

export function decodeSeasonWeek(value: unknown, field = 'seasonWeek'): SeasonWeek {
  if (!isRecord(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  const season = integer(value.season, `${field}.season`);
  const week = integer(value.week, `${field}.week`);
  if (season <= 0 || week <= 0) throw new ReadApiError(`Invalid read API field: ${field}`);
  return { season, phase: phase(value.phase, `${field}.phase`), week };
}

function decodeRecord(value: unknown, field: string) {
  if (value === null) return null;
  if (!isRecord(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  const wins = integer(value.wins, `${field}.wins`);
  const losses = integer(value.losses, `${field}.losses`);
  const ties = integer(value.ties, `${field}.ties`);
  if (wins < 0 || losses < 0 || ties < 0) throw new ReadApiError(`Invalid read API field: ${field}`);
  const scope = value.scope;
  if (scope !== 'preseason' && scope !== 'regular_season') {
    throw new ReadApiError(`Invalid read API field: ${field}.scope`);
  }
  return {
    wins,
    losses,
    ties,
    scope: scope as 'preseason' | 'regular_season',
    snapshotAt: value.snapshotAt === undefined ? null : nullableString(value.snapshotAt, `${field}.snapshotAt`),
  };
}

function decodeTeam(value: unknown, field: string): ApiTeam {
  if (!isRecord(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  return {
    id: requiredString(value.id, `${field}.id`),
    displayName: requiredString(value.displayName, `${field}.displayName`),
    abbreviation: requiredString(value.abbreviation, `${field}.abbreviation`),
    logoKey: value.logoKey === undefined ? null : nullableString(value.logoKey, `${field}.logoKey`),
    pregameRecord: decodeRecord(value.pregameRecord, `${field}.pregameRecord`),
    postgameRecord: decodeRecord(value.postgameRecord, `${field}.postgameRecord`),
  };
}

const GAME_STATES: readonly ApiGameState[] = ['scheduled', 'in_progress', 'final', 'delayed', 'postponed', 'cancelled'];
const RATING_STATES: readonly ApiRatingState[] = ['pending', 'provisional', 'confirmed', 'unavailable'];

function decodeStatus(value: unknown, field: string): ApiGame['status'] {
  if (!isRecord(value) || !GAME_STATES.includes(value.state as ApiGameState)) {
    throw new ReadApiError(`Invalid read API field: ${field}`);
  }
  const score = value.score;
  let decodedScore: ApiGame['status']['score'] = null;
  if (score !== null) {
    if (!isRecord(score)) throw new ReadApiError(`Invalid read API field: ${field}.score`);
    const home = nullableNumber(score.home, `${field}.score.home`);
    const away = nullableNumber(score.away, `${field}.score.away`);
    if (home === null || away === null) throw new ReadApiError(`Invalid read API field: ${field}.score`);
    decodedScore = { home, away };
  }
  return {
    state: value.state as ApiGameState,
    detail: value.detail === undefined ? null : nullableString(value.detail, `${field}.detail`),
    period: value.period === undefined ? null : nullableNumber(value.period, `${field}.period`),
    clock: value.clock === undefined ? null : nullableString(value.clock, `${field}.clock`),
    score: decodedScore,
  };
}

function decodeRating(value: unknown, field: string): ApiRating {
  if (!isRecord(value) || !RATING_STATES.includes(value.state as ApiRatingState)) {
    throw new ReadApiError(`Invalid read API field: ${field}`);
  }
  return {
    state: value.state as ApiRatingState,
    score: nullableNumber(value.score, `${field}.score`),
    source: nullableString(value.source, `${field}.source`),
    modelVersion: nullableString(value.modelVersion, `${field}.modelVersion`),
    calculatedAt: nullableString(value.calculatedAt, `${field}.calculatedAt`),
    confirmedAt: nullableString(value.confirmedAt, `${field}.confirmedAt`),
    confirmationSupported: nullableBoolean(value.confirmationSupported, `${field}.confirmationSupported`),
    confirmationWorkRemains: nullableBoolean(value.confirmationWorkRemains, `${field}.confirmationWorkRemains`),
  };
}

function decodeFreshness(value: unknown, field: string): ApiFreshness {
  if (!isRecord(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  return {
    scheduleCheckedAt: nullableString(value.scheduleCheckedAt, `${field}.scheduleCheckedAt`),
    scheduleUpdatedAt: nullableString(value.scheduleUpdatedAt, `${field}.scheduleUpdatedAt`),
    liveSourceCheckedAt: nullableString(value.liveSourceCheckedAt, `${field}.liveSourceCheckedAt`),
    liveStateUpdatedAt: nullableString(value.liveStateUpdatedAt, `${field}.liveStateUpdatedAt`),
  };
}

export function decodeApiGame(value: unknown, field = 'games[]'): ApiGame {
  if (!isRecord(value)) throw new ReadApiError(`Invalid read API field: ${field}`);
  const oddsValue = value.odds;
  let odds: ApiGame['odds'] = null;
  if (oddsValue !== null) {
    if (!isRecord(oddsValue)) throw new ReadApiError(`Invalid read API field: ${field}.odds`);
    odds = {
      details: nullableString(oddsValue.details, `${field}.odds.details`),
      updatedAt: nullableString(oddsValue.updatedAt, `${field}.odds.updatedAt`),
    };
  }
  return {
    id: requiredString(value.id, `${field}.id`),
    espnId: requiredString(value.espnId, `${field}.espnId`),
    seasonWeek: decodeSeasonWeek(value.seasonWeek, `${field}.seasonWeek`),
    kickoffAt: nullableString(value.kickoffAt, `${field}.kickoffAt`),
    home: decodeTeam(value.home, `${field}.home`),
    away: decodeTeam(value.away, `${field}.away`),
    status: decodeStatus(value.status, `${field}.status`),
    broadcaster: nullableString(value.broadcaster, `${field}.broadcaster`),
    odds,
    rating: decodeRating(value.rating, `${field}.rating`),
    freshness: decodeFreshness(value.freshness, `${field}.freshness`),
  };
}

function pollAfter(value: unknown, field: string): number | null {
  if (value !== null && (!Number.isInteger(value) || (value as number) < 0)) {
    throw new ReadApiError(`Invalid read API field: ${field}`);
  }
  return value as number | null;
}

function decodeGames(value: unknown): readonly ApiGame[] {
  if (!Array.isArray(value)) throw new ReadApiError('Invalid read API field: games');
  return value.map((game, index) => decodeApiGame(game, `games[${index}]`));
}

export function decodeBootstrap(value: unknown): BootstrapResponse {
  if (!isRecord(value) || !Array.isArray(value.knownWeeks)) throw new ReadApiError('Invalid bootstrap response');
  const activeSeason = integer(value.activeSeason, 'activeSeason');
  if (activeSeason <= 0) throw new ReadApiError('Invalid bootstrap field: activeSeason');
  return {
    activeSeason,
    currentWeek: decodeSeasonWeek(value.currentWeek, 'currentWeek'),
    knownWeeks: value.knownWeeks.map((week, index) => decodeSeasonWeek(week, `knownWeeks[${index}]`)),
    calendarVersion: requiredString(value.calendarVersion, 'calendarVersion'),
    pollAfterSeconds: pollAfter(value.pollAfterSeconds, 'pollAfterSeconds'),
  };
}

export function decodeWeekSnapshot(value: unknown): WeekSnapshotResponse {
  if (!isRecord(value)) throw new ReadApiError('Invalid week snapshot response');
  return {
    season: integer(value.season, 'season'),
    week: decodeSeasonWeek(value.week, 'week'),
    snapshotAsOf: nullableString(value.snapshotAsOf, 'snapshotAsOf'),
    pollAfterSeconds: pollAfter(value.pollAfterSeconds, 'pollAfterSeconds'),
    games: decodeGames(value.games),
  };
}

export function decodeSeasonSnapshot(value: unknown): SeasonSnapshotResponse {
  if (!isRecord(value)) throw new ReadApiError('Invalid season snapshot response');
  return {
    season: integer(value.season, 'season'),
    snapshotAsOf: nullableString(value.snapshotAsOf, 'snapshotAsOf'),
    pollAfterSeconds: pollAfter(value.pollAfterSeconds, 'pollAfterSeconds'),
    games: decodeGames(value.games),
  };
}

interface CacheEntry<T> {
  etag: string | null;
  body: T;
  pollAfterSeconds: number | null;
}

export class ReadApiClient {
  private readonly baseUrl: string;
  private readonly cache = new Map<string, CacheEntry<ReadApiBody>>();
  private readonly fetcher: typeof fetch;

  constructor(options: { baseUrl?: string; fetcher?: typeof fetch } = {}) {
    this.baseUrl = normalizeApiBaseUrl(options.baseUrl ?? getConfiguredApiBaseUrl());
    this.fetcher = options.fetcher ?? fetch;
  }

  getBaseUrl(): string { return this.baseUrl; }

  clearCache(): void { this.cache.clear(); }

  getCacheEntry(path: string): ReadApiResponse<ReadApiBody> | null {
    const cached = this.cache.get(path);
    return cached ? {
      body: cached.body,
      data: cached.body,
      etag: cached.etag,
      notModified: false,
      pollAfterSeconds: cached.pollAfterSeconds,
    } : null;
  }

  getCachedWeekSnapshot(week: SeasonWeek): ReadApiResponse<WeekSnapshotResponse> | null {
    return this.getCacheEntry(`/api/v1/weeks/${week.season}/${week.phase}/${week.week}`) as ReadApiResponse<WeekSnapshotResponse> | null;
  }

  getCachedSeasonSnapshot(season: number): ReadApiResponse<SeasonSnapshotResponse> | null {
    return this.getCacheEntry(`/api/v1/seasons/${season}`) as ReadApiResponse<SeasonSnapshotResponse> | null;
  }

  fetchBootstrap(signal?: AbortSignal): Promise<ReadApiResponse<BootstrapResponse>> {
    return this.request('/api/v1/bootstrap', decodeBootstrap, signal);
  }

  fetchWeekSnapshot(week: SeasonWeek, signal?: AbortSignal): Promise<ReadApiResponse<WeekSnapshotResponse>> {
    const path = `/api/v1/weeks/${week.season}/${week.phase}/${week.week}`;
    return this.request(path, decodeWeekSnapshot, signal);
  }

  fetchSeasonSnapshot(season: number, signal?: AbortSignal): Promise<ReadApiResponse<SeasonSnapshotResponse>> {
    return this.request(`/api/v1/seasons/${season}`, decodeSeasonSnapshot, signal);
  }

  private async request<T extends ReadApiBody>(
    path: string,
    decoder: (value: unknown) => T,
    signal?: AbortSignal,
  ): Promise<ReadApiResponse<T>> {
    const cached = this.cache.get(path);
    const response = await this.fetchRaw(path, cached?.etag ?? null, signal);
    if (response.status === 304) {
      if (cached) {
        const currentEtag = response.headers.get('ETag') ?? cached.etag;
        cached.etag = currentEtag;
        return {
          body: cached.body as T,
          data: cached.body as T,
          etag: currentEtag,
          notModified: true,
          pollAfterSeconds: cached.pollAfterSeconds,
        };
      }
      // A validator can survive a process/storage transition without its body.
      // Recover exactly once with an unconditional request.
      const recovered = await this.fetchRaw(path, null, signal);
      if (recovered.status === 304) {
        throw new ReadApiError('Read API returned 304 without a retained body', { status: 304 });
      }
      return this.decodeAndCache(path, recovered, decoder);
    }
    return this.decodeAndCache(path, response, decoder);
  }

  private async fetchRaw(path: string, etag: string | null, signal?: AbortSignal): Promise<Response> {
    let response: Response;
    try {
      // Native browser fetch requires its ordinary global receiver. Calling a
      // stored function as `this.fetcher(...)` supplies the client as `this`
      // and fails before a network request in Chromium.
      const fetcher = this.fetcher;
      response = await fetcher(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: etag ? { 'If-None-Match': etag } : undefined,
        signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ReadApiError('Read API network request failed', { retryable: true });
    }

    if (response.status === 304) return response;
    if (!response.ok) {
      throw new ReadApiError(`Read API request failed (${response.status})`, {
        status: response.status,
        retryAfterMs: parseRetryAfter(response.headers.get('Retry-After')),
        retryable: response.status === 429 || response.status === 503,
      });
    }
    return response;
  }

  private async decodeAndCache<T extends ReadApiBody>(
    path: string,
    response: Response,
    decoder: (value: unknown) => T,
  ): Promise<ReadApiResponse<T>> {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new ReadApiError('Read API returned invalid JSON', { status: response.status });
    }
    const body = decoder(payload);
    const entry: CacheEntry<T> = {
      body,
      etag: response.headers.get('ETag'),
      pollAfterSeconds: body.pollAfterSeconds,
    };
    this.cache.set(path, entry as CacheEntry<ReadApiBody>);
    return {
      body,
      data: body,
      etag: entry.etag,
      notModified: false,
      pollAfterSeconds: entry.pollAfterSeconds,
    };
  }
}

export const readApi = new ReadApiClient();

export const fetchBootstrap = (signal?: AbortSignal) => readApi.fetchBootstrap(signal);
export const fetchWeekSnapshot = (week: SeasonWeek, signal?: AbortSignal) => readApi.fetchWeekSnapshot(week, signal);
export const fetchSeasonSnapshot = (season: number, signal?: AbortSignal) => readApi.fetchSeasonSnapshot(season, signal);
