"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = file => fs.readFileSync(path.join(__dirname, "../crew/web", file), "utf8");
// Same realm as the assertions, so arrays and objects compare structurally.
globalThis.window = {};
vm.runInThisContext(web("notes-model.js"));
const M = globalThis.window.CrewNotesModel;

const NOW = new Date(2026, 10, 11, 15, 0).getTime(); // Wednesday
const iso = (days, hours = 0) => new Date(NOW - days * 86400000 - hours * 3600000).toISOString();
let serial = 0;
const note = patch => ({id: `n${++serial}`, project: null, title: null, text: "一条想法", original_text: "一条想法", tags: [], state: "open", stage: "new",
  version: 1, created_at: iso(1), updated_at: iso(1), pinned_at: null, snoozed_until: null, merged_into: null, merged_from: [], last_discussed_at: null,
  reviewed_at: null, summaries: [], discussions: {count: 0, last_at: null, session_id: null, last_seq: null}, stale: false, source: {type: "human"}, leader: null, ...patch});

test("tag filters default to all-of, switch to any-of, and untagged/workspace/view narrow independently", () => {
  const a = note({tags: ["产品", "体验"]}), b = note({tags: ["产品"], project: "Crew"}), c = note({tags: []}), d = note({tags: ["产品"], state: "archived"});
  const promoted = note({tags: ["产品"], state: "archived", stage: "promoted"}), stale = note({tags: ["体验"], stale: true});
  const all = [a, b, c, d, promoted, stale], ids = list => list.map(item => item.id);
  const view = patch => ({...M.DEFAULTS, ...patch});
  assert.deepEqual(ids(M.filter(all, view({tags: ["产品", "体验"]}))), [a.id]);
  assert.deepEqual(ids(M.filter(all, view({tags: ["产品", "体验"], mode: "any"}))), [a.id, b.id, stale.id]);
  assert.deepEqual(ids(M.filter(all, view({untagged: true}))), [c.id]);
  assert.deepEqual(ids(M.filter(all, view({ws: "Crew"}))), [b.id]);
  assert.deepEqual(ids(M.filter(all, view({global: true, tags: ["产品"]}))), [a.id]);
  assert.deepEqual(ids(M.filter(all, view({view: "stale"}))), [stale.id]);
  assert.deepEqual(ids(M.filter(all, view({view: "archived"}))), [d.id], "promoted ideas are listed under their own view");
  assert.deepEqual(ids(M.filter(all, view({view: "promoted", tags: ["产品"]}))), [promoted.id]);
  const counts = M.counts(all);
  assert.deepEqual([counts.open, counts.stale, counts.promoted, counts.archived, counts.untagged], [4, 1, 1, 1, 1]);
  assert.equal(counts.tags.get("产品"), 2, "tag counts cover open ideas only");
});

test("time grouping puts pinned, then needs-review, then today / this week / earlier; stage grouping follows the three stages", () => {
  const pinnedOld = note({pinned_at: iso(0, 1), updated_at: iso(40)}), pinnedNew = note({pinned_at: iso(0, 0.5), updated_at: iso(40)});
  const stale = note({stale: true, updated_at: iso(20)}), today = note({updated_at: iso(0, 2)}), monday = note({updated_at: iso(2)});
  const earlier = note({updated_at: iso(6)}), discussed = note({stage: "discussed", updated_at: iso(0, 3)});
  const sections = M.group([earlier, stale, monday, today, pinnedOld, pinnedNew, discussed], {...M.DEFAULTS}, NOW);
  assert.deepEqual(sections.map(section => section.key), ["pinned", "stale", "today", "week", "earlier"]);
  assert.deepEqual(sections[0].items.map(item => item.id), [pinnedNew.id, pinnedOld.id], "most recently pinned first");
  assert.equal(sections[1].tone, "warn");
  assert.deepEqual(sections[2].items.map(item => item.id), [today.id, discussed.id]);
  assert.deepEqual(sections[3].items.map(item => item.id), [monday.id]);
  assert.deepEqual(sections[4].items.map(item => item.id), [earlier.id]);
  const archivedView = M.group([note({state: "archived", pinned_at: iso(0), updated_at: iso(0, 1)})], {...M.DEFAULTS, view: "archived"}, NOW);
  assert.deepEqual(archivedView.map(section => section.key), ["today"], "pinned and review sections only exist on the open wall");
  const byStage = M.group([today, discussed, pinnedOld], {...M.DEFAULTS, group: "stage"}, NOW);
  assert.deepEqual(byStage.map(section => [section.key, section.label]), [["new", "新想法"], ["discussed", "讨论过"]]);
  assert.equal(byStage[0].items[0].id, pinnedOld.id, "pinned ideas lead their stage");
  const byDiscussion = M.sorted([note({updated_at: iso(0)}), note({last_discussed_at: iso(3)}), note({last_discussed_at: iso(1)})], "discussed");
  assert.deepEqual(byDiscussion.map(item => item.last_discussed_at), [iso(1), iso(3), null]);
});

test("duplicate hints use local character-bigram Jaccard at 0.6 and only pair open ideas", () => {
  assert.equal(M.similarity("切页要有翻页动画", "切页要有翻页动画"), 1);
  assert(M.similarity("切页要有翻页动画。", "切页时要有翻页动画") >= M.THRESHOLD);
  assert(M.similarity("切页要有翻页动画", "QQ 通知合并成每日摘要") < 0.1);
  assert.equal(M.similarity("", "任何内容"), 0);
  const a = note({text: "Git 页分支做成卡片"}), b = note({text: "Git 页的分支做成卡片"}), c = note({text: "任务报告支持导出 PDF"}), d = note({text: "Git 页分支做成卡片", state: "archived"});
  const pairs = M.duplicates([a, b, c, d]);
  assert.deepEqual(pairs.get(a.id), [b.id]); assert.deepEqual(pairs.get(b.id), [a.id]);
  assert.equal(pairs.has(c.id), false); assert.equal(pairs.has(d.id), false);
});

