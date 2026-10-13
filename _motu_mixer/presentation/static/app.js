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

export function startApp({ document, WebSocket, setTimeout, location, createClient = createMixerClient }) {
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
    node.setAttribute('class', axis === 'y' ? 'fader' : spec.disabled ? 'pan disabled' : 'pan');
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
        class: `eq-point band-${b.band}${b.bypass ? ' bypassed' : ''}`,
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

  function eqPanel(eq, enabled) {
    const panel = element('div', 'eq-panel');
    const head = element('div', 'eq-head');
    const close = element('button', 'eq-close', '×');
    close.setAttribute('data-control', 'eq-close');
    close.setAttribute('data-gesture', 'close_eq');
    head.append(element('h2', 'eq-title', eq.title), close);
    const bands = element('div', 'eq-bands');
    for (const b of eq.bands) {
      const card = element('div', `eq-band band-${b.band}`);
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
      bands.append(card);
    }
    panel.append(head, eqGraphView(eq), bands);
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
    strips.replaceChildren(...view.strips.map((spec) => strip(spec, 'strip', view.controlsEnabled, view.panRow)));
    if (view.master) strips.append(strip(view.master, 'strip master', view.controlsEnabled, view.panRow));
    routing.replaceChildren(...routeSections(view.routing, view.controlsEnabled));
    document.body.setAttribute('data-eq', view.eq ? 'open' : 'closed');
    eqHost.replaceChildren(...(view.eq ? [eqPanel(view.eq, view.controlsEnabled)] : []));
    if (drag && !drag.element.hasPointerCapture(drag.pointerId)) drag.element.setPointerCapture(drag.pointerId);
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
    if (gesture === 'drag-eq') {
      drag = { control, axis: 'eq', pointerId: event.pointerId, rect: eqGraph.getBoundingClientRect(), element: eqGraph };
      eqGraph.setPointerCapture(event.pointerId);
      client.gesture({ type: 'eq_drag', control, ...eqPosition(event) });
      return;
    }
    const now = event.timeStamp ?? Date.now();
    if (lastPress && lastPress.control === control && now - lastPress.time < DOUBLE_PRESS_MS) {
      lastPress = null;
      client.gesture({ type: 'reset', control });
      return;
    }
    lastPress = { control, time: now };
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
    if (gesture === 'close_eq') client.gesture({ type: 'close_eq' });
    if (gesture === 'eq_toggle') client.gesture({ type: 'eq_set', control, value: node.getAttribute('data-on') === '1' });
  });

  document.addEventListener('change', (event) => {
    const node = controlOf(event.target);
    if (node && node.getAttribute('data-gesture') === 'route') {
      client.gesture({ type: 'route', control: node.getAttribute('data-control'), value: node.value });
    }
    if (node && node.getAttribute('data-gesture') === 'eq-field') {
      client.gesture({ type: 'eq_set', control: node.getAttribute('data-control'), value: node.value });
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
  if (/^#eq:(in|out):\d+$/.test(location.hash ?? '')) client.gesture({ type: 'open_eq', control: location.hash.slice(1) });
  connect();
}
