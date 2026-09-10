import http from 'node:http';

const port = Number(process.env.MOCK_READ_API_PORT ?? 8011);
let scenario = 'lifecycle';
let counts = new Map();
let requests = [];

const week = (number, phase = 'regular_season', season = 2026) => ({ season, phase, week: number });
const knownWeeks = [week(1, 'preseason', 2020), week(5, 'preseason', 2020), week(17, 'regular_season', 2020), week(1), week(2), week(1, 'postseason')];
const record = (wins, losses, ties = 0) => ({ wins, losses, ties, scope: 'regular_season', snapshotAt: '2026-09-10T00:00:00Z' });
const team = (id, displayName, abbreviation, pregameRecord = record(0, 0), postgameRecord = null) => ({ id, displayName, abbreviation, logoKey: id, pregameRecord, postgameRecord });
const rating = (state = 'pending', score = null, confirmationSupported = true) => ({
  state,
  score,
  source: state === 'confirmed' ? 'nflverse' : state === 'provisional' ? 'espn' : null,
  modelVersion: score === null ? null : 'rating-v1',
  calculatedAt: score === null ? null : '2026-09-11T04:00:00Z',
  confirmedAt: state === 'confirmed' ? '2026-09-12T04:00:00Z' : null,
  confirmationSupported,
  confirmationWorkRemains: confirmationSupported && state !== 'confirmed',
});
const game = ({
  id = 'game-1', seasonWeek = week(1), kickoffAt = '2026-09-11T00:20:00Z',
  home = team('26', 'Seattle Seahawks', 'SEA', record(0, 0)),
  away = team('17', 'New England Patriots', 'NE', record(0, 0)),
  state = 'scheduled', detail = null, period = null, clock = null, score = null,
  gameRating = rating(), broadcaster = 'NBC', odds = { details: 'SEA -3.5', updatedAt: '2026-09-10T00:00:00Z' },
} = {}) => ({
  id, espnId: id, seasonWeek, kickoffAt, home, away,
  status: { state, detail, period, clock, score }, broadcaster, odds, rating: gameRating,
  freshness: { scheduleCheckedAt: '2026-09-11T04:00:00Z', scheduleUpdatedAt: '2026-09-10T00:00:00Z', liveSourceCheckedAt: '2026-09-11T04:00:00Z', liveStateUpdatedAt: '2026-09-11T04:00:00Z' },
});
const envelope = (games, pollAfterSeconds, selectedWeek = week(1)) => ({ season: selectedWeek.season, week: selectedWeek, snapshotAsOf: games.length ? '2026-09-11T04:00:00Z' : null, pollAfterSeconds, games });
const bootstrap = (pollAfterSeconds = null) => ({ activeSeason: 2026, currentWeek: week(1), knownWeeks, calendarVersion: 'browser-mock-v1', pollAfterSeconds });

const corsHeaders = (request) => {
  const origin = request.headers.origin;
  const allowed = origin === 'http://127.0.0.1:3000' || origin === 'http://localhost:3000';
  return allowed ? {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'If-None-Match',
    'Access-Control-Expose-Headers': 'ETag, Cache-Control, Retry-After',
    'Access-Control-Allow-Private-Network': 'true',
    Vary: 'Origin',
  } : {};
};
const count = (key) => { const next = (counts.get(key) ?? 0) + 1; counts.set(key, next); return next; };
const reply = (request, response, status, body = null, headers = {}) => {
  const allHeaders = { ...corsHeaders(request), 'Cache-Control': status >= 400 ? 'no-store' : 'public, max-age=0, must-revalidate', ...headers };
  if (body !== null) allHeaders['Content-Type'] = 'application/json';
  response.writeHead(status, allHeaders);
  response.end(body === null ? '' : JSON.stringify(body));
  requests.push({ scenario, path: new URL(request.url, `http://${request.headers.host}`).pathname, status, origin: request.headers.origin ?? null, ifNoneMatch: request.headers['if-none-match'] ?? null, at: new Date().toISOString() });
};

const lifecycleWeek = (step) => {
  if (step === 1) return { etag: '"life-1"', body: envelope([game()], 1) };
  if (step === 5) return { etag: '"life-2"', body: envelope([game({ state: 'delayed', detail: 'Weather delay', odds: null })], 1) };
  if (step === 6) return { etag: '"life-3"', body: envelope([game({ state: 'in_progress', detail: '1st Quarter', period: 1, clock: '08:14', score: { home: 7, away: 3 }, odds: null })], 1) };
  if (step === 7) return { etag: '"life-4"', body: envelope([game({ state: 'in_progress', detail: '2nd Quarter', period: 2, clock: '04:02', score: { home: 10, away: 3 }, odds: null })], 1) };
  if (step === 8) return { etag: '"life-5"', body: envelope([game({ state: 'final', detail: 'Final', score: { home: 24, away: 17 }, odds: null, gameRating: rating('provisional', 7.8) })], 1) };
  return { etag: '"life-6"', body: envelope([game({ state: 'final', detail: 'Final', score: { home: 24, away: 17 }, odds: null, gameRating: rating('confirmed', 8.1) })], null) };
};

