import { expect, test, devices } from '@playwright/test';

test('desktop navigation, quick actions and trust information', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const navigation = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(navigation.getByRole('link', { name: 'Home', exact: true })).toHaveAttribute('aria-current', 'page');
  const routes = ['Assess', 'Guidance', 'Hospitals', 'Doctors', 'My Appointments'];
  for (const title of routes) {
    const link = navigation.getByRole('link', { name: title, exact: true });
    await link.click();
    await expect(page.getByRole('heading', { level: 1, name: title })).toBeFocused();
    await expect(link).toHaveAttribute('aria-current', 'page');
    await expect(page.getByText(/currently unavailable\./)).toBeVisible();
    await expect(page.getByText('Guidance is not a diagnosis.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
  }
  await page.goBack();
  await expect(page.getByRole('heading', { level: 1, name: 'Doctors' })).toBeVisible();
  await page.getByRole('link', { name: 'Back to home' }).click();
  const quick = page.getByRole('region', { name: 'Quick actions' });
  await expect(quick.getByRole('link')).toHaveCount(4);
  for (const [label, title] of [
    ['Check symptoms', 'Assess'], ['Open AI Agent', 'AI Agent'],
    ['Find hospitals', 'Hospitals'], ['Find doctors', 'Doctors'],
  ]) {
    const card = quick.getByRole('link', { name: new RegExp(label) });
    if (title !== 'AI Agent') await expect(card).toContainText('Unavailable');
    await card.click();
    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
    await page.getByRole('link', { name: 'Back to home' }).click();
  }
  await expect(page.getByRole('heading', { name: 'When to call for emergency help' })).toBeVisible();
  await page.goto('/#/unknown');
  await expect(page.getByRole('heading', { level: 1, name: 'Page unavailable' })).toBeVisible();
});

test.describe('small Android viewport', () => {
  const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices['Galaxy S5'];
  test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });

  test('keyboard menu, touch targets and reduced motion', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Your health, understood.' })).toBeVisible();
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toHaveCSS('outline-width', '3px');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();

    const menu = page.getByRole('button', { name: 'Open menu' });
    const navigation = page.getByRole('navigation', { name: 'Main navigation' });
    const emergency = page.getByRole('link', { name: 'Call Rescue 1122' });
    await expect(navigation).toBeHidden();
    for (const element of [menu, emergency]) {
      const box = await element.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(48);
      expect(box?.height).toBeGreaterThanOrEqual(48);
    }
    await menu.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Close menu' })).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Tab');
    await expect(navigation.getByRole('link', { name: 'Home', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(menu).toBeFocused();
    await page.keyboard.press('Space');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { level: 1, name: 'Assess' })).toBeFocused();
    await expect(navigation).toBeHidden();
    await page.getByRole('link', { name: 'Back to home' }).click();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    await expect(page.getByRole('link', { name: /Check symptoms/ })).toHaveCSS('transition-duration', '0s');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    await page.getByRole('heading', { name: 'When to call for emergency help' }).scrollIntoViewIfNeeded();
    await expect(emergency).toBeInViewport();
  });
});

test('tablet layout fits the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Quick actions' }).getByRole('link')).toHaveCount(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(768);
});

test('API downtime is distinct from going offline', async ({ page }) => {
  const connection = page.getByRole('contentinfo').getByRole('status');
  await page.route('**/api/v1/health/live', async (route) => {
    await expect(connection).toHaveText('Checking service connection…');
    await route.fulfill({ status: 503, body: '{}' });
  });
  await page.goto('/');
  await expect(connection).toHaveText('Service connection unavailable');
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
});
