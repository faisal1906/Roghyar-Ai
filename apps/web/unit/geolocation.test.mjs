import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createLocationController, FIX_MAX_AGE_MS, validateFix } from '../src/geolocation.ts';

const NOW = 100_000;
const fix = (timestamp = NOW, coords = {}) => ({ timestamp, coords: { latitude: 10, longitude: 20, accuracy: 3, ...coords } });

function harness(overrides = {}) {
  const states = [];
  const watches = [];
  const clears = [];
  const timers = new Set();
  let now = NOW;
  const geolocation = {
    watchPosition(success, error, options) {
      watches.push({ success, error, options });
      return watches.length - 1;
    },
    clearWatch(id) { clears.push(id); },
  };
  const controller = createLocationController({
    secure: true, geolocation, now: () => now,
    schedule(callback, delay) {
      const timer = { callback, deadline: now + delay };
      timers.add(timer);
      return () => timers.delete(timer);
    },
    ...overrides,
  }, state => states.push(state));
  return {
    ...controller, watches, clears, states, state: () => states.at(-1),
    advance(ms) {
      now += ms;
      for (const timer of timers) if (timer.deadline <= now) {
        timers.delete(timer);
        timer.callback();
      }
    },
  };
}

test('LOC01 unit validates boundaries and rejects malformed or stale fixes', () => {
  assert.equal(validateFix(fix(NOW, { latitude: -90, longitude: 180, accuracy: 0 }), NOW)?.freshness, 'current');
  assert.ok(validateFix(fix(NOW, { latitude: 90, longitude: -180 }), NOW));
  for (const coords of [{ latitude: 91 }, { longitude: -181 }, { latitude: NaN }, { longitude: Infinity },
    { accuracy: -1 }, { accuracy: Infinity }, { latitude: '1' }]) {
    assert.equal(validateFix(fix(NOW, coords), NOW), null);
  }
  for (const timestamp of [0, -1, NOW + 1, NaN, Infinity, NOW - FIX_MAX_AGE_MS]) {
    assert.equal(validateFix(fix(timestamp), NOW), null);
  }
});

test('LOC01 unit opens one standard accuracy watch and updates fixes', async () => {
  const location = harness();
  assert.deepEqual(location.watches, []);
  await location.start();
  await location.start();
  assert.equal(location.watches.length, 1);
  const watch = location.watches[0];
  assert.deepEqual(watch.options, { enableHighAccuracy: false, maximumAge: 0, timeout: 15_000 });
  watch.success(fix());
  location.advance(1);
  watch.success(fix(NOW + 1, { latitude: 42 }));
  watch.success(fix(NOW + 1, { latitude: 91 }));
  watch.success(fix(NOW, { latitude: 10 }));
  assert.equal(location.state().fix.latitude, 42);
});

test('LOC01 unit expires cached and current fixes and recovers', async () => {
  const location = harness();
  await location.start();
  const watch = location.watches[0];
  watch.success(fix(NOW - FIX_MAX_AGE_MS));
  assert.deepEqual(location.state(), { status: 'stale', watching: true });
  watch.success(fix());
  location.advance(FIX_MAX_AGE_MS);
  assert.deepEqual(location.state(), { status: 'stale', watching: true });
  watch.success(fix(NOW + FIX_MAX_AGE_MS));
  assert.equal(location.state().status, 'available');
});

test('LOC01 unit clears watch zero and ignores callbacks across restart and disposal', async () => {
  const location = harness();
  await location.start();
  const previous = location.watches[0];
  previous.success(fix());
  location.stop();
  location.stop();
  assert.deepEqual(location.clears, [0]);
  assert.deepEqual(location.state(), { status: 'stopped', watching: false });
  await location.start();
  previous.success(fix());
  previous.error({ code: 1 });
  assert.equal(location.state().status, 'permission');
  location.dispose();
  const count = location.states.length;
  location.watches[1].success(fix());
  assert.equal(location.states.length, count);
  assert.deepEqual(location.clears, [0, 1]);
});

test('LOC01 unit ignores permission completion after stopping', async () => {
  let resolve;
  const location = harness({ permissions: { query: () => new Promise(done => { resolve = done; }) } });
  const starting = location.start();
  assert.equal(location.state().status, 'checking');
  location.stop();
  resolve({ state: 'granted' });
  await starting;
  assert.deepEqual(location.watches, []);
  assert.equal(location.state().status, 'stopped');
});

test('LOC01 unit handles browser errors and rejects denied permission', async () => {
  const denied = harness({ permissions: { query: async () => ({ state: 'denied' }) } });
  await denied.start();
  assert.equal(denied.state().status, 'denied');
  assert.equal(denied.watches.length, 0);
  const location = harness({ permissions: { query: async () => { throw Error(); } } });
  await location.start();
  const watch = location.watches[0];
  watch.error({ code: 2 });
  assert.equal(location.state().status, 'unavailable');
  watch.error({ code: 3 });
  assert.equal(location.state().status, 'timeout');
  watch.success(fix());
  watch.error({ code: 1 });
  assert.deepEqual(location.state(), { status: 'revoked', watching: false });
  assert.deepEqual(location.clears, [0]);
});

test('LOC01 unit handles unsupported insecure and throwing browsers', async () => {
  for (const [overrides, expected] of [
    [{ secure: false }, 'insecure'],
    [{ geolocation: undefined }, 'unsupported'],
    [{ geolocation: { watchPosition() { throw Error('private'); }, clearWatch() {} } }, 'unavailable'],
  ]) {
    const location = harness(overrides);
    await location.start();
    assert.deepEqual(location.state(), { status: expected, watching: false });
  }
});

test('LOC01 unit clears a watch denied synchronously', async () => {
  const clears = [];
  const location = harness({ geolocation: {
    watchPosition(_, error) { error({ code: 1 }); return 0; },
    clearWatch(id) { clears.push(id); },
  } });
  await location.start();
  assert.equal(location.state().status, 'denied');
  assert.deepEqual(clears, [0]);
});
