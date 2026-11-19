import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    speechProbe: {
      starts: Array<{ language: string; local: boolean; continuous: boolean }>;
      stops: number;
      aborts: number;
      installs: Array<{ langs: string[]; processLocally: boolean }>;
      listening(): void;
      finish(): void;
      error(code: string): void;
      result(value: string, final: boolean): void;
    };
  }
}

async function installSpeechProbe(page: Page, supported = true, packs: boolean | 'stalled' = false) {
  await page.addInitScript(({ supported, packs }) => {
    let current: BrowserRecognition | undefined;
    let installed = false;
    const probe: Window['speechProbe'] = {
      starts: [], stops: 0, aborts: 0, installs: [],
      listening: () => current?.onstart?.(),
      finish: () => current?.onend?.(),
      error: (error) => current?.onerror?.({ error }),
      result: (transcript, isFinal) => current?.onresult?.({
        resultIndex: 0, results: [{ isFinal, 0: { transcript } }],
      }),
    };
    class BrowserRecognition {
      lang = '';
      continuous = true;
      processLocally = supported ? false : undefined;
      onstart?: () => void;
      onend?: () => void;
      onerror?: (event: { error: string }) => void;
      onresult?: (event: {
        resultIndex: number;
        results: { isFinal: boolean; 0: { transcript: string } }[];
      }) => void;
      start() {
        current = this;
        probe.starts.push({ language: this.lang, local: this.processLocally === true, continuous: this.continuous });
      }
      stop() { probe.stops++; }
      abort() { probe.aborts++; }
    }
    if (packs) Object.assign(BrowserRecognition, {
      available: async ({ langs }: { langs: string[] }) => {
        if (langs[0] !== 'en') return 'unavailable';
        return installed ? 'available' : 'downloadable';
      },
      install: async (options: { langs: string[]; processLocally: boolean }) => {
        probe.installs.push(options);
        if (packs === 'stalled') return new Promise<boolean>(() => {});
        installed = true;
        return true;
      },
    });
    // Unprefixed recognition is missing, as on browsers exposing only the legacy name.
    Object.defineProperty(window, 'SpeechRecognition', { value: undefined });
    Object.defineProperty(window, 'webkitSpeechRecognition', { value: BrowserRecognition });
    window.speechProbe = probe;
  }, { supported, packs });
}

const status = (page: Page) => page.getByRole('region', { name: 'Speak to AI Agent' }).getByRole('status');
const record = (page: Page) => page.getByRole('button', { name: 'Start recording' });
const transcript = (page: Page) => page.getByRole('textbox', { name: 'Symptom description' });

test('starts only by request and inserts final speech once with editable text', async ({ page }) => {
  await installSpeechProbe(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Speak to AI Agent' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Speak to AI Agent' })
    .getByText('AI Agent se Pochy', { exact: true })).toBeVisible();
  await transcript(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await expect.poll(() => page.evaluate(() => window.speechProbe.starts.length)).toBe(0);
  await page.getByLabel('Recognition language').selectOption({ label: 'Urdu' });
  await record(page).click();
  await expect(status(page)).toContainText('Waiting for microphone permission');
  expect(await page.evaluate(() => window.speechProbe.starts)).toEqual([
    { language: 'ur-PK', local: true, continuous: false },
  ]);

  await page.evaluate(() => window.speechProbe.listening());
  await expect(status(page)).toHaveText('Listening…');
  await page.evaluate(() => window.speechProbe.result('interim test text', false));
  await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
  await page.getByRole('button', { name: 'Stop recording' }).click();
  await expect(status(page)).toHaveText('Processing speech…');
  expect(await page.evaluate(() => window.speechProbe.stops)).toBe(1);
  await page.evaluate(() => {
    window.speechProbe.result('test segment', true);
    window.speechProbe.result('test segment', true);
    window.speechProbe.finish();
  });
  await expect(status(page)).toContainText('Transcript ready');
  await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY test segment');

  await page.getByRole('button', { name: 'Edit text' }).click();
  await expect(transcript(page)).toBeFocused();
  await transcript(page).fill('Corrected test text');
  await page.getByRole('link', { name: /Open AI Agent/ }).click();
  await expect(transcript(page)).toHaveValue('Corrected test text');
  await page.getByRole('button', { name: 'Clear text' }).click();
  await expect(transcript(page)).toHaveValue('');
  await expect(page.getByText('Submitting symptoms for analysis is currently unavailable.')).toBeVisible();
  await transcript(page).fill('Another synthetic draft');
  await page.reload();
  await expect(transcript(page)).toHaveValue('');
});

