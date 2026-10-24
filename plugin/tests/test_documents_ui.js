"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const web = path.join(__dirname, "../crew/web");
const plain = value => JSON.parse(JSON.stringify(value));
function documentsHarness(api = async () => ({documents:[]})) {
  let clock = 100000;
  class Clock extends Date { static now() { return clock; } }
  const sandbox = {window:{addEventListener(){}}, document:{addEventListener(){}, getElementById:() => null}, Date:Clock};
  vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(web, "documents.js"), "utf8"), sandbox);
  return {module:sandbox.window.CrewDocuments, create:() => sandbox.window.CrewDocuments.create({project:"alpha", api, toast(){}}), advance:ms => { clock += ms; }};
}

test("document ordering and filters prioritize important summaries without mixing groups", () => {
  const {module} = documentsHarness();
  const docs = [
    {id:1, title:"普通汇总", kind:"summary", important:false, leader_session_id:"one", updated_at:"2026-10-24"},
    {id:2, title:"重要报告", kind:"report", important:true, leader_session_id:"two", updated_at:"2026-10-24"},
    {id:3, title:"重要汇总", summary:null, path:"docs/交付.md", kind:"summary", important:true, leader_session_id:"one", updated_at:"2026-10-23"}
  ];
  assert.deepEqual(plain(module.sorted(docs)).map(doc => doc.id), [3, 2, 1]);
  assert.deepEqual(plain(module.filtered(docs, {query:"交付", group:"one", important:true})).map(doc => doc.id), [3]);
  assert.deepEqual(plain(module.filtered(docs, {group:"two", kind:"summary"})), []);
  assert.deepEqual(docs.map(doc => doc.id), [1, 2, 3]);
});

test("document refresh deduplicates in-flight work and uses a bounded live-view cache", async () => {
  let requests = 0, release;
  const ready = new Promise(resolve => { release = resolve; });
  const harness = documentsHarness(async () => { requests++; await ready; return {documents:[{id:requests}]}; });
  const client = harness.create();
  const first = client.refresh(); const duplicate = client.refresh(true); release(); await Promise.all([first, duplicate]);
  assert.equal(requests, 1); assert.equal(client.count(), 1);
  await client.refresh(); assert.equal(requests, 1);
  harness.advance(31000); await client.refresh(); assert.equal(requests, 2);
});

test("failed document reads are also cached to avoid a retry loop from state renders", async () => {
  let requests = 0;
  const harness = documentsHarness(async () => { requests++; throw Error("offline"); });
  const client = harness.create(); await client.refresh(); await client.refresh();
  assert.equal(requests, 1); assert.equal(client.count(), null);
  await client.refresh(true); assert.equal(requests, 2);
});

function filesHarness() {
  const handlers = {}, calls = [], fetches = [], notices = [];
  const actions = {".file-body":{innerHTML:""}, ".file-close":{}, ".file-open":{}, ".file-reveal":{}};
  const classes = new Set(), panel = {setAttribute(){}, innerHTML:"", classList:{add:value => classes.add(value), remove:value => classes.delete(value)}, querySelector:selector => actions[selector]};
  const sandbox = {window:{markdownit:require(path.join(web, "vendor/markdown-it.min.js"))},
    document:{createElement:() => panel, body:{append(){}}, addEventListener:(event, listener) => { handlers[event] = listener; }},
    location:{search:"?project=alpha"}, URLSearchParams, URL, console, matchMedia:() => ({matches:false, addEventListener(){}}), setInterval(){}, setTimeout, clearTimeout,
    fetch:async url => { fetches.push(url); return {ok:true, json:async () => ({content:"[子文件](./child.md)", truncated:false})}; }};
  vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(web, "chat.js"), "utf8"), sandbox);
  sandbox.window.CrewChat.initFiles(async (url, data) => {
    calls.push([url, plain(data)]);
    if (url.endsWith("resolve")) {
      if (data.document_id === 13) throw Error("文档不属于当前项目");
      return {error:null, type:"preview", kind:"markdown", path:"C:/alpha/docs/summary.md", open_with:"vscode", session_id:"trusted-owner"};
    }
    return {ok:true};
  }, value => notices.push(value));
  async function click(id) {
    const link = {dataset:{documentId:id, filePath:"C:/outside/untrusted.md", fileSession:"forged-owner"},
      hasAttribute:name => name === "data-document-id", closest:selector => selector === "[data-file-path]" ? link : null};
    await handlers.click({target:link, preventDefault(){}, stopImmediatePropagation(){}});
  }
  return {click, calls, fetches, notices, actions, classes};
}
test("document preview carries a numeric ID through resolve, content, open and reveal", async () => {
  const harness = filesHarness(); await harness.click("12");
  assert.deepEqual(harness.calls[0], ["/api/files/resolve", {document_id:12}]);
  const params = new URL(harness.fetches[0], "http://fixture").searchParams;
  assert.equal(params.get("project"), "alpha"); assert.equal(params.get("document_id"), "12");
  assert(!params.has("path")); assert(!params.has("session_id"));
  assert(harness.actions[".file-body"].innerHTML.includes('data-file-session="trusted-owner"'));
  await harness.actions[".file-open"].onclick(); await harness.actions[".file-reveal"].onclick();
  assert.deepEqual(harness.calls.slice(1), [["/api/files/open", {document_id:12}], ["/api/files/reveal", {document_id:12}]]);
});
test("invalid or rejected document IDs never fall back to a supplied file path", async () => {
  const harness = filesHarness();
  for (const value of ["", "0", "12oops", "9007199254740993"]) await harness.click(value);
  assert.equal(harness.calls.length, 0); assert.equal(harness.fetches.length, 0);
  await harness.click("13");
  assert.deepEqual(harness.calls, [["/api/files/resolve", {document_id:13}]]);
  assert.equal(harness.fetches.length, 0); assert(!harness.classes.has("show"));
});
