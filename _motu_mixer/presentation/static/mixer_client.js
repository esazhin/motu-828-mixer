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

function outputSlotName(slot, linked = false) {
  if (slot === 0) return 'Main 1-2';
  if (slot >= 2 && slot <= 9) return linked && slot % 2 === 0 ? `Ana ${slot - 1}-${slot}` : `Ana ${slot - 1}`;
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

// Analog input block (domain/preamp.py): one byte per channel. Gain is whole dB: Mic 1-2 0-74, Line 3-10 and the
// insert returns 0-20. Polarity and pad are 0/1. 48V is shown, never written; the returns appear when an insert is detected.
const INPUT_PROPS = { gain: 5001, phase: 5002, pad: 5003, phantom: 5004 };
const INPUT_NAMES = INPUT_EQ_NAMES;
const MIC_GAIN_MAX = 74;
const LINE_GAIN_MAX = 20;
const RETURNS_START = 10;
const INPUT_FIELDS = ['gain', 'phase', 'pad', 'phantom', 'jack', 'return_detect'];
// Switching 48V on asks first (ribbon mics and some line-level gear do not tolerate it); "don't ask again" is kept per browser.
const ASK_48V_KEY = 'motu-mixer:ask-48v';
const PHANTOM_WARNING = 'Phantom power can damage ribbon microphones and some line-level equipment. Check what is plugged into this input first.';

function loadAsk48v(storage) {
  try { return !storage || storage.getItem(ASK_48V_KEY) !== 'off'; } catch { return true; }
}

const gainMax = (index) => (index < 2 ? MIC_GAIN_MAX : LINE_GAIN_MAX);

function gainValue(index, value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(clamp(number, 0, gainMax(index))) : null;
}

// The monitor chain (docs/monitor-chain.md): Main → carrier → Ana stage → … → Main jacks, one 3-band EQ per stage.
// The stages of a chain that is off are kept per browser so "on" rebuilds the same chain; the unit's state says what is on.
const CHAIN_KEY = 'motu-mixer:chain';
const DEFAULT_STAGE = { bus: 6, carrier: 26 };          // Ana 5-6 carried by ADAT B 7-8 (idle on this unit, proven by W7)
const REVERB_RETURN = 42;                               // the default carrier until W8 showed the reverb applied on its path
const STAGE_CLASSES = ['stage-0', 'stage-1', 'stage-2', 'stage-3', 'stage-4'];
// Named presets: the stages and the output EQ of Main and of every stage, kept per browser; loading one writes the EQ
// and switches the chain on. The name last saved or loaded is shown while the unit's chain and EQ still match it.
const CHAIN_PRESETS_KEY = 'motu-mixer:chain-presets';
const CHAIN_PRESET_KEY = 'motu-mixer:chain-preset';

function validStages(parsed) {
  return Array.isArray(parsed) && parsed.every((s) => Number.isInteger(s.bus) && Number.isInteger(s.carrier));
}

function loadChainDraft(storage) {
  try {
    const saved = storage ? storage.getItem(CHAIN_KEY) : null;
    const parsed = saved ? JSON.parse(saved) : null;
    if (validStages(parsed)) {
      // a draft saved while the reverb return was the default carrier moves to today's default
      const moved = parsed.map((s) => (s.carrier === REVERB_RETURN ? { ...s, carrier: DEFAULT_STAGE.carrier } : s));
      return moved.filter((s, k) => moved.findIndex((o) => o.carrier === s.carrier) === k);
    }
  } catch { /* unreadable: start from the default */ }
  return [DEFAULT_STAGE];
}

function loadChainPresets(storage) {
  try {
    const parsed = JSON.parse((storage && storage.getItem(CHAIN_PRESETS_KEY)) || 'null');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.fromEntries(Object.entries(parsed).filter(([, v]) => v && validStages(v.stages)));
    }
  } catch { /* unreadable: no presets */ }
  return {};
}

