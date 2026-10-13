import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createMixerClient } from './mixer_client.js';

const LINE_3 = 2;
const MAIN = 0;
const ANA_3 = 4;
const cell = (bus, input) => bus * 44 + input;

function stateMessage({ fader = {}, status = 'synced' } = {}) {
  const faders = Array(704).fill(0);
  for (const [index, linear] of Object.entries(fader)) faders[index] = linear;
  const inputs = Array(44).fill(0);
  inputs[28] = 1;
  const buses = Array(16).fill(0);
  buses[0] = 1;
  return {
    type: 'state',
    status,
    read_only_reason: null,
    device: { model: '828E', serial: '828E000000', api: 9, firmware: '1.1.8' },
    links: { inputs, buses },
    cells: { fader: faders, pan: Array(704).fill(0.5), mute: Array(704).fill(0), solo: Array(704).fill(0) },
    bus_master: { fader: Array(16).fill(1.0), mute: Array(16).fill(0) },
    routing: { 43: 84, 40: 85 },
    loops: [],
    policy: {},
    layout: {
      tabs: [
        { label: 'Main 1-2', bus: MAIN, stereo: true, pan: [LINE_3, 3] },
        { label: 'Ana 3', bus: ANA_3, stereo: false, pan: [] },
      ],
      strips: [
        { label: 'Line 3', input: LINE_3, pan_visible: true },
        { label: 'Line 4', input: 3, pan_visible: true },
        { label: 'Computer 1-2', input: 28, pan_visible: false },
      ],
      output_pairs: [{ label: 'Line out 9-10', left: 43, right: 40 }],
      sources: [
        { label: 'Main 1-2 mix', left: 76, right: 77 },
        { label: 'Ana 7-8 mix', left: 84, right: 85 },
        { label: 'nothing', left: 127, right: 127 },
      ],
      source_labels: { 84: 'Ana 7 mix', 85: 'Ana 8 mix' },
    },
  };
}

function recordingClient() {
  const sent = [];
  const renders = [];
  // nothing collapsed, so these tests see every strip (group collapsing has its own tests below)
  const storage = { getItem: () => '', setItem() {} };
  const client = createMixerClient({ send: (m) => sent.push(m), render: (v) => renders.push(v), storage });
  return { client, sent, renders, view: () => renders.at(-1) };
}

const strip = (view, label) => view.strips.find((s) => s.label === label);
const MINUS_6_DB_POSITION = 54 / 72;

test('receiving the state renders the full view of the selected tab', () => {
  const { client, sent, renders, view } = recordingClient();
  client.receive(stateMessage({ fader: { [cell(MAIN, LINE_3)]: 1.0 } }));

  assert.equal(renders.length, 1);
  assert.deepEqual(sent, []);
  const v = view();
  assert.equal(v.status, 'synced');
  assert.equal(v.controlsEnabled, true);
  assert.equal(v.banner, null);
  assert.deepEqual(v.tabs, [
    { control: 'tab:0', label: 'Main 1-2', selected: true },
    { control: 'tab:4', label: 'Ana 3', selected: false },
    { control: 'tab:routing', label: 'Routing', selected: false },
  ]);
  assert.equal(v.mode, 'mixer');
  assert.equal(v.panRow, true, 'a stereo bus gives every strip a pan row');
  assert.deepEqual(v.strips.map((s) => s.label), ['Line 3', 'Line 4', 'Computer 1-2']);
  const line3 = strip(v, 'Line 3');
  assert.deepEqual(line3.fader, { control: 'fader:0:2', position: 60 / 72, text: '0.0 dB', meter: { position: 0, zone: 'off' } });
  assert.deepEqual(line3.pan, { control: 'pan:0:2', position: 0.5, text: 'C' });
  assert.deepEqual(line3.mute, { control: 'mute:0:2', on: false });
  assert.deepEqual(line3.solo, { control: 'solo:0:2', on: false });
  assert.deepEqual(line3.link, { control: 'input_link:2', on: false });
  // a linked pair on a stereo bus shows its pan, disabled, as CueMix does (PanFader: enabled = !isStereo)
  assert.deepEqual(strip(v, 'Computer 1-2').pan, { control: 'pan:0:28', position: 0.5, text: 'stereo', disabled: true });
  assert.equal(strip(v, 'Computer 1-2').fader.text, '−∞');
  assert.deepEqual(v.master, {
    label: 'Main 1-2',
    fader: { control: 'master:0', position: 60 / 72, text: '0.0 dB', meter: { position: 0, zone: 'off' } },
    mute: { control: 'master_mute:0', on: false },
    link: { control: 'bus_link:0', on: true },
    eq: null,
  });
  assert.equal(line3.eq, null, 'no EQ data in this state, so no EQ buttons');
  assert.deepEqual(v.routing, [], 'routing lives on its own tab');
});

