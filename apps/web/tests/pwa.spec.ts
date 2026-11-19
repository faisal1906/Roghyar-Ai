import { createServer, type Server } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const dist = path.resolve(fileURLToPath(new URL('../dist/', import.meta.url)));
const contentTypes: Record<string, string> = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.webmanifest': 'application/manifest+json',
};
const synthetic = 'SYNTHETIC—DEVELOPMENT ONLY';

async function fixture(version: 'original' | 'update' | 'broken' = 'original') {
  let workerVersion = version;
  const server: Server = createServer(async (request, response) => {
    const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
    if (pathname.startsWith('/api/v1/')) {
      response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'private, no-store' });
      response.end(JSON.stringify(pathname.endsWith('/live') ? { status: 'ok' } : { private: synthetic }));
      return;
    }
    const file = path.resolve(dist, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(dist + path.sep)) {
      response.writeHead(404).end();
      return;
    }
    try {
      const body = await readFile(file);
      response.writeHead(200, {
        'Content-Type': contentTypes[path.extname(file)] ?? 'application/octet-stream',
        'Cache-Control': 'no-store',
      });
      response.write(body);
      if (pathname === '/sw.js' && workerVersion !== 'original') {
        response.write('\n// Browser update fixture: ' + workerVersion);
        if (workerVersion === 'broken') {
          response.write("\nself.addEventListener('install', event => event.waitUntil(Promise.reject(new Error('fixture install failure'))));");
        }
      }
      response.end();
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Fixture server did not bind');
  return {
    origin: 'http://127.0.0.1:' + address.port,
    version(next: typeof version) { workerVersion = next; },
    close() { server.closeAllConnections(); return new Promise<void>(resolve => server.close(() => resolve())); },
  };
}

async function controlled(page: Page) {
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
}

const updateStatus = (page: Page) => page.locator('.pwa-update').getByRole('status');
const draft = (page: Page) => page.getByRole('textbox', { name: /Symptom description/ });

test('production manifest and worker meet Chromium installability checks', async ({ page, context }) => {
  await page.goto('/');
  await controlled(page);
  const manifest = await page.evaluate(async () => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="manifest"]');
    if (!link) throw new Error('Manifest link missing');
    return (await fetch(link.href)).json();
  });
  expect(manifest).toMatchObject({
    name: 'ROGHYAR AI', short_name: 'ROGHYAR AI', id: '/', scope: '/', start_url: '/',
    display: 'standalone', theme_color: '#16463d', background_color: '#f5f8f7',
  });
  for (const size of [192, 512]) {
    const icon = manifest.icons.find(({ sizes }: { sizes: string }) => sizes === size + 'x' + size);
    expect(icon?.type).toBe('image/png');
    const actual = await page.evaluate(async (source: string) => {
      const response = await fetch(source);
      const bitmap = await createImageBitmap(await response.blob());
      return [bitmap.width, bitmap.height];
    }, icon.src);
    expect(actual).toEqual([size, size]);
  }
  const session = await context.newCDPSession(page);
  expect((await session.send('Page.getInstallabilityErrors')).installabilityErrors).toEqual([]);
});

test('updates preserve drafts in both active tabs until explicit reload', async ({ page, context }) => {
  const server = await fixture();
  try {
    await page.goto(server.origin + '/#/assess');
    await controlled(page);
    const other = await context.newPage();
    await other.goto(server.origin + '/#/ai-agent');
    await controlled(other);
    await draft(page).fill(synthetic + ' assessment');
    await draft(other).fill(synthetic + ' voice');

    server.version('update');
    await page.evaluate(async () => { await (await navigator.serviceWorker.ready).update(); });
    await expect(updateStatus(page)).toContainText('update is available');
    await expect(draft(page)).toHaveValue(synthetic + ' assessment');
    await page.getByRole('button', { name: 'Prepare update' }).click();
    await expect(updateStatus(page)).toContainText('update is ready');
    await expect(updateStatus(other)).toContainText('update is ready');
    await expect(draft(other)).toHaveValue(synthetic + ' voice');

    await page.getByRole('button', { name: 'Reload app' }).click();
    await expect(page.getByRole('button', { name: 'Keep editing' })).toBeFocused();
    await page.getByRole('button', { name: 'Keep editing' }).click();
    await expect(page.getByRole('button', { name: 'Reload app' })).toBeFocused();
    await expect(draft(page)).toHaveValue(synthetic + ' assessment');

    await page.getByRole('button', { name: 'Reload app' }).click();
    await page.getByRole('button', { name: 'Reload and clear draft' }).click();
    await expect(draft(page)).toHaveValue('');
    await expect(draft(other)).toHaveValue(synthetic + ' voice');
    await other.close();
  } finally { await server.close(); }
});

