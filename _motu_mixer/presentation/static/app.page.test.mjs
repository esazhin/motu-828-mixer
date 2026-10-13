import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const MINUS = '−';
const SET_LINE_3_ON_ANA_3 = { kind: 'set_level', bus: 4, input: 2, db: -6.0 };

function view(bus, { faderText = `${MINUS}∞`, mute = false } = {}) {
  return {
    status: 'synced',
    statusText: 'synced',
    banner: null,
    controlsEnabled: true,
    tabs: [
      { control: 'tab:0', label: 'Main 1-2', selected: bus === 0 },
      { control: 'tab:4', label: 'Ana 3', selected: bus === 4 },
    ],
    strips: [{
      label: 'Line 3',
      fader: { control: `fader:${bus}:2`, position: 0, text: faderText },
      pan: bus === 0 ? { control: 'pan:0:2', position: 0, text: 'L100' } : null,
      mute: { control: `mute:${bus}:2`, on: mute },
      solo: { control: `solo:${bus}:2`, on: false },
      link: { control: 'input_link:2', on: false },
    }],
    master: {
      label: bus === 0 ? 'Main 1-2' : 'Ana 3',
      fader: { control: `master:${bus}`, position: 0.83, text: '0.0 dB' },
      mute: { control: `master_mute:${bus}`, on: false },
      link: { control: `bus_link:${bus}`, on: bus === 0 },
    },
    routing: [{
      label: 'Line out 9-10',
      control: 'route:43:40',
      value: '84-85',
      options: [{ value: '76-77', label: 'Main 1-2 mix' }, { value: '84-85', label: 'Ana 7-8 mix' }],
    }],
  };
}

// a client that records what it is handed, sends a canned change for drags and taps, and renders canned views
function recordingClient() {
  const record = { gestures: [], received: [], renders: 0 };
  record.createClient = ({ send, render }) => ({
    gesture(g) {
      record.gestures.push(g);
      if (g.type === 'drag') send({ type: 'change', action: SET_LINE_3_ON_ANA_3 });
      record.renders += 1;
      if (g.type === 'drag') render(view(Number(g.control.split(':')[1]), { faderText: `${MINUS}6.0 dB` }));
      else if (g.type === 'select_tab') render(view(0));
      else if (g.type === 'toggle') render(view(4, { mute: true }));
      else render(view(4));
    },
    receive(message) {
      record.received.push(message);
      record.renders += 1;
      render(message.type === 'state' ? view(message.bus) : view(4));
    },
  });
  return record;
}

function start(extra = {}) {
  const document = fakeDocument();
  const client = recordingClient();
  const timers = [];
  FakeSocket.opened = [];
  startApp({
    document,
    WebSocket: FakeSocket,
    setTimeout: (fn, ms) => timers.push({ fn, ms }),
    location: PAGE_LOCATION,
    createClient: client.createClient,
    ...extra,
  });
  const socket = FakeSocket.opened.at(-1);
  socket.emit('open');
  socket.emit('message', { data: JSON.stringify({ type: 'state', bus: 4 }) });
  return { document, client, timers, socket };
}

for (const pointerType of ['touch', 'mouse']) {
  test(`dragging the Line 3 fader by ${pointerType} gives drags with its control id, keeps the page still and sends the change`, () => {
    const { document, client, socket } = start();
    const fader = find(document, 'fader:4:2');
    assert.equal(fader.style['touch-action'], 'none');

    const down = fire(fader, 'pointerdown', { pointerType, clientY: 200 });
    assert.ok(down.defaultPrevented);
    assert.ok(fader.hasPointerCapture(1));
    // the -6 dB position on the -60…+12 dB taper is 54/72 of the way up the 200 px fader
    const move = fire(find(document, 'fader:4:2'), 'pointermove', { pointerType, clientY: 50 });
    assert.ok(move.defaultPrevented, 'the move does not scroll the page');
    assert.ok(find(document, 'fader:4:2').hasPointerCapture(1), 'the redrawn fader still holds the pointer');
    assert.match(document.textContent, /−6\.0 dB/);
    fire(find(document, 'fader:4:2'), 'pointerup', { pointerType, clientY: 50 });

    assert.deepEqual(client.gestures, [
      { type: 'drag', control: 'fader:4:2', value: 0 },
      { type: 'drag', control: 'fader:4:2', value: 0.75 },
      { type: 'release', control: 'fader:4:2' },
    ]);
    assert.deepEqual(socket.sent, [
      { type: 'change', action: SET_LINE_3_ON_ANA_3 },
      { type: 'change', action: SET_LINE_3_ON_ANA_3 },
    ]);
  });
}

