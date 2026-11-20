import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

type Mock = {
  watches: number;
  queries: number;
  clears: number[];
  emit: (latitude?: number, longitude?: number, offset?: number, watch?: number) => void;
  fail: (code: number) => void;
  permission: (state: PermissionState) => void;
};
declare global { interface Window { locationMock: Mock } }

async function mockLocation(page: Page, settings: { state?: PermissionState; unsupported?: boolean; insecure?: boolean } = {}) {
  await page.addInitScript(({ state, unsupported, insecure }) => {
    const permission = Object.assign(new EventTarget(), { state: state ?? 'prompt' });
    const callbacks: Array<{ success: PositionCallback; error: PositionErrorCallback }> = [];
    const mock: Mock = {
      watches: 0, queries: 0, clears: [],
      emit(latitude = 12.3456789, longitude = 23.4567891, offset = 0, watch = callbacks.length - 1) {
        callbacks[watch].success({ timestamp: Date.now() + offset,
          coords: { latitude, longitude, accuracy: 10 } } as GeolocationPosition);
      },
      fail(code) {
        callbacks.at(-1)?.error({ code, message: 'synthetic private error' } as GeolocationPositionError);
      },
      permission(state) {
        permission.state = state;
        permission.dispatchEvent(new Event('change'));
      },
    };
    window.locationMock = mock;
    Object.defineProperty(navigator, 'permissions', { value: { query: async () => {
      mock.queries++;
      return permission;
    } } });
    Object.defineProperty(navigator, 'geolocation', { value: unsupported ? undefined : {
      watchPosition(success: PositionCallback, error: PositionErrorCallback) {
        callbacks.push({ success, error });
        return mock.watches++;
      },
      clearWatch(id: number) { mock.clears.push(id); },
    } });
    if (insecure) Object.defineProperty(window, 'isSecureContext', { value: false });
  }, settings);
}

const start = (page: Page) => page.getByRole('button', { name: 'Use my location' });
const stop = (page: Page) => page.getByRole('button', { name: 'Stop sharing location' });
const panel = (page: Page) => page.getByRole('region', { name: 'Your location' });
const status = (page: Page) => panel(page).getByRole('status');
const counts = (page: Page) => page.evaluate(() => ({
  watches: window.locationMock.watches, queries: window.locationMock.queries, clears: window.locationMock.clears,
}));
async function hospitals(page: Page) {
  await page.goto('/#/hospitals');
  await expect(page.getByRole('heading', { name: 'Hospitals', exact: true })).toBeVisible();
}

test('LOC01 browser requires explicit consent and keeps directory unavailable', async ({ page }) => {
  await mockLocation(page);
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Hospitals', exact: true })).toBeVisible();
  expect(await counts(page)).toEqual({ watches: 0, queries: 0, clears: [] });
  await page.getByRole('link', { name: 'Hospitals', exact: true }).click();
  await expect(page.getByText('Verified hospital listings and nearby search are currently unavailable.')).toBeVisible();
  expect(await counts(page)).toEqual({ watches: 0, queries: 0, clears: [] });
  await start(page).click();
  await expect(status(page)).toContainText('Waiting for browser permission');
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toHaveText('Location obtained on this device.');
  expect(await counts(page)).toEqual({ watches: 1, queries: 1, clears: [] });
});

test('LOC01 browser refreshes stale fixes and clears watch zero on stop', async ({ page }) => {
  await mockLocation(page, { state: 'granted' });
  await hospitals(page);
  await start(page).click();
  await expect(status(page)).toContainText('Looking for your location');
  await page.evaluate(() => window.locationMock.emit(undefined, undefined, -31_000));
  await expect(status(page)).toContainText('fix has expired');
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toContainText('Location obtained');
  await stop(page).click();
  await expect(status(page)).toContainText('sharing stopped');
  await expect(start(page)).toBeFocused();
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toContainText('sharing stopped');
  expect((await counts(page)).clears).toEqual([0]);
});

test('LOC01 browser releases on navigation visibility and pagehide', async ({ page }) => {
  await mockLocation(page);
  await hospitals(page);
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.getByRole('link', { name: 'Back to home' }).click();
  await expect.poll(async () => (await counts(page)).clears).toEqual([0]);
  await page.evaluate(() => { window.locationMock.emit(); window.location.hash = '/hospitals'; });
  await expect(start(page)).toBeVisible();
  await expect(status(page)).toBeEmpty();
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(status(page)).toContainText('sharing stopped');
  expect(await counts(page)).toMatchObject({ watches: 2, clears: [0, 1] });
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await expect(status(page)).toContainText('sharing stopped');
  expect(await counts(page)).toMatchObject({ watches: 3, clears: [0, 1, 2] });
});

test('LOC01 browser handles denied and revoked permission without prompting again', async ({ page }) => {
  await mockLocation(page, { state: 'denied' });
  await hospitals(page);
  await start(page).click();
  await expect(status(page)).toContainText('permission is blocked');
  expect((await counts(page)).watches).toBe(0);
  await page.evaluate(() => window.locationMock.permission('granted'));
  expect((await counts(page)).watches).toBe(0);
  await page.getByRole('button', { name: 'Retry location' }).click();
  await expect(stop(page)).toBeVisible();
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toContainText('Location obtained');
  await page.evaluate(() => window.locationMock.permission('prompt'));
  await expect(status(page)).toContainText('permission changed');
  expect(await counts(page)).toMatchObject({ watches: 1, clears: [0] });
  await page.evaluate(() => window.locationMock.permission('granted'));
  expect((await counts(page)).watches).toBe(1);
});