test('the Routing tab shows the routing rows and no strips; a bus tab shows strips and no routing', () => {
  const { client, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: 'tab:routing' });
  const v = view();
  assert.equal(v.mode, 'routing');
  assert.deepEqual(v.strips, []);
  assert.equal(v.master, null);
  assert.equal(v.tabs.at(-1).selected, true);
  assert.equal(v.tabs[0].selected, false);
  assert.deepEqual(v.routing, [{
    label: 'Line out 9-10',
    control: 'route:43:40',
    value: '84-85',
    section: 'Main & line outs',
    current: { label: 'Ana 7-8 mix', kind: 'mix' },
    options: [
      { value: '76-77', label: 'Main 1-2 mix' },
      { value: '84-85', label: 'Ana 7-8 mix' },
      { value: '127-127', label: 'nothing' },
    ],
    groups: [
      { label: 'Mixes', options: [{ value: '76-77', label: 'Main 1-2 mix' }, { value: '84-85', label: 'Ana 7-8 mix' }] },
      { label: 'Off', options: [{ value: '127-127', label: 'nothing' }] },
    ],
  }]);
});

test('every server message is accepted and renders exactly once', () => {
  const { client, renders } = recordingClient();
  const messages = [
    stateMessage(),
    { type: 'delta', changes: [{ prop: 'fader', index: cell(MAIN, LINE_3), value: 0.5, db: -6.02 }] },
    { type: 'status', status: 'synced', read_only_reason: null, loops: [] },
    { type: 'accepted', accepted: true, sent: ['f0 f7'] },
    { type: 'refused', accepted: false, kind: 'loop', reason: 'Refused: S/PDIF in → Ana 1-2 → S/PDIF out', path: null },
  ];
  messages.forEach((message, i) => {
    client.receive(message);
    assert.equal(renders.length, i + 1, `${message.type} renders once`);
  });
  assert.equal(strip(renders[1], 'Line 3').fader.text, '−6.0 dB');
  assert.equal(renders.at(-1).banner, 'Refused: S/PDIF in → Ana 1-2 → S/PDIF out');
});

test('a drag of Line 3 on Ana 3 to the -6 dB position sends a level change and renders once', () => {
  const { client, sent, renders, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: 'tab:4' });
  const control = strip(view(), 'Line 3').fader.control;
  assert.equal(control, 'fader:4:2');

  client.gesture({ type: 'drag', control, value: MINUS_6_DB_POSITION });

  assert.equal(renders.length, 3);
  assert.deepEqual(sent, [{ type: 'change', action: { kind: 'set_level', bus: 4, input: 2, db: -6.0 } }]);
  assert.equal(strip(view(), 'Line 3').fader.text, '−6.0 dB');
});

test('dragging a pan or a master and to the bottom sends their changes', () => {
  const { client, sent } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'drag', control: 'pan:0:2', value: 0.25 });
  client.gesture({ type: 'drag', control: 'master:0', value: 57 / 72 });
  client.gesture({ type: 'drag', control: 'fader:0:2', value: 0 });
  assert.deepEqual(sent.map((m) => m.action), [
    { kind: 'set_pan', bus: 0, input: 2, pan: 0.25 },
    { kind: 'set_master_level', bus: 0, db: -3.0 },
    { kind: 'set_level', bus: 0, input: 2, db: '-inf' },
  ]);
});

