"use strict";
// A small executable DOM for interaction tests of modules that build nodes with
// the DOM API: element tree, reflected attributes, dataset, bubbling events,
// focus and <dialog>. There is deliberately no HTML parser.
const kebab = name => name.replace(/[A-Z]/g, char => "-" + char.toLowerCase());
const camel = name => name.replace(/-([a-z])/g, (_, char) => char.toUpperCase());

class Event {
  constructor(type, init = {}) { this.type = type; this.bubbles = Boolean(init.bubbles); this.defaultPrevented = false; this.stopped = false; Object.assign(this, init.detail || {}); }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

class Text {
  constructor(document, text) { this.ownerDocument = document; this.parentNode = null; this.data = String(text); this.nodeType = 3; }
  get textContent() { return this.data; }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this); this.parentNode = null; }
}

function parseCompound(text) {
  const result = {tag:null, id:null, classes:[], attributes:[]};
  const pattern = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/g;
  let match, consumed = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index !== consumed) throw Error("Unsupported selector: " + text);
    consumed = pattern.lastIndex;
    if (match[1]) result.tag = match[1].toUpperCase();
    else if (match[2]) result.id = match[2];
    else if (match[3]) result.classes.push(match[3]);
    else result.attributes.push({name:match[4], value:match[5] ?? match[6] ?? match[7] ?? null});
  }
  if (consumed !== text.length) throw Error("Unsupported selector: " + text);
  return result;
}
function splitSelector(selector) {
  const parts = []; let current = "", depth = 0, quote = null;
  for (const char of selector.trim()) {
    if (quote) { current += char; if (char === quote) quote = null; continue; }
    if (char === '"' || char === "'") { quote = char; current += char; continue; }
    if (char === "[") depth++;
    if (char === "]") depth--;
    if (/\s/.test(char) && depth === 0) { if (current) parts.push(current); current = ""; continue; }
    current += char;
  }
  if (current) parts.push(current);
  return parts.map(parseCompound);
}
function matchesCompound(node, compound) {
  if (!(node instanceof Element)) return false;
  if (compound.tag && node.tagName !== compound.tag) return false;
  if (compound.id && node.getAttribute("id") !== compound.id) return false;
  if (compound.classes.some(name => !node.classList.contains(name))) return false;
  return compound.attributes.every(({name, value}) => node.hasAttribute(name) && (value === null || node.getAttribute(name) === value));
}

