"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const read = file => fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8");

function render({search = "?project=田野", hash = "", width = 375, native = true, desktop = false} = {}) {
  const messages = [];
  let receive;
  function element() {
    const children = [];
    const attrs = new Map();
    return {
      children, dataset: {}, textContent: "", style: {},
      append(...items) { children.push(...items); },
      prepend(item) { children.unshift(item); },
      addEventListener() {},
      setAttribute(key, value) { attrs.set(key, value); },
      getAttribute(key) { return attrs.get(key); },
      hasAttribute(key) { return attrs.has(key); },
      removeAttribute(key) { attrs.delete(key); },
      toggleAttribute(key, present) { if (present) attrs.set(key, ""); else attrs.delete(key); },
      querySelector(selector) { return children.find(node => selector === "." + node.className?.split(" ").at(-1)) || null; },
      querySelectorAll() { return []; },
    };
  }
  const topbar = element(), toast = element();
  const media = {matches: width <= 760, addEventListener() {}};
  const body = element();
  body.classList = {contains: () => false, toggle() {}};
  const document = {
    body, readyState: "complete", addEventListener() {},
    getElementById: id => id === "toast-root" ? toast : null,
    querySelector: selector => selector === "#main > .wh-head > .right" ? topbar : null,
    createElement: tag => {
      const item = element();
      if (tag === "nav") {
        const tabs = new Map();
        item.querySelector = selector => {
          if (!tabs.has(selector)) tabs.set(selector, element());
          return tabs.get(selector);
        };
      }
      return item;
    },
  };
  const sandbox = {
    location: {search, hash}, document, URLSearchParams,
    localStorage: {setItem() {}}, history: {pushState() {}, replaceState() {}},
    matchMedia: () => media, addEventListener() {}, queueMicrotask,
    MutationObserver: class { observe() {} },
    setTimeout() {}, clearTimeout() {},
    crewDesktopShell: desktop,
    CrewAndroid: native ? {
      addEventListener(event, callback) { assert.equal(event, "message"); receive = callback; },
      postMessage(raw) { messages.push(JSON.parse(raw)); },
    } : undefined,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("browser-utils.js"), sandbox);
  vm.runInContext(read("mobile-shell.js"), sandbox);
  const respond = (id, contents) => receive({data: JSON.stringify({id, ...contents})});
  return {topbar, messages, respond, toast, bridge: sandbox.CrewAndroidBridge};
}

test("a phone workspace has a voice button that sends its workspace to the native bridge", async () => {
  const page = render();
  const [button] = page.topbar.children;
  assert.equal(button.getAttribute("aria-label"), "语音");
  const opening = button.onclick();
  assert.equal(button.disabled, true);
  assert.deepEqual(page.messages[0], {id: "1", method: "openVoice", params: {project: "田野"}});
  page.respond("1", {ok: true, result: null});
  await opening;
  assert.equal(button.disabled, false);
});

test("the shortcut is hidden outside the Android phone workspace home", () => {
  for (const options of [{native: false}, {desktop: true}, {width: 761},
    {search: ""}, {search: "?project="}, {search: "?project=a&project=b"}, {hash: "#todos"}]) {
    assert.equal(render(options).topbar.children.length, 0, JSON.stringify(options));
  }
});

test("concurrent bridge replies are matched by id and native errors reach the caller", async () => {
  const page = render();
  const a = page.bridge.call("available"), b = page.bridge.call("notificationsEnabled");
  page.respond(page.messages[1].id, {ok: true, result: false});
  page.respond(page.messages[0].id, {ok: true, result: {version: 1}});
  assert.equal(await b, false);
  assert.equal((await a).version, 1);
  const open = page.topbar.children[0].onclick();
  page.respond(page.messages[2].id, {ok: false, error: "请先选择工作区"});
  await open;
  assert.equal(page.toast.children[0].textContent, "请先选择工作区");
});