test('a toggle sends the new state of the control it names and renders once', () => {
  const { client, sent, renders, view } = recordingClient();
  client.receive(stateMessage());
  const v = view();
  for (const control of [strip(v, 'Line 3').mute.control, strip(v, 'Line 3').solo.control,
    strip(v, 'Line 3').link.control, v.master.mute.control, v.master.link.control]) {
    client.gesture({ type: 'toggle', control, value: true });
  }
  assert.equal(renders.length, 6);
  assert.deepEqual(sent, [
    { type: 'change', action: { kind: 'set_mute', bus: 0, input: 2, on: true } },
    { type: 'change', action: { kind: 'set_solo', bus: 0, input: 2, on: true } },
    { type: 'change', action: { kind: 'set_input_link', input: 2, on: true } },
    { type: 'change', action: { kind: 'set_master_mute', bus: 0, on: true } },
    { type: 'change', action: { kind: 'set_bus_link', bus: 0, on: true } },
  ]);
});

test('a route choice sends the source pair for the output pair and renders once', () => {
  const { client, sent, renders, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: 'tab:routing' });
  const route = view().routing[0];
  client.gesture({ type: 'route', control: route.control, value: route.options[0].value });
  assert.equal(renders.length, 3);
  assert.deepEqual(sent, [{ type: 'change', action: { kind: 'set_route', output_slots: [43, 40], sources: [76, 77] } }]);
});

test('selecting a tab and releasing a fader send nothing and render once each', () => {
  const { client, sent, renders, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: 'tab:4' });
  assert.equal(renders.length, 2);
  assert.equal(view().tabs[1].selected, true);
  assert.equal(view().master.label, 'Ana 3');
  assert.equal(strip(view(), 'Line 3').pan, null);
  client.gesture({ type: 'release', control: 'fader:4:2' });
  assert.equal(renders.length, 3);
  assert.deepEqual(sent, []);
});

test('a held fader keeps its position while the unit reports another value, then shows that value on release', () => {
  const { client, sent, view } = recordingClient();
  client.receive(stateMessage({ fader: { [cell(MAIN, LINE_3)]: 1.0 } }));
  client.gesture({ type: 'drag', control: 'fader:0:2', value: MINUS_6_DB_POSITION });
  assert.deepEqual(sent.at(-1).action, { kind: 'set_level', bus: 0, input: 2, db: -6.0 });

  client.receive({ type: 'delta', changes: [{ prop: 'fader', index: cell(MAIN, LINE_3), value: 10 ** (-10 / 20), db: -10.0 }] });
  assert.deepEqual(strip(view(), 'Line 3').fader, { control: 'fader:0:2', position: MINUS_6_DB_POSITION, text: '−6.0 dB', meter: { position: 0, zone: 'off' } });

  client.gesture({ type: 'release', control: 'fader:0:2' });
  assert.equal(strip(view(), 'Line 3').fader.text, '−10.0 dB');
  assert.ok(Math.abs(strip(view(), 'Line 3').fader.position - 50 / 72) < 1e-9);
  assert.equal(sent.length, 1);
});

test('a report of linear 5.0 reads +14.0 dB with the slider at the top and sends nothing', () => {
  const { client, sent, view } = recordingClient();
  client.receive(stateMessage({ fader: { [cell(ANA_3, LINE_3)]: 5.0 } }));
  client.gesture({ type: 'select_tab', control: 'tab:4' });
  assert.deepEqual(strip(view(), 'Line 3').fader, { control: 'fader:4:2', position: 1, text: '+14.0 dB', meter: { position: 0, zone: 'off' } });
  assert.deepEqual(sent, []);
});

