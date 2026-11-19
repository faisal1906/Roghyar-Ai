import assert from 'node:assert/strict';
import test from 'node:test';
import { reloadAction } from '../src/pwa-state.ts';

test('waiting, activating and failed workers never authorize a reload', () => {
  for (const status of ['idle', 'waiting', 'activating', 'error', 'unsupported']) {
    assert.equal(reloadAction(status, false, false), 'wait');
    assert.equal(reloadAction(status, true, true), 'wait');
  }
});

test('an updated worker requires explicit confirmation to discard a draft', () => {
  assert.equal(reloadAction('reload', false, false), 'reload');
  assert.equal(reloadAction('reload', true, false), 'confirm');
  assert.equal(reloadAction('reload', true, true), 'reload');
});