test('legacy remote-only recognition offers typing without starting', async ({ page }) => {
  await installSpeechProbe(page, false);
  await page.goto('/#/ai-agent');
  await expect(status(page)).toContainText('unavailable in this browser');
  await expect(record(page)).toBeDisabled();
  await transcript(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
  expect(await page.evaluate(() => window.speechProbe.starts)).toEqual([]);
});

test('language setup is opt-in and does not activate the microphone', async ({ page }) => {
  await installSpeechProbe(page, true, true);
  await page.goto('/#/ai-agent');
  const download = page.getByRole('button', { name: 'Download language pack' });
  await expect(download).toBeVisible();
  await expect(record(page)).toBeDisabled();
  expect(await page.evaluate(() => window.speechProbe.installs)).toEqual([]);
  await page.getByLabel('Recognition language').selectOption({ label: 'Urdu' });
  await expect(status(page)).toContainText('selected language is unavailable');
  await expect(download).toBeHidden();
  await page.getByLabel('Recognition language').selectOption({ label: 'English' });
  await download.click();
  await expect(record(page)).toBeEnabled();
  expect(await page.evaluate(() => window.speechProbe.installs)).toEqual([
    { langs: ['en'], processLocally: true },
  ]);
  expect(await page.evaluate(() => window.speechProbe.starts)).toEqual([]);
});

test('stalled language setup times out and preserves typed input', async ({ page }) => {
  await installSpeechProbe(page, true, 'stalled');
  await page.clock.install();
  await page.goto('/#/ai-agent');
  await page.getByRole('button', { name: 'Download language pack' }).click();
  await expect(status(page)).toContainText('Downloading');
  await transcript(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await page.clock.fastForward(61000);
  await expect(status(page)).toContainText('did not finish setting up');
  await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
  expect(await page.evaluate(() => window.speechProbe.starts)).toEqual([]);
});

test('permission denial, no speech and language failure preserve edits', async ({ page }) => {
  await installSpeechProbe(page);
  await page.goto('/#/ai-agent');
  await transcript(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  for (const [code, expected] of [
    ['not-allowed', 'Microphone access was blocked'],
    ['no-speech', 'No speech was heard'],
    ['network', 'Speech recognition could not finish'],
    ['language-not-supported', 'The selected language is unavailable'],
  ]) {
    await page.getByLabel('Recognition language').selectOption({ label: 'Pashto' });
    await record(page).click();
    await page.evaluate(code => window.speechProbe.error(code), code);
    await expect(status(page)).toContainText(expected);
    await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
  }
  expect(await page.evaluate(() => window.speechProbe.starts[0])).toEqual({
    language: 'ps-AF', local: true, continuous: false,
  });
});

test('cancel, page hide and navigation release the microphone session', async ({ page }) => {
  await installSpeechProbe(page);
  await page.goto('/#/ai-agent');
  await record(page).click();
  await page.getByRole('button', { name: 'Cancel recording' }).click();
  await expect(record(page)).toBeFocused();
  expect(await page.evaluate(() => window.speechProbe.aborts)).toBe(1);
  await record(page).click();
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await expect(status(page)).toContainText('Ready to record');
  expect(await page.evaluate(() => window.speechProbe.aborts)).toBe(2);
  await record(page).click();
  await page.evaluate(() => window.speechProbe.listening());
  await page.getByRole('link', { name: 'Back to home' }).click();
  await expect(page.getByRole('heading', { name: 'Your health, understood.' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.speechProbe.aborts)).toBe(3);
  await page.evaluate(() => window.speechProbe.result('late segment', true));
  await expect(transcript(page)).toHaveValue('');
  await expect(page.getByRole('link', { name: 'Call Rescue 1122' })).toHaveAttribute('href', 'tel:1122');
});

test('mobile keyboard recording keeps the status with reduced motion', async ({ page }) => {
  await installSpeechProbe(page);
  await page.setViewportSize({ width: 360, height: 640 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#/ai-agent');
  await record(page).focus();
  await page.keyboard.press('Enter');
  await page.evaluate(() => window.speechProbe.listening());
  await expect(status(page)).toHaveText('Listening…');
  await expect(record(page)).toHaveCSS('animation-name', 'none');
  const box = await record(page).boundingBox();
  expect(box?.height).toBeGreaterThanOrEqual(48);
  expect(box?.width).toBeGreaterThanOrEqual(48);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await page.getByRole('button', { name: 'Stop recording' }).focus();
  await page.keyboard.press('Space');
  await expect(status(page)).toHaveText('Processing speech…');
  await expect(transcript(page)).toBeFocused();
  expect(await page.evaluate(() => window.speechProbe.starts[0].language)).toBe('en');
});

test('opening emergency help stops voice capture without clearing the draft', async ({ page }) => {
  await installSpeechProbe(page);
  await page.goto('/#/ai-agent');
  await transcript(page).fill('SYNTHETIC—DEVELOPMENT ONLY');
  await record(page).click();
  await page.evaluate(() => window.speechProbe.listening());
  await expect(status(page)).toHaveText('Listening…');
  await page.getByRole('banner').getByRole('button', { name: 'Emergency help' }).click();
  const dialog = page.getByRole('dialog', { name: 'Possible emergency — act now' });
  await expect(dialog).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.speechProbe.aborts)).toBe(1);
  await dialog.getByRole('button', { name: 'Acknowledge and close' }).click();
  await expect(transcript(page)).toHaveValue('SYNTHETIC—DEVELOPMENT ONLY');
});