test('a refused change snaps the fader back to the unit value and shows the refusal', () => {
  const { client, view } = recordingClient();
  client.receive(stateMessage({ fader: { [cell(MAIN, LINE_3)]: 1.0 } }));
  client.gesture({ type: 'drag', control: 'fader:0:2', value: MINUS_6_DB_POSITION });
  client.receive({ type: 'refused', accepted: false, kind: 'read_only', reason: 'Refused: API version 10, expected 9', path: null });
  assert.equal(strip(view(), 'Line 3').fader.text, '0.0 dB');
  assert.equal(view().banner, 'Refused: API version 10, expected 9');
});

test('status banners name the read-only reason, the connection state and a present loop, and disable controls', () => {
  const { client, sent, view } = recordingClient();
  client.receive(stateMessage());
  client.receive({ type: 'status', status: 'read_only', read_only_reason: 'API version 10, expected 9', loops: [] });
  assert.equal(view().banner, 'Read-only: API version 10, expected 9');
  assert.equal(view().controlsEnabled, false);
  client.gesture({ type: 'drag', control: 'fader:0:2', value: MINUS_6_DB_POSITION });
  assert.deepEqual(sent, []);
  assert.equal(strip(view(), 'Line 3').fader.position, 0);

  client.receive({ type: 'status', status: 'syncing', read_only_reason: null, loops: [] });
  assert.equal(view().banner, 'syncing');
  client.receive({ type: 'status', status: 'no_device', read_only_reason: null, loops: [] });
  assert.equal(view().banner, 'no 828 connected');
  client.receive({ type: 'status', status: 'synced', read_only_reason: null, loops: [['S/PDIF in', 'Ana 1-2', 'S/PDIF out']] });
  assert.equal(view().banner, 'Loop present: S/PDIF in → Ana 1-2 → S/PDIF out');
  assert.equal(view().controlsEnabled, true);
});

test('a reset on a fader, a pan or a master sends 0 dB or centre for that control and renders once', () => {
  const { client, sent, renders } = recordingClient();
  client.receive(stateMessage());
  sent.length = 0;
  renders.length = 0;
  client.gesture({ type: 'reset', control: `fader:${MAIN}:${LINE_3}` });
  client.gesture({ type: 'reset', control: `pan:${MAIN}:${LINE_3}` });
  client.gesture({ type: 'reset', control: `master:${MAIN}` });
  assert.deepEqual(sent, [
    { type: 'change', action: { kind: 'set_level', bus: MAIN, input: LINE_3, db: 0 } },
    { type: 'change', action: { kind: 'set_pan', bus: MAIN, input: LINE_3, pan: 0.5 } },
    { type: 'change', action: { kind: 'set_master_level', bus: MAIN, db: 0 } },
  ]);
  assert.equal(renders.length, 3);
});

test('meters go to renderMeters on the fader scale with a zone, never to a full render', () => {
  const renders = [];
  const meterRenders = [];
  const client = createMixerClient({ send() {}, render: (v) => renders.push(v), renderMeters: (m) => meterRenders.push(m) });
  client.receive(stateMessage());
  renders.length = 0;
  const inputs = Array(44).fill(null);
  inputs[LINE_3] = -12;          // -12 dBFS sits at the fader's -12 dB mark
  inputs[3] = -0.5;              // clipping
  const buses = Array(16).fill(null);
  buses[MAIN] = -24;
  client.receive({ type: 'meters', inputs, buses });
  assert.equal(renders.length, 0);
  assert.equal(meterRenders.length, 1);
  const meters = meterRenders[0];
  assert.deepEqual(meters[`fader:${MAIN}:${LINE_3}`], { position: 48 / 72, zone: 'warm' });
  assert.deepEqual(meters[`fader:${MAIN}:3`], { position: 59.5 / 72, zone: 'clip' });
  assert.deepEqual(meters[`fader:${MAIN}:28`], { position: 0, zone: 'off' });
  assert.deepEqual(meters[`master:${MAIN}`], { position: 36 / 72, zone: 'ok' });
  // a full redraw afterwards carries the same meter, so it does not blink out
  client.gesture({ type: 'select_tab', control: `tab:${MAIN}` });
  assert.deepEqual(strip(renders.at(-1), 'Line 3').fader.meter, { position: 48 / 72, zone: 'warm' });
});

