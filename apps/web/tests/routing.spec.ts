import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('LOC06 browser keeps directions unavailable and emergency accessible', async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 760 });
  const requests: string[] = [];
  context.on('request', request => { requests.push(request.url()); });
  await page.goto('/#/hospitals');
  await expect(page.getByText('Directions and travel-time estimates are unavailable.')).toBeVisible();
  const main = page.getByRole('main');
  await expect(main.getByRole('link', { name: /directions|maps/i })).toHaveCount(0);
  await expect(main.getByRole('button', { name: /directions|maps/i })).toHaveCount(0);
  expect((await new AxeBuilder({ page }).include('.unavailable-view').withTags(['wcag2a', 'wcag2aa']).analyze()).violations).toEqual([]);
  const emergency = page.getByRole('button', { name: 'Emergency help' }).first();
  await emergency.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog').getByRole('link', { name: 'CALL 1122 NOW' }).first()).toHaveAttribute('href', 'tel:1122');
  expect(requests.every(url => url.startsWith('http://127.0.0.1:'))).toBe(true);
});

test('LOC06 browser keeps offline emergency independent of routing', async ({ page, context }) => {
  await page.goto('/#/hospitals');
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise<void>(resolve =>
      navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true }));
  });
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByText('Directions and travel-time estimates are unavailable.')).toBeVisible();
  await page.getByRole('button', { name: 'Emergency help' }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('You’re offline.');
  await expect(dialog.getByRole('link', { name: 'CALL 1122 NOW' }).first()).toHaveAttribute('href', 'tel:1122');
});
