import assert from 'node:assert/strict';
import test from 'node:test';
import { durationPresets, emptyDraft, normalizeDuration, validateDraft } from '../src/assessment-draft.ts';

const valid = {
  ...emptyDraft, symptoms: 'SYNTHETIC—DEVELOPMENT ONLY', severity: 'Mild', age: '24',
  duration: { kind: 'preset', hours: 1 },
};

test('requires symptom words, severity, duration and age', () => {
  const { errors, assessment } = validateDraft(emptyDraft);
  assert.deepEqual(Object.keys(errors), ['symptoms', 'severity', 'duration', 'age']);
  assert.equal(assessment, null);
  for (const symptoms of ['    ', '...', '123', '\u200f']) assert.ok(validateDraft({ ...valid, symptoms }).errors.symptoms);
  for (const symptoms of ['سر میں درد ہے', 'سر مې درد کوي']) {
    assert.equal(validateDraft({ ...valid, symptoms }).assessment.symptoms, symptoms);
  }
});

test('normalizes each duration preset to the specified hours', () => {
  assert.deepEqual(durationPresets.map(({ hours }) => normalizeDuration({ kind: 'preset', hours })),
    [1, 2, 12, 24, 48].map(hours => ({ kind: 'hours', hours })));
});

test('normalizes custom duration and rejects invalid days', () => {
  for (const [days, hours] of [['1', 24], ['5', 120], ['15', 360], ['365', 8760]]) {
    assert.deepEqual(normalizeDuration({ kind: 'custom', days }), { kind: 'hours', hours });
  }
  for (const days of ['', '0', '-1', '1.5', '366', '1e1']) {
    assert.equal(normalizeDuration({ kind: 'custom', days }), null);
    assert.ok(validateDraft({ ...valid, duration: { kind: 'custom', days } }).errors.days);
  }
});

test('keeps more than a year separate from precise hours', () => {
  assert.deepEqual(normalizeDuration({ kind: 'over-year' }), { kind: 'over-year' });
});

test('accepts ages 0 and 120 and rejects invalid numbers', () => {
  for (const age of ['0', '120']) assert.equal(validateDraft({ ...valid, age }).assessment.age, Number(age));
  for (const age of ['', '-1', '121', '1.5', '1e1']) assert.ok(validateDraft({ ...valid, age }).errors.age);
});

test('preserves each severity and normalizes optional entries without inference', () => {
  for (const severity of ['Mild', 'Moderate', 'Severe']) {
    assert.equal(validateDraft({ ...valid, severity }).assessment.severity, severity);
  }
  assert.deepEqual(validateDraft(valid).assessment.conditions, []);
  const { assessment } = validateDraft({ ...valid,
    conditions: ' sample A \n\n sample B ', allergies: ' sample C ', medicines: ' sample D ',
  });
  assert.deepEqual(assessment.conditions, ['sample A', 'sample B']);
  assert.deepEqual(assessment.allergies, ['sample C']);
  assert.deepEqual(assessment.medicines, ['sample D']);
});
