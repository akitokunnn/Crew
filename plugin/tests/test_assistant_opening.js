"use strict";
// "助手开局" on a blank workspace opens /assistant.html#opening=<project>. The assistant page writes the
// opening intent into an empty composer and never sends anything by itself.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = file => fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8");

function assistantPage(hash, {projects, filled = true}) {
  const nodes = new Map(), calls = [], replaced = [], listeners = new Map(), prefills = [];
  const stub = () => ({hidden: false, textContent: "", innerHTML: "", dataset: {}, onclick: null, append() {}, replaceChildren() {}, addEventListener() {}});
  const sandbox = {URLSearchParams, URL, Date, JSON, Promise, console,
    matchMedia: () => ({matches: false, addEventListener() {}}),
    localStorage: {getItem: () => null},
    location: {hash, pathname: "/assistant.html", search: "", href: "http://127.0.0.1/assistant.html" + hash},
    history: {replaceState: (_state, _title, url) => { replaced.push(url); sandbox.location.hash = ""; }},
    setTimeout: () => 0, clearTimeout() {},
    addEventListener: (type, fn) => listeners.set(type, fn),
    document: {documentElement: {dataset: {}}, addEventListener() {}, createElement: stub, createTextNode: text => ({text}), getElementById: id => { if (!nodes.has(id)) nodes.set(id, stub()); return nodes.get(id); }},
    fetch: async (url, options) => {
      calls.push({url, method: options.method});
      const value = url === "/api/op/assistant.state" ? {assistant: {session_id: "assistant"}, projects, todos: [], goals: [], sounds: {}, now: "now"}
        : url === "/api/op/interaction.list" ? {requests: []} : {};
      return {ok: true, status: 200, json: async () => value};
    },
    CrewSound: {observe() {}}, CrewPersonalNotes: {create: () => ({open() {}})},
    CrewChat: {initFiles() {}, unmount() {}, update() {}, mount() {}, setNote() {}, prefill: text => { prefills.push(text); return filled; }}};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(web("browser-utils.js"), sandbox);
  vm.runInContext(web("assistant.js"), sandbox);
  return {sandbox, nodes, calls, replaced, listeners, prefills};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test("the opening intent names the workspace, asks for a plan before acting, and is never sent by the page", async () => {
  const page = assistantPage("#opening=fitness", {projects: [{name: "fitness", display_name: "健身打卡"}]});
  await settle(); await settle();
  assert.equal(page.prefills.length, 1);
  const text = page.prefills[0];
  assert.match(text, /工作区「健身打卡」开局/);
  assert.match(text, /领域划分/);
  assert.match(text, /leader 编制/);
  assert.match(text, /首批任务/);
  assert.match(text, /等我确认方案后再创建和启动/);
  assert(!page.calls.some(call => call.url === "/api/op/chat.send"), "the opening is only written into the composer");
  assert.deepEqual(page.replaced, ["/assistant.html"], "the hash is used once, so a reload does not write it again");
  await settle();
  page.listeners.get("hashchange")(); await settle();
  assert.equal(page.prefills.length, 1);
});

test("an existing draft and a removed workspace are both reported instead of silently ignored", async () => {
  const drafted = assistantPage("#opening=fitness", {projects: [{name: "fitness", display_name: null}], filled: false});
  await settle(); await settle();
  assert.match(drafted.prefills[0], /工作区「fitness」开局/);
  assert.match(drafted.nodes.get("assistant-toast").textContent, /输入框里有未发送的内容，开局说明没有覆盖它/);
  const gone = assistantPage("#opening=old", {projects: []});
  await settle(); await settle();
  assert.equal(gone.prefills.length, 0);
  assert.equal(gone.nodes.get("assistant-toast").textContent, "没有找到工作区「old」，可能已被移除。");
  assert.deepEqual(gone.replaced, ["/assistant.html"]);
});

test("an already open assistant takes a later opening through the hash change", async () => {
  const page = assistantPage("", {projects: [{name: "a", display_name: "甲"}, {name: "b", display_name: "乙"}]});
  await settle(); await settle();
  assert.equal(page.prefills.length, 0);
  page.sandbox.location.hash = "#opening=b";
  page.listeners.get("hashchange")(); await settle();
  assert.equal(page.prefills.length, 1);
  assert.match(page.prefills[0], /工作区「乙」开局/);
});
