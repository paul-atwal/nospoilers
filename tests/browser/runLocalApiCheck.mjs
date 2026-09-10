import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const appUrl = process.env.BROWSER_APP_URL ?? 'http://127.0.0.1:3001';
const evidenceDir = new URL('../../docs/diagrams/chunk-c/verification/screenshots/', import.meta.url);
await mkdir(evidenceDir, { recursive: true });
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/Vancouver' });
const page = await context.newPage();
const pageErrors = [];
const consoleErrors = [];
const requests = [];
page.on('pageerror', (error) => pageErrors.push(String(error)));
page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
page.on('request', (request) => requests.push(request.url()));

await page.goto(appUrl, { waitUntil: 'domcontentloaded' });
await page.getByText('Seahawks', { exact: true }).first().waitFor();
assert(await page.locator('article').count() === 2, 'seeded week did not render both games');
assert(await page.getByText('8.4', { exact: true }).isVisible(), 'confirmed rating from DynamoDB was not rendered');
assert(await page.getByText('Confirmed rating', { exact: true }).count() === 0, 'confirmed rating caption leaked into the card');
assert(await page.getByLabel('Spread SEA -3.5').isVisible(), 'scheduled odds from DynamoDB were not rendered');
assert(await page.getByLabel('Seahawks score 24').count() === 0, 'final score leaked before reveal');
assert(await page.locator('img[src^="/team-logos/"]').count() === 4, 'local team logos were not used');
await page.getByRole('button', { name: /Reveal score for Patriots at Seahawks/ }).click();
await page.getByLabel('Seahawks score 24').waitFor();
assert(await page.getByText('1-0').isVisible(), 'postgame record did not replace pregame record after reveal');
await page.screenshot({ path: new URL('actual-local-api-week.png', evidenceDir).pathname, fullPage: false });

const transport = await page.evaluate(async () => {
  const response = await fetch('/api/v1/weeks/2020/preseason/1');
  const body = await response.json();
  const etag = response.headers.get('ETag');
  const notModified = await fetch('/api/v1/weeks/2020/preseason/1', { headers: { 'If-None-Match': etag } });
  return { status: response.status, games: body.games.length, pollAfterSeconds: body.pollAfterSeconds, etag, notModifiedStatus: notModified.status };
});
assert(transport.status === 200 && transport.games === 0 && transport.pollAfterSeconds === null, 'known-empty history contract failed');
assert(transport.etag && transport.notModifiedStatus === 304, 'actual API ETag revalidation failed');

await page.getByRole('button', { name: 'Show Best of Season' }).click();
await page.getByText('Season Leaders').waitFor();
await page.waitForFunction(() => document.querySelectorAll('article').length === 1);
assert(await page.locator('article').count() === 1, 'actual season view did not filter to its eligible final game');
assert(await page.getByText('8.4', { exact: true }).isVisible(), 'actual season rating missing');
await page.screenshot({ path: new URL('actual-local-api-season.png', evidenceDir).pathname, fullPage: false });

assert(pageErrors.length === 0, `page errors: ${pageErrors.join(' | ')}`);
assert(consoleErrors.length === 0, `console errors: ${consoleErrors.join(' | ')}`);
assert(requests.every((url) => !url.includes('site.api.espn.com') && !url.includes('/excitement/')), 'source or per-game request observed');

await context.close();
await browser.close();
process.stdout.write(`${JSON.stringify({ ok: true, transport, requestCount: requests.length }, null, 2)}\n`);
