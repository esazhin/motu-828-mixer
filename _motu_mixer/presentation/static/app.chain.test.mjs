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
    stages: [{ index: 1, bus: 6, carrier: 26, label: 'Ana 5-6', carrierLabel: 'ADAT B 7-8', slot: 6, eq: 'eq:out:6', remove: 'chainrm:0', cls: 'stage-1' }],
    preset: { names: ['Mixing', 'Listening'], name: on ? 'Mixing' : null, edited: false },
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
      eq: { control: 'eq:out:0', on: true }, chain: { control: 'chain', on, preset: on ? { names: ['Mixing'], name: 'Mixing', edited: true } : null },
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
  assert.equal(button.textContent, 'Chain');
  fire(button, 'click');
  assert.deepEqual(gestures, [{ type: 'open_chain' }]);
  const lit = find(start({ open: false, on: true }).document, 'chain');
  assert.equal(lit.textContent, 'Chain', 'the engaged preset goes in the tooltip, not the label');
  assert.equal(lit.getAttribute('title'), 'Monitor chain is on with the Mixing preset (edited): open the chain window');
  assert.equal(lit.getAttribute('class'), 'chain-open on');
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
  assert.match(stages[1].textContent, /carried by ADAT B 7-8/);
  assert.match(stages[2].textContent, /fed by stage 1/);
  assert.match(panel.textContent, /Preset · Mixing/);
  assert.equal(find(document, 'chain-preset-pick').children.length, 3, 'a placeholder and the two saved names');
  assert.equal(find(document, 'chain-preset-pick').value, 'Mixing', 'the engaged preset is picked');
  assert.equal(find(document, 'chain-preset-name').value, 'Mixing');
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
  fire(find(document, 'chain-preset-load'), 'click');            // nothing picked yet: no gesture
  find(document, 'chain-preset-pick').value = 'Listening';
  fire(find(document, 'chain-preset-load'), 'click');
  find(document, 'chain-preset-name').value = ' Room ';
  fire(find(document, 'chain-preset-save'), 'click');
  assert.equal(find(document, 'chain-preset-name').value, ' Room ', 'what was typed survives the redraw');
  assert.equal(find(document, 'chain-preset-pick').value, 'Listening', 'and so does the pick');
  fire(find(document, 'chain-preset-delete'), 'click');
  fire(find(document, 'chain-close'), 'click');
  assert.deepEqual(gestures, [
    { type: 'chain_power', on: true },
    { type: 'chain_remove', index: 0 },
    { type: 'chain_add', bus: 8, carrier: 40 },
    { type: 'chain_load', name: 'Listening' },
    { type: 'chain_save', name: 'Room' },
    { type: 'chain_delete', name: 'Listening' },
    { type: 'close_chain' },
  ]);
});

test('a redraw keeps the chain window where it was scrolled, and a second press on a point within 350 ms zeroes its band', () => {
  const { document, gestures } = start({ on: true });
  const host = document.getElementById('chain');
  const panel = host.children[0];
  panel.scrollTop = 240;
  // Chrome clamps the scroll when the content shrinks mid-redraw (the shared graph moves out before the swap)
  const remove = panel.removeChild.bind(panel);
  panel.removeChild = (child) => { panel.scrollTop = 0; return remove(child); };
  const toggle = find(document, 'eqb:out:6:0:bypass');
  toggle.focus();                                   // a real click leaves the button focused
  fire(toggle, 'click');
  assert.equal(host.children[0], panel, 'the same panel node is refilled');
  assert.equal(panel.scrollTop, 240, 'and put back where it was scrolled, read before the new content was built');
  assert.notEqual(document.activeElement, toggle, 'the removed button was blurred before the refill (Chrome scrolls otherwise)');
  assert.equal(document.activeElement, find(document, 'eqb:out:6:0:bypass'), 'and its successor has the focus');
  assert.notEqual(document.activeElement, document.body);
  gestures.length = 0;
  // the graph captures the pointer during a drag, so this is detected from the presses, not the browser's dblclick
  fire(find(document, 'eqpt:out:6:0'), 'pointerdown', { timeStamp: 1000 });
  fire(find(document, 'eqpt:out:6:0'), 'pointerup', { timeStamp: 1060 });
  fire(find(document, 'eqpt:out:6:0'), 'pointerdown', { timeStamp: 1200 });
  assert.deepEqual(gestures.map((g) => g.type), ['eq_drag', 'release', 'eq_zero']);
  assert.deepEqual(gestures.at(-1), { type: 'eq_zero', control: 'eqpt:out:6:0' });
  // a press on another point, or one later than 350 ms, is a drag
  fire(find(document, 'eqpt:out:6:1'), 'pointerdown', { timeStamp: 1300 });
  fire(find(document, 'eqpt:out:6:1'), 'pointerup', { timeStamp: 1350 });
  fire(find(document, 'eqpt:out:6:1'), 'pointerdown', { timeStamp: 1800 });
  assert.ok(!gestures.slice(3).some((g) => g.type === 'eq_zero'));
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
