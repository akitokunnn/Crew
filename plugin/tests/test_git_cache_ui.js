"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const tick=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
const snapshot=label=>({enabled:false,repository:label,choices:{branches:["main"],base:"main",stable:null}});
const commit=(sha,subject,parents=[],refs=[])=>({sha:sha.repeat(40),subject,parents:parents.map(item=>item.repeat(40)),refs,author:"Fixture",time:"2026-10-26T00:00:00Z",body:"",pull_requests:[],parent_branches:[]});
function enabled(commits=[commit("a","第一次读取",[],["main"])],next_offset=null) {
  const latest=commits[0],branch={name:"main",latest,origin_relation:"",sessions:[],worktrees:[],ahead:0,behind:0,conflicts:[],unique_commits:[],merged:true};
  return {...snapshot("已启用的仓库"),enabled:true,config:{base:"main",stable:null,admin_session_id:"chief"},base:branch,stable:null,branches:[branch],remote_only:[],recent_merge:null,ref_unique_commits:{main:[]},requests:[],commits,next_offset,github:null,github_reason:"fixture offline",last_fetch_at:null};
}
function harness() {
  let markup="",read=async()=>snapshot("仓库快照");
  const calls=[],handlers=new Map(),timers=new Map(),popup={hidden:true},scroll={scrollTop:0};let changed=0;const toasts=[];
  const host={get innerHTML(){return markup;},set innerHTML(value){markup=value;},querySelector:selector=>selector==="#git-detail"?popup:selector===".git-page"&&markup.includes("git-page")?scroll:null,querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)};
  const sandbox={document:{addEventListener(){},removeEventListener(){}},window:{addEventListener(){},removeEventListener(){}},setInterval:fn=>{timers.set(1,fn);return 1;},clearInterval:id=>timers.delete(id),clearTimeout(){}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/git.js"),"utf8")+"\nglobalThis.component=CrewGit;",sandbox);
  const ui=sandbox.component;
  const settings=(project="demo",root="C:/fixture/demo")=>({project,root,catalog:()=>[{id:"codex",name:"Codex",available:true}],defaultRuntime:()=>"codex",api:async(route,body)=>{calls.push({project,root,route,body});return read(route,body);},toast:message=>toasts.push(message),onChanged:async()=>{changed++;}});
  function mount(project,root){ui.mount(host,settings(project,root));}
  function click(act,value){const button={dataset:act==="tab"?{gitTab:value}:act==="branch"?{gitBranch:value}:{gitAct:act}};handlers.get("click")({target:{closest:selector=>selector==="[data-git-act]"&&button.dataset.gitAct||selector==="[data-git-tab]"&&button.dataset.gitTab||selector==="[data-git-branch]"&&button.dataset.gitBranch?button:null}});}
  return {host,calls,timers,ui,mount,click,scroll,toasts,get changed(){return changed;},read:fn=>{read=fn;}};
}
test("returning to the same workspace shows its snapshot immediately without another scan or polling",async()=>{
  const h=harness();h.mount();await tick();assert(h.host.innerHTML.includes("仓库快照"));
  h.ui.unmount();h.host.innerHTML="聊天";h.read(()=>new Promise(()=>{}));h.mount();
  assert(h.host.innerHTML.includes("仓库快照"));assert.equal(h.calls.length,1);assert.equal(h.timers.size,0);h.ui.unmount();
});
test("a scan started before leaving remains shared and fills the cache without overwriting chat",async()=>{
  const h=harness();let resolve;h.read(()=>new Promise(done=>{resolve=done;}));h.mount();await tick();h.ui.unmount();h.host.innerHTML="聊天和草稿";
  resolve(snapshot("后台完成的读取"));await tick();assert.equal(h.host.innerHTML,"聊天和草稿");h.mount();
  assert(h.host.innerHTML.includes("后台完成的读取"));assert.equal(h.calls.length,1);h.ui.unmount();
});
test("returning while the first read is pending shares that request instead of starting another",async()=>{
  const h=harness();let resolve;h.read(()=>new Promise(done=>{resolve=done;}));h.mount();await tick();h.ui.unmount();h.host.innerHTML="聊天";h.mount();await tick();
  assert.equal(h.calls.length,1);resolve(snapshot("唯一请求"));await tick();assert(h.host.innerHTML.includes("唯一请求"));h.ui.unmount();
});
test("manual refresh keeps the last snapshot visible on failure and remains read only",async()=>{
  const h=harness();h.read(async()=>enabled());h.mount();await tick();let reject;
  h.read(()=>new Promise((_resolve,fail)=>{reject=fail;}));h.click("refresh");await tick();assert(h.host.innerHTML.includes("第一次读取"));assert(h.host.innerHTML.includes("正在更新，显示上次结果"));
  reject(Error("fixture scan failed"));await tick();assert(h.host.innerHTML.includes("第一次读取"));assert(h.host.innerHTML.includes("fixture scan failed"));assert(h.host.innerHTML.includes("读取于"));
  h.read(async()=>enabled([commit("b","更新后的结果",[],["main"])]));h.click("refresh");await tick();assert(h.host.innerHTML.includes("更新后的结果"));assert(!h.host.innerHTML.includes("fixture scan failed"));assert(h.calls.every(call=>call.body===undefined));h.ui.unmount();
});
test("paging, selected view, branch and scroll survive leaving while more history is loading",async()=>{
  const h=harness();h.read(async()=>enabled([commit("a","第一页",["b"],["main"])],80));h.mount();await tick();let resolve;
  h.read(()=>new Promise(done=>{resolve=done;}));h.click("load-more");await tick();h.click("branch","main");h.click("tab","branches");h.scroll.scrollTop=260;h.ui.unmount();h.host.innerHTML="聊天";h.scroll.scrollTop=0;
  resolve(enabled([commit("b","第二页")],null));await tick();h.mount();assert.equal(h.calls.length,2);assert(h.host.innerHTML.includes('id="git-branches-tab" aria-controls="git-panel" aria-selected="true"'));assert.equal(h.scroll.scrollTop,260);
  h.click("tab","graph");assert(h.host.innerHTML.includes("第二页"));assert(h.host.innerHTML.includes('data-git-branch="main" aria-pressed="true"'));h.ui.unmount();
});
test("configuration saved while Git is unmounted invalidates without starting a scan",async()=>{
  const h=harness();h.mount();await tick();h.ui.unmount();h.host.innerHTML="聊天";h.ui.reload("demo");assert.equal(h.calls.length,1);assert.equal(h.host.innerHTML,"聊天");
  h.read(async()=>snapshot("新配置"));h.mount();await tick();assert.equal(h.calls.length,2);assert(h.host.innerHTML.includes("新配置"));h.ui.unmount();
});
test("workspace or root changes invalidate the snapshot and late old replies cannot replace it",async()=>{
  const h=harness();let resolve;h.read(()=>new Promise(done=>{resolve=done;}));h.mount();await tick();h.ui.unmount();
  h.read(async()=>snapshot("新目录"));h.mount("demo","D:/moved");await tick();resolve(snapshot("旧目录"));await tick();assert.equal(h.calls.length,2);assert(h.host.innerHTML.includes("新目录"));assert(!h.host.innerHTML.includes("旧目录"));
  h.read(async()=>snapshot("另一项目"));h.mount("other","D:/moved");await tick();assert.equal(h.calls.length,3);assert(h.host.innerHTML.includes("另一项目"));h.ui.unmount();
});
test("fetch survives leaving and returning without repeating the write or toast",async()=>{
  const h=harness();h.read(async()=>enabled());h.mount();await tick();let resolve;
  h.read((route,body)=>body?new Promise(done=>{resolve=done;}):Promise.resolve(enabled()));h.click("fetch");await tick();h.ui.unmount();h.host.innerHTML="聊天";h.mount();assert(h.host.innerHTML.includes("正在拉取"));
  assert.equal(h.calls.filter(call=>call.body).length,1);resolve({ok:true,output:"updated"});await tick();assert.equal(h.calls.filter(call=>call.body).length,1);assert.deepEqual(h.toasts,["updated"]);h.ui.unmount();h.mount();assert.deepEqual(h.toasts,["updated"]);h.ui.unmount();
});
