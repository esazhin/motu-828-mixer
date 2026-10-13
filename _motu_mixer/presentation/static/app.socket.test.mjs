import { test } from 'node:test';
import assert from 'node:assert/strict';

import { startApp } from './app.js';

// a document whose elements accept every call and draw nothing: this file is about the socket, not the page
function inertDocument() {
  const element = () => ({
    style: { setProperty() {} },
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    childNodes: [],
    children: [],
    textContent: '',
    appendChild: (child) => child,
    insertBefore: (child) => child,
    append() {},
    replaceChildren() {},
    remove() {},
    setAttribute() {},
    setAttributeNS() {},
    getAttribute: () => null,
    addEventListener() {},
    removeEventListener() {},
  });
  return {
    body: element(),
    documentElement: element(),
    getElementById: element,
    querySelector: element,
    createElement: element,
    createElementNS: element,
    createTextNode: element,
    addEventListener() {},
  };
}

class RecordingSocket {
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
    RecordingSocket.opened.push(this);
  }
  send(data) { this.sent.push(data); }
  close() { this.readyState = 3; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn); }
  emit(type, event = {}) {
    if (type === 'open') this.readyState = 1;
    if (type === 'close') this.readyState = 3;
    for (const fn of this.listeners[type] ?? []) fn.call(this, event);
    if (typeof this[`on${type}`] === 'function') this[`on${type}`](event);
  }
}

test('the page socket opens /ws on the page host, sends changes as JSON, hands every server message to the client and reconnects after 1 s then every 2 s', () => {
  const received = [];
  let clientSend;
  const createClient = ({ send }) => {
    clientSend = send;
    return { gesture() {}, receive(message) { received.push(message); } };
  };
  const timers = [];
  const setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  RecordingSocket.opened = [];
  startApp({
    document: inertDocument(),
    WebSocket: RecordingSocket,
    setTimeout,
    location: { protocol: 'http:', host: 'mixer-host:8280', hostname: 'mixer-host', port: '8280', href: 'http://mixer-host:8280/' },
    createClient,
  });
  const url = 'ws://mixer-host:8280/ws';
  assert.equal(RecordingSocket.opened.length, 1);
  const first = RecordingSocket.opened[0];
  assert.equal(first.url, url);
  first.emit('open');

  // the connection starts with the full state, then deltas, status, a fresh state and the replies to changes
  const state = { type: 'state', status: 'synced', read_only_reason: null, loops: [], links: { inputs: [], buses: [] } };
  const later = [
    { type: 'delta', changes: [{ prop: 'fader', index: 2, value: 0.5, db: -6.02 }] },
    { type: 'status', status: 'read_only', read_only_reason: 'API version 10, expected 9', loops: [] },
    { type: 'state', status: 'synced', read_only_reason: null, loops: [], links: { inputs: [1], buses: [1] } },
    { type: 'accepted', sent: ['f0 00 00 3b 00 01 00 0a 03 78 02 03 00 04 00 05 00 4d 4e f7'] },
    { type: 'refused', kind: 'loop', reason: 'Refused: S/PDIF in → Ana 1-2 → S/PDIF out', path: ['S/PDIF in', 'Ana 1-2', 'S/PDIF out'] },
  ];
  for (const message of [state, ...later]) first.emit('message', { data: JSON.stringify(message) });
  assert.deepEqual(received, [state, ...later]);

  // a change from the client goes out as one JSON text frame
  const change = { type: 'change', action: { kind: 'set_level', bus: 4, input: 2, db: -6.0 } };
  clientSend(change);
  assert.equal(first.sent.length, 1);
  assert.equal(typeof first.sent[0], 'string');
  assert.deepEqual(JSON.parse(first.sent[0]), change);

  const reconnectAfterClose = (socket, expectedMs) => {
    const before = timers.length;
    socket.emit('close', { code: 1006 });
    const scheduled = timers.slice(before);
    assert.equal(scheduled.length, 1, 'one reconnect is scheduled');
    assert.equal(scheduled[0].ms, expectedMs);
    const opened = RecordingSocket.opened.length;
    scheduled[0].fn();
    assert.equal(RecordingSocket.opened.length, opened + 1, 'the reconnect opens a new socket');
    const next = RecordingSocket.opened.at(-1);
    assert.equal(next.url, url);
    return next;
  };

  // the server goes away: retry after 1 s, then every 2 s while it stays away
  const second = reconnectAfterClose(first, 1000);
  const third = reconnectAfterClose(second, 2000);
  const fourth = reconnectAfterClose(third, 2000);

  // the new connection starts again with the state, which reaches the client
  received.length = 0;
  fourth.emit('open');
  const fresh = { type: 'state', status: 'synced', read_only_reason: null, loops: [], links: { inputs: [0], buses: [0] } };
  fourth.emit('message', { data: JSON.stringify(fresh) });
  assert.deepEqual(received, [fresh]);
  clientSend(change);
  assert.deepEqual(fourth.sent.map((frame) => JSON.parse(frame)), [change]);

  // after a connection that worked, the next loss again retries after 1 s
  reconnectAfterClose(fourth, 1000);
});
