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
    { control: 'tab:inputs', label: 'Inputs', selected: false },
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
    chain: null,
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

// ---- the Inputs page: analog input block ----
function inputsState() {
  const state = stateMessage();
  state.inputs = {   // the live capture of 2026-10-12: Mic 47/42 dB, Line 3-10 17 16 12 12 12 12 9 9
    gain: [47, 42, 17, 16, 12, 12, 12, 12, 9, 9, 0, 0],
    phase: [0, 0, 0, 1, 0, 0, 0, 0, 0, 0],
    pad: [0, 1], phantom: [1, 0], jack: [0, 1], return_detect: [0, 1],
  };
  return state;
}

test('the Inputs tab shows one strip per analog input: gain on its own scale with its meter, polarity, pad and 48V on the mics', () => {
  const { client, view } = recordingClient();
  client.receive(inputsState());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  const v = view();
  assert.equal(v.mode, 'inputs');
  assert.deepEqual(v.strips, []);
  assert.deepEqual(v.tabs.map((t) => [t.control, t.selected]), [['tab:0', false], ['tab:4', false], ['tab:inputs', true], ['tab:routing', false]]);
  assert.deepEqual(v.inputs.map((r) => r.label), ['Mic 1', 'Mic 2', 'Line 3', 'Line 4', 'Line 5', 'Line 6', 'Line 7', 'Line 8', 'Line 9', 'Line 10']);
  const mic1 = v.inputs[0];
  const off = { position: 0, zone: 'off' };
  assert.deepEqual(mic1.gain, { control: 'ingain:0', field: 'ingainf:0', position: 47 / 74, value: 47, max: 74, text: '47 dB', meter: off });
  assert.deepEqual(mic1.phantom, { control: 'inphantom:0', on: true, enabled: true });
  assert.deepEqual(mic1.pad, { control: 'inpad:0', on: false, enabled: true });
  assert.equal(mic1.jack, 'mic jack');
  assert.equal(mic1.insert, null);
  const mic2 = v.inputs[1];
  assert.equal(mic2.jack, 'line / instrument jack');
  assert.deepEqual(mic2.pad, { control: 'inpad:1', on: true, enabled: false }, 'a line jack greys the pad out, as CueMix does');
  assert.deepEqual(mic2.insert, { control: 'ingain:11', field: 'ingainf:11', position: 0, value: 0, max: 20, text: '0 dB' }, 'a return has no meter');
  const line4 = v.inputs[3];
  assert.deepEqual(line4.gain, { control: 'ingain:3', field: 'ingainf:3', position: 16 / 20, value: 16, max: 20, text: '16 dB', meter: off });
  assert.deepEqual(line4.phase, { control: 'inphase:3', on: true });
  assert.equal(line4.pad, null);
  assert.equal(line4.phantom, null);
  client.gesture({ type: 'select_tab', control: 'tab:0' });
  assert.equal(view().mode, 'mixer');
});

test('gain, polarity and pad controls send whole-dB and 0/1 property writes within range; 48V off needs no confirmation', () => {
  const { client, sent, view } = recordingClient();
  client.receive(inputsState());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  sent.length = 0;
  client.gesture({ type: 'drag', control: 'ingain:0', value: 0.5 });
  assert.equal(view().inputs[0].gain.text, '37 dB', 'the slider shows where it is held');
  client.gesture({ type: 'release', control: 'ingain:0' });
  client.gesture({ type: 'input_set', control: 'ingainf:2', value: '18.4' });
  client.gesture({ type: 'input_set', control: 'ingainf:2', value: '99' });
  client.gesture({ type: 'input_set', control: 'ingainf:2', value: 'x' });
  client.gesture({ type: 'toggle', control: 'inphase:5', value: true });
  client.gesture({ type: 'toggle', control: 'inpad:0', value: true });
  client.gesture({ type: 'toggle', control: 'inphantom:0', value: false });
  assert.deepEqual(sent.map((m) => m.action), [
    { kind: 'set_property', prop: 5001, index: 0, value: 37 },
    { kind: 'set_property', prop: 5001, index: 2, value: 18 },
    { kind: 'set_property', prop: 5001, index: 2, value: 20 },
    { kind: 'set_property', prop: 5002, index: 5, value: 1 },
    { kind: 'set_property', prop: 5003, index: 0, value: 1 },
    { kind: 'set_property', prop: 5004, index: 0, value: 0 },
  ]);
  assert.equal(view().confirm, null);
});

