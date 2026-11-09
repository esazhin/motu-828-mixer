// The browser edge: draws the client's view state, turns pointer and click events into gestures, owns the socket.
import { createMixerClient } from './mixer_client.js';

const SVG = 'http://www.w3.org/2000/svg';
const FADER = { width: 40, height: 200, handle: 20 };
const PAN = { width: 80, height: 24, handle: 12 };
const DOUBLE_PRESS_MS = 350;
const GRAPH = { width: 600, height: 220, range: 24 };
// 8.24 values arrive as e.g. -5.600000023841858: show gain to 0.1 dB, Q to 0.01, frequency to whole Hz
const FIELD_FORMAT = {
  freq: (v) => Math.round(v),
  gain: (v) => Number(v.toFixed(1)),
  q: (v) => Number(v.toFixed(2)),
};   // EQ graph: 20 Hz-20 kHz (log) x +/-24 dB   // a second press on the same fader or pan this soon resets it (0 dB / centre)

function browserStorage() {
  try { return globalThis.localStorage ?? null; } catch { return null; }   // blocked storage throws on access
}

const INPUTS_REFRESH_MS = 3000;   // the unit does not push front-panel preamp changes: ask for its state while the Inputs page shows

export function startApp({ document, WebSocket, setTimeout, location, createClient = createMixerClient, storage = browserStorage(), fetch = globalThis.fetch }) {
  const status = document.getElementById('status');
  const tabs = document.getElementById('tabs');
  const strips = document.getElementById('strips');
  const routing = document.getElementById('routing');
  const eqHost = document.getElementById('eq') ?? (() => {
    const node = document.createElement('div');
    node.setAttribute('id', 'eq');
    document.body.appendChild(node);
    return node;
  })();
  const confirmHost = document.getElementById('confirm') ?? (() => {
    const node = document.createElement('div');
    node.setAttribute('id', 'confirm');
    document.body.appendChild(node);
    return node;
  })();
  let confirmRemember = null;   // the "don't ask again" box of the open confirmation
  const chainHost = document.getElementById('chain') ?? (() => {
    const node = document.createElement('div');
    node.setAttribute('id', 'chain');
    document.body.appendChild(node);
    return node;
  })();
  let chainPicks = null;        // the bus and carrier selects of the open chain window
  let chainPresetRefs = null;   // the preset picker and name box of the open chain window
  let inputsShown = false;
  let refreshing = false;

  function refreshInputs() {
    if (!inputsShown) { refreshing = false; return; }
    if (fetch && socket && socket.readyState === WebSocket.OPEN) {
      try { Promise.resolve(fetch('/api/resync', { method: 'POST' })).catch(() => {}); } catch { /* offline: try again next tick */ }
    }
    setTimeout(refreshInputs, INPUTS_REFRESH_MS);
  }
  let eqGraph = null;   // reused across redraws so a dragged point keeps its pointer capture
  let socket = null;
  let retryMs = 1000;
  let drag = null;
  let lastPress = null;
  let meterNodes = new Map();

  const client = createClient({
    send(message) {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
    },
    render: draw,
    renderMeters,
    storage,
  });

  function svg(tag, attributes) {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
    return node;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.setAttribute('class', className);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function slider(spec, axis) {
    const box = axis === 'y' ? FADER : PAN;
    const node = drag && drag.control === spec.control ? drag.element : svg('svg', {});
    node.replaceChildren();
    node.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    node.setAttribute('class', axis === 'y' ? spec.disabled ? 'fader disabled' : 'fader' : spec.disabled ? 'pan disabled' : 'pan');
    node.setAttribute('data-control', spec.control);
    if (!spec.disabled) node.setAttribute('data-gesture', `drag-${axis}`);
    node.style.setProperty('touch-action', 'none');
    if (axis === 'y') {
      // the track doubles as the meter: the fill rises from the bottom to the input's level on the fader scale
      const meter = svg('rect', { class: 'meter off', x: box.width / 2 - 4, width: 8, rx: 2 });
      placeMeter(meter, spec.meter ?? { position: 0, zone: 'off' });
      meterNodes.set(spec.control, meter);
      node.append(
        svg('rect', { class: 'track', x: box.width / 2 - 4, y: 0, width: 8, height: box.height, rx: 2 }),
        meter,
        svg('rect', { class: 'handle', x: 2, y: (1 - spec.position) * (box.height - box.handle), width: box.width - 4, height: box.handle, rx: 3 }),
      );
    } else {
      node.append(
        svg('rect', { class: 'track', x: 0, y: box.height / 2 - 2, width: box.width, height: 4 }),
        svg('rect', { class: 'handle', x: spec.position * (box.width - box.handle), y: 2, width: box.handle, height: box.height - 4, rx: 3 }),
      );
    }
    return node;
  }

  function placeMeter(node, meter) {
    const travel = FADER.height - FADER.handle;
    const bottom = FADER.height - FADER.handle / 2;
    const top = meter.position > 0 ? (1 - meter.position) * travel + FADER.handle / 2 : bottom;
    node.setAttribute('y', top);
    node.setAttribute('height', bottom - top);
    node.setAttribute('class', `meter ${meter.zone}`);
  }

  function renderMeters(meters) {
    for (const [control, meter] of Object.entries(meters)) {
      const node = meterNodes.get(control);
      if (node) placeMeter(node, meter);
    }
  }

  function button(spec, gesture, text, enabled, kind) {
    const node = element('button', `${gesture} ${kind}${spec.on ? ' on' : ''}`, text);
    node.setAttribute('data-control', spec.control);
    node.setAttribute('data-gesture', gesture);
    node.setAttribute('data-on', spec.on ? '1' : '0');
    if (!enabled) node.setAttribute('disabled', '');
    return node;
  }

  // Every strip has the same rows (label, pan row, fader, readout, buttons) so faders and buttons line up;
  // a missing pan or solo is held by a placeholder of the same size.
  function strip(spec, className, enabled, panRow) {
    const node = element('div', className);
    node.append(element('div', 'label', spec.label));
    if (panRow || spec.pan) {
      const row = element('div', 'pan-row');
      if (spec.pan) row.append(slider(spec.pan, 'x'), element('div', 'readout', spec.pan.text));
      else row.append(element('div', 'pan-spacer'));
      node.append(row);
    }
    node.append(slider(spec.fader, 'y'), element('div', 'readout', spec.fader.text));
    const buttons = element('div', 'buttons');
    if (spec.eq) {
      const eq = element('button', spec.eq.on ? 'eq-open on' : 'eq-open', 'EQ');
      eq.setAttribute('data-control', spec.eq.control);
      eq.setAttribute('data-gesture', 'open_eq');
      buttons.append(eq);
    } else {
      buttons.append(element('div', 'button-spacer'));
    }
    if (spec.chain) {
      const preset = spec.chain.preset;
      const chain = element('button', spec.chain.on ? 'chain-open on' : 'chain-open', 'Chain');
      chain.setAttribute('data-control', spec.chain.control);
      chain.setAttribute('data-gesture', 'open_chain');
      chain.setAttribute('title', spec.chain.on
        ? `Monitor chain is on${preset && preset.name ? ` with the ${preset.name} preset${preset.edited ? ' (edited)' : ''}` : ''}: open the chain window`
        : 'Open the monitor chain window');
      buttons.append(chain);
    }
    buttons.append(button(spec.mute, 'toggle', 'M', enabled, 'mute'));
    buttons.append(spec.solo ? button(spec.solo, 'toggle', 'S', enabled, 'solo') : element('div', 'button-spacer'));
    buttons.append(button(spec.link, 'toggle', 'link', enabled, 'link'));
    node.append(buttons);
    return node;
  }

  function option(o) {
    const item = document.createElement('option');
    item.value = o.value;
    item.textContent = o.label;
    return item;
  }

  // One card per output: its name, a pill naming what feeds it now, and the choices grouped by kind of source.
  function route(row, enabled) {
    const node = element('div', 'route');
    const head = element('div', 'route-head');
    head.append(element('span', 'label', row.label));
    if (row.current) head.append(element('span', `source ${row.current.kind}`, row.current.label));
    const select = document.createElement('select');
    select.setAttribute('data-control', row.control);
    select.setAttribute('data-gesture', 'route');
    if (row.groups) {
      for (const group of row.groups) {
        const optgroup = document.createElement('optgroup');
        optgroup.setAttribute('label', group.label);
        optgroup.append(...group.options.map(option));
        select.append(optgroup);
      }
    } else {
      select.append(...row.options.map(option));
    }
    select.value = row.value;
    if (!enabled) select.setAttribute('disabled', '');
    node.append(head, select);
    return node;
  }

  function routeSections(rows, enabled) {
    const sections = new Map();
    for (const row of rows) {
      const name = row.section ?? 'Routing';
      if (!sections.has(name)) sections.set(name, []);
      sections.get(name).push(row);
    }
    return [...sections].map(([name, sectionRows]) => {
      const section = element('section', 'route-section');
      const grid = element('div', 'route-grid');
      grid.append(...sectionRows.map((row) => route(row, enabled)));
      section.append(element('h3', 'section-title', name), grid);
      return section;
    });
  }

  // An input strip, laid out like a mixer strip: name and jack, a vertical gain fader with the input's live meter in
  // its track, the dB value as an editable box, then polarity, pad and the 48V indicator where mute/solo/link sit.
  function inputStrip(row, gain, enabled, label, sub) {
    const node = element('div', row.mic ? 'strip input mic' : 'strip input');
    node.append(element('div', 'label', label));
    node.append(element('div', 'jack', sub ?? ''));
    node.append(slider({ ...gain, disabled: !enabled }, 'y'));
    const field = document.createElement('input');
    field.setAttribute('type', 'number');
    field.setAttribute('class', 'readout gain-field');
    field.setAttribute('min', '0');
    field.setAttribute('max', String(gain.max));
    field.setAttribute('step', '1');
    field.setAttribute('data-control', gain.field);
    field.setAttribute('data-gesture', 'input-field');
    field.setAttribute('title', `0-${gain.max} dB`);
    field.value = gain.value === null || gain.value === undefined ? '' : String(gain.value);
    if (!enabled) field.setAttribute('disabled', '');
    node.append(field);
    const buttons = element('div', 'buttons');
    const phase = button(row.phase, 'toggle', 'Phase', enabled, 'phase');
    phase.setAttribute('title', row.phase.on ? 'Polarity inverted (Ø)' : 'Invert polarity (Ø)');
    buttons.append(phase);
    buttons.append(row.pad ? button(row.pad, 'toggle', 'Pad', enabled && row.pad.enabled, 'pad') : element('div', 'button-spacer'));
    if (row.phantom) {
      const phantom = button(row.phantom, 'toggle', '48V', enabled && row.phantom.enabled, 'phantom');
      phantom.setAttribute('title', row.phantom.on ? 'Phantom power is on' : 'Switch phantom power on (asks first)');
      buttons.append(phantom);
    } else {
      buttons.append(element('div', 'button-spacer'));
    }
    node.append(buttons);
    return node;
  }

  // The confirmation for switching 48V on: what it does, the warning, a "don't ask again" box, cancel or go ahead.
  function confirmPanel(confirm) {
    const panel = element('div', 'confirm-panel');
    panel.setAttribute('data-control', confirm.control);
    panel.append(element('h2', 'confirm-title', confirm.title), element('p', 'confirm-text', confirm.text));
    const remember = element('label', 'confirm-remember');
    confirmRemember = document.createElement('input');
    confirmRemember.setAttribute('type', 'checkbox');
    confirmRemember.setAttribute('data-control', 'confirm-remember');
    remember.append(confirmRemember, element('span', 'confirm-remember-text', "Don't ask me again"));
    const actions = element('div', 'confirm-actions');
    const cancel = element('button', 'confirm-cancel', 'Cancel');
    cancel.setAttribute('data-control', 'confirm-cancel');
    cancel.setAttribute('data-gesture', 'cancel_confirm');
    const ok = element('button', 'confirm-ok', 'Switch 48V on');
    ok.setAttribute('data-control', 'confirm-ok');
    ok.setAttribute('data-gesture', 'confirm');
    actions.append(cancel, ok);
    panel.append(remember, actions);
    return panel;
  }

  function inputStrips(rows, enabled) {
    return rows.flatMap((row) => {
      const strips = [inputStrip(row, row.gain, enabled, row.label, row.jack)];
      if (row.insert) strips.push(inputStrip({ ...row, pad: null, phantom: null }, row.insert, enabled, `${row.label} insert`, 'return'));
      return strips;
    });
  }

  const fx = (f) => GRAPH.width * Math.log(f / 20) / Math.log(1000);
  const gy = (g) => GRAPH.height / 2 - g * (GRAPH.height / 2) / GRAPH.range;

  function eqGraphView(eq) {
    if (!eqGraph) eqGraph = svg('svg', {});
    const node = eqGraph;
    node.replaceChildren();
    node.setAttribute('viewBox', `0 0 ${GRAPH.width} ${GRAPH.height}`);
    node.setAttribute('class', 'eq-graph');
    node.style.setProperty('touch-action', 'none');
    for (const f of [50, 100, 200, 500, 1000, 2000, 5000, 10000]) {
      node.append(svg('line', { class: 'eq-grid', x1: fx(f), x2: fx(f), y1: 0, y2: GRAPH.height }));
      const label = svg('text', { class: 'eq-axis', x: fx(f) + 3, y: GRAPH.height - 4 });
      label.textContent = f >= 1000 ? `${f / 1000}k` : String(f);
      node.append(label);
    }
    for (const g of [-18, -12, -6, 0, 6, 12, 18]) {
      node.append(svg('line', { class: g === 0 ? 'eq-grid zero' : 'eq-grid', x1: 0, x2: GRAPH.width, y1: gy(g), y2: gy(g) }));
    }
    const points = eq.curve.freqs.map((f, i) => `${fx(f).toFixed(1)},${gy(Math.max(-GRAPH.range, Math.min(GRAPH.range, eq.curve.db[i]))).toFixed(1)}`);
    node.append(svg('path', { class: 'eq-curve', d: `M${points.join(' L')}` }));
    for (const b of eq.bands) {
      const point = svg('circle', {
        class: `eq-point ${b.cls ?? `band-${b.band}`}${b.bypass ? ' bypassed' : ''}`,
        cx: fx(b.freq ?? 1000), cy: gy(b.gainEnabled ? b.gain ?? 0 : 0), r: 9,
      });
      point.setAttribute('data-control', b.point);
      point.setAttribute('data-gesture', 'drag-eq');
      node.append(point);
    }
    return node;
  }

  function eqField(b, field, label, value, enabled, attrs) {
    const wrap = element('label', 'eq-field');
    const input = document.createElement('input');
    input.setAttribute('type', 'number');
    for (const [k, v] of Object.entries(attrs)) input.setAttribute(k, v);
    input.setAttribute('data-control', `${b.control}:${field}`);
    input.setAttribute('data-gesture', 'eq-field');
    input.value = value === null || value === undefined ? '' : String(FIELD_FORMAT[field](value));
    if (!enabled) input.setAttribute('disabled', '');
    wrap.append(element('span', 'eq-field-label', label), input);
    return wrap;
  }

  function bandCard(b, enabled) {
    const card = element('div', `eq-band ${b.cls ?? `band-${b.band}`}`);
    const top = element('div', 'eq-band-head');
    const toggle = element('button', b.bypass ? 'eq-toggle' : 'eq-toggle on', b.bypass ? 'OFF' : 'ON');
    toggle.setAttribute('data-control', `${b.control}:bypass`);
    toggle.setAttribute('data-gesture', 'eq_toggle');
    toggle.setAttribute('data-on', b.bypass ? '0' : '1');
    if (!enabled) toggle.setAttribute('disabled', '');
    top.append(element('span', 'eq-band-name', `Band ${b.band + 1}`), toggle);
    card.append(top);
    if (b.modes.length > 1) {
      const select = document.createElement('select');
      select.setAttribute('data-control', `${b.control}:mode`);
      select.setAttribute('data-gesture', 'eq-field');
      for (const m of b.modes) {
        const option = document.createElement('option');
        option.value = String(m.value);
        option.textContent = m.label;
        select.append(option);
      }
      select.value = String(b.mode);
      if (!enabled) select.setAttribute('disabled', '');
      card.append(select);
    } else {
      card.append(element('div', 'eq-mode-fixed', b.modes[0].label));
    }
    card.append(
      eqField(b, 'freq', 'Hz', b.freq, enabled, { min: 20, max: 20000, step: 1 }),
      eqField(b, 'gain', 'dB', b.gain, enabled && b.gainEnabled, { min: -20, max: 20, step: 0.1 }),
      eqField(b, 'q', 'Q', b.q, enabled && b.qEnabled, { min: 0.45, max: 10, step: 0.01 }),
    );
    return card;
  }

  function eqPanel(eq, enabled) {
    const panel = element('div', 'eq-panel');
    const head = element('div', 'eq-head');
    const close = element('button', 'eq-close', '×');
    close.setAttribute('data-control', 'eq-close');
    close.setAttribute('data-gesture', 'close_eq');
    head.append(element('h2', 'eq-title', eq.title), close);
    const bands = element('div', 'eq-bands');
    bands.append(...eq.bands.map((b) => bandCard(b, enabled)));
    panel.append(head, eqGraphView(eq), bands);
    return panel;
  }

  // The chain window: the switch, the stages in order with their carriers, a picker for the next stage, and the
  // combined EQ (every stage's bands on one graph, cards grouped by stage) that edits each stage's own properties.
  function selectOf(control, options, placeholder) {
    const select = document.createElement('select');
    select.setAttribute('data-control', control);
    const first = document.createElement('option');
    first.value = '';
    first.textContent = placeholder;
    select.append(first, ...options.map((o) => option({ value: String(o.value), label: o.label })));
    return select;
  }

  // Presets: a picker of the saved names with Load and Delete, a name box with Save, and the name the unit matches now.
  function presetRow(preset, enabled) {
    const row = element('div', 'chain-presets');
    const shown = preset.name ? `Preset · ${preset.name}${preset.edited ? ' (edited)' : ''}` : 'No preset';
    row.append(element('span', preset.name ? 'chain-preset-current' : 'chain-preset-current none', shown));
    const pick = selectOf('chain-preset-pick', preset.names.map((n) => ({ value: n, label: n })), 'saved presets…');
    const previous = chainPresetRefs;
    pick.value = previous && preset.names.includes(previous.pick.value) ? previous.pick.value : (preset.name ?? '');
    const load = element('button', 'chain-preset-load', 'Load');
    load.setAttribute('data-control', 'chain-preset-load');
    load.setAttribute('data-gesture', 'chain_load');
    const remove = element('button', 'chain-preset-delete', 'Delete');
    remove.setAttribute('data-control', 'chain-preset-delete');
    remove.setAttribute('data-gesture', 'chain_delete');
    const name = document.createElement('input');
    name.setAttribute('type', 'text');
    name.setAttribute('placeholder', 'preset name');
    name.setAttribute('data-control', 'chain-preset-name');
    const typed = previous && previous.name.value !== (previous.shown ?? '') ? previous.name.value : null;
    name.value = typed ?? preset.name ?? '';
    const save = element('button', 'chain-preset-save', 'Save');
    save.setAttribute('data-control', 'chain-preset-save');
    save.setAttribute('data-gesture', 'chain_save');
    if (!enabled) load.setAttribute('disabled', '');
    chainPresetRefs = { pick, name, shown: preset.name };
    row.append(pick, load, remove, name, save);
    return row;
  }

  function chainPanel(chain, enabled) {
    const panel = element('div', 'eq-panel chain-panel');
    const head = element('div', 'eq-head');
    const close = element('button', 'eq-close', '×');
    close.setAttribute('data-control', 'chain-close');
    close.setAttribute('data-gesture', 'close_chain');
    const power = element('button', chain.on ? 'chain-power on' : 'chain-power', chain.on ? 'Chain ON' : 'Chain OFF');
    power.setAttribute('data-control', 'chain-power');
    power.setAttribute('data-gesture', 'chain_power');
    power.setAttribute('data-on', chain.on ? '1' : '0');
    if (!enabled) power.setAttribute('disabled', '');
    const title = element('div', 'chain-title-row');
    title.append(element('h2', 'eq-title', 'Monitor chain'), power);
    head.append(title, close);
    const list = element('div', 'chain-stages');
    const main = element('div', 'chain-stage stage-0');
    main.append(element('span', 'chain-stage-name', 'Main 1-2'), element('span', 'chain-stage-note', 'the mix, stage 0'));
    list.append(main);
    for (const s of chain.stages) {
      const row = element('div', `chain-stage ${s.cls}`);
      row.append(element('span', 'chain-stage-name', `${s.index} · ${s.label}`), element('span', 'chain-stage-note', `carried by ${s.carrierLabel}`));
      const eq = element('button', 'eq-open', 'EQ');
      eq.setAttribute('data-control', s.eq);
      eq.setAttribute('data-gesture', 'open_eq');
      const remove = element('button', 'chain-remove', 'remove');
      remove.setAttribute('data-control', s.remove);
      remove.setAttribute('data-gesture', 'chain_remove');
      if (!enabled && chain.on) remove.setAttribute('disabled', '');
      row.append(eq, remove);
      list.append(row);
    }
    const jacks = element('div', 'chain-stage chain-jacks');
    jacks.append(element('span', 'chain-stage-name', 'Main out jacks'),
      element('span', 'chain-stage-note', chain.on ? `fed by stage ${chain.stages.length}` : 'fed by the Main mix (hardware)'));
    list.append(jacks);
    panel.append(head, list, presetRow(chain.preset, enabled));
    if (chain.canAdd) {
      const add = element('div', 'chain-add');
      const bus = selectOf('chain-add-bus', chain.stageOptions, 'stage bus pair…');
      const carrier = selectOf('chain-add-carrier', chain.carrierOptions, 'carried by…');
      if (chainPicks) { bus.value = chainPicks.bus.value; carrier.value = chainPicks.carrier.value; }
      chainPicks = { bus, carrier };
      const button = element('button', 'chain-add-button', 'Add stage');
      button.setAttribute('data-control', 'chain-add');
      button.setAttribute('data-gesture', 'chain_add');
      add.append(bus, carrier, button);
      panel.append(add);
    } else {
      chainPicks = null;
    }
    if (chain.sections.length) {
      panel.append(element('h3', 'section-title', `Combined EQ · ${chain.bands.length} bands`), eqGraphView(chain));
      for (const sec of chain.sections) {
        const group = element('div', `chain-section ${sec.cls}`);
        group.append(element('div', 'chain-section-title', sec.index === 0 ? 'Main 1-2' : `Stage ${sec.index} · ${sec.label}`));
        const cards = element('div', 'eq-bands');
        cards.append(...sec.bands.map((b) => bandCard(b, enabled)));
        group.append(cards);
        panel.append(group);
      }
    }
    return panel;
  }

  function draw(view) {
    meterNodes = new Map();
    status.textContent = view.banner ?? view.statusText;
    status.setAttribute('data-status', view.status ?? 'connecting');
    tabs.replaceChildren(...view.tabs.map((tab) => {
      const node = element('button', tab.selected ? 'tab selected' : 'tab', tab.label);
      node.setAttribute('data-control', tab.control);
      node.setAttribute('data-gesture', 'select_tab');
      return node;
    }));
    document.body.setAttribute('data-mode', view.mode ?? 'mixer');
    const columns = view.strips.map((spec) => strip(spec, 'strip', view.controlsEnabled, view.panRow));
    // each foldable group's tab goes where the group starts; later tabs first so earlier positions stay valid
    for (const head of [...(view.groupHeads ?? [])].reverse()) {
      const tab = element('button', head.collapsed ? 'group-tab collapsed' : 'group-tab',
        head.collapsed ? `+ ${head.label} ${head.count}` : `− ${head.label}`);
      tab.setAttribute('data-control', head.control);
      tab.setAttribute('data-gesture', 'toggle_group');
      tab.setAttribute('title', head.collapsed ? `Show ${head.count} ${head.label} channels` : `Hide ${head.label} channels`);
      columns.splice(head.before, 0, tab);
    }
    strips.replaceChildren(...(view.mode === 'inputs' ? inputStrips(view.inputs ?? [], view.controlsEnabled) : columns));
    if (view.master) strips.append(strip(view.master, 'strip master', view.controlsEnabled, view.panRow));
    routing.replaceChildren(...routeSections(view.routing, view.controlsEnabled));
    document.body.setAttribute('data-confirm', view.confirm ? 'open' : 'closed');
    if (!view.confirm) confirmRemember = null;
    confirmHost.replaceChildren(...(view.confirm ? [confirmPanel(view.confirm)] : []));
    document.body.setAttribute('data-eq', view.eq ? 'open' : 'closed');
    replacePanel(eqHost, view.eq ? () => eqPanel(view.eq, view.controlsEnabled) : null);
    document.body.setAttribute('data-chain', view.chain ? 'open' : 'closed');
    if (!view.chain) { chainPicks = null; chainPresetRefs = null; }
    replacePanel(chainHost, view.chain ? () => chainPanel(view.chain, view.controlsEnabled) : null);
    const showingInputs = view.mode === 'inputs';
    if (showingInputs && !inputsShown) { inputsShown = true; if (!refreshing) { refreshing = true; refreshInputs(); } }
    if (!showingInputs) inputsShown = false;
    if (drag && !drag.element.hasPointerCapture(drag.pointerId)) drag.element.setPointerCapture(drag.pointerId);
  }

  // The overlay's panel is the scroll container: a redraw fills the same node and puts the scroll position back.
  // The position is read before the new content is built, because building it moves the shared EQ graph out of the
  // live panel, which shrinks and lets Chrome clamp the scroll if a layout lands in between. The clicked button has
  // focus, and Chrome also scrolls when a focused child is removed, so it is blurred first and its successor (the
  // same control in the new content) focused again without scrolling.
  function replacePanel(host, build) {
    const current = host.children[0];
    const top = current ? current.scrollTop || 0 : 0;
    const panel = build ? build() : null;
    if (!panel || !current) {
      host.replaceChildren(...(panel ? [panel] : []));
      return;
    }
    const active = document.activeElement;
    const focused = active && isWithin(active, current) ? active.getAttribute('data-control') : null;
    if (focused && typeof active.blur === 'function') active.blur();
    current.setAttribute('class', panel.getAttribute('class'));
    current.replaceChildren(...panel.childNodes);
    current.scrollTop = top;
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => { if (host.children[0] === current && current.scrollTop !== top) current.scrollTop = top; });
    }
    const successor = focused === null ? null : findControl(current, focused);
    if (successor && typeof successor.focus === 'function') successor.focus({ preventScroll: true });
  }

  function isWithin(node, root) {
    for (let n = node; n; n = n.parentNode) if (n === root) return true;
    return false;
  }

  function findControl(root, control) {
    for (const child of root.children) {
      if (child.getAttribute('data-control') === control) return child;
      const inner = findControl(child, control);
      if (inner) return inner;
    }
    return null;
  }

  function controlOf(node) {
    for (let n = node; n && n.getAttribute; n = n.parentNode) {
      if (n.getAttribute('data-control') !== null) return n;
    }
    return null;
  }

  function eqPosition(event) {
    const { left, top, width, height } = drag.rect;
    const x = Math.min(1, Math.max(0, (event.clientX - left) / width));
    const y = Math.min(1, Math.max(0, (event.clientY - top) / height));
    const gain = Math.min(20, Math.max(-20, GRAPH.range - 2 * GRAPH.range * y));
    return { freq: 20 * Math.pow(1000, x), gain };
  }

  function position(event) {
    const { left, top, width, height } = drag.rect;
    const at = drag.axis === 'y' ? 1 - (event.clientY - top) / height : (event.clientX - left) / width;
    return Math.min(1, Math.max(0, at));
  }

  document.addEventListener('pointerdown', (event) => {
    const node = controlOf(event.target);
    const gesture = node && node.getAttribute('data-gesture');
    if (!gesture || !gesture.startsWith('drag-')) return;
    event.preventDefault();
    const control = node.getAttribute('data-control');
    // a second press within 350 ms resets: 0 dB on a fader, centre on a pan, 0 dB gain on an EQ point. Detected here
    // because the pointer is captured during a drag, so the browser's own dblclick would land on the captor instead.
    const now = event.timeStamp ?? Date.now();
    const twice = lastPress && lastPress.control === control && now - lastPress.time < DOUBLE_PRESS_MS;
    lastPress = twice ? null : { control, time: now };
    if (gesture === 'drag-eq') {
      if (twice) {
        client.gesture({ type: 'eq_zero', control });
        return;
      }
      drag = { control, axis: 'eq', pointerId: event.pointerId, rect: eqGraph.getBoundingClientRect(), element: eqGraph };
      eqGraph.setPointerCapture(event.pointerId);
      client.gesture({ type: 'eq_drag', control, ...eqPosition(event) });
      return;
    }
    if (twice) {
      client.gesture({ type: 'reset', control });
      return;
    }
    drag = {
      control: node.getAttribute('data-control'),
      axis: gesture.slice(5),
      pointerId: event.pointerId,
      rect: node.getBoundingClientRect(),
      element: node,
    };
    node.setPointerCapture(event.pointerId);
    client.gesture({ type: 'drag', control: drag.control, value: position(event) });
  });

  document.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    event.preventDefault();
    if (drag.axis === 'eq') client.gesture({ type: 'eq_drag', control: drag.control, ...eqPosition(event) });
    else client.gesture({ type: 'drag', control: drag.control, value: position(event) });
  });

  for (const type of ['pointerup', 'pointercancel']) {
    document.addEventListener(type, (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const { control } = drag;
      drag = null;
      client.gesture({ type: 'release', control });
    });
  }

  document.addEventListener('click', (event) => {
    const node = controlOf(event.target);
    const gesture = node && node.getAttribute('data-gesture');
    const control = node && node.getAttribute('data-control');
    if (gesture === 'toggle') client.gesture({ type: 'toggle', control, value: node.getAttribute('data-on') !== '1' });
    if (gesture === 'select_tab') client.gesture({ type: 'select_tab', control });
    if (gesture === 'open_eq') client.gesture({ type: 'open_eq', control });
    if (gesture === 'toggle_group') client.gesture({ type: 'toggle_group', control });
    if (gesture === 'close_eq') client.gesture({ type: 'close_eq' });
    if (gesture === 'confirm') client.gesture({ type: 'confirm', remember: Boolean(confirmRemember && confirmRemember.checked) });
    if (gesture === 'open_chain') client.gesture({ type: 'open_chain' });
    if (gesture === 'close_chain') client.gesture({ type: 'close_chain' });
    if (gesture === 'chain_power') client.gesture({ type: 'chain_power', on: node.getAttribute('data-on') !== '1' });
    if (gesture === 'chain_remove') client.gesture({ type: 'chain_remove', index: Number(control.split(':')[1]) });
    if (gesture === 'chain_add' && chainPicks && chainPicks.bus.value !== '' && chainPicks.carrier.value !== '') {
      client.gesture({ type: 'chain_add', bus: Number(chainPicks.bus.value), carrier: Number(chainPicks.carrier.value) });
    }
    if (gesture === 'cancel_confirm') client.gesture({ type: 'cancel_confirm' });
    if (gesture === 'eq_toggle') client.gesture({ type: 'eq_set', control, value: node.getAttribute('data-on') === '1' });
    if (gesture === 'chain_load' && chainPresetRefs && chainPresetRefs.pick.value !== '') client.gesture({ type: 'chain_load', name: chainPresetRefs.pick.value });
    if (gesture === 'chain_delete' && chainPresetRefs && chainPresetRefs.pick.value !== '') client.gesture({ type: 'chain_delete', name: chainPresetRefs.pick.value });
    if (gesture === 'chain_save' && chainPresetRefs) {
      const name = chainPresetRefs.name.value.trim() || chainPresetRefs.pick.value;
      if (name) client.gesture({ type: 'chain_save', name });
    }
  });

  document.addEventListener('change', (event) => {
    const node = controlOf(event.target);
    if (node && node.getAttribute('data-gesture') === 'route') {
      client.gesture({ type: 'route', control: node.getAttribute('data-control'), value: node.value });
    }
    if (node && node.getAttribute('data-gesture') === 'eq-field') {
      client.gesture({ type: 'eq_set', control: node.getAttribute('data-control'), value: node.value });
    }
    if (node && node.getAttribute('data-gesture') === 'input-field') {
      client.gesture({ type: 'input_set', control: node.getAttribute('data-control'), value: node.value });
    }
  });

  function connect() {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`);
    socket.addEventListener('open', () => { retryMs = 1000; });
    socket.addEventListener('message', (event) => client.receive(JSON.parse(event.data)));
    socket.addEventListener('close', () => {
      setTimeout(connect, retryMs);
      retryMs = 2000;
    });
  }

  if (location.hash === '#routing') client.gesture({ type: 'select_tab', control: 'tab:routing' });
  if (location.hash === '#inputs') client.gesture({ type: 'select_tab', control: 'tab:inputs' });
  if (location.hash === '#chain') client.gesture({ type: 'open_chain' });
  if (/^#eq:(in|out):\d+$/.test(location.hash ?? '')) client.gesture({ type: 'open_eq', control: location.hash.slice(1) });
  connect();
}
