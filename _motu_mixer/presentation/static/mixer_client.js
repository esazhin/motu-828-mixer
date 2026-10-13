// The page's view of the mixer: server messages and gestures in, a complete view state and change messages out.
import { FREQ_MAX, FREQ_MIN, GAIN_MAX, Q_MAX, Q_MIN, logFreqs, response } from './eq_math.js';

const BOTTOM_DB = -60;
const TOP_DB = 12;
const MINUS = '−';

const STATUS_TEXT = {
  synced: 'synced',
  syncing: 'syncing',
  no_device: 'no 828 connected',
  no_reply: 'no 828 answered',
  busy: 'control port busy',
  disconnected: 'disconnected',
  released: 'released',
};

function linearToDb(linear) {
  return linear > 0 ? 20 * Math.log10(linear) : -Infinity;
}

function dbToPosition(db) {
  return Math.min(1, Math.max(0, (db - BOTTOM_DB) / (TOP_DB - BOTTOM_DB)));
}

function positionToDb(position) {
  if (position <= 0) return -Infinity;
  return Math.round((BOTTOM_DB + Math.min(1, position) * (TOP_DB - BOTTOM_DB)) * 10) / 10;
}

function dbText(db) {
  if (db === -Infinity) return `${MINUS}∞`;
  const rounded = Math.round(db * 10) / 10;
  if (rounded === 0) return '0.0 dB';
  return `${rounded > 0 ? '+' : MINUS}${Math.abs(rounded).toFixed(1)} dB`;
}

function panText(pan) {
  if (Math.abs(pan - 0.5) < 0.005) return 'C';
  const amount = Math.round(Math.abs(pan - 0.5) * 200);
  return `${pan < 0.5 ? 'L' : 'R'}${amount}`;
}

// Live meters (dBFS) drawn in the fader track on the fader's own dB scale; the zone colours the fill.
function meterView(dbfs) {
  if (dbfs === null || dbfs === undefined) return { position: 0, zone: 'off' };
  const zone = dbfs > -1 ? 'clip' : dbfs > -6 ? 'hot' : dbfs > -18 ? 'warm' : 'ok';
  return { position: dbToPosition(dbfs), zone };
}

// EQ (domain/eq.py): input EQ on Mic/Line 1-10 (4 bands), output EQ on each mix bus (3 bands).
const EQ = {
  in: { base: 1002, bands: 4, key: 'input' },
  out: { base: 1022, bands: 3, key: 'output' },
};
const EQ_FIELDS = ['mode', 'bypass', 'freq', 'gain', 'q'];
const MODE_LABELS = ['Peak', 'Low shelf', 'High shelf', 'High-pass'];
const INPUT_EQ_NAMES = ['Mic 1', 'Mic 2', 'Line 3', 'Line 4', 'Line 5', 'Line 6', 'Line 7', 'Line 8', 'Line 9', 'Line 10'];
const EQ_FREQS = logFreqs(96);

function modesFor(bands, band) {
  if (band === 0) return [0, 1, 3];
  if (band === bands - 1) return [0, 2];
  return [0];
}

function outputSlotName(slot) {
  if (slot === 0) return 'Main 1-2';
  if (slot >= 2 && slot <= 9) return `Ana ${slot - 1}`;
  if (slot === 10) return 'Phones 1-2';
  if (slot === 12) return 'Phones 3-4';
  if (slot === 13) return 'Reverb';
  return `Bus ${slot}`;
}

function eqSlotForBus(bus, linked) {
  if (bus >= 14) return 13;
  return linked ? bus & ~1 : bus;
}

const round = (value, step) => Math.round(value / step) * step;
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function eqValue(field, value) {
  if (field === 'freq') return Math.round(clamp(Number(value), FREQ_MIN, FREQ_MAX));
  if (field === 'gain') return Number(round(clamp(Number(value), -GAIN_MAX, GAIN_MAX), 0.1).toFixed(1));
  if (field === 'q') return Number(round(clamp(Number(value), Q_MIN, Q_MAX), 0.01).toFixed(2));
  if (field === 'bypass') return value ? 1 : 0;
  return Number(value);
}