test('switching 48V on asks first: cancel sends nothing, confirming sends the write', () => {
  const { client, sent, view } = recordingClient();
  client.receive(inputsState());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  sent.length = 0;
  client.gesture({ type: 'toggle', control: 'inphantom:1', value: true });
  assert.deepEqual(sent, [], 'nothing goes to the unit until confirmed');
  assert.equal(view().confirm.title, 'Switch 48V on for Mic 2?');
  assert.equal(view().confirm.control, 'inphantom:1');
  assert.match(view().confirm.text, /ribbon/);
  client.gesture({ type: 'cancel_confirm' });
  assert.equal(view().confirm, null);
  assert.deepEqual(sent, []);
  client.gesture({ type: 'toggle', control: 'inphantom:1', value: true });
  client.gesture({ type: 'confirm', remember: false });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_property', prop: 5004, index: 1, value: 1 }]);
  assert.equal(view().confirm, null);
  client.gesture({ type: 'toggle', control: 'inphantom:1', value: true });
  assert.ok(view().confirm, 'without "don\'t ask again" the next switch-on asks again');
  client.gesture({ type: 'confirm', remember: false });
});

test('"don\'t ask again" is kept per browser and skips the 48V confirmation from then on', () => {
  const sent = [];
  const renders = [];
  const storage = memoryStorage({ 'motu-mixer:collapsed': '' });
  const client = createMixerClient({ send: (m) => sent.push(m), render: (v) => renders.push(v), storage });
  client.receive(inputsState());
  client.gesture({ type: 'toggle', control: 'inphantom:0', value: true });
  client.gesture({ type: 'confirm', remember: true });
  assert.equal(storage.data['motu-mixer:ask-48v'], 'off');
  sent.length = 0;
  client.gesture({ type: 'toggle', control: 'inphantom:1', value: true });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_property', prop: 5004, index: 1, value: 1 }]);
  assert.equal(renders.at(-1).confirm, null);
  // a fresh page in the same browser remembers it
  const again = createMixerClient({ send: (m) => sent.push(m), render() {}, storage });
  again.receive(inputsState());
  sent.length = 0;
  again.gesture({ type: 'toggle', control: 'inphantom:0', value: true });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_property', prop: 5004, index: 0, value: 1 }]);
});

test('a 48V confirmation given after the connection dropped sends nothing', () => {
  const { client, sent, view } = recordingClient();
  client.receive(inputsState());
  client.gesture({ type: 'toggle', control: 'inphantom:0', value: true });
  client.receive({ type: 'status', status: 'disconnected', read_only_reason: null });
  sent.length = 0;
  client.gesture({ type: 'confirm', remember: false });
  assert.deepEqual(sent, []);
  assert.equal(view().confirm, null);
});

test('input block changes reported by the unit update the Inputs page, and nothing is sent while not synced', () => {
  const { client, sent, view } = recordingClient();
  client.receive(inputsState());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  client.receive({ type: 'delta', changes: [{ prop: 'in_gain', index: 9, value: 10 }, { prop: 'in_phantom', index: 1, value: 1 }, { prop: 'in_return_detect', index: 0, value: 1 }] });
  assert.equal(view().inputs[9].gain.value, 10);
  assert.equal(view().inputs[1].phantom.on, true);
  assert.equal(view().inputs[0].insert.control, 'ingain:10');
  client.receive({ type: 'status', status: 'syncing', read_only_reason: null });
  sent.length = 0;
  client.gesture({ type: 'toggle', control: 'inphase:2', value: true });
  client.gesture({ type: 'input_set', control: 'ingainf:2', value: '5' });
  assert.deepEqual(sent, []);
});

test('on the Inputs page the live meters go to the input faders', () => {
  const meterMaps = [];
  const client = createMixerClient({ send() {}, render() {}, renderMeters: (m) => meterMaps.push(m), storage: { getItem: () => '', setItem() {} } });
  client.receive(inputsState());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  const inputs = Array(44).fill(null);
  inputs[0] = -3;       // Mic 1 hot
  inputs[9] = -30;      // Line 10 quiet
  client.receive({ type: 'meters', inputs, buses: Array(16).fill(null) });
  const map = meterMaps.at(-1);
  assert.deepEqual(Object.keys(map), Array.from({ length: 10 }, (_, i) => `ingain:${i}`));
  assert.equal(map['ingain:0'].zone, 'hot');
  assert.equal(map['ingain:9'].zone, 'ok');
  assert.equal(map['ingain:1'].zone, 'off');
});

