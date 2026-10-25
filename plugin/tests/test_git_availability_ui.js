"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const tick=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
const unavailable=(extra={})=>({available:false,reason:"这是普通文件夹，Git 功能暂不可用。",enabled:false,repository:"普通工作区",config:null,requests:[],choices:{branches:[],base:null,stable:null},...extra});
const healthy=()=>({enabled:false,repository:"已有仓库",choices:{branches:["main"],base:"main",stable:null}});
function harness(initial){
  let response=initial,markup="",read=null;const handlers=new Map(),timers=new Map(),documentListeners=new Map(),windowListeners=new Map(),calls=[],popup={hidden:true};
  const host={get innerHTML(){return markup;},set innerHTML(value){markup=value;},querySelector:selector=>selector==="#git-detail"?popup:null,querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)};
  const sandbox={document:{addEventListener:(name,fn)=>documentListeners.set(name,fn),removeEventListener:name=>documentListeners.delete(name)},window:{addEventListener:(name,fn)=>windowListeners.set(name,fn),removeEventListener:name=>windowListeners.delete(name)},setInterval:fn=>{timers.set(1,fn);return 1;},clearInterval:id=>timers.delete(id),clearTimeout(){}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/git.js"),"utf8")+"\nglobalThis.component=CrewGit;",sandbox);
  const ui=sandbox.component;ui.mount(host,{project:"plain",catalog:()=>[{id:"codex",name:"Codex",available:true}],defaultRuntime:()=>"codex",api:async(route,body)=>{calls.push({route,body});return read?read():response;},toast(){},onChanged:async()=>{}});
  function check(){const button={dataset:{gitAct:"check"},disabled:false};handlers.get("click")?.({target:{closest:selector=>selector==="[data-git-act]"?button:null}});}
  return{host,ui,calls,handlers,timers,documentListeners,windowListeners,check,set:value=>{response=value;read=null;},read:callback=>{read=callback;}};
}
test("a plain workspace Git page explains optional Git features without an enable form or background Git checks",async()=>{
  const h=harness(unavailable());await tick();assert(h.host.innerHTML.includes("这是普通文件夹"));assert(h.host.innerHTML.includes("其他功能照常"));assert(h.host.innerHTML.includes("worktree"));assert(h.host.innerHTML.includes('data-git-act="check"'));assert(!h.host.innerHTML.includes('id="git-enable-form"'));assert(!h.host.innerHTML.includes('data-git-act="fetch"'));
  [...h.timers.values()][0]();await tick();assert.equal(h.calls.length,1);assert(h.calls.every(call=>call.body===undefined));h.ui.unmount();
});
test("unavailable Git takes priority over an existing enabled configuration and escapes the actual reason",async()=>{
  const h=harness(unavailable({enabled:true,reason:"未安装 Git <检查路径>"}));await tick();assert(h.host.innerHTML.includes("未安装 Git &lt;检查路径&gt;"));assert(!h.host.innerHTML.includes('data-git-act="enable"'));assert(!h.host.innerHTML.includes('data-git-act="fetch"'));assert(!h.host.innerHTML.includes("和 git 管理员聊"));h.ui.unmount();
});
test("manual checking is read only and deduplicates while external Git setup becomes available",async()=>{
  const h=harness(unavailable());await tick();let release;h.read(()=>new Promise(resolve=>{release=resolve;}));h.check();h.check();await tick();assert.equal(h.calls.length,2);assert(h.host.innerHTML.includes("正在检查"));assert(!h.host.innerHTML.includes('id="git-enable-form"'));release(healthy());await tick();assert(h.host.innerHTML.includes('id="git-enable-form"'));assert(h.calls.every(call=>call.route.startsWith("/api/git?")&&call.body===undefined));h.ui.unmount();
});
test("a failed manual check preserves the ordinary workspace explanation and stays retryable",async()=>{
  const h=harness(unavailable());await tick();h.read(()=>{throw Error("连接暂时失败 <重试>");});h.check();await tick();assert(h.host.innerHTML.includes("连接暂时失败 &lt;重试&gt;"));assert(h.host.innerHTML.includes("这是普通文件夹"));assert(!h.host.innerHTML.includes('id="git-enable-form"'));h.set(unavailable({reason:"Git 仍未安装"}));h.check();await tick();assert(h.host.innerHTML.includes("Git 仍未安装"));assert(!h.host.innerHTML.includes("连接暂时失败"));assert(h.calls.every(call=>call.body===undefined));h.ui.unmount();
});
test("returning to chat cancels Git listeners and a late check cannot replace that chat or its draft",async()=>{
  const h=harness(unavailable());await tick();let release;h.read(()=>new Promise(resolve=>{release=resolve;}));h.check();await tick();assert.equal(h.calls.length,2);h.ui.unmount();h.host.innerHTML="聊天与未发送草稿";release(healthy());await tick();assert.equal(h.host.innerHTML,"聊天与未发送草稿");assert.equal(h.timers.size,0);assert.equal(h.handlers.size,0);assert.equal(h.documentListeners.size,0);assert.equal(h.windowListeners.size,0);assert(h.calls.every(call=>call.body===undefined));
});