test('a mono bus has no pan row and no pans', () => {
  const { client, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: `tab:${ANA_3}` });
  assert.equal(view().panRow, false);
  assert.ok(view().strips.every((s) => s.pan === null));
});

test('a disabled pan ignores drags and resets', () => {
  const { client, sent } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'drag', control: `pan:${MAIN}:28`, value: 0.2 });
  client.gesture({ type: 'reset', control: `pan:${MAIN}:28` });
  assert.deepEqual(sent, []);
});

test('routing rows fall into sections and each shows what feeds it, by kind', () => {
  const { client, view } = recordingClient();
  const state = stateMessage();
  state.layout.output_pairs = [
    { label: 'Main 1-2', left: 37, right: 39 }, { label: 'Phones 1', left: 47, right: 57 },
    { label: 'S/PDIF out', left: 58, right: 59 }, { label: 'ADAT A out 1-2', left: 60, right: 61 },
    { label: 'Computer in 1-2', left: 0, right: 1 },
  ];
  state.layout.sources.push({ label: 'Line 7-8', left: 45, right: 32 }, { label: 'ADAT A 1-2', left: 60, right: 61 },
    { label: 'Computer out 1-2', left: 0, right: 1 }, { label: 'S/PDIF in L-R', left: 58, right: 59 });
  state.layout.source_labels = { 45: 'Line 7', 32: 'Line 8', 0: 'Computer out 1', 1: 'Computer out 2' };
  state.routing = { 37: 76, 39: 77, 47: 127, 57: 127, 58: 45, 59: 32, 60: 0, 61: 1, 0: 58, 1: 59 };
  client.receive(state);
  client.gesture({ type: 'select_tab', control: 'tab:routing' });
  const rows = Object.fromEntries(view().routing.map((r) => [r.label, r]));
  assert.equal(rows['Main 1-2'].section, 'Main & line outs');
  assert.equal(rows['Phones 1'].section, 'Phones');
  assert.equal(rows['S/PDIF out'].section, 'S/PDIF');
  assert.equal(rows['ADAT A out 1-2'].section, 'ADAT');
  assert.equal(rows['Computer in 1-2'].section, 'Computer');
  assert.deepEqual(rows['S/PDIF out'].current, { label: 'Line 7-8', kind: 'input' });
  assert.deepEqual(rows['Main 1-2'].current, { label: 'Main 1-2 mix', kind: 'mix' });
  assert.deepEqual(rows['Phones 1'].current, { label: 'nothing', kind: 'off' });
  assert.deepEqual(rows['ADAT A out 1-2'].current, { label: 'Computer out 1-2', kind: 'computer' });
  assert.deepEqual(rows['Computer in 1-2'].current, { label: 'S/PDIF in L-R', kind: 'input' });
  assert.deepEqual(rows['S/PDIF out'].groups.map((g) => g.label), ['Mixes', 'Inputs', 'ADAT', 'Computer', 'Off']);
});

// ---- EQ ----
function eqState() {
  const state = stateMessage();
  const col = (n, v) => Array(n).fill(v);
  state.eq = {
    output: { mode: col(42, 0), bypass: col(42, 1), freq: col(42, 1000), gain: col(42, 0), q: col(42, 1) },
    input: { mode: col(40, 0), bypass: col(40, 1), freq: col(40, 1000), gain: col(40, 0), q: col(40, 1) },
  };
  // Main 1-2 (slot 0): the live EQ of 2026-10-12
  [[0, 0, 200, -3.0, 6.0], [1, 0, 353, -5.6, 2.0], [2, 2, 3543, -2.7, 3.0]].forEach(([b, mode, freq, gain, q]) => {
    state.eq.output.mode[b] = mode; state.eq.output.bypass[b] = 0; state.eq.output.freq[b] = freq;
    state.eq.output.gain[b] = gain; state.eq.output.q[b] = q;
  });
  return state;
}

