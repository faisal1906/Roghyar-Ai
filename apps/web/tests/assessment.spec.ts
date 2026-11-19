import { expect, test, type Page } from '@playwright/test';

const symptoms = (page: Page) => page.getByRole('textbox', { name: /Symptom description/ });
const age = (page: Page) => page.getByRole('spinbutton', { name: /Age in years/ });
const days = (page: Page) => page.getByRole('spinbutton', { name: /Number of days/ });
const review = (page: Page) => page.getByRole('button', { name: 'Review assessment' }).click();

async function requiredDetails(page: Page) {
  await symptoms(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await page.getByRole('radio', { name: 'Mild', exact: true }).check();
  await page.getByRole('radio', { name: '1 hour', exact: true }).check();
  await age(page).fill('35');
}

const summaryValue = (page: Page, label: string) => page.locator('dt').filter({ hasText: new RegExp('^' + label + '$') })
  .locator('xpath=following-sibling::dd');

test('requires meaningful words and focuses the first invalid field', async ({ page }) => {
  await page.goto('/#/assess');
  await symptoms(page).fill(' .. ');
  await review(page);
  await expect(symptoms(page)).toBeFocused();
  await expect(symptoms(page)).toHaveAccessibleDescription('Describe your symptoms using words.');
  await symptoms(page).fill('سر میں درد ہے');
  await expect(symptoms(page)).toHaveAttribute('dir', 'auto');
  await review(page);
  const mild = page.getByRole('radio', { name: 'Mild', exact: true });
  await expect(mild).toBeFocused();
  await page.keyboard.press('Space');
  await expect(mild).toBeChecked();
  await review(page);
  await expect(page.getByRole('radio', { name: '1 hour', exact: true })).toBeFocused();
});

test('reviews all preset durations and severity choices without server submission', async ({ page }) => {
  await page.goto('/#/assess');
  await requiredDetails(page);
  const mutations: string[] = [];
  page.on('request', request => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.method());
  });
  for (const [index, label] of ['1 hour', 'About 2 hours', 'Half a day', '1 day', '2 days'].entries()) {
    const severity = ['Mild', 'Moderate', 'Severe'][index % 3];
    await page.getByRole('radio', { name: severity, exact: true }).check();
    await page.getByRole('radio', { name: label, exact: true }).check();
    await review(page);
    await expect(page.getByRole('heading', { name: 'Review your assessment' })).toBeFocused();
    await expect(summaryValue(page, 'Duration')).toHaveText(label);
    await expect(summaryValue(page, 'Severity')).toHaveText(severity);
    await expect(page.getByText('Clinical analysis is currently unavailable. Your assessment has not been sent.')).toBeVisible();
    await expect(page).toHaveURL(/#\/assess$/);
    await page.getByRole('button', { name: 'Edit assessment' }).click();
  }
  expect(mutations).toEqual([]);
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
});

test('rejects invalid custom days and age, then reviews exact and open-ended durations', async ({ page }) => {
  await page.goto('/#/assess');
  await requiredDetails(page);
  await page.getByRole('radio', { name: 'Custom days' }).check();
  for (const invalid of ['0', '-2', '1.5', '366']) {
    await days(page).fill(invalid);
    await review(page);
    await expect(days(page)).toBeFocused();
    await expect(days(page)).toHaveAttribute('aria-invalid', 'true');
  }
  await days(page).fill('4');
  await page.getByRole('button', { name: 'Increase days' }).click();
  await expect(days(page)).toHaveValue('5');
  for (const invalid of ['-1', '121', '1.5']) {
    await age(page).fill(invalid);
    await review(page);
    await expect(age(page)).toBeFocused();
    await expect(age(page)).toHaveAccessibleDescription('Enter a whole number from 0 to 120 years.');
  }
  await age(page).fill('0');
  await review(page);
  await expect(page.getByText('5 days (120 hours)')).toBeVisible();
  await page.getByRole('button', { name: 'Edit assessment' }).click();
  await days(page).fill('16');
  await page.getByRole('button', { name: 'Decrease days' }).click();
  await age(page).fill('120');
  await review(page);
  await expect(page.getByText('15 days (360 hours)')).toBeVisible();
  await page.getByRole('button', { name: 'Edit assessment' }).click();
  await page.getByRole('radio', { name: 'More than a year' }).check();
  await expect(days(page)).toBeHidden();
  await review(page);
  await expect(summaryValue(page, 'Duration')).toHaveText('More than a year');
});

test('shares drafts across views, safely clears and drops everything on reload', async ({ page }) => {
  await page.goto('/');
  await symptoms(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await page.getByRole('link', { name: /Open AI Agent/ }).click();
  await expect(symptoms(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
  await symptoms(page).fill('سر مې درد کوي');
  await page.getByRole('link', { name: 'Assess', exact: true }).click();
  await expect(symptoms(page)).toHaveValue('سر مې درد کوي');
  await requiredDetails(page);
  await page.getByLabel(/Sex/).selectOption('Female');
  const conditions = page.getByRole('textbox', { name: /Existing medical conditions/ });
  await conditions.fill('SYNTHETIC—DEVELOPMENT ONLY A\nSYNTHETIC—DEVELOPMENT ONLY B');
  await page.getByRole('textbox', { name: /Known allergies/ }).fill('SYNTHETIC—DEVELOPMENT ONLY C');
  await page.getByRole('textbox', { name: /Current medicines/ }).fill('SYNTHETIC—DEVELOPMENT ONLY D');
  await page.getByRole('link', { name: 'Home', exact: true }).click();
  await page.getByRole('link', { name: 'Assess', exact: true }).click();
  await expect(conditions).toHaveValue(/ONLY B/);
  await conditions.fill('SYNTHETIC—DEVELOPMENT ONLY A');
  await review(page);
  await expect(page.getByText('Female', { exact: true })).toBeVisible();
  for (const suffix of ['A', 'C', 'D']) await expect(page.getByText('SYNTHETIC—DEVELOPMENT ONLY ' + suffix, { exact: true })).toBeVisible();
  await expect(page.getByText('SYNTHETIC—DEVELOPMENT ONLY B')).toHaveCount(0);
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Clear assessment' }).click();
  await expect(page.getByRole('heading', { name: 'Review your assessment' })).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Clear assessment' }).click();
  await expect(symptoms(page)).toHaveValue('');
  await expect(age(page)).toHaveValue('');
  await page.getByRole('link', { name: 'Home', exact: true }).click();
  await expect(symptoms(page)).toHaveValue('');
  await page.getByRole('link', { name: 'Assess', exact: true }).click();
  await requiredDetails(page);
  await conditions.fill('SYNTHETIC—DEVELOPMENT ONLY');
  await page.reload();
  await expect(symptoms(page)).toHaveValue('');
  await expect(age(page)).toHaveValue('');
  await expect(conditions).toHaveValue('');
  await expect(page.getByRole('radio', { name: 'Mild', exact: true })).not.toBeChecked();
  await expect(page.locator('input[type=file], [capture]')).toHaveCount(0);
});

test('small viewport fits touch controls and keeps emergency access while scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/assess');
  await page.getByRole('radio', { name: 'Custom days' }).check();
  const increase = page.getByRole('button', { name: 'Increase days' });
  const box = await increase.boundingBox();
  expect(box?.height).toBeGreaterThanOrEqual(48);
  expect(box?.width).toBeGreaterThanOrEqual(48);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await expect(increase).toHaveCSS('transition-duration', '0s');
  await page.getByRole('button', { name: 'Review assessment' }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toBeInViewport();
});
