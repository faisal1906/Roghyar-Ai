import { expect, test } from '@playwright/test';

test('shell reaches the real API and still renders offline', async ({ context, page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'ROGHYAR AI' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
  await expect(page.getByRole('status')).toHaveText('API reachable');
  await expect(page.getByText('Health guidance and appointment booking are currently unavailable.')).toBeVisible();

  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'ROGHYAR AI' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('API unavailable');
});
