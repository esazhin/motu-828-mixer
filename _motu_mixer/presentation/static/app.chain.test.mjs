import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';
import { FakeSocket, PAGE_LOCATION, fakeDocument, find, fire } from './fake_browser.mjs';

const band = (slot, b, stage, mode, freq, gain) => ({
  band: b, index: slot * 3 + b, mode, freq, gain, q: 1, bypass: 0, control: `eqb:out:${slot}:${b}`, point: `eqpt:out:${slot}:${b}`,
  modes: [{ value: 0, label: 'Peak' }], gainEnabled: true, qEnabled: true, stage, cls: `stage-${stage}`,
});

function chain(on) {
  const main = [band(0, 0, 0, 0, 200, -3), band(0, 1, 0, 0, 353, -5.6), band(0, 2, 0, 0, 3543, -2.7)];
  const stage = [band(6, 0, 1, 1, 60, -4), band(6, 1, 1, 0, 1000, 0), band(6, 2, 1, 0, 8000, 0)];
  return {
    on,
    stages: [{ index: 1, bus: 6, carrier: 42, label: 'Ana 5-6', carrierLabel: 'Reverb return L-R', slot: 6, eq: 'eq:out:6', remove: 'chainrm:0', cls: 'stage-1' }],
    canAdd: true,
    stageOptions: [{ value: 2, label: 'Ana 1-2 (feeds Line out 3-4)' }, { value: 8, label: 'Ana 7-8' }],
    carrierOptions: [{ value: 40, label: 'Computer 13-14' }],
    sections: [{ index: 0, label: 'Main 1-2', slot: 0, cls: 'stage-0', bands: main }, { index: 1, label: 'Ana 5-6', slot: 6, cls: 'stage-1', bands: stage }],
    curve: { freqs: [20, 200, 2000, 20000], db: [-4, -3, -1, -2.7] },
    bands: [...main, ...stage],
  };
}

function view({ open = true, on = false, mode = 'mixer' } = {}) {
  return {
    status: 'synced', statusText: 'synced', banner: null, controlsEnabled: true, mode, panRow: false,
    tabs: [{ control: 'tab:0', label: 'Main 1-2', selected: true }],
    strips: [], routing: [], eq: null, inputs: [],
    master: {
      label: 'Main 1-2', fader: { control: 'master:0', position: 0.83, text: '0.0 dB' },
      mute: { control: 'master_mute:0', on: false }, link: { control: 'bus_link:0', on: true },
      eq: { control: 'eq:out:0', on: true }, chain: { control: 'chain', on },
    },
    chain: open ? chain(on) : null,
  };
}

function start(options = {}) {
  const document = fakeDocument();
  const gestures = [];
  const fetched = [];
  const timers = [];
  FakeSocket.opened = [];
  startApp({
    document, WebSocket: FakeSocket, setTimeout: (fn, ms) => timers.push([fn, ms]), location: PAGE_LOCATION,
    fetch: (url, init) => { fetched.push([url, init?.method]); return Promise.resolve({}); },
    createClient: ({ render }) => ({ gesture(g) { gestures.push(g); render(view(options)); }, receive() { render(view(options)); } }),
  });
  FakeSocket.opened.at(-1).emit('open');
  FakeSocket.opened.at(-1).emit('message', { data: JSON.stringify({ type: 'state' }) });
  return { document, gestures, fetched, timers };
}

const all = (node) => node.children.flatMap((c) => [c, ...all(c)]);
const byClass = (node, prefix) => all(node).filter((n) => (n.getAttribute('class') ?? '').startsWith(prefix));

test('the Main master has a Chain button that opens the chain window', () => {
  const { document, gestures } = start({ open: false });
  const button = find(document, 'chain');
  assert.equal(button.getAttribute('class'), 'chain-open');
  assert.equal(document.body.getAttribute('data-chain'), 'closed');
  fire(button, 'click');
  assert.deepEqual(gestures, [{ type: 'open_chain' }]);
});

