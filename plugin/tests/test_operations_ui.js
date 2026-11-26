"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const sandbox = {window:{}}; vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web/operations-ui.js"), "utf8"), sandbox);
const ui = sandbox.window.CrewOperations, plain = value => JSON.parse(JSON.stringify(value));

test("operations preflight follows explicit task/default choices and only infers a sole installed carrier", () => {
  const catalog=[{id:"claude",name:"Claude Code",available:true},{id:"codex",name:"Codex",available:true}];
  const ai={default:{runtime:null,model:null,effort:null},operations:null};
  assert.match(ui.brainIssue({ai,runtimes:catalog}),/多个 AI 载体/);
  assert.equal(ui.brainIssue({ai,runtimes:[catalog[1]]}),"");
  ai.default.runtime="claude";assert.equal(ui.brainIssue({ai,runtimes:catalog}),"");
  ai.operations={runtime:"codex"};assert.match(ui.brainIssue({ai,runtimes:[catalog[0]]}),/codex.*未就绪/);
  assert.equal(ui.brainIssue({ai,runtimes:catalog}),"");
  ai.operations={runtime:null};assert.match(ui.brainIssue({ai,runtimes:catalog}),/多个 AI 载体/);
  assert.match(ui.brainIssue({ai,runtimes:[]}),/安装并登录/);
});
test("operations form preserves exact argument boundaries and quoted Windows paths", () => {
  const policy = {enabled:true, repository:"C:\\Crew source", reproduce:[["C:\\Python Tools\\python.exe", "-m", "unittest", "tests.test_one"]], verify:[["node", "--test", "desktop/test/a.test.cjs"]], timeout_seconds:180, merge_ref:"main"};
  const draft = ui.policyDraft(policy); assert.equal(typeof draft.timeout_seconds, "string");
  assert.deepEqual(plain(ui.policyValue(draft)), policy); assert.deepEqual(policy.reproduce[0], ["C:\\Python Tools\\python.exe", "-m", "unittest", "tests.test_one"]);
});
test("operations form rejects malformed JSON and commands without program names", () => {
  assert.throws(() => ui.parseCommands('["python"', "复现命令"), /复现命令格式不正确/);
  for (const text of ['"python"', '["python"]', '[[]]', '[[" "]]', '[["python", 3]]']) assert.throws(() => ui.parseCommands(text, "验证命令"), /验证命令需要填写命令列表/);
  assert.deepEqual(plain(ui.parseCommands("[]", "验证命令")), []);
});

function operationsHarness(){
  const element=()=>({innerHTML:"",textContent:"",hidden:false,classList:{toggle(){},add(){}},setAttribute(){},addEventListener(){},append(){},replaceChildren(){},querySelector(){return element();},querySelectorAll(){return [];},focus(){},scrollIntoView(){}});
  const panel=element(),root={...element(),querySelector:selector=>selector===".co-panel"?panel:element()};
  const sandbox={document:{createElement:()=>root,addEventListener(){},hidden:false},setTimeout,clearTimeout};
  sandbox.window=sandbox;
  vm.createContext(sandbox);
  for (const script of ["browser-utils.js", "operations-ui.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",script),"utf8"),sandbox);
  }
  return {ui:sandbox.window.CrewOperations,root,panel,sandbox};
}
test("a LAN host keeps its operations chat and repair policy on the host: only status and connection remain",()=>{
  const {ui,root,panel}=operationsHarness();let reads=0;
  ui.create({api:{launch:{get:()=>{reads++;return new Promise(()=>{});}}},remote:true}).mount({append(){}},{home:"/Users/host/.crew",backend:"/Applications/Crew/backend"});
  assert.doesNotMatch(root.innerHTML,/data-operations-tab="chat"|data-operations-tab="config"|运维 AI 大脑/);
  assert.match(root.innerHTML,/data-operations-tab="status" aria-controls="co-operations-panel" aria-selected="true"/);assert.match(root.innerHTML,/data-operations-tab="launch"/);
  assert.match(panel.innerHTML,/主机数据目录 · HOME/);assert.match(panel.innerHTML,/\/Users\/host\/\.crew/);assert.match(panel.innerHTML,/更改连接与目录/);
  assert.doesNotMatch(panel.innerHTML,/co-actual-service/);assert.equal(reads,0,"host version and health come from the host health panel");
  const local=operationsHarness();
  local.ui.create({api:{snapshot:()=>new Promise(()=>{}),configGet(){},launch:{get:()=>new Promise(()=>{})}}}).mount({append(){}},{});
  assert.match(local.root.innerHTML,/data-operations-tab="chat"[^>]*aria-selected="true"/);assert.match(local.root.innerHTML,/data-operations-tab="config"/);assert.match(local.root.innerHTML,/运维 AI 大脑/);
});
test("a home-page shortcut opens the connection tab and hands its intent to the launch settings once",()=>{
  const {ui,sandbox}=operationsHarness(),prepared=[],mounts=[];
  sandbox.window.CrewLaunchSettings={create:()=>({mount:host=>mounts.push(host),hide(){},prepare:value=>prepared.push(value)})};
  const operations=ui.create({api:{launch:{get:()=>new Promise(()=>{})}},remote:true});
  operations.openLaunch("local");
  assert.deepEqual(prepared,[]);
  const close=operations.mount({append(){}},{});
  // The launch tab rendered (its settings mounted) and received the intent exactly once.
  assert.deepEqual(prepared,["local"]);assert.equal(mounts.length,1);
  close();operations.mount({append(){}},{});
  assert.deepEqual(prepared,["local"]);assert.equal(mounts.length,2);
});
test("without connection settings a shortcut leaves the operations tabs unchanged",()=>{
  const {ui,root}=operationsHarness();
  const operations=ui.create({api:{snapshot:()=>new Promise(()=>{}),configGet(){}}});
  operations.openLaunch("pair");operations.mount({append(){}},{});
  assert.match(root.innerHTML,/data-operations-tab="chat" aria-controls="co-operations-panel" aria-selected="true"/);
});
