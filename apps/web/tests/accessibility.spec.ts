import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const routes = ['/', '/ai-agent', '/assess', '/guidance', '/hospitals', '/doctors', '/appointments', '/unknown'];
const emergency = (page: Page) => page.getByRole('banner').getByRole('button', { name: 'Emergency help' });
const alert = (page: Page) => page.getByRole('dialog', { name: 'Possible emergency — act now' });
const description = (page: Page) => page.getByRole('textbox', { name: /Symptom description/ });

async function openRoute(page: Page, route: string) {
  await page.goto('/#' + route);
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible();
}

async function checkReflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(await page.evaluate(() => document.documentElement.clientWidth));
}

async function review(page: Page, text: string) {
  await description(page).fill(text);
  await page.getByRole('radio', { name: 'Mild', exact: true }).check();
  await page.getByRole('radio', { name: '1 hour', exact: true }).check();
  await page.getByRole('spinbutton', { name: /Age in years/ }).fill('35');
  await page.getByRole('button', { name: 'Review assessment' }).click();
  await expect(page.getByRole('heading', { name: 'Review your assessment' })).toBeFocused();
}

async function scan(page: Page, label: string) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) })), label).toEqual([]);
}

for (const width of [320, 360, 375, 390, 768, 1024, 1440]) {
  test(`all routes, review and emergency help reflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 });
    for (const route of routes) {
      await openRoute(page, route);
      await checkReflow(page);
      await expect(page.getByRole('main')).toHaveCount(1);
      await expect(page.getByRole('contentinfo')).toHaveCount(1);
      const call = page.getByRole('link', { name: 'Call Rescue 1122' });
      await expect(call).toBeInViewport();
      expect((await call.boundingBox())?.height).toBeGreaterThanOrEqual(48);
    }
    await openRoute(page, '/assess');
    await review(page, 'SYNTHETIC—DEVELOPMENT ONLY');
    await checkReflow(page);
    await emergency(page).click();
    await expect(alert(page).getByRole('link', { name: 'CALL 1122 NOW' }).first()).toBeFocused();
    expect(await alert(page).evaluate(node => node.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test('automated WCAG scan covers routes, validation, review and the modal', async ({ page }) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of routes) {
    await openRoute(page, route);
    await scan(page, route);
  }
  await openRoute(page, '/assess');
  await page.getByRole('button', { name: 'Review assessment' }).click();
  await expect(description(page)).toBeFocused();
  await expect(page.locator('form > [role="status"]')).toHaveText('Check 4 fields before reviewing your assessment.');
  await expect(description(page)).toHaveAccessibleDescription('Describe your symptoms using words.');
  await scan(page, 'invalid assessment');
  await review(page, 'SYNTHETIC—DEVELOPMENT ONLY');
  await scan(page, 'review');
  await emergency(page).click();
  await scan(page, 'emergency modal');
});

for (const zoom of [200, 400]) {
  test(`${zoom}% zoom-equivalent reflow keeps keyboard and emergency controls usable`, async ({ page }) => {
    // CSS viewport equivalent to reflow on a 1280px desktop.
    await page.setViewportSize({ width: 1280 * 100 / zoom, height: 900 * 100 / zoom });
    await openRoute(page, '/assess');
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await expect(skip).toHaveCSS('outline-style', 'solid');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();
    await page.getByRole('button', { name: 'Review assessment' }).click();
    await expect(description(page)).toBeFocused();
    const top = await description(page).evaluate(element => element.getBoundingClientRect().top);
    const headerBottom = await page.getByRole('banner').evaluate(element => element.getBoundingClientRect().bottom);
    expect(top).toBeGreaterThanOrEqual(headerBottom - 1);
    await checkReflow(page);
    await emergency(page).click();
    const primary = alert(page).getByRole('link', { name: 'CALL 1122 NOW' }).first();
    await expect(primary).toBeInViewport();
    await page.keyboard.press('Shift+Tab');
    await expect(alert(page).getByRole('button', { name: 'Acknowledge and close' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(primary).toBeFocused();
  });
}

test('larger browser font settings and Arabic-script draft text preserve reflow', async ({ page, context }) => {
  await page.setViewportSize({ width: 360, height: 760 });
  const session = await context.newCDPSession(page);
  await session.send('Page.setFontSizes', { fontSizes: { standard: 32, fixed: 26 } });
  await openRoute(page, '/assess');
  await expect(page.locator('html')).toHaveCSS('font-size', '32px');
  await page.getByRole('radio', { name: 'Custom days' }).check();
  await checkReflow(page);
  for (const text of ['سر میں درد ہے', 'سر مې درد کوي'.repeat(24)]) {
    await description(page).fill(text);
    await expect(description(page)).toHaveCSS('direction', 'rtl');
    await review(page, text);
    await expect(page.locator('.summary-symptoms dd')).toHaveCSS('direction', 'rtl');
    await checkReflow(page);
    await page.getByRole('button', { name: 'Edit assessment' }).click();
  }
  await emergency(page).click();
  await expect(alert(page).getByRole('link', { name: 'CALL 1122 NOW' }).first()).toBeInViewport();
  expect(await alert(page).evaluate(node => node.scrollWidth)).toBeLessThanOrEqual(360);
});