// Strip groups that fold away to save width; collapsed by default, remembered per browser.
const STRIP_GROUPS = [
  { key: 'adat', label: 'ADAT', from: 12, to: 27 },
  { key: 'computer', label: 'Computer', from: 28, to: 41 },
];
const COLLAPSED_KEY = 'motu-mixer:collapsed';

function loadCollapsed(storage) {
  let saved = null;
  try { saved = storage ? storage.getItem(COLLAPSED_KEY) : null; } catch { saved = null; }
  return new Set(saved === null || saved === undefined ? STRIP_GROUPS.map((g) => g.key) : String(saved).split(',').filter(Boolean));
}

const groupOf = (input) => STRIP_GROUPS.find((g) => input >= g.from && input <= g.to) ?? null;

const DELTA_TARGETS = {
  fader: (s) => s.cells.fader,
  pan: (s) => s.cells.pan,
  mute: (s) => s.cells.mute,
  solo: (s) => s.cells.solo,
  master_fader: (s) => s.bus_master.fader,
  master_mute: (s) => s.bus_master.mute,
  input_link: (s) => s.links.inputs,
  bus_link: (s) => s.links.buses,
  routing: (s) => s.routing,
  ...Object.fromEntries(Object.entries(EQ).flatMap(([prefix, layout]) =>
    EQ_FIELDS.map((field) => [`${prefix}_eq_${field}`, (s) => s.eq?.[layout.key][field]]))),
};