class Element {
  constructor(document, tag) {
    this.ownerDocument = document; this.tagName = tag.toUpperCase(); this.nodeType = 1;
    this.parentNode = null; this.childNodes = []; this.attributeMap = new Map(); this.listeners = new Map();
    this.checked = false; this.value = ""; this.open = false; this.style = {};
    const element = this;
    this.dataset = new Proxy({}, {
      get: (_, key) => typeof key === "string" ? element.getAttribute("data-" + kebab(key)) ?? undefined : undefined,
      set: (_, key, value) => { element.setAttribute("data-" + kebab(key), value); return true; },
      has: (_, key) => element.hasAttribute("data-" + kebab(key)),
      ownKeys: () => [...element.attributeMap.keys()].filter(name => name.startsWith("data-")).map(name => camel(name.slice(5))),
      getOwnPropertyDescriptor: (_, key) => element.hasAttribute("data-" + kebab(key)) ? {enumerable:true, configurable:true, value:element.getAttribute("data-" + kebab(key))} : undefined
    });
  }
  get children() { return this.childNodes.filter(node => node instanceof Element); }
  get id() { return this.getAttribute("id") ?? ""; }
  set id(value) { this.setAttribute("id", value); }
  get className() { return this.getAttribute("class") ?? ""; }
  set className(value) { this.setAttribute("class", value); }
  get type() { return this.getAttribute("type") ?? (this.tagName === "BUTTON" ? "submit" : ""); }
  set type(value) { this.setAttribute("type", value); }
  get disabled() { return this.hasAttribute("disabled"); }
  set disabled(value) { if (value) this.setAttribute("disabled", ""); else this.removeAttribute("disabled"); }
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(value) { if (value) this.setAttribute("hidden", ""); else this.removeAttribute("hidden"); }
  get classList() {
    const names = () => this.className.split(/\s+/).filter(Boolean);
    return {contains:name => names().includes(name),
      add:(...values) => { this.className = [...new Set([...names(), ...values])].join(" "); },
      remove:(...values) => { this.className = names().filter(name => !values.includes(name)).join(" "); },
      toggle:(name, force) => { const on = force ?? !names().includes(name); if (on) this.classList.add(name); else this.classList.remove(name); return on; }};
  }
  setAttribute(name, value) { this.attributeMap.set(name.toLowerCase(), String(value)); }
  getAttribute(name) { return this.attributeMap.has(name.toLowerCase()) ? this.attributeMap.get(name.toLowerCase()) : null; }
  hasAttribute(name) { return this.attributeMap.has(name.toLowerCase()); }
  removeAttribute(name) { this.attributeMap.delete(name.toLowerCase()); }
  get textContent() { return this.childNodes.map(node => node.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); if (value !== "" && value !== null && value !== undefined) this.append(String(value)); }
  append(...nodes) {
    for (let node of nodes) {
      if (typeof node === "string") node = new Text(this.ownerDocument, node);
      node.remove?.();
      node.parentNode = this; this.childNodes.push(node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    if (this.ownerDocument.activeElement && !this.ownerDocument.activeElement.isConnected) this.ownerDocument.activeElement = this.ownerDocument.body;
    this.append(...nodes);
    if (this.ownerDocument.activeElement && !this.ownerDocument.activeElement.isConnected) this.ownerDocument.activeElement = this.ownerDocument.body;
  }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this); this.parentNode = null; }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node === this.ownerDocument.documentElement; }
  contains(other) { for (let node = other; node; node = node.parentNode) if (node === this) return true; return false; }
  descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
  matches(selector) {
    const parts = splitSelector(selector);
    if (!matchesCompound(this, parts[parts.length - 1])) return false;
    let node = this.parentNode;
    for (let index = parts.length - 2; index >= 0; index--) {
      while (node && !matchesCompound(node, parts[index])) node = node.parentNode;
      if (!node) return false;
      node = node.parentNode;
    }
    return true;
  }
  querySelectorAll(selector) { return this.descendants().filter(node => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let node = this; node instanceof Element; node = node.parentNode) if (node.matches(selector)) return node; return null; }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== listener)); }
  dispatchEvent(event) {
    event.target = this;
    for (let node = this; node; node = event.bubbles ? node.parentNode : null) {
      event.currentTarget = node;
      for (const listener of [...(node.listeners?.get(event.type) || [])]) listener.call(node, event);
      if (event.stopped) break;
    }
    return !event.defaultPrevented;
  }
  focus() { if (this.isConnected) this.ownerDocument.activeElement = this; }
  click() {
    if (this.disabled) return;
    if (this.tagName === "INPUT" && this.type === "checkbox") {
      this.checked = !this.checked;
      this.dispatchEvent(new Event("click", {bubbles:true}));
      this.dispatchEvent(new Event("change", {bubbles:true}));
      return;
    }
    this.dispatchEvent(new Event("click", {bubbles:true}));
  }
  showModal() { if (!this.isConnected) throw Error("dialog must be connected"); this.open = true; this.setAttribute("open", ""); }
  close() { this.open = false; this.removeAttribute("open"); this.dispatchEvent(new Event("close")); }
}

function createDocument() {
  const document = {activeElement:null};
  document.createElement = tag => new Element(document, tag);
  document.createTextNode = text => new Text(document, text);
  document.documentElement = new Element(document, "html");
  document.body = new Element(document, "body");
  document.documentElement.append(document.body);
  document.activeElement = document.body;
  return document;
}
// Native Escape on an open modal dialog fires "cancel", then closes unless prevented.
function pressEscape(dialog) {
  const event = new Event("cancel");
  if (dialog.dispatchEvent(event)) dialog.close();
}
const visibleText = node => node.textContent.replace(/\s+/g, " ").trim();

module.exports = {createDocument, Event, pressEscape, visibleText};