test('the master strip opens its bus EQ and Mic/Line strips their input EQ; other inputs have none', () => {
  const { client, view } = recordingClient();
  client.receive(eqState());
  assert.deepEqual(view().master.eq, { control: 'eq:out:0', on: true });
  assert.deepEqual(strip(view(), 'Line 3').eq, { control: 'eq:in:2', on: false });
  assert.equal(strip(view(), 'Computer 1-2').eq, null);
});

test('opening the Main EQ shows its three bands, their allowed modes and the summed curve', () => {
  const { client, view } = recordingClient();
  client.receive(eqState());
  client.gesture({ type: 'open_eq', control: 'eq:out:0' });
  const eq = view().eq;
  assert.equal(eq.title, 'Main 1-2 EQ');
  assert.equal(eq.bands.length, 3);
  assert.deepEqual(eq.bands.map((b) => [b.mode, b.freq, b.gain, b.q, b.bypass]),
    [[0, 200, -3.0, 6.0, 0], [0, 353, -5.6, 2.0, 0], [2, 3543, -2.7, 3.0, 0]]);
  assert.deepEqual(eq.bands.map((b) => b.modes.map((m) => m.value)), [[0, 1, 3], [0], [0, 2]]);
  assert.deepEqual(eq.bands.map((b) => [b.gainEnabled, b.qEnabled]), [[true, true], [true, true], [true, false]]);
  assert.equal(eq.curve.freqs.length, eq.curve.db.length);
  const at = (f) => eq.curve.db[eq.curve.freqs.reduce((best, x, i) => Math.abs(Math.log(x / f)) < Math.abs(Math.log(eq.curve.freqs[best] / f)) ? i : best, 0)];
  assert.ok(at(10000) < -2 && at(10000) > -4, 'the high shelf holds the top ~-2.7 dB');
  client.gesture({ type: 'close_eq' });
  assert.equal(view().eq, null);
});

test('editing a band sends that one property within range and shows it at once', () => {
  const { client, sent, view } = recordingClient();
  client.receive(eqState());
  client.gesture({ type: 'open_eq', control: 'eq:out:0' });
  sent.length = 0;
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:1:gain', value: -4.56 });
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:1:gain', value: 30 });
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:0:freq', value: 12.4 });
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:2:q', value: 0.1 });
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:0:mode', value: 3 });
  client.gesture({ type: 'eq_set', control: 'eqb:out:0:2:bypass', value: true });
  assert.deepEqual(sent.map((m) => m.action), [
    { kind: 'set_property', prop: 1025, index: 1, value: -4.6 },
    { kind: 'set_property', prop: 1025, index: 1, value: 20 },
    { kind: 'set_property', prop: 1024, index: 0, value: 20 },
    { kind: 'set_property', prop: 1026, index: 2, value: 0.45 },
    { kind: 'set_property', prop: 1022, index: 0, value: 3 },
    { kind: 'set_property', prop: 1023, index: 2, value: 1 },
  ]);
  assert.equal(view().eq.bands[1].gain, 20);
  assert.equal(view().eq.bands[0].mode, 3);
  assert.equal(view().eq.bands[0].gainEnabled, false, 'a high-pass has no gain');
});

