import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const harness = 'http://127.0.0.1:4174/private-admin-dev.html';
const staffToken = 'staff1.' + 'a'.repeat(43);

test('BK06 private dashboard presents scoped records and clears the in-memory session', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.route('**/admin/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/admin/session' && request.method() === 'POST') {
      expect(request.postDataJSON()).toEqual({ proof: 'SYNTHETIC STAFF PROOF' });
      return route.fulfill({ json: { token: staffToken, token_type: 'Bearer' } });
    }
    expect(request.headers().authorization).toBe('Bearer ' + staffToken);
    if (request.method() === 'DELETE') return route.fulfill({ json: { status: 'revoked' } });
    if (path === '/admin/audit') return route.fulfill({ status: 403, json: {} });
    return route.fulfill({ json: { facility: 'synthetic-facility', role: 'appointment_manager',
      records: path === '/admin/appointments' ? [{ id: 'synthetic-appointment', status: 'pending' }] : [] } });
  });
  await page.goto(harness);
  await page.getByLabel('Staff identity proof').fill('SYNTHETIC STAFF PROOF');
  await page.getByRole('button', { name: 'Sign in' }).press('Enter');
  await expect(page.getByText('synthetic-appointment')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Hospital administration' })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.getByRole('button', { name: 'Audit' }).click();
  await expect(page.getByRole('status')).toHaveText('Your staff role cannot open this section.');
  await page.getByRole('button', { name: 'Doctors' }).click();
  await expect(page.getByText('No records to show.')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByLabel('Staff identity proof')).toBeVisible();
});

test('BK06 public build does not mount the administration dashboard', async ({ page }) => {
  await page.goto('/private-admin-dev.html');
  await expect(page.getByRole('heading', { name: 'Hospital administration' })).toHaveCount(0);
  expect((await page.request.get('http://127.0.0.1:8000/admin/doctors')).status()).toBe(404);
});
