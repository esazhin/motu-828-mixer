import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const FADER_HEIGHT = 200;
const HANDLE = 20;

function view({ meter } = {}) {
  return {
    status: 'synced', statusText: 'synced', banner: null, controlsEnabled: true,
    tabs: [{ control: 'tab:0', label: 'Main 1-2', selected: true }],
    strips: [{
      label: 'Line 3',
      fader: { control: 'fader:0:2', position: 0.5, text: '−24.0 dB', ...(meter ? { meter } : {}) },
      pan: { control: 'pan:0:2', position: 0.5, text: 'C' },
      mute: { control: 'mute:0:2', on: false },
      solo: { control: 'solo:0:2', on: false },
      link: { control: 'input_link:2', on: false },
    }],
    master: {
      label: 'Main 1-2',
      fader: { control: 'master:0', position: 0.83, text: '0.0 dB' },
      mute: { control: 'master_mute:0', on: false },
      link: { control: 'bus_link:0', on: true },
    },
    routing: [],
  };
}

function start() {
  const document = fakeDocument();
  const record = { gestures: [], renders: 0, renderMeters: null };
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION,
    createClient: ({ render, renderMeters }) => {
      record.renderMeters = renderMeters;
      return {
        gesture(g) { record.gestures.push(g); record.renders += 1; render(view()); },
        receive(message) { record.renders += 1; render(view(message.meter ? { meter: message.meter } : {})); },
      };
    },
  });
  const socket = FakeSocket.opened.at(-1);
  socket.emit('open');
  socket.emit('message', { data: JSON.stringify({ type: 'state' }) });
  return { document, record };
}

const meterOf = (fader) => fader.children.find((n) => (n.getAttribute('class') ?? '').startsWith('meter'));

test('each fader carries a meter in its track, empty until a level arrives, under the handle', () => {
  const { document } = start();
  for (const control of ['fader:0:2', 'master:0']) {
    const fader = find(document, control);
    const classes = fader.children.map((n) => n.getAttribute('class'));
    assert.deepEqual(classes, ['track', 'meter off', 'handle']);
    assert.equal(Number(meterOf(fader).getAttribute('height')), 0);
  }
});

test('renderMeters fills the track up to the level on the fader scale and colours it, without redrawing the page', () => {
  const { document, record } = start();
  const fader = find(document, 'fader:0:2');
  const rendersBefore = record.renders;
  record.renderMeters({ 'fader:0:2': { position: 0.5, zone: 'hot' }, 'master:0': { position: 0, zone: 'off' }, 'fader:9:9': { position: 1, zone: 'clip' } });
  assert.equal(record.renders, rendersBefore, 'no full render');
  assert.equal(find(document, 'fader:0:2'), fader, 'the same fader element, not a rebuilt one');
  const meter = meterOf(fader);
  assert.equal(meter.getAttribute('class'), 'meter hot');
  // the fill's top sits where the handle's centre would be at that position, its bottom at the position-0 centre
  const top = (1 - 0.5) * (FADER_HEIGHT - HANDLE) + HANDLE / 2;
  assert.equal(Number(meter.getAttribute('y')), top);
  assert.equal(Number(meter.getAttribute('height')), FADER_HEIGHT - HANDLE / 2 - top);
});

test('a second press on the same fader or pan within 350 ms resets it instead of starting a drag', () => {
  for (const control of ['fader:0:2', 'pan:0:2', 'master:0']) {
    const { document, record } = start();
    fire(find(document, control), 'pointerdown', { timeStamp: 1000 });
    fire(find(document, control), 'pointerup', { timeStamp: 1080 });
    const second = fire(find(document, control), 'pointerdown', { timeStamp: 1250 });
    fire(find(document, control), 'pointerup', { timeStamp: 1300 });
    assert.ok(second.defaultPrevented);
    const types = record.gestures.map((g) => g.type);
    assert.deepEqual(types, ['drag', 'release', 'reset'], control);
    assert.deepEqual(record.gestures.at(-1), { type: 'reset', control });
  }
});

test('presses further apart than 350 ms, or on two different controls, are ordinary drags', () => {
  const { document, record } = start();
  fire(find(document, 'fader:0:2'), 'pointerdown', { timeStamp: 1000 });
  fire(find(document, 'fader:0:2'), 'pointerup', { timeStamp: 1050 });
  fire(find(document, 'fader:0:2'), 'pointerdown', { timeStamp: 1500 });
  fire(find(document, 'fader:0:2'), 'pointerup', { timeStamp: 1550 });
  fire(find(document, 'master:0'), 'pointerdown', { timeStamp: 1600 });
  fire(find(document, 'master:0'), 'pointerup', { timeStamp: 1650 });
  assert.ok(!record.gestures.some((g) => g.type === 'reset'));
});