function loadChainPreset(storage) {
  try { return (storage && storage.getItem(CHAIN_PRESET_KEY)) || null; } catch { return null; }
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
  ...Object.fromEntries(INPUT_FIELDS.map((field) => [`in_${field}`, (s) => s.inputs?.[field]])),
};

export function createMixerClient({ send, render, renderMeters = null, storage = null }) {
  const collapsed = loadCollapsed(storage);
  let ask48v = loadAsk48v(storage);
  let pending = null;          // a 48V switch-on waiting for the user's confirmation: { action, control }
  let server = null;
  let meters = null;
  let status = null;
  let readOnlyReason = null;
  let loops = [];
  let refusal = null;
  let selectedBus = null;
  let routingSelected = false;
  let inputsSelected = false;
  let eqTarget = null;          // { kind: 'in' | 'out', slot }
  let chainOpen = false;
  let chainDraft = loadChainDraft(storage);
  let chainPresets = loadChainPresets(storage);
  let chainPreset = loadChainPreset(storage);     // the name shown, while the chain and EQ still match it
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
      title: `${kind === 'out' ? outputSlotName(slot, server.links.buses[slot & ~1] === 1) : INPUT_EQ_NAMES[slot]} EQ`,
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

  // ---- the monitor chain ----
  function chainStages() {
    const chain = server.chain;
    return chain.on ? chain.stages.map((s) => ({ bus: s.bus, carrier: s.carrier })) : chainDraft;
  }

  function saveChainDraft() {
    try { storage?.setItem(CHAIN_KEY, JSON.stringify(chainDraft)); } catch { /* private mode: keep it for this page */ }
  }

  function saveChainPresets() {
    try {
      storage?.setItem(CHAIN_PRESETS_KEY, JSON.stringify(chainPresets));
      storage?.setItem(CHAIN_PRESET_KEY, chainPreset ?? '');
    } catch { /* private mode: keep them for this page */ }
  }

  // ---- chain presets: the stages plus [mode, bypass, freq, gain, q] of every band of Main and of each stage ----
  function eqSnapshot(stages) {
    if (!server?.eq) return null;
    return Object.fromEntries([0, ...stages.map((s) => s.bus)].map((slot) => [String(slot), eqBands('out', slot).map((b) => EQ_FIELDS.map((f) => b[f]))]));
  }

  function eqSame(field, a, b) {
    if (a === null || a === undefined || b === null || b === undefined) return a == b;   // eslint-disable-line eqeqeq
    return eqValue(field, a) === eqValue(field, b);
  }

  function sameStages(a, b) {
    return a.length === b.length && a.every((s, k) => s.bus === b[k].bus && s.carrier === b[k].carrier);
  }

  function presetMatch(preset) {            // 'exact' | 'edited' | null
    if (!preset) return null;
    const stages = chainStages();
    if (!sameStages(stages, preset.stages)) return 'edited';
    const now = eqSnapshot(stages);
    if (!now || !preset.eq) return 'exact';
    const same = Object.entries(preset.eq).every(([slot, bands]) =>
      bands.every((band, k) => band.every((v, i) => eqSame(EQ_FIELDS[i], v, now[slot]?.[k]?.[i]))));
    return same ? 'exact' : 'edited';
  }

  function presetView() {
    const match = presetMatch(chainPresets[chainPreset]);
    return { names: Object.keys(chainPresets).sort(), name: match ? chainPreset : null, edited: match === 'edited' };
  }

  function loadPreset(name) {
    const preset = chainPresets[name];
    if (!preset || !server?.chain) return null;
    const stages = preset.stages.map((s) => ({ bus: s.bus, carrier: s.carrier }));
    chainDraft = stages;
    saveChainDraft();
    chainPreset = name;
    saveChainPresets();
    const actions = [];
    if (server.eq) {
      for (const [slot, bands] of Object.entries(preset.eq ?? {})) {
        bands.forEach((band, k) => EQ_FIELDS.forEach((field, i) => {
          const current = server.eq.output[field][Number(slot) * EQ.out.bands + k];
          if (band[i] !== null && band[i] !== undefined && !eqSame(field, band[i], current)) actions.push(eqWrite('out', Number(slot), k, field, band[i]));
        }));
      }
    }
    if (!(server.chain.on && sameStages(server.chain.stages, stages))) actions.push(chainAction(true, stages));
    return actions;
  }

  function stageBands(slot, stage) {
    return eqBands('out', slot).map((b) => ({
      ...b,
      control: `eqb:out:${slot}:${b.band}`,
      point: `eqpt:out:${slot}:${b.band}`,
      modes: modesFor(EQ.out.bands, b.band).map((value) => ({ value, label: MODE_LABELS[value] })),
      gainEnabled: b.mode !== 3,
      qEnabled: b.mode === 0,
      stage,
      cls: STAGE_CLASSES[stage],
    }));
  }

  function chainView() {
    if (!chainOpen || !server?.chain) return null;
    const chain = server.chain;
    const stages = chainStages();
    const label = (list, key, value) => list.find((o) => o[key] === value)?.label ?? `${key} ${value}`;
    const rows = stages.map((s, k) => ({
      index: k + 1,
      bus: s.bus,
      carrier: s.carrier,
      label: label(chain.stage_options, 'bus', s.bus),
      carrierLabel: label(chain.carrier_options, 'input', s.carrier),
      slot: s.bus,
      eq: `eq:out:${s.bus}`,
      remove: `chainrm:${k}`,
      cls: STAGE_CLASSES[k + 1],
    }));
    const usedBuses = new Set(stages.map((s) => s.bus));
    const usedCarriers = new Set(stages.map((s) => s.carrier));
    const sections = server.eq ? [{ index: 0, label: 'Main 1-2', slot: 0, cls: STAGE_CLASSES[0], bands: stageBands(0, 0) },
      ...rows.map((r) => ({ index: r.index, label: r.label, slot: r.slot, cls: r.cls, bands: stageBands(r.slot, r.index) }))] : [];
    const bands = sections.flatMap((sec) => sec.bands);
    return {
      on: chain.on,
      stages: rows,
      canAdd: stages.length < chain.max_stages,
      stageOptions: chain.stage_options.filter((o) => !usedBuses.has(o.bus)).map((o) => ({
        value: o.bus, label: o.feeds.length ? `${o.label} (feeds ${o.feeds.join(', ')})` : o.label,
      })),
      carrierOptions: chain.carrier_options.filter((o) => !usedCarriers.has(o.input)).map((o) => ({
        value: o.input, label: o.live ? `${o.label} (in use)` : o.label,
      })),
      sections,
      curve: { freqs: EQ_FREQS, db: response(bands, EQ_FREQS) },
      bands,
      preset: presetView(),
    };
  }

  function chainAction(on, stages = chainStages()) {
    return { kind: 'set_chain', on, stages: on ? stages.map((s) => ({ bus: s.bus, carrier: s.carrier })) : [] };
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

  // The Inputs page: one card per analog input with its gain, polarity and, on the mic inputs, pad and 48V.
  // Mic/Line inputs 0-9 meter at their mixer input (post-FX tap); the insert returns have no meter.
  function gainView(index) {
    const control = `ingain:${index}`;
    const max = gainMax(index);
    const base = { control, field: `ingainf:${index}`, max };
    if (index < RETURNS_START) base.meter = meterView(meters?.inputs[index]);
    if (held.has(control)) {
      const value = Math.round(held.get(control) * max);
      return { ...base, position: value / max, value, text: `${value} dB` };
    }
    const value = server.inputs.gain[index];
    if (value === null || value === undefined) return { ...base, position: 0, value: null, text: '—' };
    return { ...base, position: value / max, value, text: `${value} dB` };
  }

  function inputsView() {
    if (!server.inputs) return [];
    const { phase, pad, phantom, jack, return_detect: returns } = server.inputs;
    return INPUT_NAMES.map((label, index) => {
      const mic = index < 2;
      return {
        label,
        index,
        mic,
        gain: gainView(index),
        phase: { control: `inphase:${index}`, on: phase[index] === 1 },
        pad: mic ? { control: `inpad:${index}`, on: pad[index] === 1, enabled: jack[index] !== 1 } : null,
        phantom: mic ? { control: `inphantom:${index}`, on: phantom[index] === 1, enabled: jack[index] !== 1 } : null,
        jack: !mic || jack[index] === null || jack[index] === undefined ? null : jack[index] === 1 ? 'line / instrument jack' : 'mic jack',
        insert: mic && returns[index] === 1 ? gainView(RETURNS_START + index) : null,
      };
    });
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
      confirm: pending ? { control: pending.control, title: pending.title, text: PHANTOM_WARNING } : null,
    };
    if (!server || server.layout.tabs.length === 0) {
      return { ...base, mode: 'mixer', panRow: false, tabs: [], strips: [], master: null, routing: [], inputs: [], eq: null };
    }
    const tab = selectedTab();
    const bus = tab.bus;
    const onBus = !routingSelected && !inputsSelected;
    const tabs = [
      ...server.layout.tabs.map((t) => ({ control: `tab:${t.bus}`, label: t.label, selected: onBus && t.bus === bus })),
      { control: 'tab:inputs', label: 'Inputs', selected: inputsSelected },
      { control: 'tab:routing', label: 'Routing', selected: routingSelected },
    ];
    if (routingSelected) {
      return { ...base, mode: 'routing', panRow: false, tabs, strips: [], master: null, routing: routingView(), inputs: [], eq: null };
    }
    if (inputsSelected) {
      return { ...base, mode: 'inputs', panRow: false, tabs, strips: [], master: null, routing: [], inputs: inputsView(), eq: null };
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
        chain: bus === 0 && server.chain ? { control: 'chain', on: server.chain.on, preset: server.chain.on ? presetView() : null } : null,
      },
      eq: eqView(),
      chain: chainView(),
      routing: [],
      inputs: [],
    };
  }

  function meterMap() {
    const map = {};
    if (!server || server.layout.tabs.length === 0 || routingSelected) return map;
    if (inputsSelected) {
      if (server.inputs) for (let index = 0; index < RETURNS_START; index++) map[`ingain:${index}`] = meterView(meters?.inputs[index]);
      return map;
    }
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
    if (kind === 'ingain') return inputAction('gain', numbers[0], Math.round(position * gainMax(numbers[0])));
    return null;
  }

  function inputAction(field, index, value) {
    if (value === null) return null;
    return { kind: 'set_property', prop: INPUT_PROPS[field], index, value };
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
    if (kind === 'inphase') return inputAction('phase', numbers[0], on ? 1 : 0);
    if (kind === 'inpad') return inputAction('pad', numbers[0], on ? 1 : 0);
    if (kind === 'inphantom') return inputAction('phantom', numbers[0], on ? 1 : 0);
    return null;
  }

  function act(g) {
    const [kind, ...rest] = String(g.control).split(':');
    const numbers = rest.map(Number);
    if (g.type === 'select_tab') {
      if (g.control === 'tab:routing') [routingSelected, inputsSelected] = [true, false];
      else if (g.control === 'tab:inputs') [routingSelected, inputsSelected] = [false, true];
      else if (kind === 'tab') {
        routingSelected = inputsSelected = false;
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
      chainOpen = false;
      return null;
    }
    if (g.type === 'close_eq') {
      eqTarget = null;
      return null;
    }
    if (g.type === 'open_chain') {
      chainOpen = true;
      eqTarget = null;
      return null;
    }
    if (g.type === 'close_chain') {
      chainOpen = false;
      return null;
    }
    if (g.type === 'chain_add' || g.type === 'chain_remove') {
      if (!server?.chain) return null;
      const stages = chainStages().slice();
      if (g.type === 'chain_add') {
        const bus = Number(g.bus), carrier = Number(g.carrier);
        if (!Number.isInteger(bus) || !Number.isInteger(carrier) || stages.length >= server.chain.max_stages) return null;
        if (stages.some((s) => s.bus === bus || s.carrier === carrier)) return null;
        stages.push({ bus, carrier });
      } else {
        if (!(g.index >= 0 && g.index < stages.length)) return null;
        stages.splice(g.index, 1);
      }
      chainDraft = stages;
      saveChainDraft();
      // a chain that is on is rebuilt with its new stages, or switched off when none is left
      if (server.chain.on && enabled()) return stages.length ? chainAction(true, stages) : chainAction(false);
      return null;
    }
    if (g.type === 'chain_save') {
      const name = String(g.name ?? '').trim();
      if (!name || !server?.chain) return null;
      const stages = chainStages().map((s) => ({ bus: s.bus, carrier: s.carrier }));
      chainPresets = { ...chainPresets, [name]: { stages, eq: eqSnapshot(stages) } };
      chainPreset = name;
      saveChainPresets();
      return null;
    }
    if (g.type === 'chain_delete') {
      if (!(g.name in chainPresets)) return null;
      chainPresets = Object.fromEntries(Object.entries(chainPresets).filter(([k]) => k !== g.name));
      if (chainPreset === g.name) chainPreset = null;
      saveChainPresets();
      return null;
    }
    if (g.type === 'cancel_confirm') {
      pending = null;
      return null;
    }
    if (g.type === 'confirm') {
      if (!pending) return null;
      if (g.remember) {
        ask48v = false;
        try { storage?.setItem(ASK_48V_KEY, 'off'); } catch { /* private mode: keep it for this page */ }
      }
      const action = enabled() ? pending.action : null;
      pending = null;
      return action;
    }
    if (!enabled()) return null;
    if (g.type === 'chain_power' && server?.chain) {
      if (!g.on) return server.chain.on ? chainAction(false) : null;
      if (!chainDraft.length) { chainDraft = [DEFAULT_STAGE]; saveChainDraft(); }
      return chainAction(true, chainDraft);
    }
    if (g.type === 'chain_load') return loadPreset(g.name);
    if (kind === 'pan' && !panEnabled(numbers[0], numbers[1])) return null;
    if (g.type === 'eq_set' && kind === 'eqb' && server?.eq) {
      const [eqKind, slot, band, field] = rest;
      return eqWrite(eqKind, Number(slot), Number(band), field, g.value);
    }
    if (g.type === 'eq_zero' && kind === 'eqpt' && server?.eq) {
      const [eqKind, slot, band] = [rest[0], Number(rest[1]), Number(rest[2])];
      if (eqBands(eqKind, slot)[band].mode === 3) return null;        // a high-pass has no gain to zero
      return eqWrite(eqKind, slot, band, 'gain', 0);
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
    if (g.type === 'toggle' && kind === 'inphantom' && g.value && ask48v) {
      pending = { action: toggleAction(kind, numbers, true), control: g.control, title: `Switch 48V on for ${INPUT_NAMES[numbers[0]]}?` };
      return null;
    }
    if (g.type === 'toggle') return toggleAction(kind, numbers, Boolean(g.value));
    if (g.type === 'input_set' && kind === 'ingainf' && server?.inputs) {
      return inputAction('gain', numbers[0], gainValue(numbers[0], g.value));
    }
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
        if (server.chain?.on) { chainDraft = server.chain.stages.map((s) => ({ bus: s.bus, carrier: s.carrier })); saveChainDraft(); }
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