test('dragging a band point sends its frequency and gain; a high-pass point sends only its frequency', () => {
  const { client, sent } = recordingClient();
  client.receive(eqState());
  client.gesture({ type: 'open_eq', control: 'eq:in:2' });
  sent.length = 0;
  client.gesture({ type: 'eq_drag', control: 'eqpt:in:2:1', freq: 2513.7, gain: 4.04 });
  assert.deepEqual(sent.map((m) => m.action), [
    { kind: 'set_property', prop: 1004, index: 9, value: 2514 },
    { kind: 'set_property', prop: 1005, index: 9, value: 4 },
  ]);
  client.gesture({ type: 'eq_set', control: 'eqb:in:2:0:mode', value: 3 });
  sent.length = 0;
  client.gesture({ type: 'eq_drag', control: 'eqpt:in:2:0', freq: 80, gain: 9 });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_property', prop: 1004, index: 8, value: 80 }]);
});

test('an EQ change reported by the unit updates the open panel', () => {
  const { client, view } = recordingClient();
  client.receive(eqState());
  client.gesture({ type: 'open_eq', control: 'eq:out:0' });
  client.receive({ type: 'delta', changes: [{ prop: 'out_eq_gain', index: 0, value: 2.5 }] });
  assert.equal(view().eq.bands[0].gain, 2.5);
});

test('a linked bus pair opens its first slot, as CueMix does', () => {
  const { client, view } = recordingClient();
  const state = eqState();
  state.layout.tabs.push({ label: 'Ana 1-2', bus: 2, stereo: true, pan: [] });
  state.links.buses[2] = 1;
  client.receive(state);
  client.gesture({ type: 'select_tab', control: 'tab:3' });
  assert.equal(view().master.eq.control, 'eq:out:2');
});

// ---- collapsible groups ----
function groupState() {
  const state = stateMessage();
  state.layout.strips = [
    { label: 'Line 3', input: 2 }, { label: 'ADAT A 1', input: 12 }, { label: 'ADAT A 2', input: 13 },
    { label: 'ADAT B 8', input: 27 }, { label: 'Computer 1-2', input: 28 }, { label: 'Computer 3', input: 30 },
    { label: 'Reverb L-R', input: 42 },
  ];
  return state;
}

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

test('ADAT and Computer strips start collapsed behind a group tab at the place they would start', () => {
  const renders = [];
  const client = createMixerClient({ send() {}, render: (v) => renders.push(v), storage: memoryStorage() });
  client.receive(groupState());
  const v = renders.at(-1);
  assert.deepEqual(v.strips.map((s) => s.label), ['Line 3', 'Reverb L-R']);
  assert.deepEqual(v.groupHeads, [
    { key: 'adat', label: 'ADAT', count: 3, collapsed: true, control: 'group:adat', before: 1 },
    { key: 'computer', label: 'Computer', count: 2, collapsed: true, control: 'group:computer', before: 1 },
  ]);
});

test('toggling a group shows its strips in place, and the choice is remembered across pages', () => {
  const storage = memoryStorage();
  const renders = [];
  const client = createMixerClient({ send() {}, render: (v) => renders.push(v), storage });
  client.receive(groupState());
  client.gesture({ type: 'toggle_group', control: 'group:adat' });
  let v = renders.at(-1);
  assert.deepEqual(v.strips.map((s) => s.label), ['Line 3', 'ADAT A 1', 'ADAT A 2', 'ADAT B 8', 'Reverb L-R']);
  assert.deepEqual(v.groupHeads.map((g) => [g.key, g.collapsed, g.before]), [['adat', false, 1], ['computer', true, 4]]);
  assert.equal(storage.data['motu-mixer:collapsed'], 'computer');

  // a fresh page with the same storage starts with ADAT open
  const later = [];
  const again = createMixerClient({ send() {}, render: (x) => later.push(x), storage });
  again.receive(groupState());
  assert.deepEqual(later.at(-1).groupHeads.map((g) => g.collapsed), [false, true]);
});

test('a storage that throws (private mode) falls back to the defaults', () => {
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const renders = [];
  const client = createMixerClient({ send() {}, render: (v) => renders.push(v), storage: broken });
  client.receive(groupState());
  client.gesture({ type: 'toggle_group', control: 'group:computer' });
  assert.deepEqual(renders.at(-1).groupHeads.map((g) => g.collapsed), [true, false]);
});
