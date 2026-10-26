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
const documentsHost=()=>({innerHTML:"",dataset:{render:"old page"}});

test("task documents render only the explicit task list, preserve ordering and escape user text",()=>{
  const {client,module}=documentsHarness();const docs=[{id:1,title:"普通记录",kind:"other",important:false},{id:2,title:"<script>汇总</script>",path:"docs/a.md",kind:"summary",important:true}];
  assert.deepEqual(plain(module.sorted(docs)).map(item=>item.id),[2,1]);const first=client.taskCards(docs,"goal-a");assert(first.includes("&lt;script&gt;"));assert(first.indexOf('data-document-card="2"')<first.indexOf('data-document-card="1"'));
  const other=client.taskCards([{id:3,title:"另一个任务文档"}],"goal-b");assert(!other.includes("普通记录"));assert(!other.includes("汇总"));assert(other.includes("另一个任务文档"));assert.equal(client.taskCards([],"goal-empty"),"");
});

test("task document actions bind numeric document IDs and retain the important mark without a hidden center",async()=>{
  const calls=[],h=documentsHarness(async(url,body)=>{calls.push({url,body:plain(body)});return {document:{id:7,title:"任务文档",kind:"summary",important:body.important}};});const list=[{id:7,title:"任务文档",kind:"summary",important:false}];h.client.taskCards(list,"goal");await h.click("pin",7);assert(h.client.taskCards(list,"goal").includes('aria-pressed="true"'));await h.click("open",7);assert.deepEqual(calls[1],{url:"/api/files/open",body:{document_id:7}});assert(!calls.some(call=>call.url==="/api/documents"));
});

test("project materials read every saved document without a task filter or model request",async()=>{
  const calls=[],docs=Array.from({length:7},(_,index)=>({id:index+1,title:`历史资料 ${index+1}`,path:`docs/${index+1}.md`,session_id:`member-${index}`,leader_session_id:null}));
  const h=documentsHarness(async(url,body)=>{calls.push({url,body});return {documents:docs};}),host=documentsHost();
  h.client.mount(host);assert(host.innerHTML.includes("正在读取项目资料"));await tick();
  assert.deepEqual(calls,[{url:"/api/documents?project=alpha",body:undefined}]);assert(host.innerHTML.includes("7 份文档"));
  for(const item of docs)assert(host.innerHTML.includes(`data-document-card="${item.id}"`));
  assert(host.innerHTML.includes("汇集本项目保存的文档；任务交付可在对应任务中查看"));assert(!("render" in host.dataset));
  assert.equal(h.client.taskCards([],"unbound-goal"),"");
  await h.click("source",3,"project");assert.equal(h.sources[0].session_id,"member-2");
  await h.click("open",3,"project");assert.deepEqual(plain(calls[1]),{url:"/api/files/open",body:{document_id:3}});
});

test("project materials retry errors, deduplicate pending reads and show the empty result",async()=>{
  let count=0,release;const h=documentsHarness(async()=>{count++;if(count===1)throw Error("资料 <暂不可用>");return new Promise(resolve=>{release=resolve;});}),host=documentsHost();
  h.client.mount(host);await tick();assert(host.innerHTML.includes("资料 &lt;暂不可用&gt;"));assert(host.innerHTML.includes("重试读取"));
  h.client.mount(host);assert.equal(count,1);
  const retry=h.click("refresh-project");h.client.mount(host);const same=h.client.refresh();assert.equal(count,2);assert(host.innerHTML.includes("正在读取项目资料"));
  release({documents:[]});await same;await retry;assert(host.innerHTML.includes("本项目还没有保存的文档"));assert(!host.innerHTML.includes("暂不可用"));assert.equal(count,2);
});

test("late project reads cannot replace a task view and document actions retain their rendered scope",async()=>{
  let release;const calls=[],h=documentsHarness(async(url,body)=>{calls.push({url,body:body===undefined?undefined:plain(body)});if(body)return {document:{id:body.id,title:"已标重要",important:body.important}};return new Promise(resolve=>{release=resolve;});}),host=documentsHost();
  const task=[{id:8,title:"仅任务可见",session_id:"task-member",important:false}];
  h.client.mount(host);h.client.unmount();host.innerHTML=h.client.taskCards(task,"goal");
  release({documents:[{id:7,title:"仅项目可见",session_id:"legacy-member",important:false}]});await tick();
  assert(host.innerHTML.includes("仅任务可见"));assert(!host.innerHTML.includes("仅项目可见"));
  await h.click("source",8);await h.click("source",7,"project");assert.deepEqual(h.sources.map(item=>item.session_id),["task-member","legacy-member"]);
  await h.click("open",7);assert.equal(calls.length,1);
  h.client.mount(host);assert(host.innerHTML.includes("仅项目可见"));assert(!host.innerHTML.includes("仅任务可见"));
  await h.click("pin",7,"project");assert(host.innerHTML.includes('aria-pressed="true"'));assert(h.client.taskCards(task,"goal").includes("仅任务可见"));assert(!h.client.taskCards(task,"goal").includes("已标重要"));
  await h.click("pin",8);assert(h.client.taskCards(task,"goal").includes('aria-pressed="true"'));assert(!host.innerHTML.includes("仅任务可见"));
});

test("project materials navigation has a sidebar entry and restores its hash route on refresh",()=>{
  const nodes=new Map(),calls=[];
  const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:"",dataset:{},classList:{toggle(){},remove(){}}});return nodes.get(id);};
  const sandbox={window:{},document:{getElementById:node,addEventListener(){}},URLSearchParams,location:{search:"?project=alpha",pathname:"/",hash:"#documents"},history:{replaceState(_state,_title,url){sandbox.location.hash=url.slice(url.indexOf("#"));}},CrewChat:{unmount(){}},CrewGit:{unmount(){}},CrewTheme:{logo:()=>""}};
  vm.createContext(sandbox);
  const source=fs.readFileSync(path.join(web,"app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(source.replace("  setup();\n})();",`window.ui={readRoute,render,navigate,configure(){state={project:{name:"alpha"},leaders:[],members:[],decisions:[],goals:[],channel:{unread:0},candidates:{running:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},notification:{channel:{state:"unconfigured",detail:null},manual_away:false,away:false,error:null}};documents={mount:host=>window.mounted(host),unmount:()=>window.unmounted()};replace=(host,html)=>{host.innerHTML=html;};renderRight=()=>{};renderCard=()=>{};renderAll=()=>{document.getElementById("main").innerHTML="全部";};},get view(){return view;}};\n})();`),sandbox);
  sandbox.window.mounted=host=>calls.push(["mount",host]);sandbox.window.unmounted=()=>calls.push(["unmount"]);
  const ui=sandbox.window.ui;ui.configure();ui.readRoute();ui.render();assert.equal(ui.view,"documents");assert(node("sidebar").innerHTML.includes('data-view="documents"'));assert(node("sidebar").innerHTML.includes("项目资料"));assert.equal(calls[0][1],node("main"));
  ui.navigate("all");assert.equal(sandbox.location.hash,"#all");assert.equal(calls[1][0],"unmount");
  ui.navigate("documents");assert.equal(sandbox.location.hash,"#documents");ui.readRoute();assert.equal(ui.view,"documents");
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