test('a failed worker update leaves the current offline shell and draft usable', async ({ page, context }) => {
  const server = await fixture();
  try {
    await page.goto(server.origin + '/#/assess');
    await controlled(page);
    await draft(page).fill(synthetic);
    server.version('broken');
    await page.evaluate(async () => { await (await navigator.serviceWorker.ready).update(); });
    await expect(updateStatus(page)).toContainText('could not finish');
    await expect(draft(page)).toHaveValue(synthetic);
    await context.setOffline(true);
    await page.reload();
    await expect(draft(page)).toHaveValue('');
    const help = page.getByRole('banner').getByRole('button', { name: 'Emergency help' });
    await help.click();
    await expect(page.getByRole('dialog').getByRole('link', { name: 'CALL 1122 NOW' })).toHaveCount(2);
  } finally { await server.close(); }
});

test('a failed initial worker installation can be retried without reloading', async ({ page }) => {
  const server = await fixture('broken');
  try {
    await page.goto(server.origin + '/#/assess');
    await expect(updateStatus(page)).toContainText('could not finish');
    await draft(page).fill(synthetic);
    server.version('original');
    await page.getByRole('button', { name: 'Retry offline setup' }).click();
    await controlled(page);
    await expect(draft(page)).toHaveValue(synthetic);
  } finally { await server.close(); }
});

test('offline cache is public-only and routes request no camera, microphone or location', async ({ page, context }) => {
  const server = await fixture();
  try {
    await page.addInitScript(() => {
      const attempts: string[] = [];
      Object.defineProperty(window, 'deviceRequests', { value: attempts });
      Object.defineProperty(navigator, 'geolocation', { value: {
        getCurrentPosition: () => attempts.push('position'), watchPosition: () => attempts.push('watch'),
      } });
      Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: () => attempts.push('media') } });
      class RecognitionProbe { processLocally = false; start() { attempts.push('recognition'); } }
      Object.defineProperty(window, 'SpeechRecognition', { value: RecognitionProbe });
    });
    await page.goto(server.origin + '/#/assess');
    await controlled(page);
    await draft(page).fill(synthetic);
    const audit = await page.evaluate(async () => {
      const privateResponse = await (await fetch('/api/v1/private-sample')).json();
      const requests = (await Promise.all((await caches.keys()).map(async name => (await caches.open(name)).keys()))).flat();
      return {
        privateResponse, cached: requests.map(request => new URL(request.url).pathname),
        local: localStorage.length, session: sessionStorage.length, db: (await indexedDB.databases()).length,
      };
    });
    expect(audit.privateResponse).toEqual({ private: synthetic });
    expect(audit.cached).toContain('/index.html');
    expect(audit.cached.every(name => /^\/(?:assets\/[^/]+\.(?:js|css)|index\.html|manifest\.webmanifest|icon-\d+\.png)$/.test(name))).toBe(true);
    expect([audit.local, audit.session, audit.db]).toEqual([0, 0, 0]);

    await context.setOffline(true);
    for (const route of ['/', '/ai-agent', '/assess', '/guidance', '/hospitals', '/doctors', '/appointments']) {
      await page.goto(server.origin + '/#' + route);
      await page.reload();
      await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.getByRole('contentinfo').getByRole('status')).toContainText('You’re offline.');
      expect(await page.evaluate(() => Reflect.get(window, 'deviceRequests'))).toEqual([]);
    }
    await page.goto(server.origin + '/#/assess');
    await expect(draft(page)).toHaveValue('');
    expect(await page.evaluate(async () => {
      try { await fetch('/api/v1/private-sample'); return 'served'; }
      catch { return 'unavailable'; }
    })).toBe('unavailable');
    await expect(page.goto(server.origin + '/api/v1/private-sample')).rejects.toThrow();
  } finally { await server.close(); }
});