test('a state without the input block shows an empty Inputs page', () => {
  const { client, view } = recordingClient();
  client.receive(stateMessage());
  client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  assert.deepEqual(view().inputs, []);
});

// ---- the monitor chain ----
function chainState({ on = false } = {}) {
  const state = eqState();
  state.layout.tabs.push({ label: 'Ana 5-6', bus: 6, stereo: true, pan: [] });
  state.links.buses[6] = 1;
  const stageOptions = [[2, 'Ana 1-2', ['Line out 3-4'], 0], [4, 'Ana 3-4', [], 1], [6, 'Ana 5-6', [], 0], [8, 'Ana 7-8', ['Line out 9-10'], 4]];
  state.chain = {
    on,
    stages: on ? [{ bus: 6, carrier: 26, label: 'Ana 5-6', carrier_label: 'ADAT B 7-8' }] : [],
    max_stages: 4,
    stage_options: stageOptions.map(([bus, label, feeds, live_inputs]) => ({ bus, label, feeds, live_inputs })),
    carrier_options: [{ input: 40, label: 'Computer 13-14', live: true }, { input: 26, label: 'ADAT B 7-8', live: false }],
  };
  // Ana 5-6's EQ (slot 6): a 60 Hz low shelf at -4 dB
  state.eq.output.mode[18] = 1; state.eq.output.bypass[18] = 0; state.eq.output.freq[18] = 60; state.eq.output.gain[18] = -4;
  return state;
}

test('the Main tab master carries a Chain button lit when a chain is on; other tabs have none', () => {
  const { client, view } = recordingClient();
  client.receive(chainState());
  assert.deepEqual(view().master.chain, { control: 'chain', on: false, preset: null });
  client.receive(chainState({ on: true }));
  assert.deepEqual(view().master.chain, { control: 'chain', on: true, preset: { names: [], name: null, edited: false } });
  client.gesture({ type: 'select_tab', control: 'tab:4' });
  assert.equal(view().master.chain, null);
  client.receive(stateMessage());
  assert.equal(view().master.chain, null, 'a server without the chain shows no button');
});

test('the chain window lists Main, the draft stages and the jacks, offers only unused pairs, and sums every stage\'s EQ', () => {
  const { client, view } = recordingClient();
  client.receive(chainState());
  assert.equal(view().chain, null);
  client.gesture({ type: 'open_chain' });
  const chain = view().chain;
  assert.equal(chain.on, false);
  assert.deepEqual(chain.stages.map((s) => [s.index, s.bus, s.carrier, s.label, s.carrierLabel, s.eq, s.remove, s.cls]),
    [[1, 6, 26, 'Ana 5-6', 'ADAT B 7-8', 'eq:out:6', 'chainrm:0', 'stage-1']], 'the default draft: Ana 5-6 carried by ADAT B 7-8');
  assert.equal(chain.canAdd, true);
  assert.deepEqual(chain.stageOptions, [
    { value: 2, label: 'Ana 1-2 (feeds Line out 3-4)' }, { value: 4, label: 'Ana 3-4 (1 live input soloed out while on)' },
    { value: 8, label: 'Ana 7-8 (feeds Line out 9-10; 4 live inputs soloed out while on)' },
  ]);
  assert.deepEqual(chain.carrierOptions, [{ value: 40, label: 'Computer 13-14 (in use)' }]);
  assert.deepEqual(chain.sections.map((s) => [s.index, s.label, s.slot, s.cls, s.bands.length]), [[0, 'Main 1-2', 0, 'stage-0', 3], [1, 'Ana 5-6', 6, 'stage-1', 3]]);
  assert.equal(chain.bands.length, 6);
  assert.deepEqual(chain.bands.map((b) => b.control).slice(3), ['eqb:out:6:0', 'eqb:out:6:1', 'eqb:out:6:2']);
  assert.ok(chain.bands.every((b) => b.cls === STAGE(b.stage)));
  const at = (f) => chain.curve.db[chain.curve.freqs.reduce((best, x, i) => Math.abs(Math.log(x / f)) < Math.abs(Math.log(chain.curve.freqs[best] / f)) ? i : best, 0)];
  assert.ok(at(30) < -3 && at(30) > -5, `the stage's low shelf shows in the summed curve (${at(30)})`);
  assert.ok(at(10000) < -2 && at(10000) > -4, 'and so does Main\'s high shelf');
  client.gesture({ type: 'close_chain' });
  assert.equal(view().chain, null);
});
const STAGE = (k) => `stage-${k}`;

