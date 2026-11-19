import assert from 'node:assert/strict';
import test from 'node:test';
import { activateEmergency, emergencyHospitalMessage, emergencyWarningSigns, immediateActions } from '../src/emergency.ts';

test('manual activation opens one stable emergency state', () => {
  const activation = { source: 'manual' };
  const state = activateEmergency(null, activation);
  assert.equal(state, activation);
  assert.equal(activateEmergency(state, { source: 'manual' }), state);
});

test('reviewed-rule events cannot automatically activate the alert in UI-04', () => {
  const event = { source: 'reviewed-rule', categoryId: 'test-category',
    warning: { contentId: 'test-warning', revision: '1', text: 'SYNTHETIC—DEVELOPMENT ONLY' } };
  assert.equal(activateEmergency(null, event), null);
  const manual = { source: 'manual' };
  assert.equal(activateEmergency(manual, event), manual);
});

test('static warning content and hospital fallbacks are independent of a provider', () => {
  assert.equal(emergencyWarningSigns.length, 8);
  assert.match(immediateActions[0], /1122/);
  assert.match(emergencyHospitalMessage(false), /unavailable/);
  assert.match(emergencyHospitalMessage(true), /offline.*condition.*nearest emergency hospital/);
});
