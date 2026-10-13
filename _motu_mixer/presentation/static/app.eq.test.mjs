import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const band = (b, mode, freq, gain, q, bypass, modes) => ({
  band: b, index: b, mode, freq, gain, q, bypass, control: `eqb:out:0:${b}`, point: `eqpt:out:0:${b}`,
  modes: modes.map((value) => ({ value, label: ['Peak', 'Low shelf', 'High shelf', 'High-pass'][value] })),
  gainEnabled: mode !== 3, qEnabled: mode === 0,
});

function view(withEq) {
  return {
    status: 'synced', statusText: 'synced', banner: null, controlsEnabled: true, mode: 'mixer', panRow: false,
    tabs: [{ control: 'tab:0', label: 'Main 1-2', selected: true }],
    strips: [{
      label: 'Line 3', fader: { control: 'fader:0:2', position: 0.5, text: '−24.0 dB' }, pan: null,
      mute: { control: 'mute:0:2', on: false }, solo: { control: 'solo:0:2', on: false },
      link: { control: 'input_link:2', on: false }, eq: { control: 'eq:in:2', on: false },
    }],
    master: {
      label: 'Main 1-2', fader: { control: 'master:0', position: 0.83, text: '0.0 dB' },
      mute: { control: 'master_mute:0', on: false }, link: { control: 'bus_link:0', on: true },
      eq: { control: 'eq:out:0', on: true },
    },
    routing: [],
    eq: withEq ? {
      title: 'Main 1-2 EQ', kind: 'out', slot: 0,
      bands: [band(0, 0, 200, -3, 6, 0, [0, 1, 3]), band(1, 0, 353.4, -5.600000023841858, 2.000001, 0, [0]), band(2, 2, 3543, -2.7, 3, 1, [0, 2])],
      curve: { freqs: [20, 200, 2000, 20000], db: [0, -3, -1, -2.7] },
    } : null,
  };
}

function start(withEq = true) {
  const document = fakeDocument();
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION,
    createClient: ({ render }) => ({ gesture(g) { gestures.push(g); render(view(withEq)); }, receive() { render(view(withEq)); } }),
  });
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  return { document, gestures };
}

test('strips carry an EQ button that opens their EQ, lit when the EQ is in use', () => {
  const { document, gestures } = start(false);
  const button = find(document, 'eq:out:0');
  assert.equal(button.getAttribute('class'), 'eq-open on');
  assert.equal(find(document, 'eq:in:2').getAttribute('class'), 'eq-open');
  fire(button, 'click');
  assert.deepEqual(gestures, [{ type: 'open_eq', control: 'eq:out:0' }]);
  assert.equal(document.getElementById('eq').children.length, 0, 'no panel until an EQ is open');
});

test('the EQ panel shows its title, the curve, one point per band and each band\'s controls', () => {
  const { document, gestures } = start();
  const panel = document.getElementById('eq');
  assert.match(panel.textContent, /Main 1-2 EQ/);
  const all = (node) => node.children.flatMap((c) => [c, ...all(c)]);
  const curve = all(panel).find((n) => n.getAttribute('class') === 'eq-curve');
  assert.ok(curve.getAttribute('d').startsWith('M'));
  assert.equal(all(panel).filter((n) => (n.getAttribute('data-gesture') ?? '') === 'drag-eq').length, 3);
  assert.equal(find(document, 'eqpt:out:0:2').getAttribute('class'), 'eq-point band-2 bypassed');
  assert.equal(find(document, 'eqb:out:0:0:mode').tagName, 'SELECT');
  assert.equal(find(document, 'eqb:out:0:1:mode'), null, 'a peak-only band has no mode menu');
  assert.equal(find(document, 'eqb:out:0:2:q').getAttribute('disabled'), '', 'a shelf has no Q');
  // 8.24 values are shown rounded: gain to 0.1 dB, Q to 0.01, frequency to whole Hz
  assert.equal(find(document, 'eqb:out:0:1:gain').value, '-5.6');
  assert.equal(find(document, 'eqb:out:0:1:q').value, '2');
  assert.equal(find(document, 'eqb:out:0:1:freq').value, '353');
  fire(find(document, 'eq-close'), 'click');
  assert.deepEqual(gestures.at(-1), { type: 'close_eq' });
});

test('band controls send eq_set: fields, mode and the on/off toggle', () => {
  const { document, gestures } = start();
  const gain = find(document, 'eqb:out:0:1:gain');
  gain.value = '-4';
  fire(gain, 'change');
  const mode = find(document, 'eqb:out:0:0:mode');
  mode.value = '3';
  fire(mode, 'change');
  fire(find(document, 'eqb:out:0:2:bypass'), 'click');     // band 3 is bypassed: the toggle turns it on
  fire(find(document, 'eqb:out:0:0:bypass'), 'click');     // band 1 is on: the toggle bypasses it
  assert.deepEqual(gestures, [
    { type: 'eq_set', control: 'eqb:out:0:1:gain', value: '-4' },
    { type: 'eq_set', control: 'eqb:out:0:0:mode', value: '3' },
    { type: 'eq_set', control: 'eqb:out:0:2:bypass', value: false },
    { type: 'eq_set', control: 'eqb:out:0:0:bypass', value: true },
  ]);
});

test('dragging a band point maps the graph position to frequency (log) and gain', () => {
  const { document, gestures } = start();
  // the fake graph is 40 x 200 px: the middle is 632 Hz (log midpoint of 20-20k) and 0 dB
  fire(find(document, 'eqpt:out:0:1'), 'pointerdown', { clientX: 20, clientY: 100, timeStamp: 1 });
  fire(find(document, 'eqpt:out:0:1'), 'pointermove', { clientX: 40, clientY: 0, timeStamp: 2 });
  fire(document.getElementById('eq'), 'pointerup', { timeStamp: 3 });
  const drags = gestures.filter((g) => g.type === 'eq_drag');
  assert.equal(drags[0].control, 'eqpt:out:0:1');
  assert.ok(Math.abs(drags[0].freq - 632.46) < 0.5 && Math.abs(drags[0].gain) < 1e-9);
  assert.ok(Math.abs(drags[1].freq - 20000) < 1e-6 && drags[1].gain === 20);
});

test('opening the page at #eq:out:0 opens that EQ', () => {
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document: fakeDocument(), WebSocket: FakeSocket, setTimeout() {}, location: { ...PAGE_LOCATION, hash: '#eq:out:0' },
    createClient: () => ({ gesture(g) { gestures.push(g); }, receive() {} }),
  });
  assert.deepEqual(gestures, [{ type: 'open_eq', control: 'eq:out:0' }]);
});