export function createMixerClient({ send, render, renderMeters = null, storage = null }) {
  const collapsed = loadCollapsed(storage);
  let server = null;
  let meters = null;
  let status = null;
  let readOnlyReason = null;
  let loops = [];
  let refusal = null;
  let selectedBus = null;
  let routingSelected = false;
  let eqTarget = null;          // { kind: 'in' | 'out', slot }
  const held = new Map();

  function enabled() {
    return status === 'synced';
  }

  function faderView(control, linear) {
    if (held.has(control)) {
      const position = held.get(control);
      return { control, position, text: dbText(positionToDb(position)) };
    }
    if (linear === null || linear === undefined) return { control, position: 0, text: '—' };
    const db = linearToDb(linear);
    return { control, position: db === -Infinity ? 0 : dbToPosition(db), text: dbText(db) };
  }

  function eqBands(kind, slot) {
    const layout = EQ[kind];
    const column = server.eq[layout.key];
    return Array.from({ length: layout.bands }, (_, band) => {
      const index = slot * layout.bands + band;
      return Object.fromEntries([['band', band], ['index', index], ...EQ_FIELDS.map((f) => [f, column[f][index]])]);
    });
  }

  function eqInUse(kind, slot) {
    if (!server.eq) return false;
    return eqBands(kind, slot).some((b) => !b.bypass && (b.mode === 3 || Math.abs(b.gain ?? 0) >= 0.05));
  }

  function eqButton(kind, slot) {
    return server.eq ? { control: `eq:${kind}:${slot}`, on: eqInUse(kind, slot) } : null;
  }

  function eqView() {
    if (!eqTarget || !server?.eq) return null;
    const { kind, slot } = eqTarget;
    const layout = EQ[kind];
    const bands = eqBands(kind, slot).map((b) => ({
      ...b,
      control: `eqb:${kind}:${slot}:${b.band}`,
      point: `eqpt:${kind}:${slot}:${b.band}`,
      modes: modesFor(layout.bands, b.band).map((value) => ({ value, label: MODE_LABELS[value] })),
      gainEnabled: b.mode !== 3,
      qEnabled: b.mode === 0,
    }));
    return {
      title: `${kind === 'out' ? outputSlotName(slot) : INPUT_EQ_NAMES[slot]} EQ`,
      kind,
      slot,
      bands,
      curve: { freqs: EQ_FREQS, db: response(bands, EQ_FREQS) },
    };
  }

  function eqWrite(kind, slot, band, field, value) {
    const layout = EQ[kind];
    const index = slot * layout.bands + band;
    const typed = eqValue(field, value);
    server.eq[layout.key][field][index] = typed;      // shown at once; the unit's report confirms or corrects it
    return { kind: 'set_property', prop: layout.base + EQ_FIELDS.indexOf(field), index, value: typed };
  }

  function tabOf(bus) {
    const tabs = server.layout.tabs;
    return tabs.find((t) => t.bus === bus) ?? tabs.find((t) => t.stereo && t.bus === (bus & ~1)) ?? null;
  }

  function panEnabled(bus, input) {
    const tab = server ? tabOf(bus) : null;
    return Boolean(tab && tab.pan.includes(input));
  }

  function panView(control, pan) {
    const position = held.has(control) ? held.get(control) : pan ?? 0.5;
    return { control, position, text: panText(position) };
  }

  function selectedTab() {
    const tabs = server.layout.tabs;
    return tabs.find((t) => t.bus === selectedBus) ??
      tabs.find((t) => t.stereo && t.bus === (selectedBus & ~1)) ?? tabs[0];
  }

  function sourceLabel(code) {
    return server.layout.source_labels[String(code)] ?? `source ${code}`;
  }

  // Routing is shown by section, each row naming what feeds it, its choices grouped by kind of source.
  const SECTIONS = ['Main & line outs', 'Phones', 'S/PDIF', 'ADAT', 'Computer'];
  const GROUPS = [['mix', 'Mixes'], ['input', 'Inputs'], ['adat', 'ADAT'], ['computer', 'Computer'], ['off', 'Off']];

  function sectionOf(label) {
    for (const [prefix, section] of [['Phones', 'Phones'], ['S/PDIF', 'S/PDIF'], ['ADAT', 'ADAT'], ['Computer', 'Computer']]) {
      if (label.startsWith(prefix)) return section;
    }
    return SECTIONS[0];
  }

  function kindOf(code) {
    if (code === 127) return 'off';
    if (code >= 76 && code <= 91) return 'mix';
    if (code >= 0 && code <= 31) return 'computer';
    if (code >= 60 && code <= 75) return 'adat';
    return 'input';
  }

  function routingView() {
    const options = server.layout.sources.map((s) => ({ value: `${s.left}-${s.right}`, label: s.label }));
    const rows = server.layout.output_pairs.map((pair) => {
      const left = server.routing[String(pair.left)];
      const right = server.routing[String(pair.right)];
      const value = `${left}-${right}`;
      const known = options.find((o) => o.value === value);
      const label = known ? known.label
        : left === right ? sourceLabel(left) : `${sourceLabel(left)} / ${sourceLabel(right)}`;
      const all = known ? options : [{ value, label }, ...options];
      const groups = GROUPS
        .map(([kind, groupLabel]) => ({ label: groupLabel, options: all.filter((o) => kindOf(Number(o.value.split('-')[0])) === kind) }))
        .filter((g) => g.options.length);
      return {
        label: pair.label,
        control: `route:${pair.left}:${pair.right}`,
        value,
        section: sectionOf(pair.label),
        current: { label, kind: kindOf(left) },
        options: all,
        groups,
      };
    });
    return SECTIONS.flatMap((section) => rows.filter((r) => r.section === section));
  }

  function banner() {
    const lines = [];
    if (status === 'read_only') lines.push(`Read-only: ${readOnlyReason}`);
    else if (status !== null && status !== 'synced') lines.push(STATUS_TEXT[status] ?? status);
    for (const path of loops) lines.push(`Loop present: ${path.join(' → ')}`);
    if (refusal) lines.push(refusal);
    return lines.length ? lines.join(' · ') : null;
  }

  function viewState() {
    const base = {
      status,
      statusText: status === 'read_only' ? `Read-only: ${readOnlyReason}` : STATUS_TEXT[status] ?? 'connecting',
      banner: banner(),
      controlsEnabled: enabled(),
    };
    if (!server || server.layout.tabs.length === 0) {
      return { ...base, mode: 'mixer', panRow: false, tabs: [], strips: [], master: null, routing: [], eq: null };
    }
    const tab = selectedTab();
    const bus = tab.bus;
    const tabs = [
      ...server.layout.tabs.map((t) => ({ control: `tab:${t.bus}`, label: t.label, selected: !routingSelected && t.bus === bus })),
      { control: 'tab:routing', label: 'Routing', selected: routingSelected },
    ];
    if (routingSelected) {
      return { ...base, mode: 'routing', panRow: false, tabs, strips: [], master: null, routing: routingView(), eq: null };
    }
    const allStrips = server.layout.strips;
    const visible = allStrips.filter((strip) => !collapsed.has(groupOf(strip.input)?.key));
    const groupHeads = STRIP_GROUPS
      .map((g) => {
        const members = allStrips.filter((strip) => groupOf(strip.input) === g);
        if (!members.length) return null;
        const first = allStrips.indexOf(members[0]);
        const before = allStrips.slice(0, first).filter((strip) => visible.includes(strip)).length;
        return { key: g.key, label: g.label, count: members.length, collapsed: collapsed.has(g.key), control: `group:${g.key}`, before };
      })
      .filter(Boolean);
    const strips = visible.map((strip) => {
      const input = strip.input;
      const cell = bus * 44 + input;
      return {
        label: strip.label,
        fader: { ...faderView(`fader:${bus}:${input}`, server.cells.fader[cell]), meter: meterView(meters?.inputs[input]) },
        // on a stereo bus every strip has a pan row; a linked pair's pan is shown disabled, as in CueMix
        pan: !tab.stereo ? null
          : tab.pan.includes(input) ? panView(`pan:${bus}:${input}`, server.cells.pan[cell])
            : { control: `pan:${bus}:${input}`, position: 0.5, text: 'stereo', disabled: true },
        mute: { control: `mute:${bus}:${input}`, on: server.cells.mute[cell] === 1 },
        solo: { control: `solo:${bus}:${input}`, on: server.cells.solo[cell] === 1 },
        link: { control: `input_link:${input & ~1}`, on: server.links.inputs[input & ~1] === 1 },
        eq: input < 10 ? eqButton('in', input) : null,
      };
    });
    return {
      ...base,
      mode: 'mixer',
      panRow: Boolean(tab.stereo),
      tabs,
      strips,
      groupHeads,
      master: {
        label: tab.label,
        fader: { ...faderView(`master:${bus}`, server.bus_master.fader[bus]), meter: meterView(meters?.buses[bus]) },
        mute: { control: `master_mute:${bus}`, on: server.bus_master.mute[bus] === 1 },
        link: { control: `bus_link:${bus & ~1}`, on: server.links.buses[bus & ~1] === 1 },
        eq: eqButton('out', eqSlotForBus(bus, server.links.buses[bus & ~1] === 1)),
      },
      eq: eqView(),
      routing: [],
    };
  }

  function meterMap() {
    const map = {};
    if (!server || server.layout.tabs.length === 0 || routingSelected) return map;
    const bus = selectedTab().bus;
    for (const strip of server.layout.strips) map[`fader:${bus}:${strip.input}`] = meterView(meters?.inputs[strip.input]);
    map[`master:${bus}`] = meterView(meters?.buses[bus]);
    return map;
  }

  function resetAction(kind, numbers) {
    if (kind === 'fader') return { kind: 'set_level', bus: numbers[0], input: numbers[1], db: 0 };
    if (kind === 'pan') return { kind: 'set_pan', bus: numbers[0], input: numbers[1], pan: 0.5 };
    if (kind === 'master') return { kind: 'set_master_level', bus: numbers[0], db: 0 };
    return null;
  }

  function dragAction(kind, numbers, position) {
    if (kind === 'fader') return { kind: 'set_level', bus: numbers[0], input: numbers[1], db: dbOrInf(position) };
    if (kind === 'pan') return { kind: 'set_pan', bus: numbers[0], input: numbers[1], pan: position };
    if (kind === 'master') return { kind: 'set_master_level', bus: numbers[0], db: dbOrInf(position) };
    return null;
  }

  function dbOrInf(position) {
    const db = positionToDb(position);
    return db === -Infinity ? '-inf' : db;
  }

  function toggleAction(kind, numbers, on) {
    if (kind === 'mute') return { kind: 'set_mute', bus: numbers[0], input: numbers[1], on };
    if (kind === 'solo') return { kind: 'set_solo', bus: numbers[0], input: numbers[1], on };
    if (kind === 'master_mute') return { kind: 'set_master_mute', bus: numbers[0], on };
    if (kind === 'input_link') return { kind: 'set_input_link', input: numbers[0], on };
    if (kind === 'bus_link') return { kind: 'set_bus_link', bus: numbers[0], on };
    return null;
  }

  function act(g) {
    const [kind, ...rest] = String(g.control).split(':');
    const numbers = rest.map(Number);
    if (g.type === 'select_tab') {
      if (g.control === 'tab:routing') routingSelected = true;
      else if (kind === 'tab') {
        routingSelected = false;
        selectedBus = numbers[0];
      }
      return null;
    }
    if (g.type === 'release') {
      held.delete(g.control);
      return null;
    }
    if (g.type === 'toggle_group' && kind === 'group') {
      const key = rest[0];
      if (collapsed.has(key)) collapsed.delete(key);
      else collapsed.add(key);
      try { storage?.setItem(COLLAPSED_KEY, [...collapsed].join(',')); } catch { /* private mode: keep it for this page */ }
      return null;
    }
    if (g.type === 'open_eq' && kind === 'eq') {
      eqTarget = { kind: rest[0], slot: Number(rest[1]) };
      return null;
    }
    if (g.type === 'close_eq') {
      eqTarget = null;
      return null;
    }
    if (!enabled()) return null;
    if (kind === 'pan' && !panEnabled(numbers[0], numbers[1])) return null;
    if (g.type === 'eq_set' && kind === 'eqb' && server?.eq) {
      const [eqKind, slot, band, field] = rest;
      return eqWrite(eqKind, Number(slot), Number(band), field, g.value);
    }
    if (g.type === 'eq_drag' && kind === 'eqpt' && server?.eq) {
      const [eqKind, slot, band] = [rest[0], Number(rest[1]), Number(rest[2])];
      const actions = [eqWrite(eqKind, slot, band, 'freq', g.freq)];
      if (eqBands(eqKind, slot)[band].mode !== 3) actions.push(eqWrite(eqKind, slot, band, 'gain', g.gain));
      return actions;
    }
    if (g.type === 'reset') {
      held.delete(g.control);
      return resetAction(kind, numbers);
    }
    if (g.type === 'drag') {
      const action = dragAction(kind, numbers, g.value);
      if (action) held.set(g.control, g.value);
      return action;
    }
    if (g.type === 'toggle') return toggleAction(kind, numbers, Boolean(g.value));
    if (g.type === 'route' && kind === 'route') {
      const sources = String(g.value).split('-').map(Number);
      return { kind: 'set_route', output_slots: numbers, sources };
    }
    return null;
  }

  function applyDelta(changes) {
    for (const change of changes) {
      const target = DELTA_TARGETS[change.prop];
      if (!server || !target) continue;
      target(server)[change.index] = change.value;
      if (change.prop === 'routing' && change.label) server.layout.source_labels[String(change.value)] = change.label;
    }
  }

  return {
    gesture(g) {
      const action = act(g);
      for (const one of Array.isArray(action) ? action : action ? [action] : []) {
        refusal = null;
        send({ type: 'change', action: one });
      }
      render(viewState());
    },
    receive(message) {
      if (message.type === 'meters') {
        // ~17 a second: update the meter fills in place, never rebuild the page (it would reset open dropdowns)
        meters = message;
        if (renderMeters) renderMeters(meterMap());
        return;
      }
      if (message.type === 'state') {
        server = structuredClone(message);
        ({ status, read_only_reason: readOnlyReason } = message);
        loops = message.loops ?? [];
        if (selectedBus === null && message.layout.tabs.length) selectedBus = message.layout.tabs[0].bus;
      } else if (message.type === 'delta') {
        applyDelta(message.changes);
      } else if (message.type === 'status') {
        ({ status, read_only_reason: readOnlyReason } = message);
        loops = message.loops ?? [];
      } else if (message.type === 'accepted') {
        refusal = null;
      } else if (message.type === 'refused') {
        refusal = message.reason;
        held.clear();
      }
      render(viewState());
    },
  };
}
