"use strict";
// The rail on the assistant and ideas pages: a workspace page (?project=X) shows that workspace's own 助手 and 想法,
// lit by the stable name in the URL; the 系统管理员 page has no project and its entry sits at the bottom of the rail
// in a browser (the desktop shows it in the window's top-right corner instead).
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = file => fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8");

function rail({page, search = "", recent = null, desktop = false, projects = [{name: "A"}, {name: "B & co", display_name: "乙"}],
  readRooms = async () => ({ok: true, json: async () => ({rooms:[]})})}) {
  const sidebar = {innerHTML: ""};
  const events = new Map();
  const sandbox = {URLSearchParams, URL, console, Promise,
    location: {search, hash: "", pathname: page === "assistant-page" ? "/assistant.html" : "/notes.html"},
    localStorage: {getItem: key => key === "crew.mobile.project" ? recent : null, setItem() {}},
    matchMedia: () => ({matches: false, addEventListener() {}}),
    addEventListener(name, callback) { events.set(name, callback); },
    fetch: async url => url.startsWith("/api/op/war_room.list") ? readRooms() : {ok: true, json: async () => ({projects})},
    document: {body: {classList: {contains: name => name === page}}, documentElement: {dataset: {}, style: {setProperty() {}}},
      getElementById: id => id === "sidebar" ? sidebar : null, addEventListener() {}}};
  sandbox.window = sandbox;
  if (desktop) sandbox.crewDesktopTheme = {onChange() {}, get: async () => ({mode: "system"})};
  vm.runInNewContext(web("browser-utils.js"), sandbox);
  vm.runInNewContext(web("global-rail.js"), sandbox);
  sandbox.CrewGlobalRail.update({projects});
  return {sidebar, events};
}
const links = html => [...html.matchAll(/<a class="rail-button([^"]*)" href="([^"]*)"[^>]*aria-label="([^"]*)"/g)].map(([, cls, href, label]) => ({href: href.replace(/&amp;/g, "&"), label, active: cls.includes("active")}));

test("a workspace's ideas page lights its own 想法 and links its own 助手, never another workspace's", () => {
  const {sidebar: {innerHTML: html}} = rail({page: "notes-page", search: "?project=B%20%26%20co", recent: "A"});
  const items = links(html);
  const own = items.filter(item => /^\/(assistant|notes)\.html/.test(item.href));
  assert.deepEqual(own.map(item => [item.href, item.label, item.active]), [
    ["/assistant.html?project=B%20%26%20co", "乙 · 助手", false],
    ["/notes.html?project=B%20%26%20co", "乙 · 想法", true],
    ["/assistant.html", "系统管理员", false]]);
  assert(items.some(item => item.href === "/?project=B%20%26%20co#architecture"), "the rail follows the page's workspace, not the recent one");
  assert.doesNotMatch(html, /全局助手|想法墙/);
});

test("the 系统管理员 page lights the bottom entry and keeps the rail on the most recent workspace", () => {
  const {sidebar: {innerHTML: html}} = rail({page: "assistant-page", recent: "A"});
  const items = links(html);
  assert.deepEqual(items.filter(item => item.active).map(item => item.href), ["/assistant.html"]);
  assert(items.some(item => item.href === "/assistant.html?project=A" && !item.active));
  assert(items.some(item => item.href === "/notes.html?project=A" && !item.active));
  assert.match(html, /rail-admin[^"]*" href="\/assistant\.html" aria-current="page"/);
});

test("inside the desktop the rail has no 系统管理员 entry: the window's top-right button is it", () => {
  const {sidebar: {innerHTML: html}} = rail({page: "assistant-page", search: "?project=A", desktop: true});
  assert.doesNotMatch(html, /rail-admin|系统管理员/);
  assert(links(html).some(item => item.href === "/assistant.html?project=A" && item.active));
});

test("独立页读作战室失败时，悬停和无障碍名称保留服务端原因，重读成功后恢复状态", async () => {
  let available = false;
  const {sidebar, events} = rail({page: "notes-page", search: "?project=A", async readRooms() {
    return available ? {ok: true, json: async () => ({rooms:[]})} :
      {ok: false, json: async () => ({error:'房间 "A&B" 暂时不可读'})};
  }});
  assert.match(sidebar.innerHTML, /data-war-room-state="loading"/);
  await new Promise(setImmediate);
  const label = '作战室 · 读取失败 · 房间 &quot;A&amp;B&quot; 暂时不可读';
  assert(sidebar.innerHTML.includes(`title="${label}" aria-label="${label}"`));
  assert.match(sidebar.innerHTML, /data-war-room-state="error"/);
  available = true;
  events.get("focus")();
  await new Promise(setImmediate);
  assert.match(sidebar.innerHTML, /data-war-room-state="empty"/);
  assert.doesNotMatch(sidebar.innerHTML, /暂时不可读/);
});

test("pageProject accepts exactly one non-empty project and nothing else", () => {
  const sandbox = {location: {search: ""}, URLSearchParams};
  sandbox.window = sandbox;
  vm.runInNewContext(web("browser-utils.js"), sandbox);
  const read = search => JSON.parse(JSON.stringify(sandbox.CrewBrowser.pageProject(search)));
  assert.deepEqual(read(""), {mode: "home", project: null});
  assert.deepEqual(read("?project=A%20B"), {mode: "workspace", project: "A B"});
  for (const search of ["?project=", "?project=%20", "?project=A&project=B", "?project=A&ws=B", "?context_project=A"]) assert.equal(read(search).mode, "invalid", search);
});
