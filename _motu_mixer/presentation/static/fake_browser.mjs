// A minimal document, pointer events and socket for driving app.js under node --test.

class FakeNode {
  constructor(ownerDocument, tagName, text = null) {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName;
    this.nodeType = text === null ? 1 : 3;
    this.data = text;
    this.parentNode = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.style = { setProperty(name, value) { this[name] = value; } };
    this.listeners = {};
    this.value = '';
    this.captured = new Set();
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(value) { this.replaceChildren(this.ownerDocument.createTextNode(String(value))); }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    this.childNodes.push(child);
    child.parentNode = this;
    return child;
  }
  append(...nodes) { for (const n of nodes) this.appendChild(n); }
  replaceChildren(...nodes) {
    for (const n of [...this.childNodes]) this.removeChild(n);
    this.append(...nodes);
  }
  removeChild(child) {
    this.childNodes.splice(this.childNodes.indexOf(child), 1);
    child.parentNode = null;
    return child;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 40, height: 200 }; }
}

export function fakeDocument() {
  const document = new FakeNode(null, '#document');
  document.ownerDocument = document;
  document.createElement = (tag) => new FakeNode(document, tag.toUpperCase());
  document.createElementNS = (_ns, tag) => new FakeNode(document, tag);
  document.createTextNode = (text) => new FakeNode(document, '#text', text);
  document.body = document.appendChild(document.createElement('body'));
  for (const id of ['status', 'tabs', 'strips', 'routing']) {
    document.body.appendChild(document.createElement('div')).setAttribute('id', id);
  }
  document.getElementById = (id) => all(document).find((n) => n.getAttribute('id') === id) ?? null;
  return document;
}

function all(node) {
  return node.children.flatMap((child) => [child, ...all(child)]);
}

export function find(document, control) {
  return all(document).find((n) => n.getAttribute('data-control') === control) ?? null;
}

export function fire(target, type, init = {}) {
  const event = {
    type, target, defaultPrevented: false, pointerId: 1, pointerType: 'mouse', clientX: 20, clientY: 100,
    preventDefault() { this.defaultPrevented = true; },
    ...init,
  };
  for (let node = target; node; node = node.parentNode) {
    for (const fn of node.listeners[type] ?? []) fn.call(node, event);
  }
  return event;
}

export class FakeSocket {
  static OPEN = 1;
  static opened = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    this.listeners = {};
    FakeSocket.opened.push(this);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  emit(type, event = {}) {
    this.readyState = { open: 1, close: 3 }[type] ?? this.readyState;
    for (const fn of this.listeners[type] ?? []) fn(event);
  }
}

export const PAGE_LOCATION = { protocol: 'http:', host: 'mixer-host:8280' };