const seasonGames = () => {
  const eligible = Array.from({ length: 12 }, (_, index) => game({
    id: `ranked-${index + 1}`,
    seasonWeek: index === 11 ? week(1, 'postseason') : week(Math.min(index + 1, 18)),
    home: team(`ranked-home-${index + 1}`, `Ranked Home ${index + 1}`, 'SEA', record(8, 2), record(9, 2)),
    away: team(`ranked-away-${index + 1}`, `Ranked Away ${index + 1}`, 'NE', record(7, 3), record(7, 4)),
    state: 'final', detail: 'Final', score: { home: 24, away: 17 }, odds: null,
    gameRating: rating(index % 2 ? 'provisional' : 'confirmed', 9.9 - index * 0.2),
  }));
  return [
    ...eligible,
    game({ id: 'preseason-high', seasonWeek: week(1, 'preseason'), state: 'final', detail: 'Final', score: { home: 30, away: 20 }, gameRating: rating('confirmed', 10), odds: null }),
    game({ id: 'upcoming-rated', state: 'scheduled', gameRating: rating('confirmed', 9.95) }),
    game({ id: 'final-pending', state: 'final', detail: 'Final', score: { home: 17, away: 14 }, gameRating: rating('pending', null), odds: null }),
  ];
};

const server = http.createServer((request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);
  if (request.method === 'OPTIONS') return reply(request, response, 200, null);
  if (url.pathname === '/__set') {
    scenario = url.searchParams.get('name') ?? 'lifecycle'; counts = new Map(); requests = [];
    return reply(request, response, 200, { scenario });
  }
  if (url.pathname === '/__log') return reply(request, response, 200, { scenario, counts: Object.fromEntries(counts), requests });

  if (url.pathname === '/api/v1/bootstrap') {
    const step = count('bootstrap');
    // React StrictMode mounts the development tree twice. Fail both mount
    // attempts so the explicit Retry path is deterministic.
    if (scenario === 'bootstrap-fail' && step <= 2) return reply(request, response, 500, { detail: 'test bootstrap failure' });
    if (request.headers['if-none-match'] === '"bootstrap-1"') return reply(request, response, 304, null, { ETag: '"bootstrap-1"' });
    return reply(request, response, 200, bootstrap(null), { ETag: '"bootstrap-1"' });
  }

  if (url.pathname === '/api/v1/seasons/2026') {
    const step = count('season');
    return reply(request, response, 200, { season: 2026, snapshotAsOf: '2026-09-11T04:00:00Z', pollAfterSeconds: step === 1 ? 1 : null, games: seasonGames() }, { ETag: `"season-${step}"` });
  }

  const match = url.pathname.match(/^\/api\/v1\/weeks\/(\d+)\/([^/]+)\/(\d+)$/);
  if (!match) return reply(request, response, 404, { detail: 'not found' });
  const selectedWeek = week(Number(match[3]), match[2], Number(match[1]));
  const key = `week-${selectedWeek.season}-${selectedWeek.phase}-${selectedWeek.week}`;
  const step = count(key);

  if (scenario === 'race') {
    const body = envelope([game({ id: `race-${selectedWeek.week}`, seasonWeek: selectedWeek, home: team(`race-home-${selectedWeek.week}`, selectedWeek.week === 1 ? 'Slow Week One' : 'Fast Week Two', 'SEA') })], null, selectedWeek);
    return setTimeout(() => reply(request, response, 200, body, { ETag: `"race-${selectedWeek.week}"` }), selectedWeek.week === 1 ? 1500 : 0);
  }
  if (scenario === 'missing') {
    const body = envelope([game({ id: 'missing-game', kickoffAt: null, home: team('historic', 'Historical Home', 'HIS', null, record(2, 1)), away: team('unknown', 'Unknown Away', 'UNK', null), state: 'final', detail: 'Final', score: null, gameRating: rating('unavailable', null), odds: null })], null, selectedWeek);
    return reply(request, response, 200, body, { ETag: '"missing-1"' });
  }
  if (scenario === 'unsupported') {
    const body = envelope([game({ state: 'final', detail: 'Final', score: { home: 20, away: 17 }, gameRating: rating('provisional', 6.4, false), odds: null })], null, selectedWeek);
    return reply(request, response, 200, body, { ETag: '"unsupported-1"' });
  }
  if (scenario === 'unavailable') {
    const body = step === 1
      ? envelope([game({ state: 'final', detail: 'Final', score: { home: 20, away: 17 }, gameRating: rating('unavailable', null), odds: null })], 1, selectedWeek)
      : envelope([game({ state: 'final', detail: 'Final', score: { home: 20, away: 17 }, gameRating: rating('confirmed', 7.1), odds: null })], null, selectedWeek);
    return reply(request, response, 200, body, { ETag: `"unavailable-${step}"` });
  }
  if (scenario === 'postponed') {
    const body = step === 1
      ? envelope([game({ state: 'postponed', detail: 'Postponed' })], 1, selectedWeek)
      : envelope([game({ state: 'in_progress', detail: 'Resumed — 1st Quarter', period: 1, clock: '12:00', score: { home: 0, away: 0 }, odds: null })], null, selectedWeek);
    return reply(request, response, 200, body, { ETag: `"postponed-${step}"` });
  }
  if (scenario === 'lifecycle') {
    if (step === 2 && request.headers['if-none-match'] === '"life-1"') return reply(request, response, 304, null, { ETag: '"life-1"' });
    if (step === 3) return reply(request, response, 429, { detail: 'test throttle' }, { 'Retry-After': '1' });
    if (step === 4) return reply(request, response, 503, { detail: 'test outage' }, { 'Retry-After': '1' });
    const result = lifecycleWeek(step);
    return reply(request, response, 200, result.body, { ETag: result.etag });
  }
  return reply(request, response, 200, envelope([], null, selectedWeek), { ETag: '"empty"' });
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write(`Mock read API listening on http://127.0.0.1:${port} (${scenario})\n`);
});
