import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';

class FakeNode {
  constructor(ownerDocument, tagName, text = null) {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName;
    this.nodeName = tagName;
    this.nodeType = text === null ? 1 : 3;
    this.data = text;
    this.parentNode = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.dataset = {};
    this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } };
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      toggle: (name, on = !classes.has(name)) => (on ? classes.add(name) : classes.delete(name), on),
      contains: (name) => classes.has(name),
    };
    this.listeners = {};
    this.value = '';
    this.captured = new Set();
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes.at(-1) ?? null; }
  get id() { return this.getAttribute('id') ?? ''; }
  set id(value) { this.setAttribute('id', value); }
  get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(value) {
    this.replaceChildren();
    if (value !== '' && value !== null && value !== undefined) this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }
  set innerHTML(value) {
    assert.equal(value, '', 'the page is built from elements, not markup');
    this.replaceChildren();
  }
  appendChild(child) { return this.insertBefore(child, null); }
  insertBefore(child, ref) {
    child.remove();
    const at = ref === null ? this.childNodes.length : this.childNodes.indexOf(ref);
    this.childNodes.splice(at, 0, child);
    child.parentNode = this;
    return child;
  }
  append(...nodes) {
    for (const n of nodes) this.appendChild(typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n);
  }
  replaceChildren(...nodes) {
    for (const n of [...this.childNodes]) this.removeChild(n);
    this.append(...nodes);
  }
  removeChild(child) {
    this.childNodes.splice(this.childNodes.indexOf(child), 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(value);
  }
  setAttributeNS(_ns, name, value) { this.setAttribute(name, value); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn); }
  setPointerCapture(id) { this.captured.add(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  getBoundingClientRect() { return { x: 0, y: 0, left: 0, top: 0, width: 40, height: 200, right: 40, bottom: 200 }; }
}

function fakeDocument() {
  const document = new FakeNode(null, '#document');
  document.ownerDocument = document;
  document.createElement = (tag) => new FakeNode(document, tag.toUpperCase());
  document.createElementNS = (_ns, tag) => new FakeNode(document, tag);
  document.createTextNode = (text) => new FakeNode(document, '#text', String(text));
  document.documentElement = document.appendChild(document.createElement('html'));
  document.body = document.documentElement.appendChild(document.createElement('body'));
  document.getElementById = (id) =>
    all(document).find((n) => n.getAttribute('id') === id) ??
    Object.assign(document.body.appendChild(document.createElement('div')), { id });
  document.querySelector = (selector) => (selector.startsWith('#') ? document.getElementById(selector.slice(1)) : null);
  return document;
}

function all(node) {
  return node.children.flatMap((child) => [child, ...all(child)]);
}

function controlOf(node) {
  return node.getAttribute('data-control') ?? node.dataset.control ?? null;
}

function find(document, control) {
  return all(document).find((n) => controlOf(n) === control) ?? null;
}

function fire(target, type, init = {}) {
  let stopped = false;
  const event = {
    type, target, bubbles: true, defaultPrevented: false,
    pointerId: 1, pointerType: 'touch', isPrimary: true, button: 0, buttons: 1,
    clientX: 20, clientY: 100, offsetX: 20, offsetY: 100,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { stopped = true; },
    stopImmediatePropagation() { stopped = true; },
    ...init,
  };
  for (let node = target; node && !stopped; node = node.parentNode) {
    event.currentTarget = node;
    for (const fn of [...(node.listeners[type] ?? [])]) fn.call(node, event);
    if (typeof node[`on${type}`] === 'function') node[`on${type}`](event);
  }
  return event;
}

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static opened = [];
  constructor(url) {
    Object.assign(this, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.listeners = {};
    FakeSocket.opened.push(this);
  }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn); }
  emit(type, event = {}) {
    if (type === 'open') this.readyState = 1;
    for (const fn of this.listeners[type] ?? []) fn.call(this, event);
    if (typeof this[`on${type}`] === 'function') this[`on${type}`](event);
  }
}

function mixerView(bus, busLabel) {
  return {
    status: 'synced',
    banner: null,
    controlsEnabled: true,
    tabs: [
      { control: 'tab:0', label: 'Main 1-2', selected: bus === 0 },
      { control: 'tab:4', label: 'Ana 3', selected: bus === 4 },
    ],
    strips: [{
      label: 'Line 3',
      fader: { control: `fader:${bus}:2`, position: 0.8, text: '-6.0 dB' },
      pan: bus === 0 ? { control: 'pan:0:2', position: 0.0, text: 'L' } : null,
      mute: { control: `mute:${bus}:2`, on: false },
      solo: { control: `solo:${bus}:2`, on: false },
      link: { control: 'input_link:2', on: false },
    }],
    master: {
      label: busLabel,
      fader: { control: `master:${bus}`, position: 0.85, text: '0.0 dB' },
      mute: { control: `master_mute:${bus}`, on: false },
      link: { control: `bus_link:${bus}`, on: bus === 0 },
    },
    routing: [{
      label: 'Line out 9-10',
      control: 'route:42',
      value: '84-85',
      options: [
        { value: '76-77', label: 'Main 1-2 mix' },
        { value: '84-85', label: 'Ana 7-8 mix' },
        { value: '127-127', label: 'nothing' },
      ],
    }],
  };
}

