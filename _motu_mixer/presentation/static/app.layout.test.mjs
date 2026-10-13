import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

function view() {
  const strip = (input, pan) => ({
    label: `In ${input}`,
    fader: { control: `fader:0:${input}`, position: 0.5, text: '−24.0 dB' },
    pan,
    mute: { control: `mute:0:${input}`, on: false },
    solo: { control: `solo:0:${input}`, on: false },
    link: { control: `input_link:${input}`, on: false },
  });
  return {
    status: 'synced', statusText: 'synced', banner: null, controlsEnabled: true, mode: 'mixer', panRow: true,
    tabs: [{ control: 'tab:0', label: 'Main 1-2', selected: true }, { control: 'tab:routing', label: 'Routing', selected: false }],
    strips: [
      strip(2, { control: 'pan:0:2', position: 0.5, text: 'C' }),
      strip(28, { control: 'pan:0:28', position: 0.5, text: 'stereo', disabled: true }),
    ],
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
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION,
    createClient: ({ render }) => ({ gesture(g) { gestures.push(g); }, receive() { render(view()); } }),
  });
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  return { document, gestures };
}

const strips = (document) => document.getElementById('strips').children;
const kinds = (strip) => strip.children.map((n) => (n.getAttribute('class') ?? '').split(' ')[0]);

test('every strip, the master included, has the same rows so faders and buttons line up', () => {
  const { document } = start();
  const rows = [...strips(document)].map(kinds);
  assert.equal(rows.length, 3);
  for (const row of rows) assert.deepEqual(row, rows[0]);
  // the master's missing pan and solo are held by placeholders of the same size
  const master = strips(document).at(-1);
  const panRow = master.children.find((n) => n.getAttribute('class') === 'pan-row');
  assert.deepEqual(panRow.children.map((n) => n.getAttribute('class')), ['pan-spacer']);
  const buttons = master.children.find((n) => n.getAttribute('class') === 'buttons');
  assert.deepEqual(buttons.children.map((n) => n.getAttribute('class')), ['button-spacer', 'toggle mute', 'button-spacer', 'toggle link on']);
});

test('a linked pair shows its pan greyed out and a drag on it is not a gesture', () => {
  const { document, gestures } = start();
  const pan = find(document, 'pan:0:28');
  assert.equal(pan.getAttribute('class'), 'pan disabled');
  assert.equal(pan.getAttribute('data-gesture'), null);
  fire(pan, 'pointerdown', { timeStamp: 1 });
  assert.deepEqual(gestures, []);
  // an ordinary pan still drags
  fire(find(document, 'pan:0:2'), 'pointerdown', { timeStamp: 2 });
  assert.equal(gestures[0].type, 'drag');
});

test('buttons carry their kind so mute, solo and link can be styled apart', () => {
  const { document } = start();
  assert.equal(find(document, 'mute:0:2').getAttribute('class'), 'toggle mute');
  assert.equal(find(document, 'solo:0:2').getAttribute('class'), 'toggle solo');
  assert.equal(find(document, 'input_link:2').getAttribute('class'), 'toggle link');
});

test('the routing tab draws one titled section per kind of output, each row with its current source and grouped choices', () => {
  const document = fakeDocument();
  FakeSocket.opened = [];
  const row = (label, section, current, value) => ({
    label, section, current, value, control: `route:${label}`,
    options: [{ value: '76-77', label: 'Main 1-2 mix' }, { value: '45-32', label: 'Line 7-8' }],
    groups: [{ label: 'Mixes', options: [{ value: '76-77', label: 'Main 1-2 mix' }] },
      { label: 'Inputs', options: [{ value: '45-32', label: 'Line 7-8' }] }],
  });
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION,
    createClient: ({ render }) => ({
      gesture() {},
      receive() {
        render({ ...view(), mode: 'routing', strips: [], master: null, routing: [
          row('Main 1-2', 'Main & line outs', { label: 'Main 1-2 mix', kind: 'mix' }, '76-77'),
          row('S/PDIF out', 'S/PDIF', { label: 'Line 7-8', kind: 'input' }, '45-32'),
        ] });
      },
    }),
  });
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  const routing = document.getElementById('routing');
  const sections = routing.children;
  assert.deepEqual(sections.map((s) => s.children[0].textContent), ['Main & line outs', 'S/PDIF']);
  const spdif = find(document, 'route:S/PDIF out');
  assert.equal(spdif.value, '45-32');
  assert.deepEqual(spdif.children.map((g) => [g.tagName, g.getAttribute('label')]), [['OPTGROUP', 'Mixes'], ['OPTGROUP', 'Inputs']]);
  const card = spdif.parentNode;
  const pill = card.children[0].children.find((n) => (n.getAttribute('class') ?? '').startsWith('source'));
  assert.equal(pill.getAttribute('class'), 'source input');
  assert.equal(pill.textContent, 'Line 7-8');
});

test('opening the page at #routing selects the Routing tab', () => {
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document: fakeDocument(), WebSocket: FakeSocket, setTimeout() {}, location: { ...PAGE_LOCATION, hash: '#routing' },
    createClient: () => ({ gesture(g) { gestures.push(g); }, receive() {} }),
  });
  assert.deepEqual(gestures, [{ type: 'select_tab', control: 'tab:routing' }]);
});

test('group tabs sit among the strips where each group starts, and a click toggles that group', () => {
  const document = fakeDocument();
  const gestures = [];
  let storageSeen;
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout() {}, location: PAGE_LOCATION, storage: { getItem: () => null, setItem() {} },
    createClient: ({ render, storage }) => {
      storageSeen = storage;
      return {
        gesture(g) { gestures.push(g); },
        receive() {
          render({ ...view(), groupHeads: [
            { key: 'adat', label: 'ADAT', count: 16, collapsed: true, control: 'group:adat', before: 1 },
            { key: 'computer', label: 'Computer', count: 14, collapsed: false, control: 'group:computer', before: 2 },
          ] });
        },
      };
    },
  });
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  assert.ok(storageSeen && typeof storageSeen.getItem === 'function', 'the client is given the browser storage');
  const row = document.getElementById('strips').children.map((n) => n.getAttribute('class').split(' ').slice(0, 2).join(' '));
  assert.deepEqual(row, ['strip', 'group-tab collapsed', 'strip', 'group-tab', 'strip master']);
  const adat = find(document, 'group:adat');
  assert.match(adat.textContent, /ADAT/);
  assert.match(adat.textContent, /16/);
  fire(adat, 'click');
  assert.deepEqual(gestures, [{ type: 'toggle_group', control: 'group:adat' }]);
});