test('the chain switch sends set_chain with the draft stages, and off sends set_chain off', () => {
  const { client, sent, view } = recordingClient();
  client.receive(chainState());
  client.gesture({ type: 'open_chain' });
  sent.length = 0;
  client.gesture({ type: 'chain_power', on: true });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_chain', on: true, stages: [{ bus: 6, carrier: 26 }] }]);
  sent.length = 0;
  client.gesture({ type: 'chain_power', on: false });
  assert.deepEqual(sent, [], 'off when nothing is on sends nothing');
  client.receive(chainState({ on: true }));
  client.gesture({ type: 'chain_power', on: false });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_chain', on: false, stages: [] }]);
  client.receive({ type: 'status', status: 'syncing', read_only_reason: null });
  sent.length = 0;
  client.gesture({ type: 'chain_power', on: true });
  assert.deepEqual(sent, [], 'nothing while not synced');
  assert.equal(view().chain.on, true);
});

test('adding and removing stages edits the draft while off, rebuilds the chain while on, and is remembered per browser', () => {
  const sent = [];
  const renders = [];
  const storage = memoryStorage({ 'motu-mixer:collapsed': '' });
  const client = createMixerClient({ send: (m) => sent.push(m), render: (v) => renders.push(v), storage });
  client.receive(chainState());
  client.gesture({ type: 'open_chain' });
  client.gesture({ type: 'chain_add', bus: 8, carrier: 40 });
  assert.deepEqual(sent, [], 'editing the draft sends nothing while off');
  let chain = renders.at(-1).chain;
  assert.deepEqual(chain.stages.map((s) => [s.bus, s.carrier]), [[6, 26], [8, 40]]);
  assert.deepEqual(chain.stageOptions.map((o) => o.value), [2, 4]);
  assert.deepEqual(chain.carrierOptions, []);
  assert.equal(JSON.parse(storage.data['motu-mixer:chain']).length, 2);
  client.gesture({ type: 'chain_add', bus: 8, carrier: 26 });          // a used bus or carrier is ignored
  assert.equal(renders.at(-1).chain.stages.length, 2);
  client.gesture({ type: 'chain_remove', index: 0 });
  assert.deepEqual(renders.at(-1).chain.stages.map((s) => [s.bus, s.carrier]), [[8, 40]]);
  // a fresh page in this browser starts from the saved draft
  const again = createMixerClient({ send() {}, render: (v) => renders.push(v), storage });
  again.receive(chainState());
  again.gesture({ type: 'open_chain' });
  assert.deepEqual(renders.at(-1).chain.stages.map((s) => [s.bus, s.carrier]), [[8, 40]]);
  // while on, a change is sent as the whole new chain; removing the last stage switches off
  client.receive(chainState({ on: true }));
  sent.length = 0;
  client.gesture({ type: 'chain_add', bus: 8, carrier: 40 });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_chain', on: true, stages: [{ bus: 6, carrier: 26 }, { bus: 8, carrier: 40 }] }]);
  sent.length = 0;
  client.gesture({ type: 'chain_remove', index: 0 });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_chain', on: false, stages: [] }]);
});

test('opening a stage EQ from the chain window closes it, and the chain on the unit overrides the draft', () => {
  const { client, view } = recordingClient();
  client.receive(chainState({ on: true }));
  client.gesture({ type: 'open_chain' });
  client.gesture({ type: 'open_eq', control: 'eq:out:6' });
  assert.equal(view().chain, null);
  assert.equal(view().eq.title, 'Ana 5-6 EQ');
  client.gesture({ type: 'open_chain' });
  assert.equal(view().eq, null);
  assert.deepEqual(view().chain.stages.map((s) => [s.bus, s.carrier]), [[6, 26]]);
});

