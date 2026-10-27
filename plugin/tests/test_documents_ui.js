"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const web = path.join(__dirname, "../crew/web");
const plain = value => JSON.parse(JSON.stringify(value));
function documentsHarness(api = async () => ({})) {
  const handlers={},notices=[],sources=[];
  const sandbox = {window:{}, document:{addEventListener:(type,fn)=>handlers[type]=fn}};
  vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(web, "documents.js"), "utf8"), sandbox);
  const client=sandbox.window.CrewDocuments.create({project:"alpha",api,toast:text=>notices.push(text),onSource:item=>sources.push(plain(item)),onChange(){}});
  return {module:sandbox.window.CrewDocuments,client,notices,sources,click:async(act,id,scope="goal:goal")=>{const button={dataset:{docAct:act,docId:String(id),docScope:scope}};handlers.click({target:{closest:()=>button}});await tick();}};
}
async function tick() { for(let i=0;i<20;i++)await Promise.resolve(); }

test("task documents render only the explicit task list, preserve ordering and escape user text",()=>{
  const {client,module}=documentsHarness();const docs=[{id:1,title:"普通记录",kind:"other",important:false},{id:2,title:"<script>汇总</script>",path:"docs/a.md",kind:"summary",important:true}];
  assert.deepEqual(plain(module.sorted(docs)).map(item=>item.id),[2,1]);const first=client.taskCards(docs,"goal-a");assert(first.includes("&lt;script&gt;"));assert(first.indexOf('data-document-card="2"')<first.indexOf('data-document-card="1"'));
  const other=client.taskCards([{id:3,title:"另一个任务文档"}],"goal-b");assert(!other.includes("普通记录"));assert(!other.includes("汇总"));assert(other.includes("另一个任务文档"));assert.equal(client.taskCards([],"goal-empty"),"");
});

test("task document actions bind numeric document IDs and retain the important mark without a hidden center",async()=>{
  const calls=[],h=documentsHarness(async(url,body)=>{calls.push({url,body:plain(body)});return {document:{id:7,title:"任务文档",kind:"summary",important:body.important}};});const list=[{id:7,title:"任务文档",kind:"summary",important:false}];h.client.taskCards(list,"goal");await h.click("pin",7);assert(h.client.taskCards(list,"goal").includes('aria-pressed="true"'));await h.click("open",7);assert.deepEqual(calls[1],{url:"/api/files/open",body:{document_id:7}});assert(!calls.some(call=>call.url==="/api/documents"));
});

test("task documents keep independent action scopes without a global material surface",async()=>{
  const calls=[],h=documentsHarness(async(url,body)=>{calls.push({url,body:plain(body)});return {document:{id:8,title:"A任务文档",important:true}};});
  const a=[{id:8,title:"A任务文档",session_id:"a-owner",important:false}],b=[{id:9,title:"B任务文档",session_id:"b-owner",important:false}];
  h.client.taskCards(a,"a");h.client.taskCards(b,"b");
  await h.click("source",8,"goal:a");await h.click("source",9,"goal:b");
  assert.deepEqual(h.sources.map(item=>item.session_id),["a-owner","b-owner"]);
  await h.click("open",8,"goal:b");assert.equal(calls.length,0,"wrong task scope cannot open another task's document");
  await h.click("pin",8,"goal:a");assert(h.client.taskCards(a,"a").includes('aria-pressed="true"'));
  assert(!h.client.taskCards(b,"b").includes("A任务文档"));
  assert.equal(h.client.mount,undefined);assert.equal(h.client.refresh,undefined);
  assert(calls.every(call=>call.url==="/api/documents/pin"));
});

test("sidebar has no materials overview and old document routes return to the workspace",()=>{
  const nodes=new Map(),calls=[];
  const node=id=>{if(!nodes.has(id))nodes.set(id,{querySelector:()=>null,setAttribute(){},style:{setProperty(){}},matches:()=>false,innerHTML:"",dataset:{},classList:{toggle(){},remove(){}}});return nodes.get(id);};
  const sandbox={createAttention:require("./attention_fixture.cjs").createAttention,window:{},document:{getElementById:node,addEventListener(){}},URLSearchParams,location:{search:"?project=alpha",pathname:"/",hash:"#documents"},history:{replaceState(_state,_title,url){sandbox.location.hash=url.slice(url.indexOf("#"));}},CrewChat:{unmount(){}},CrewGit:{unmount(){}},CrewTheme:{logo:()=>""}};
  vm.createContext(sandbox);
  const source=fs.readFileSync(path.join(web,"app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(source.replace("  setup();\n})();",`window.ui={readRoute,render,syncRoute,configure(){attentionFeature=createAttention(()=>state,document);state={project:{name:"alpha"},leaders:[],members:[],decisions:[],goals:[],channel:{unread:0},candidates:{running:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},notification:{channel:{state:"unconfigured",detail:null},manual_away:false,away:false,error:null}};documents={mount:()=>window.overview(),unmount(){}};domRenderer={replace:(host,html)=>{host.innerHTML=html;}};renderRight=()=>{};renderCard=()=>{};renderAll=()=>{document.getElementById("main").innerHTML="全部";};},get view(){return view;}};\n})();`),sandbox);
  sandbox.window.overview=()=>calls.push("overview");
  const ui=sandbox.window.ui;ui.configure();ui.readRoute();ui.render();ui.syncRoute();
  assert.equal(ui.view,"all");assert.equal(sandbox.location.hash,"#all");
  assert(!node("sidebar").innerHTML.includes('data-view="documents"'));
  assert(!/项目资料|交付资料|文档中心/.test(node("sidebar").innerHTML));
  assert.deepEqual(calls,[]);
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
