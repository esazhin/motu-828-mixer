import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const STATE = {
  type: 'state',
  status: 'synced',
  read_only_reason: null,
  device: { model: '828E', serial: '828E000000', api: 9, firmware: '1.1.8' },
  links: { inputs: Array(44).fill(0), buses: Array(16).fill(0) },
  cells: { fader: Array(704).fill(0), pan: Array(704).fill(0.5), mute: Array(704).fill(0), solo: Array(704).fill(0) },
  bus_master: { fader: Array(16).fill(1.0), mute: Array(16).fill(0) },
  routing: { 43: 84, 40: 85 },
  loops: [],
  policy: {},
  layout: {
    tabs: [{ label: 'Main 1-2', bus: 0, stereo: true, pan: [2] }, { label: 'Ana 3', bus: 4, stereo: false, pan: [] }],
    strips: [{ label: 'Line 3', input: 2, pan_visible: true }],
    output_pairs: [{ label: 'Line out 9-10', left: 43, right: 40 }],
    sources: [{ label: 'Main 1-2 mix', left: 76, right: 77 }],
    source_labels: {},
  },
};

// the page as index.html starts it, with the real mixer client; the browser and the server are doubled
function startPage() {
  const document = fakeDocument();
  FakeSocket.opened = [];
  startApp({ document, WebSocket: FakeSocket, setTimeout: () => 0, location: PAGE_LOCATION });
  const socket = FakeSocket.opened.at(-1);
  socket.emit('open');
  socket.emit('message', { data: JSON.stringify(STATE) });
  return { document, socket };
}

test('the page entry point reaches the mixer client and the server socket', async (t) => {
  await t.test('a gesture reaches the mixer client', () => {
    const { document } = startPage();
    fire(find(document, 'tab:4'), 'click');
    assert.ok(find(document, 'fader:4:2'), 'the client drew the tab it was asked for');
  });

  await t.test('a change from the mixer client reaches /ws', () => {
    const { document, socket } = startPage();
    fire(find(document, 'fader:0:2'), 'pointerdown', { clientY: 50 });
    assert.equal(socket.url, 'ws://mixer-host:8280/ws');
    assert.equal(socket.sent[0].type, 'change');
  });
});