test('a pan drag runs left to right and a move from another pointer is ignored', () => {
  const { document, client } = start();
  fire(find(document, 'tab:0'), 'click');
  client.gestures.length = 0;
  const pan = find(document, 'pan:0:2');
  assert.equal(pan.style['touch-action'], 'none');
  fire(pan, 'pointerdown', { clientX: 10 });
  fire(find(document, 'pan:0:2'), 'pointermove', { pointerId: 2, clientX: 30 });
  fire(find(document, 'pan:0:2'), 'pointercancel', {});
  assert.deepEqual(client.gestures, [
    { type: 'drag', control: 'pan:0:2', value: 0.25 },
    { type: 'release', control: 'pan:0:2' },
  ]);
});

test('taps on mute, solo, link and tab buttons and a routing choice each give their gesture and are redrawn at once', () => {
  const { document, client, socket } = start();
  const cases = [
    ['click', 'mute:4:2', { type: 'toggle', control: 'mute:4:2', value: true }],
    ['click', 'mute:4:2', { type: 'toggle', control: 'mute:4:2', value: false }],
    ['click', 'solo:4:2', { type: 'toggle', control: 'solo:4:2', value: true }],
    ['click', 'input_link:2', { type: 'toggle', control: 'input_link:2', value: true }],
    ['click', 'bus_link:4', { type: 'toggle', control: 'bus_link:4', value: true }],
    ['click', 'master_mute:4', { type: 'toggle', control: 'master_mute:4', value: true }],
    ['change', 'route:43:40', { type: 'route', control: 'route:43:40', value: '76-77' }],
    ['click', 'tab:0', { type: 'select_tab', control: 'tab:0' }],
  ];
  for (const [type, control, gesture] of cases) {
    client.gestures.length = 0;
    const renders = client.renders;
    const target = find(document, control);
    if (type === 'change') target.value = '76-77';
    fire(target, type);
    assert.deepEqual(client.gestures, [gesture], control);
    assert.equal(client.renders, renders + 1);
  }
  // each render is drawn in the same turn: the tab tap's Main 1-2 view replaced the Ana 3 one
  assert.ok(find(document, 'pan:0:2'));
  assert.equal(find(document, 'fader:4:2'), null);
  assert.equal(find(document, 'route:43:40').value, '84-85');
  assert.deepEqual(socket.sent, []);
});

test('a phone waking from sleep reconnects 1 s after its socket closed and the next state replaces the view without a reload', () => {
  const reloads = [];
  const { document, client, timers, socket } = start({ location: { ...PAGE_LOCATION, reload: () => reloads.push(1) } });
  assert.ok(find(document, 'fader:4:2'));

  socket.emit('close', { code: 1006 });
  assert.deepEqual(timers.map((t) => t.ms), [1000]);
  timers[0].fn();
  const fresh = FakeSocket.opened.at(-1);
  assert.notEqual(fresh, socket);
  assert.equal(fresh.url, 'ws://mixer-host:8280/ws');
  fresh.emit('open');
  fresh.emit('message', { data: JSON.stringify({ type: 'state', bus: 0 }) });

  assert.deepEqual(client.received.at(-1), { type: 'state', bus: 0 });
  assert.ok(find(document, 'fader:0:2'), 'the fresh state is drawn');
  assert.equal(find(document, 'fader:4:2'), null, 'the old view is gone');
  assert.deepEqual(reloads, []);
});
