import { expect, test, type Page } from '@playwright/test';

const help = (page: Page) => page.getByRole('banner').getByRole('button', { name: 'Emergency help' });
const alert = (page: Page) => page.getByRole('dialog', { name: 'Possible emergency — act now' });
const acknowledge = (page: Page) => alert(page).getByRole('button', { name: 'Acknowledge and close' });
const calls = (page: Page) => alert(page).getByRole('link', { name: 'CALL 1122 NOW', exact: true });

test('opens in under one second, contains focus and restores it only on acknowledgement', async ({ page }) => {
  await page.goto('/');
  await expect(help(page)).toBeVisible();
  await page.evaluate(() => {
    document.addEventListener('click', () => {
      const start = performance.now();
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const primary = document.querySelector('dialog[open] a[href="tel:1122"]');
        if (primary?.getBoundingClientRect().height) performance.measure('manual-emergency', { start });
      }));
    }, { capture: true, once: true });
  });
  await help(page).click();
  const dialog = alert(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect(dialog).toHaveAccessibleDescription(/call Rescue 1122.*general emergency help/);
  await expect(calls(page)).toHaveCount(2);
  for (const link of await calls(page).all()) await expect(link).toHaveAttribute('href', 'tel:1122');
  await expect(calls(page).first()).toBeFocused();
  const duration = await page.evaluate(async () => {
    for (let frame = 0; frame < 60; frame++) {
      const measurement = performance.getEntriesByName('manual-emergency')[0];
      if (measurement) return measurement.duration;
      await new Promise(requestAnimationFrame);
    }
    throw new Error('Manual emergency paint was not measured');
  });
  expect(duration).toBeLessThan(1000);
  console.log('Manual emergency activation: ' + duration.toFixed(1) + ' ms');

  await page.evaluate(() => document.querySelector('textarea')?.focus());
  await expect(calls(page).first()).toBeFocused();
  await expect(page.locator('html')).toHaveCSS('overflow-y', 'hidden');
  const backgroundScroll = await page.evaluate(() => window.scrollY);
  await page.mouse.move(24, 200);
  await page.mouse.wheel(0, 800);
  await expect.poll(() => dialog.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(backgroundScroll);
  await page.mouse.click(2, 2);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await page.locator('header.site-header button[aria-haspopup="dialog"]').evaluate(
    (element: HTMLButtonElement) => { element.click(); element.click(); },
  );
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await calls(page).first().focus();
  await page.keyboard.press('Shift+Tab');
  await expect(acknowledge(page)).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(calls(page).first()).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(calls(page).last()).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(dialog).toBeHidden();
  await expect(help(page)).toBeFocused();
  await expect(page.locator('html')).not.toHaveCSS('overflow-y', 'hidden');
});

test('notice, assessment and remaining routes expose manual help', async ({ page }) => {
  await page.goto('/');
  const notice = page.getByRole('complementary', { name: 'When to call for emergency help' })
    .getByRole('button', { name: 'Emergency help' });
  await notice.click();
  await expect(alert(page)).toBeVisible();
  await acknowledge(page).click();
  await expect(notice).toBeFocused();

  await page.goto('/#/assess');
  const description = page.getByRole('textbox', { name: /Symptom description/ });
  await description.fill('SYNTHETIC—DEVELOPMENT ONLY chest pressure');
  await page.getByRole('radio', { name: 'Severe', exact: true }).check();
  await expect(alert(page)).toBeHidden();
  const assessmentHelp = page.getByRole('heading', { name: 'Symptom assessment' }).locator('..')
    .getByRole('button', { name: 'Emergency help' });
  await assessmentHelp.click();
  await expect(alert(page)).toBeVisible();
  await acknowledge(page).click();
  await expect(description).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY chest pressure');

  for (const route of ['guidance', 'hospitals', 'doctors', 'appointments', 'ai-agent']) {
    await page.goto('/#/' + route);
    await help(page).click();
    await expect(alert(page)).toBeVisible();
    await acknowledge(page).click();
  }
});

test('cached offline reload retains emergency help and both telephone links', async ({ page, context }) => {
  await page.goto('/#/assess');
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await help(page).click();
  await expect(alert(page)).toContainText('You’re offline. The app cannot determine your condition or find the nearest emergency hospital.');
  for (const link of await calls(page).all()) await expect(link).toHaveAttribute('href', 'tel:1122');
  await expect(calls(page)).toHaveCount(2);
});

test('works while FastAPI is unavailable with no lookup or consentless data sharing', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: string[] = [];
    Object.defineProperty(window, 'emergencyDeviceCalls', { value: calls });
    Object.defineProperty(navigator, 'geolocation', { value: {
      getCurrentPosition: () => calls.push('position'), watchPosition: () => calls.push('watch'),
    } });
    Object.defineProperty(navigator, 'share', { value: () => calls.push('share') });
  });
  await page.route('**/api/v1/**', route => route.fulfill({ status: 503, body: '{}' }));
  await page.goto('/#/hospitals');
  await expect(page.getByRole('contentinfo').getByRole('status')).toHaveText('Service connection unavailable');
  const requests: string[] = [];
  page.on('request', request => requests.push(request.url()));
  await help(page).click();
  await expect(alert(page)).toContainText('Verified nearby emergency hospital information is unavailable.');
  await expect(alert(page).getByRole('link')).toHaveCount(2);
  await expect(alert(page)).not.toContainText(/\d+\s*(km|minutes)\b|ambulance.*(called|sent)/i);
  expect(await page.evaluate(() => Reflect.get(window, 'emergencyDeviceCalls'))).toEqual([]);
  expect(requests).toEqual([]);
});

for (const width of [320, 360]) {
  test(`fits ${width}px with reduced motion, 48px targets and 7:1 contrast`, async ({ page }) => {
    await page.setViewportSize({ width, height: 640 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/#/assess');
    await help(page).click();
    const dialog = alert(page);
    await expect(calls(page).first()).toBeInViewport();
    await expect(dialog.locator('.emergency-alert-icon')).toHaveCSS('animation-name', 'none');
    const geometry = await dialog.evaluate(element => ({
      scroll: element.scrollWidth,
      viewport: document.documentElement.clientWidth,
    }));
    expect(geometry.scroll).toBeLessThanOrEqual(geometry.viewport);
    for (const control of [...await calls(page).all(), acknowledge(page)]) {
      const box = await control.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(48);
      expect(box?.height).toBeGreaterThanOrEqual(48);
    }

    const ratios = await dialog.evaluate(element => {
      function luminance(color: string) {
        const channels = color.match(/\d+/g)!.slice(0, 3).map(value => Number(value) / 255)
          .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
      }
      return Array.from(element.querySelectorAll('h1, h2, p, li, a, button'), node => {
        const style = getComputedStyle(node);
        let background = style.backgroundColor;
        let ancestor = node.parentElement;
        while (background === 'rgba(0, 0, 0, 0)' && ancestor) {
          background = getComputedStyle(ancestor).backgroundColor;
          ancestor = ancestor.parentElement;
        }
        const pair = [luminance(style.color), luminance(background)].sort((a, b) => b - a);
        return (pair[0] + .05) / (pair[1] + .05);
      });
    });
    expect(Math.min(...ratios)).toBeGreaterThanOrEqual(7);
  });
}
