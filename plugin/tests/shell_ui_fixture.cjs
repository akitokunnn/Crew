"use strict";
// Small DOM surface for the shell controllers. No browser or audio device is used.
function fixture() {
  const doc = {activeElement:null, visibilityState:"visible", hasFocus:() => true};
  function events(target) {
    const listeners = new Map();
    target.addEventListener = (name, listener) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(listener); };
    target.removeEventListener = (name, listener) => listeners.get(name)?.delete(listener);
    target.emit = (name, event = {}) => { const value = {target, defaultPrevented:false, preventDefault(){this.defaultPrevented=true;}, ...event}; for (const listener of listeners.get(name) || []) listener(value); return value; };
    target.listenerCount = () => [...listeners.values()].reduce((total, set) => total + set.size, 0);
    return target;
  }
  class Element {
    constructor(tag = "div") {
      events(this); this.tagName = tag.toLowerCase(); this.children = []; this.parentElement = null; this.dataset = {}; this.attributes = {};
      this.style = {setProperty(name, value){this[name]=value;}}; this.className = ""; this.disabled = false; this.hidden = false; this.value = "";
      this.offsetWidth = 180; this.offsetHeight = 100; this.rect = {left:100, right:200, top:100, bottom:120}; this.focuses = 0;
      this.classList = {contains:name => this.className.split(/\s+/).includes(name), add:name => { if (!this.classList.contains(name)) this.className = `${this.className} ${name}`.trim(); }, remove:name => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(" "); }};
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === "class") this.className = String(value);
      else if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
      else if (name === "disabled" || name === "hidden") this[name] = true;
      else if (name === "id" || name === "value") this[name] = String(value);
    }
    getAttribute(name) { return this.attributes[name] ?? null; }
    append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
    get isConnected() { return this === doc.body || this === doc.documentElement || !!this.parentElement?.isConnected; }
    get previousElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null; }
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
      if (selector.includes(",")) return selector.split(",").some(value => this.matches(value));
      if (selector.startsWith("#")) return this.id === selector.slice(1);
      if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
      if (selector.startsWith("button:not") || selector.startsWith("input:not")) return this.tagName === selector.split(":")[0] && !this.disabled;
      if (selector === "[data-act]") return this.dataset.act !== undefined;
      if (selector === '[data-act$="-menu"]') return this.dataset.act?.endsWith("-menu") || false;
      const attr = selector.match(/^\[([^=]+)="([^"]+)"\]$/);
      if (attr) return this.getAttribute(attr[1]) === attr[2];
      return this.tagName === selector;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getBoundingClientRect() { return this.rect; }
    focus() { doc.activeElement = this; this.focuses++; }
    scrollIntoView() { this.scrolled = true; }
    set innerHTML(html) {
      this.html = html; this.children = []; const stack = [this];
      for (const token of html.matchAll(/<\/?[a-z][^>]*>/gi)) {
        const source = token[0];
        if (source.startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
        const tag = source.match(/^<([a-z][\w-]*)/i)[1], child = new Element(tag);
        for (const attr of source.slice(tag.length + 1, -1).matchAll(/([\w-]+)(?:="([^"]*)"|='([^']*)')?/g)) child.setAttribute(attr[1], attr[2] ?? attr[3] ?? "");
        stack.at(-1).append(child);
        if (!["input", "br", "hr", "img", "meta", "link"].includes(tag.toLowerCase()) && !source.endsWith("/>")) stack.push(child);
      }
    }
    get innerHTML() { return this.html || ""; }
  }
  events(doc); doc.createElement = tag => new Element(tag); doc.body = new Element("body"); doc.documentElement = new Element("html");
  doc.getElementById = id => doc.body.querySelector(`#${id}`);
  const media = events({}), win = {innerWidth:500, innerHeight:400, matchMedia:() => media};
  function element(tag, attrs = {}, parent = doc.body) { const node = new Element(tag); for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value); parent?.append(node); return node; }
  return {doc, win, media, element};
}
module.exports = {fixture};
