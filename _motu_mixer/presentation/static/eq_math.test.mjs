import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bandDb, coefficients, logFreqs, response } from './eq_math.js';

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);

test('a peak reaches its gain at its centre frequency and is flat far away, both boost and cut', () => {
  for (const gain of [6, -5.6]) {
    const c = coefficients({ mode: 0, freq: 1000, gain, q: 2 });
    near(bandDb(c, 1000), gain, 0.05, 'centre');
    near(bandDb(c, 20), 0, 0.1, 'far below');
    near(bandDb(c, 19000), 0, 0.3, 'far above');
  }
});

test('a narrower Q gives a narrower peak', () => {
  const wide = coefficients({ mode: 0, freq: 1000, gain: 6, q: 0.7 });
  const narrow = coefficients({ mode: 0, freq: 1000, gain: 6, q: 6 });
  assert.ok(bandDb(wide, 1500) > bandDb(narrow, 1500) + 1);
});

test('shelves reach their gain on their own side and stay flat on the other', () => {
  const low = coefficients({ mode: 1, freq: 200, gain: -6, q: 1 });
  near(bandDb(low, 20), -6, 0.3, 'low shelf, below');
  near(bandDb(low, 10000), 0, 0.1, 'low shelf, above');
  const high = coefficients({ mode: 2, freq: 3543, gain: -2.7, q: 3 });
  near(bandDb(high, 18000), -2.7, 0.3, 'high shelf, above');
  near(bandDb(high, 50), 0, 0.05, 'high shelf, below');
});

test('the high-pass is -3 dB at its corner, cuts below and passes above, whatever gain or Q say', () => {
  const hp = coefficients({ mode: 3, freq: 100, gain: 12, q: 9 });
  near(bandDb(hp, 100), -3.01, 0.05, 'corner');
  assert.ok(bandDb(hp, 25) < -20);
  near(bandDb(hp, 5000), 0, 0.05, 'passband');
});

test('the response sums the active bands and skips bypassed ones', () => {
  const bands = [{ mode: 0, freq: 200, gain: -3, q: 6, bypass: 0 }, { mode: 0, freq: 200, gain: -3, q: 6, bypass: 1 }];
  const [atCentre] = response(bands, [200]);
  near(atCentre, -3, 0.05, 'one active band');
  assert.equal(logFreqs(96).length, 96);
  near(logFreqs(96)[0], 20, 1e-9, 'first');
  near(logFreqs(96).at(-1), 20000, 1e-6, 'last');
});