for (const kind of ['unsupported', 'insecure'] as const) {
  test(`LOC01 browser handles ${kind} location capability`, async ({ page }) => {
    await mockLocation(page, { [kind]: true });
    await hospitals(page);
    await start(page).click();
    await expect(status(page)).toContainText(kind === 'unsupported' ? 'unavailable in this browser' : 'requires a secure connection');
    await expect(start(page)).toBeDisabled();
    expect(await counts(page)).toEqual({ watches: 0, queries: 0, clears: [] });
  });
}

test('LOC01 browser reports timeout and unavailable and accepts a later fix', async ({ page }) => {
  await mockLocation(page);
  await hospitals(page);
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.evaluate(() => window.locationMock.fail(3));
  await expect(status(page)).toContainText('timed out');
  await page.evaluate(() => window.locationMock.fail(2));
  await expect(status(page)).toContainText('currently unavailable');
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toContainText('Location obtained');
  expect((await counts(page)).watches).toBe(1);
});

test('LOC01 browser keeps offline emergency independent of location', async ({ page, context }) => {
  await mockLocation(page, { state: 'denied' });
  await hospitals(page);
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise<void>(resolve =>
      navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true }));
  });
  await context.setOffline(true);
  await page.reload();
  await expect(start(page)).toBeVisible();
  expect(await counts(page)).toEqual({ watches: 0, queries: 0, clears: [] });
  await start(page).click();
  await expect(status(page)).toContainText('permission is blocked');
  await page.getByRole('button', { name: 'Emergency help' }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('You’re offline.');
  await expect(dialog.getByRole('link', { name: 'CALL 1122 NOW' }).first()).toHaveAttribute('href', 'tel:1122');
  expect(await counts(page)).toEqual({ watches: 0, queries: 1, clears: [] });
});

test('LOC01 browser keeps coordinates out of requests storage URLs and reloads', async ({ page, context }) => {
  await mockLocation(page);
  const requests: string[] = [];
  const logs: string[] = [];
  context.on('request', request => requests.push(request.url() + (request.postData() ?? '')));
  page.on('console', message => logs.push(message.text()));
  await hospitals(page);
  const storage = () => page.evaluate(async () => ({
    local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage),
    indexed: (await indexedDB.databases()).map(store => store.name),
    cookies: document.cookie, cache: await Promise.all((await caches.keys()).map(async name => {
      const cache = await caches.open(name);
      const entries = await Promise.all((await cache.keys()).map(async entry => {
        const response = await cache.match(entry);
        const digest = await crypto.subtle.digest('SHA-256', (await response?.arrayBuffer()) ?? new ArrayBuffer(0));
        return [entry.url, Array.from(new Uint8Array(digest))];
      }));
      return [name, entries];
    })),
  }));
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await expect(page.locator('.connection-status')).not.toHaveAttribute('data-connection', 'checking');
  const prior = await storage();
  const requestCount = requests.length;
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.evaluate(() => window.locationMock.emit());
  await expect(status(page)).toContainText('Location obtained');
  expect(await storage()).toEqual(prior);
  expect(requests).toHaveLength(requestCount);
  await expect(page.locator('main')).not.toContainText('12.3456789');
  await expect(page).toHaveURL(/\/#\/hospitals$/);
  await page.reload();
  await expect(status(page)).toBeEmpty();
  expect(await counts(page)).toEqual({ watches: 0, queries: 0, clears: [] });
  expect(await storage()).toEqual(prior);
  expect([...requests, ...logs].join(' ')).not.toMatch(/12\.3456789|23\.4567891/);
  expect(requests.every(url => url.startsWith('http://127.0.0.1:'))).toBe(true);
});

test('LOC01 browser provides keyboard live status and accessible mobile layout', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await mockLocation(page);
  await hospitals(page);
  await start(page).focus();
  await page.keyboard.press('Enter');
  await expect(status(page)).toHaveAttribute('aria-atomic', 'true');
  await expect(stop(page)).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(stop(page)).toBeFocused();
  expect((await stop(page).boundingBox())?.height).toBeGreaterThanOrEqual(48);
  await page.keyboard.press('Space');
  await expect(status(page)).toContainText('sharing stopped');
  await expect(start(page)).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  const results = await new AxeBuilder({ page }).include('.location-panel')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations).toEqual([]);
});

test('LOC01 browser emergency help stops an active watch and keeps call available', async ({ page }) => {
  await mockLocation(page);
  await hospitals(page);
  await start(page).click();
  await expect(stop(page)).toBeVisible();
  await page.getByRole('button', { name: 'Emergency help' }).first().click();
  await expect(page.getByRole('dialog').getByRole('link', { name: 'CALL 1122 NOW' }).first()).toBeFocused();
  await expect.poll(async () => (await counts(page)).clears).toEqual([0]);
  expect((await counts(page)).watches).toBe(1);
});
