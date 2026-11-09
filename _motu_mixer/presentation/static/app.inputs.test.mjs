import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const gain = (index, value, max, meter) => ({
  control: `ingain:${index}`, field: `ingainf:${index}`, position: value / max, value, max, text: `${value} dB`,
  ...(meter ? { meter } : {}),
});

function view(enabled = true, confirm = null) {
  return {
    status: 'synced', statusText: 'synced', banner: null, controlsEnabled: enabled, mode: 'inputs', panRow: false, confirm,
    tabs: [{ control: 'tab:0', label: 'Main 1-2', selected: false }, { control: 'tab:inputs', label: 'Inputs', selected: true }],
    strips: [], master: null, routing: [], eq: null,
    inputs: [
      {
        label: 'Mic 1', index: 0, mic: true, gain: gain(0, 47, 74, { position: 0.8, zone: 'hot' }), jack: 'mic jack',
        phase: { control: 'inphase:0', on: false }, pad: { control: 'inpad:0', on: false, enabled: true },
        phantom: { control: 'inphantom:0', on: true, enabled: true }, insert: gain(10, 3, 20),
      },
      {
        label: 'Line 4', index: 3, mic: false, gain: gain(3, 16, 20, { position: 0, zone: 'off' }), jack: null,
        phase: { control: 'inphase:3', on: true }, pad: null, phantom: null, insert: null,
      },
    ],
  };
}

function start(enabled = true, confirm = null) {
  const document = fakeDocument();
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION,
    createClient: ({ render }) => ({ gesture(g) { gestures.push(g); render(view(enabled, confirm)); }, receive() { render(view(enabled, confirm)); } }),
  });
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  return { document, gestures };
}

const all = (node) => node.children.flatMap((c) => [c, ...all(c)]);
const byClass = (node, prefix) => all(node).filter((n) => (n.getAttribute('class') ?? '').startsWith(prefix));

test('the Inputs page draws one mixer-style strip per input: vertical gain fader with meter, dB box, Ø, Pad and 48V', () => {
  const { document } = start();
  assert.equal(document.body.getAttribute('data-mode'), 'inputs');
  const row = document.getElementById('strips');
  const strips = row.children.filter((n) => (n.getAttribute('class') ?? '').startsWith('strip'));
  assert.deepEqual(strips.map((n) => n.getAttribute('class')), ['strip input mic', 'strip input mic', 'strip input']);
  assert.deepEqual(strips.map((n) => byClass(n, 'label')[0].textContent), ['Mic 1', 'Mic 1 insert', 'Line 4']);
  assert.deepEqual(strips.map((n) => byClass(n, 'jack')[0].textContent), ['mic jack', 'return', '']);
  assert.equal(document.getElementById('routing').children.length, 0);
  const fader = find(document, 'ingain:0');
  assert.equal(fader.getAttribute('class'), 'fader');
  assert.equal(fader.getAttribute('data-gesture'), 'drag-y');
  assert.equal(byClass(fader, 'meter')[0].getAttribute('class'), 'meter hot', 'the input meter fills the fader track');
  const box = find(document, 'ingainf:0');
  assert.equal(box.tagName, 'INPUT');
  assert.equal(box.value, '47');
  assert.equal(box.getAttribute('max'), '74');
  assert.equal(find(document, 'ingainf:3').getAttribute('max'), '20');
  assert.equal(find(document, 'ingainf:10').value, '3', 'the insert return has its own strip');
  assert.equal(find(document, 'inpad:0').getAttribute('class'), 'toggle pad');
  assert.equal(find(document, 'inphase:3').getAttribute('class'), 'toggle phase on');
  assert.equal(find(document, 'inpad:3'), null, 'a line input has no pad');
  const phantom = find(document, 'inphantom:0');
  assert.equal(phantom.getAttribute('class'), 'toggle phantom on');
  assert.equal(phantom.getAttribute('data-gesture'), 'toggle');
  assert.equal(find(document, 'inphantom:3'), null, 'a line input has no 48V');
  assert.equal(byClass(strips[1], 'toggle pad').length + byClass(strips[1], 'toggle phantom').length, 0, 'the insert strip has neither pad nor 48V');
  assert.equal(document.body.getAttribute('data-confirm'), 'closed');
  assert.equal(document.getElementById('confirm').children.length, 0);
});

test('the 48V confirmation shows the warning and a "don\'t ask again" box; its buttons give confirm and cancel', () => {
  const confirm = { control: 'inphantom:0', title: 'Switch 48V on for Mic 1?', text: 'Phantom power can damage ribbon microphones.' };
  const { document, gestures } = start(true, confirm);
  assert.equal(document.body.getAttribute('data-confirm'), 'open');
  const panel = document.getElementById('confirm');
  assert.match(panel.textContent, /Switch 48V on for Mic 1\?/);
  assert.match(panel.textContent, /ribbon/);
  assert.match(panel.textContent, /Don't ask me again/);
  fire(find(document, 'confirm-ok'), 'click');
  find(document, 'confirm-remember').checked = true;
  fire(find(document, 'confirm-ok'), 'click');
  fire(find(document, 'confirm-cancel'), 'click');
  assert.deepEqual(gestures, [
    { type: 'confirm', remember: false },
    { type: 'confirm', remember: true },
    { type: 'cancel_confirm' },
  ]);
});

test('gain, polarity and pad controls give their gestures', () => {
  const { document, gestures } = start();
  const box = find(document, 'ingainf:3');
  box.value = '12';
  fire(box, 'change');
  fire(find(document, 'inphase:3'), 'click');
  fire(find(document, 'inpad:0'), 'click');
  fire(find(document, 'ingain:0'), 'pointerdown', { clientX: 20, clientY: 100, timeStamp: 1 });
  fire(find(document, 'ingain:0'), 'pointerup', { timeStamp: 2 });
  assert.deepEqual(gestures, [
    { type: 'input_set', control: 'ingainf:3', value: '12' },
    { type: 'toggle', control: 'inphase:3', value: false },
    { type: 'toggle', control: 'inpad:0', value: true },
    { type: 'drag', control: 'ingain:0', value: 0.5 },
    { type: 'release', control: 'ingain:0' },
  ]);
});

test('while not synced the input controls are disabled', () => {
  const { document } = start(false);
  assert.equal(find(document, 'ingainf:0').getAttribute('disabled'), '');
  assert.equal(find(document, 'inphase:0').getAttribute('disabled'), '');
  assert.equal(find(document, 'ingain:0').getAttribute('data-gesture'), null);
  assert.equal(find(document, 'ingain:0').getAttribute('class'), 'fader disabled');
});

test('opening the page at #inputs selects the Inputs tab', () => {
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document: fakeDocument(), WebSocket: FakeSocket, setTimeout() {}, location: { ...PAGE_LOCATION, hash: '#inputs' },
    createClient: () => ({ gesture(g) { gestures.push(g); }, receive() {} }),
  });
  assert.deepEqual(gestures, [{ type: 'select_tab', control: 'tab:inputs' }]);
});
