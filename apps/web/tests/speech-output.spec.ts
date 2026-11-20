import { expect, test } from '@playwright/test';

type Voice = { lang: string; localService?: boolean };

test.describe('local speech preview', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      let voices: { lang: string; localService?: boolean }[] = [];
      const engine = Object.assign(new EventTarget(), {
        getVoices: () => voices,
        speak: (utterance: SpeechSynthesisUtterance) => {
          probe.utterances.push(utterance);
          probe.lateEnd = utterance.onend;
        },
        cancel: () => { probe.cancels++; },
      });
      const probe = {
        voices: (next: typeof voices) => { voices = next; engine.dispatchEvent(new Event('voiceschanged')); },
        utterances: [] as SpeechSynthesisUtterance[],
        cancels: 0,
        lateEnd: null as SpeechSynthesisUtterance['onend'],
      };
      Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: class {
        constructor(public text: string) {}
      } });
      Object.defineProperty(window, 'speechSynthesis', { value: engine, configurable: true });
      Object.defineProperty(window, 'previewProbe', { value: probe });
    });
  });

  test('refreshes delayed voices and speaks only fixed interface copy after an explicit action', async ({ page }) => {
    await page.goto('/');
    const play = page.getByRole('button', { name: 'Play speech preview' });
    await expect(play).toBeDisabled();
    const setVoices = (voices: Voice[]) => page.evaluate(voices => Reflect.get(window, 'previewProbe').voices(voices), voices);
    for (const [voice, status] of [
      [{ lang: 'en-US', localService: false }, 'This locale uses a remote voice'],
      [{ lang: 'en-US' }, 'cannot verify that this voice is local'],
      [{ lang: 'en-GB', localService: true }, 'Only a different voice locale'],
    ] as const) {
      await setVoices([voice]);
      await expect(page.getByText(status, { exact: false })).toBeVisible();
      await expect(play).toBeDisabled();
    }
    await setVoices([{ lang: 'en-US', localService: true }]);
    await expect(play).toBeEnabled();
    await page.getByRole('textbox', { name: 'Symptom description' }).fill('Synthetic draft must stay silent');
    expect(await page.evaluate(() => Reflect.get(window, 'previewProbe').utterances.length)).toBe(0);
    await play.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Stop playback' })).toBeVisible();
    const written = await page.evaluate(() => Reflect.get(window, 'previewProbe').utterances.map(
      (utterance: SpeechSynthesisUtterance) => [utterance.text, utterance.lang, utterance.voice?.localService],
    ));
    expect(written).toEqual([['Speak to AI Agent', 'en-US', true]]);
    await page.getByRole('button', { name: 'Stop playback' }).click();
    await page.evaluate(() => Reflect.get(window, 'previewProbe').lateEnd?.());
    await expect(page.getByText('Speech preview stopped.')).toBeVisible();
  });

  for (const interrupt of ['emergency', 'navigation', 'pagehide']) {
    test(interrupt + ' cancels playback and discards late callbacks', async ({ page }) => {
      await page.goto('/');
      await page.evaluate(() => Reflect.get(window, 'previewProbe').voices([{ lang: 'en-US', localService: true }]));
      await page.getByRole('button', { name: 'Play speech preview' }).click();
      if (interrupt === 'emergency') {
        await page.getByRole('banner').getByRole('button', { name: 'Emergency help' }).click();
        await expect(page.getByRole('dialog').locator('a[href="tel:1122"]')).toHaveCount(2);
      } else if (interrupt === 'navigation') {
        await page.evaluate(() => { window.location.hash = '/assess'; });
      } else {
        await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
      }
      await expect.poll(() => page.evaluate(() => Reflect.get(window, 'previewProbe').cancels)).toBe(1);
      await page.evaluate(() => Reflect.get(window, 'previewProbe').lateEnd?.());
      await expect(page.getByText('Speech preview finished.')).toHaveCount(0);
    });
  }
});
