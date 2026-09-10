import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const appUrl = process.env.BROWSER_APP_URL ?? 'http://127.0.0.1:3000';
const mockUrl = process.env.MOCK_READ_API_URL ?? 'http://127.0.0.1:8011';
const evidenceDir = new URL('../../docs/diagrams/chunk-c/verification/screenshots/', import.meta.url);
await mkdir(evidenceDir, { recursive: true });

const assert = (condition, message) => { if (!condition) throw new Error(message); };
const setScenario = async (name) => {
  const response = await fetch(`${mockUrl}/__set?name=${encodeURIComponent(name)}`);
  assert(response.ok, `could not select ${name}`);
};
const getLog = async () => (await (await fetch(`${mockUrl}/__log`)).json());
const screenshot = (page, name) => page.screenshot({ path: new URL(name, evidenceDir).pathname, fullPage: false });
const gotoScenario = async (page, name) => {
  await setScenario(name);
  await page.goto(`${appUrl}/?scenario=${name}&run=${Date.now()}`, { waitUntil: 'domcontentloaded' });
};

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const pageErrors = [];
const consoleErrors = [];
const warnings = [];
const requests = [];
const attachDiagnostics = (page) => {
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
    if (message.type() === 'warning') warnings.push(message.text());
  });
  page.on('request', (request) => requests.push(request.url()));
};

const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/Los_Angeles' });
const page = await context.newPage();
attachDiagnostics(page);

await gotoScenario(page, 'bootstrap-fail');
await page.getByRole('button', { name: 'Retry' }).waitFor();
await screenshot(page, 'bootstrap-retry.png');
await page.getByRole('button', { name: 'Retry' }).click();
await page.getByRole('button', { name: 'Show Best of Season' }).waitFor();

let abortNextWeekRequest = true;
await page.route('**/api/v1/weeks/2026/regular_season/1', async (route) => {
  if (abortNextWeekRequest) {
    abortNextWeekRequest = false;
    await route.abort('connectionreset');
    return;
  }
  await route.continue();
});
await gotoScenario(page, 'network');
await page.getByText('No games found.').waitFor({ timeout: 4000 });
assert(!abortNextWeekRequest, 'network failure route was not exercised');
await page.unroute('**/api/v1/weeks/2026/regular_season/1');

await gotoScenario(page, 'race');
await page.getByRole('button', { name: 'Next week' }).click();
await page.getByText('Fast Week Two').waitFor();
await page.waitForTimeout(1700);
assert(await page.getByText('Fast Week Two').isVisible(), 'late week-one response replaced week two');
assert(await page.getByText('Slow Week One').count() === 0, 'stale week-one game became visible');

await gotoScenario(page, 'lifecycle');
const scheduledSpread = page.getByLabel('Spread SEA -3.5');
await scheduledSpread.waitFor();
const scheduledCard = page.locator('article').first();
const scheduledCardBox = await scheduledCard.boundingBox();
const scheduledBadgeBox = await scheduledSpread.boundingBox();
assert(scheduledCardBox && scheduledBadgeBox, 'scheduled odds geometry was not measurable');
assert(Math.abs((scheduledBadgeBox.y + scheduledBadgeBox.height / 2) - (scheduledCardBox.y + scheduledCardBox.height / 2)) < 2, 'scheduled odds badge was not vertically centered');
assert(await page.getByText('SEA', { exact: true }).count() > 0, 'favorite team acronym was not rendered');
assert(await page.getByText('-3.5', { exact: true }).count() > 0, 'spread value was not rendered');
assert(await page.getByText('Odds', { exact: true }).count() === 0, 'odds caption leaked into the scheduled card');
assert(await page.getByRole('button', { name: /Reveal score/ }).count() === 0, 'scheduled game exposed reveal');
await screenshot(page, 'scheduled-desktop.png');
await page.getByRole('status').filter({ hasText: 'Showing stale data' }).waitFor({ state: 'visible', timeout: 5000 });
await page.getByText('Weather delay').waitFor({ timeout: 6000 });
assert(await page.getByRole('button', { name: /Reveal score/ }).count() === 0, 'scoreless delay exposed reveal');
const reveal = page.getByRole('button', { name: /Reveal score for Patriots at Seahawks/ });
await reveal.waitFor({ timeout: 6000 });
await reveal.focus();
assert(await reveal.evaluate((element) => element.matches(':focus-visible')), 'reveal control lacks visible keyboard focus');
await page.keyboard.press('Enter');
await page.getByLabel('Seahawks score 7').waitFor();
await page.getByLabel('Seahawks score 10').waitFor({ timeout: 4000 });
await screenshot(page, 'live-revealed-desktop.png');
await page.getByText('7.8', { exact: true }).waitFor({ timeout: 4000 });
await page.getByText('8.1', { exact: true }).waitFor({ timeout: 4000 });
assert(await page.getByText('Confirmed rating', { exact: true }).count() === 0, 'confirmed rating caption leaked into the card');
assert(await page.getByLabel('Seahawks score 24').isVisible(), 'revealed score did not continue updating');
const lifecycleLog = await getLog();
assert(lifecycleLog.requests.some((entry) => entry.status === 304 && entry.ifNoneMatch === '"life-1"'), '304 validator retention was not exercised');
assert(lifecycleLog.requests.some((entry) => entry.status === 429), '429 retry was not exercised');
assert(lifecycleLog.requests.some((entry) => entry.status === 503), '503 retry was not exercised');

