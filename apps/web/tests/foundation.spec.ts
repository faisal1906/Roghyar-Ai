import { expect, test } from '@playwright/test';

test('shell reaches the real API and still renders offline', async ({ context, page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your health, understood.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
  await expect(page.getByRole('status')).toHaveText('Service connected');
  await expect(page.getByText('Voice and text assistance is currently unavailable.')).toBeVisible();

  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Your health, understood.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
  await expect(page.getByRole('status')).toContainText('You’re offline.');
  await page.getByRole('link', { name: 'Hospitals', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Hospitals', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Hospitals', exact: true })).toBeVisible();
  await expect(page.getByText('Guidance is not a diagnosis.')).toBeVisible();
  await context.setOffline(false);
  await expect(page.getByRole('status')).toHaveText('Service connected');
});