test('chain presets: save keeps the stages and every stage\'s EQ, load writes the EQ back and switches on, the name shows while it matches', () => {
  const sent = [];
  const renders = [];
  const storage = memoryStorage({ 'motu-mixer:collapsed': '' });
  const client = createMixerClient({ send: (m) => sent.push(m), render: (v) => renders.push(v), storage });
  client.receive(chainState());
  client.gesture({ type: 'open_chain' });
  assert.deepEqual(renders.at(-1).chain.preset, { names: [], name: null, edited: false });
  client.gesture({ type: 'chain_save', name: ' Mixing ' });
  assert.deepEqual(renders.at(-1).chain.preset, { names: ['Mixing'], name: 'Mixing', edited: false });
  assert.deepEqual(sent, [], 'saving sends nothing');
  const saved = JSON.parse(storage.data['motu-mixer:chain-presets']).Mixing;
  assert.deepEqual(saved.stages, [{ bus: 6, carrier: 26 }]);
  assert.deepEqual(Object.keys(saved.eq), ['0', '6'], 'Main and the stage');
  assert.deepEqual(saved.eq['6'][0], [1, 0, 60, -4, 1], 'mode, bypass, freq, gain, q');
  // an EQ edit marks the preset edited; so does a different draft
  client.gesture({ type: 'eq_set', control: 'eqb:out:6:0:gain', value: '-6' });
  assert.deepEqual(renders.at(-1).chain.preset, { names: ['Mixing'], name: 'Mixing', edited: true });
  client.gesture({ type: 'chain_add', bus: 8, carrier: 40 });
  assert.equal(renders.at(-1).chain.preset.edited, true);
  // load: the band goes back and the chain is switched on with the preset's stages, in that order
  sent.length = 0;
  client.gesture({ type: 'chain_load', name: 'Mixing' });
  assert.deepEqual(sent.map((m) => m.action), [
    { kind: 'set_property', prop: 1025, index: 18, value: -4 },
    { kind: 'set_chain', on: true, stages: [{ bus: 6, carrier: 26 }] },
  ]);
  assert.deepEqual(renders.at(-1).chain.preset, { names: ['Mixing'], name: 'Mixing', edited: false });
  assert.deepEqual(renders.at(-1).chain.stages.map((s) => [s.bus, s.carrier]), [[6, 26]]);
  // on with the same stages and EQ: load has nothing to send; the master button names the engaged preset
  client.receive(chainState({ on: true }));
  sent.length = 0;
  client.gesture({ type: 'chain_load', name: 'Mixing' });
  assert.deepEqual(sent, []);
  assert.deepEqual(renders.at(-1).master.chain, { control: 'chain', on: true, preset: { names: ['Mixing'], name: 'Mixing', edited: false } });
  client.receive(chainState());
  assert.equal(renders.at(-1).master.chain.preset, null, 'no name on the button while the chain is off');
  // a fresh page in this browser knows the presets and the name
  const again = createMixerClient({ send() {}, render: (v) => renders.push(v), storage });
  again.receive(chainState({ on: true }));
  again.gesture({ type: 'open_chain' });
  assert.equal(renders.at(-1).chain.preset.name, 'Mixing');
  client.gesture({ type: 'chain_delete', name: 'Mixing' });
  assert.deepEqual(renders.at(-1).chain.preset, { names: [], name: null, edited: false });
  client.gesture({ type: 'chain_save', name: '   ' });
  assert.deepEqual(renders.at(-1).chain.preset.names, [], 'a blank name saves nothing');
});

test('a draft saved while the reverb return was the default carrier moves to ADAT B 7-8', () => {
  const storage = memoryStorage({ 'motu-mixer:collapsed': '', 'motu-mixer:chain': JSON.stringify([{ bus: 6, carrier: 42 }, { bus: 8, carrier: 26 }]) });
  const renders = [];
  const client = createMixerClient({ send() {}, render: (v) => renders.push(v), storage });
  client.receive(chainState());
  client.gesture({ type: 'open_chain' });
  assert.deepEqual(renders.at(-1).chain.stages.map((s) => [s.bus, s.carrier]), [[6, 26]], 'moved, and the pair that already used it dropped');
});

test('zeroing an EQ point sets its gain to 0 dB, except a high-pass which has none', () => {
  const { client, sent } = recordingClient();
  client.receive(chainState());
  client.gesture({ type: 'open_eq', control: 'eq:out:6' });
  sent.length = 0;
  client.gesture({ type: 'eq_zero', control: 'eqpt:out:6:0' });
  assert.deepEqual(sent.map((m) => m.action), [{ kind: 'set_property', prop: 1025, index: 18, value: 0 }]);
  client.gesture({ type: 'eq_set', control: 'eqb:out:6:1:mode', value: '3' });
  sent.length = 0;
  client.gesture({ type: 'eq_zero', control: 'eqpt:out:6:1' });
  assert.deepEqual(sent, []);
});