test('the chain window shows the switch, each stage with its carrier, the picker and the combined EQ by stage', () => {
  const { document } = start({ on: true });
  assert.equal(document.body.getAttribute('data-chain'), 'open');
  const panel = document.getElementById('chain');
  assert.equal(find(document, 'chain-power').textContent, 'Chain ON');
  assert.equal(find(document, 'chain-power').getAttribute('class'), 'chain-power on');
  assert.equal(find(document, 'chain').getAttribute('class'), 'chain-open on');
  const stages = byClass(panel, 'chain-stage ');
  assert.deepEqual(stages.map((s) => s.getAttribute('class')), ['chain-stage stage-0', 'chain-stage stage-1', 'chain-stage chain-jacks']);
  assert.match(stages[1].textContent, /1 · Ana 5-6/);
  assert.match(stages[1].textContent, /carried by Reverb return L-R/);
  assert.match(stages[2].textContent, /fed by stage 1/);
  assert.equal(find(document, 'eq:out:6').getAttribute('data-gesture'), 'open_eq');
  assert.equal(find(document, 'chainrm:0').getAttribute('data-gesture'), 'chain_remove');
  assert.equal(find(document, 'chain-add-bus').tagName, 'SELECT');
  assert.equal(find(document, 'chain-add-bus').children.length, 3, 'a placeholder and two free pairs');
  assert.match(panel.textContent, /Combined EQ · 6 bands/);
  assert.equal(all(panel).filter((n) => (n.getAttribute('data-gesture') ?? '') === 'drag-eq').length, 6, 'one draggable point per band of every stage');
  assert.equal(find(document, 'eqpt:out:6:0').getAttribute('class'), 'eq-point stage-1');
  assert.equal(find(document, 'eqpt:out:0:0').getAttribute('class'), 'eq-point stage-0');
  assert.deepEqual(byClass(panel, 'chain-section ').map((s) => s.getAttribute('class')), ['chain-section stage-0', 'chain-section stage-1']);
  assert.equal(find(document, 'eqb:out:6:0:gain').value, '-4');
});

test('the window\'s controls give their gestures: power, remove, add with the picked pairs, close', () => {
  const { document, gestures } = start({ on: false });
  fire(find(document, 'chain-power'), 'click');
  fire(find(document, 'chainrm:0'), 'click');
  fire(find(document, 'chain-add'), 'click');                     // nothing picked yet: no gesture
  find(document, 'chain-add-bus').value = '8';
  find(document, 'chain-add-carrier').value = '40';
  fire(find(document, 'chain-add'), 'click');
  fire(find(document, 'chain-close'), 'click');
  assert.deepEqual(gestures, [
    { type: 'chain_power', on: true },
    { type: 'chain_remove', index: 0 },
    { type: 'chain_add', bus: 8, carrier: 40 },
    { type: 'close_chain' },
  ]);
});

test('a stage band in the combined view edits that stage\'s own property', () => {
  const { document, gestures } = start({ on: true });
  const gain = find(document, 'eqb:out:6:0:gain');
  gain.value = '-6';
  fire(gain, 'change');
  assert.deepEqual(gestures, [{ type: 'eq_set', control: 'eqb:out:6:0:gain', value: '-6' }]);
});

test('while the Inputs page shows, the page asks the unit for its state every three seconds, and stops when it hides', () => {
  const { fetched, timers } = start({ open: false, mode: 'inputs' });
  assert.deepEqual(fetched, [['/api/resync', 'POST']]);
  assert.equal(timers.at(-1)[1], 3000);
  timers.at(-1)[0]();
  assert.equal(fetched.length, 2);
  // switching away: the next tick finds the page hidden and stops
  const { fetched: none } = start({ open: false, mode: 'mixer' });
  assert.deepEqual(none, []);
});

test('opening the page at #chain opens the chain window', () => {
  const gestures = [];
  FakeSocket.opened = [];
  startApp({
    document: fakeDocument(), WebSocket: FakeSocket, setTimeout() {}, location: { ...PAGE_LOCATION, hash: '#chain' },
    createClient: () => ({ gesture(g) { gestures.push(g); }, receive() {} }),
  });
  assert.deepEqual(gestures, [{ type: 'open_chain' }]);
});