const ACTION_FOR = {
  drag: { kind: 'set_level', bus: 4, input: 2, db: -6.0 },
  toggle: { kind: 'set_mute', bus: 4, input: 2, on: true },
  route: { kind: 'set_route', output_slots: [43, 40], sources: [76, 77] },
};

test('startApp hands gestures and server messages to the client, draws what it renders and forwards what it sends', () => {
  const document = fakeDocument();
  const calls = [];
  let view = mixerView(4, 'Ana 3');
  const createClient = ({ send, render }) => ({
    gesture(g) {
      calls.push({ gesture: g });
      if (g.type in ACTION_FOR) send({ type: 'change', action: ACTION_FOR[g.type] });
      if (g.type === 'select_tab') view = mixerView(0, 'Main 1-2');
      render(view);
    },
    receive(message) {
      calls.push({ receive: message });
      render(view);
    },
  });
  FakeSocket.opened = [];
  startApp({
    document,
    WebSocket: FakeSocket,
    setTimeout: () => 0,
    location: { protocol: 'http:', host: 'mixer-host:8280', hostname: 'mixer-host', port: '8280', href: 'http://mixer-host:8280/' },
    createClient,
  });
  const socket = FakeSocket.opened.at(-1);
  socket.emit('open');

  // every server message reaches the client as the server sent it, and each render is drawn
  const messages = [
    { type: 'state', status: 'synced', read_only_reason: null, device: { model: '828E', serial: '828E000000', api: 9, firmware: '1.1.8' }, loops: [] },
    { type: 'delta', changes: [{ prop: 'fader', index: 178, value: -6.0 }] },
    { type: 'status', status: 'synced', read_only_reason: null, loops: [] },
    { type: 'accepted', sent: ['f0 00 00 3b f7'] },
    { type: 'refused', kind: 'LOOP', reason: 'S/PDIF in → Ana 1-2 → S/PDIF out', path: ['S/PDIF in', 'Ana 1-2', 'S/PDIF out'] },
  ];
  for (const message of messages) socket.emit('message', { data: JSON.stringify(message) });
  assert.deepEqual(calls, messages.map((m) => ({ receive: m })));
  for (const control of ['tab:0', 'tab:4', 'fader:4:2', 'mute:4:2', 'solo:4:2', 'master:4', 'route:42']) {
    assert.ok(find(document, control), `${control} is drawn`);
  }
  assert.match(document.textContent, /Line 3/);
  const framesSent = () => socket.sent.map((frame) => JSON.parse(frame));
  assert.deepEqual(framesSent(), []);

  // a fader drag: drag gestures with the drawn control id and a 0–1 position, then one release
  calls.length = 0;
  fire(find(document, 'fader:4:2'), 'pointerdown', { clientY: 100, offsetY: 100 });
  fire(find(document, 'fader:4:2'), 'pointermove', { clientY: 60, offsetY: 60 });
  fire(find(document, 'fader:4:2'), 'pointerup', { clientY: 60, offsetY: 60, buttons: 0 });
  const gestures = calls.map((c) => c.gesture);
  assert.ok(gestures.length >= 2);
  const drags = gestures.slice(0, -1);
  for (const g of drags) {
    assert.equal(g.type, 'drag');
    assert.equal(g.control, 'fader:4:2');
    assert.equal(typeof g.value, 'number');
    assert.ok(g.value >= 0 && g.value <= 1, `slider position ${g.value} lies in 0–1`);
  }
  assert.equal(gestures.at(-1).type, 'release');
  assert.equal(gestures.at(-1).control, 'fader:4:2');
  assert.deepEqual(framesSent(), drags.map(() => ({ type: 'change', action: ACTION_FOR.drag })));

  // a mute tap: a toggle to the new state, and the client's change goes to the server
  calls.length = 0;
  socket.sent.length = 0;
  fire(find(document, 'mute:4:2'), 'click');
  assert.deepEqual(calls, [{ gesture: { type: 'toggle', control: 'mute:4:2', value: true } }]);
  assert.deepEqual(framesSent(), [{ type: 'change', action: ACTION_FOR.toggle }]);

  // a routing choice: the chosen source-pair id, and the client's change goes to the server
  calls.length = 0;
  socket.sent.length = 0;
  const select = find(document, 'route:42');
  select.value = '76-77';
  fire(select, 'change');
  assert.deepEqual(calls, [{ gesture: { type: 'route', control: 'route:42', value: '76-77' } }]);
  assert.deepEqual(framesSent(), [{ type: 'change', action: ACTION_FOR.route }]);

  // a tab tap: the client sends nothing, and its new view replaces the drawn one
  calls.length = 0;
  socket.sent.length = 0;
  fire(find(document, 'tab:0'), 'click');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].gesture.type, 'select_tab');
  assert.equal(calls[0].gesture.control, 'tab:0');
  assert.deepEqual(framesSent(), []);
  assert.ok(find(document, 'fader:0:2'), 'the Main 1-2 strip is drawn');
  assert.ok(find(document, 'pan:0:2'), 'the Main 1-2 pan is drawn');
  assert.equal(find(document, 'fader:4:2'), null, 'the Ana 3 strip is gone');
});