test("filter state round-trips through the URL hash and ignores unknown values", () => {
  const state = {...M.DEFAULTS, tags: ["产品", "体验 设计"], mode: "any", ws: "Crew 工作区", sort: "discussed", group: "stage", view: "stale"};
  const hash = M.toHash(state);
  assert.match(hash, /^#tags=/); assert(!hash.includes("产品"), "non-ASCII names are encoded");
  assert.deepEqual(M.parseHash(hash), state);
  assert.equal(M.toHash({...M.DEFAULTS}), "");
  assert.deepEqual(M.parseHash(""), {...M.DEFAULTS, tags: []});
  assert.deepEqual(M.parseHash("#global=1&untagged=1&tags=a"), {...M.DEFAULTS, tags: [], untagged: true, global: true});
  assert.deepEqual(M.parseHash("#sort=random&group=board&view=trash&mode=some"), {...M.DEFAULTS, tags: []});
  assert.deepEqual(M.parseHash(M.toHash({...M.DEFAULTS, global: true})), {...M.DEFAULTS, tags: [], global: true});
});

test("quick capture extracts #tags, completes the token under the caret and keeps titles from the first line", () => {
  assert.deepEqual(M.parseCapture("#产品 切页要有翻页动画 #体验\n第二行 #产品"), {text: "切页要有翻页动画\n第二行", tags: ["产品", "体验"]});
  assert.deepEqual(M.parseCapture("#只有标签"), {text: "", tags: ["只有标签"]});
  assert.deepEqual(M.parseCapture("issue#12 不是标签"), {text: "issue#12 不是标签", tags: []});
  assert.deepEqual(M.token("想法 #产", 5), {kind: "#", query: "产", start: 3, end: 5});
  assert.deepEqual(M.token("@Cr", 3), {kind: "@", query: "Cr", start: 0, end: 3});
  assert.equal(M.token("写完了 ", 4), null);
  assert(M.fuzzy("稳定性", "稳性")); assert(!M.fuzzy("体验", "产"));
  const untitled = note({text: "\n  Leader 栏放任务\n组员随派工增减"});
  assert.equal(M.title(untitled), "Leader 栏放任务"); assert.equal(M.body(untitled), "组员随派工增减");
  const titled = note({title: "侧栏", text: "Leader 栏放任务"});
  assert.equal(M.title(titled), "侧栏"); assert.equal(M.body(titled), "Leader 栏放任务");
  assert.deepEqual(M.orderTags([{name: "b", pinned: false, sort_order: 0}, {name: "a", pinned: true, sort_order: 5}, {name: "c", pinned: true, sort_order: 1}]).map(tag => tag.name), ["c", "a", "b"]);
  assert.equal(M.relative(iso(0, 1), NOW), "今天"); assert.equal(M.relative(iso(1), NOW), "昨天"); assert.equal(M.relative(iso(5), NOW), "5 天前");
});

// The assistant page reads #note=<id>, asks the chat to attach it and prefill,
// and never sends anything by itself.
function assistantPage(hash, notes) {
  const nodes = new Map(), calls = [], replaced = [], listeners = new Map(), chat = {mounts: [], notes: []};
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
      const value = url === "/api/assistant/state" ? {assistant: {session_id: "assistant", native_handoff: null}, projects: [], todos: [], goals: [], sounds: {}, now: "now"}
        : url.startsWith("/api/personal/notes") ? {notes, stale_days: 14} : {};
      return {ok: true, status: 200, json: async () => value};
    },
    CrewSound: {observe() {}}, CrewNativeHandoff: {markup: () => ""}, CrewPersonalNotes: {create: () => ({open() {}})},
    CrewChat: {initFiles() {}, unmount() {}, update() {}, mount: options => chat.mounts.push(options), setNote: (value, prefill) => chat.notes.push([value, prefill])}};
  sandbox.window = sandbox;
  vm.runInNewContext(web("assistant.js"), sandbox);
  return {sandbox, nodes, calls, replaced, listeners, chat};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test("assistant page attaches the idea from #note, prefills without sending, and clears the hash once the idea is used", async () => {
  const page = assistantPage("#note=n-7", [note({id: "n-7", title: null, text: "\n想法墙\n卡片能维护"})]);
  await settle(); await settle();
  assert.equal(page.chat.mounts.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(page.chat.notes)), [[{id: "n-7", title: "想法墙"}, "帮我把这个想法想清楚"]]);
  assert(page.calls.some(call => call.url === "/api/personal/notes?include_archived=1" && call.method === "GET"));
  assert(!page.calls.some(call => call.url === "/api/chat/send"), "opening an idea never sends a message");
  page.chat.mounts[0].onNoteCleared();
  assert.deepEqual(page.replaced, ["/assistant.html"]);

  page.sandbox.location.hash = "#note=gone";
  page.listeners.get("hashchange")(); await settle(); await settle();
  assert.equal(page.chat.notes.length, 1, "a missing idea attaches nothing");
  assert.equal(page.nodes.get("assistant-toast").textContent, "没有找到这条想法，可能已被删除。");
  assert.deepEqual(page.replaced, ["/assistant.html", "/assistant.html"]);
});

test("assistant page without #note reads no ideas", async () => {
  const page = assistantPage("", []);
  await settle(); await settle();
  assert.equal(page.chat.notes.length, 0);
  assert(!page.calls.some(call => call.url.startsWith("/api/personal/notes")));
});