await gotoScenario(page, 'postponed');
await page.getByText('Postponed').waitFor();
await page.getByText(/Resumed/).waitFor({ timeout: 4000 });

await gotoScenario(page, 'unavailable');
await page.getByText('Rating unavailable').waitFor();
await page.getByText('7.1', { exact: true }).waitFor({ timeout: 4000 });

await gotoScenario(page, 'unsupported');
await page.getByText('6.4', { exact: true }).waitFor();
await page.waitForTimeout(1400);
let unsupportedLog = await getLog();
assert(unsupportedLog.counts['week-2026-regular_season-1'] === 1, 'unsupported confirmation kept polling');
// Headless Chromium does not change document.visibilityState when switching
// tabs, so emit the same paired events a real page return produces. The app
// must coalesce them into one refresh.
await page.evaluate(() => {
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
});
await page.waitForTimeout(500);
unsupportedLog = await getLog();
assert(unsupportedLog.counts['week-2026-regular_season-1'] === 2, 'page return did not refresh stopped data exactly once');

await gotoScenario(page, 'missing');
await page.setViewportSize({ width: 390, height: 844 });
await page.getByText('Kickoff time TBD').waitFor();
assert(
  await page.getByLabel('Historical Home abbreviation').isVisible()
    && await page.getByLabel('Unknown Away abbreviation').isVisible(),
  'unknown team fallback missing',
);
assert(await page.getByRole('button', { name: /Reveal score/ }).count() === 0, 'missing score exposed reveal');
assert((await page.locator('body').innerText()).includes('0-0') === false, 'missing data fabricated a 0-0 score');
await screenshot(page, 'missing-narrow.png');

await gotoScenario(page, 'known-empty');
await page.getByText('No games found.').waitFor();

await page.setViewportSize({ width: 1440, height: 900 });
await gotoScenario(page, 'season');
await page.getByRole('button', { name: 'Show Best of Season' }).click();
await page.getByText('Ranked Home 1', { exact: true }).waitFor();
assert(await page.locator('article').count() === 10, 'Best of Season did not show exactly ten games');
assert(await page.getByText('Ranked Home 10', { exact: true }).isVisible(), 'tenth ranked game missing');
assert(await page.getByText('Ranked Home 11', { exact: true }).count() === 0, 'eleventh ranked game leaked into top ten');
assert(await page.getByText(/preseason-high|upcoming-rated|final-pending/i).count() === 0, 'ineligible season game displayed');
await page.waitForTimeout(1300);
const seasonLog = await getLog();
assert(seasonLog.counts.season === 2, 'season did not follow full-envelope polling advice');
await screenshot(page, 'season-desktop.png');
await page.setViewportSize({ width: 390, height: 844 });
assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'narrow layout has horizontal overflow');
await screenshot(page, 'season-narrow.png');

const nyContext = await browser.newContext({ viewport: { width: 900, height: 700 }, timezoneId: 'America/New_York' });
const nyPage = await nyContext.newPage();
attachDiagnostics(nyPage);
await gotoScenario(nyPage, 'unsupported');
await nyPage.getByText('EDT').waitFor();
await gotoScenario(page, 'unsupported');
await page.getByText('PDT').waitFor();
await nyContext.close();

const allowedRequest = (url) => url.startsWith(appUrl) || url.startsWith(mockUrl) || url.startsWith('https://cdn.tailwindcss.com');
const unexpectedRequests = requests.filter((url) => !allowedRequest(url));
assert(unexpectedRequests.length === 0, `unexpected browser requests: ${unexpectedRequests.join(', ')}`);
assert(requests.every((url) => !url.includes('site.api.espn.com') && !url.includes('/excitement/')), 'source or per-game request observed');
const expectedTransportErrors = ['500 (Internal Server Error)', 'ERR_CONNECTION_RESET', '429 (Too Many Requests)', '503 (Service Unavailable)'];
for (const expected of expectedTransportErrors) {
  assert(consoleErrors.some((error) => error.includes(expected)), `missing expected transport diagnostic: ${expected}`);
}
const unexpectedConsoleErrors = consoleErrors.filter((error) => !expectedTransportErrors.some((expected) => error.includes(expected)));
assert(pageErrors.length === 0, `page errors: ${pageErrors.join(' | ')}`);
assert(unexpectedConsoleErrors.length === 0, `unexpected console errors: ${unexpectedConsoleErrors.join(' | ')}`);

await context.close();
await browser.close();
process.stdout.write(JSON.stringify({ ok: true, lifecycleStatuses: lifecycleLog.requests.map((entry) => entry.status), seasonRequests: seasonLog.counts.season, expectedTransportErrors, warnings: [...new Set(warnings)], screenshots: new URL('.', evidenceDir).pathname }, null, 2) + '\n');
