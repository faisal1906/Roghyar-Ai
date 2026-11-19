import { expect, test, type Page } from '@playwright/test';

const marker = 'SYNTHETIC—DEVELOPMENT ONLY';
const symptoms = (page: Page) => page.getByRole('textbox', { name: /Symptom description/ });
const noPhotoControls = (page: Page) => page.locator(
  'input[type="file"], input[capture], video, main canvas, main img[src^="blob:"], main img[src^="data:"]',
);

test('floating bilingual shortcut opens the editable draft with no automatic voice or camera', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.addInitScript(() => {
    const calls: string[] = [];
    Object.defineProperty(window, 'phaseOneDeviceCalls', { value: calls });
    Object.defineProperty(navigator, 'geolocation', { value: {
      getCurrentPosition: () => calls.push('position'), watchPosition: () => calls.push('watch'),
    } });
    Object.defineProperty(navigator, 'mediaDevices', { value: {
      getUserMedia: () => calls.push('media'), getDisplayMedia: () => calls.push('display'),
    } });
    class SpeechProbe { processLocally = false; start() { calls.push('recognition'); } }
    Object.defineProperty(window, 'SpeechRecognition', { value: SpeechProbe });
  });
  await page.goto('/#/assess');
  const shortcut = page.getByRole('link', { name: /Speak to AI Agent.*AI Agent se Pochy/ });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(shortcut).toBeInViewport();
  await expect(shortcut.locator('..')).toHaveCSS('position', 'sticky');
  await page.setViewportSize({ width: 320, height: 640 });
  await expect(shortcut.locator('..')).toHaveCSS('position', 'static');
  await expect(noPhotoControls(page)).toHaveCount(0);
  await symptoms(page).fill(marker);
  await shortcut.scrollIntoViewIfNeeded();
  await expect(shortcut).toBeInViewport();
  expect((await shortcut.boundingBox())?.height).toBeGreaterThanOrEqual(48);
  await shortcut.focus();
  await expect(shortcut).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'AI Agent', level: 1 })).toBeFocused();
  await expect(symptoms(page)).toHaveValue(marker);
  await expect(noPhotoControls(page)).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, 'phaseOneDeviceCalls'))).toEqual([]);
});

test('an assessment review stays text-only and private through the offline emergency flow', async ({ page, context }) => {
  const traffic: { url: string; method: string; data: string | null }[] = [];
  const messages: string[] = [];
  page.on('request', request => traffic.push({ url: request.url(), method: request.method(), data: request.postData() }));
  page.on('console', message => messages.push(message.text()));
  await page.goto('/#/assess');
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
  await expect(noPhotoControls(page)).toHaveCount(0);
  await symptoms(page).fill(marker);
  await page.getByRole('radio', { name: 'Moderate', exact: true }).check();
  await page.getByRole('radio', { name: 'Custom days' }).check();
  const days = page.getByRole('spinbutton', { name: /Number of days/ });
  await days.fill('0');
  await page.getByRole('spinbutton', { name: /Age in years/ }).fill('35');
  const review = page.getByRole('button', { name: 'Review assessment' });
  await review.click();
  await expect(days).toBeFocused();
  await days.fill('15');
  await review.click();
  await expect(page.getByText('15 days (360 hours)')).toBeVisible();
  await expect(noPhotoControls(page)).toHaveCount(0);

  const gesture = page.getByRole('banner').getByRole('button', { name: 'Emergency help' });
  await context.setOffline(true);
  await gesture.click();
  const dialog = page.getByRole('dialog', { name: /Possible emergency/ });
  await expect(dialog).toContainText('You’re offline.');
  const calls = dialog.getByRole('link', { name: 'CALL 1122 NOW' });
  await expect(calls).toHaveCount(2);
  await expect(calls.first()).toBeFocused();
  for (const call of await calls.all()) await expect(call).toHaveAttribute('href', 'tel:1122');
  await dialog.getByRole('button', { name: 'Acknowledge and close' }).click();
  await expect(gesture).toBeFocused();
  await expect(page.getByText(marker, { exact: true })).toBeVisible();
  await page.reload();
  await expect(symptoms(page)).toHaveValue('');
  await expect(noPhotoControls(page)).toHaveCount(0);
  expect(traffic.every(request => ['GET', 'HEAD'].includes(request.method) && request.data === null)).toBe(true);
  expect(traffic.every(request => !decodeURIComponent(request.url).includes(marker))).toBe(true);
  expect(messages.some(message => message.includes(marker))).toBe(false);
});
