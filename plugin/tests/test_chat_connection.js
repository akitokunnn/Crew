"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function node(){const classes=new Set();return {value:"",textContent:"",innerHTML:"",hidden:false,disabled:false,style:{},dataset:{},scrollTop:0,scrollHeight:0,clientHeight:200,firstChild:null,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x);},contains:x=>classes.has(x)}};}
function host(){const nodes=new Map();return {...node(),querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);}};}
function harness(){
  const timers=new Map(),saved=new Map(),streams=[];let next=1;
  const sandbox={matchMedia:()=>({matches:true}),atob,btoa,URLSearchParams,AbortController,TextDecoder,
    localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)},
    document:{visibilityState:"visible",addEventListener(){},removeEventListener(){},createElement:node},
    setTimeout:(fn,ms)=>{const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:()=>next++,cancelAnimationFrame(){},
    fetch:async(url,{signal})=>{streams.push(String(url));return {ok:true,body:{getReader:()=>({read:()=>new Promise((_resolve,reject)=>signal.addEventListener("abort",()=>reject(Object.assign(Error("aborted"),{name:"AbortError"})),{once:true}))})}};}};
  sandbox.window=sandbox;vm.createContext(sandbox);
  for(const file of ["vendor/markdown-it.min.js","chat.js"])vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",file),"utf8"),sandbox);
  function mount(id,api,extra={}){const target=host(),session={session_id:id,name:id,busy:false,last_seq:0,activity:null,where:"bg",...extra};sandbox.CrewChat.mount({host:target,session,state:{project:{name:"fixture"},leaders:[session],members:[]},api,toast(){}});return target;}
  function update(id,extra={}){const session={session_id:id,name:id,busy:false,last_seq:1,activity:null,where:"bg",...extra};sandbox.CrewChat.update({project:{name:"fixture"},leaders:[session],members:[]});}
  return {chat:sandbox.CrewChat,mount,update,timers,saved,streams};
}
const history=()=>({events:[],busy:false,read_seq:0});

test("initial history failure retries, recovers the connection and preserves a typed draft without sending",async()=>{
  const h=harness(),calls=[];let attempts=0;
  const target=h.mount("a",async(url,data)=>{calls.push({url,data});if(++attempts===1)throw Error("temporary connection failure");return history();});await tick();
  assert.equal(attempts,1);assert.equal(h.timers.size,1);assert.match(target.querySelector(".chat-connection-error").textContent,/自动重试/);
  target.querySelector("textarea").value="重连时继续写的草稿";target.querySelector("textarea").oninput();
  assert.equal(target.querySelector(".send").disabled,true);
  const [id,timer]=[...h.timers][0];assert.equal(timer.ms,3000);h.timers.delete(id);timer.fn();await tick();
  assert.equal(attempts,2);assert.equal(h.streams.length,1);assert.equal(target.querySelector(".chat-connection-error").hidden,true);
  assert.equal(target.querySelector("textarea").value,"重连时继续写的草稿");assert.equal(target.querySelector(".send").disabled,false);
  assert(calls.every(call=>call.url.startsWith("/api/chat/history?")&&call.data===undefined));h.chat.unmount();
});

test("unmount cancels the pending retry and even an already queued callback cannot load a different leader",async()=>{
  const h=harness();let oldCalls=0,newCalls=0;
  const a=h.mount("a",async()=>{oldCalls++;throw Error("offline");});await tick();
  a.querySelector("textarea").value="A的未发送草稿";a.querySelector("textarea").oninput();
  const pending=[...h.timers.values()][0];assert(pending);h.chat.unmount();assert.equal(h.timers.size,0);
  const b=h.mount("b",async()=>{newCalls++;return history();});await tick();pending.fn();await tick();
  assert.equal(oldCalls,1);assert.equal(newCalls,1);assert.equal(b.querySelector("textarea").value,"");
  assert(h.streams.every(url=>url.includes("session_id=b")));
  const remounted=h.mount("a",async()=>history());await tick();assert.equal(remounted.querySelector("textarea").value,"A的未发送草稿");h.chat.unmount();
});

test("a late history response from an unmounted leader cannot render into or connect the current leader",async()=>{
  const h=harness();let finish;
  const a=h.mount("a",()=>new Promise(resolve=>{finish=resolve;}));
  const b=h.mount("b",async()=>history());await tick();
  finish({events:[{seq:1,type:"message",id:"old-message",text:"old leader only"}],busy:true,read_seq:0});await tick();
  assert.equal(a.querySelector(".chat-inner").innerHTML,"");assert.equal(b.querySelector(".chat-connection-error").hidden,true);
  assert(h.streams.every(url=>url.includes("session_id=b")));assert.equal(h.timers.size,0);h.chat.unmount();
});

test("the composer interrupt control targets only this turn and waits for actual idle state",async()=>{
  const h=harness(),calls=[];let acknowledge;
  const target=h.mount("a",async(url,data)=>{calls.push({url,data});if(url==="/api/session/stop")return new Promise(resolve=>{acknowledge=resolve;});return {...history(),busy:true};},{busy:true});await tick();
  assert(target.innerHTML.includes('class="chat-stop"'));const stop=target.querySelector(".chat-stop");assert.equal(stop.hidden,false);assert.equal(stop.disabled,false);
  target.querySelector("textarea").value="中断后继续编辑的草稿";target.querySelector("textarea").oninput();
  stop.onclick();await tick();assert.equal(stop.disabled,true);assert.match(stop.textContent,/正在中断/);
  const sent=calls.find(call=>call.url==="/api/session/stop");assert.deepEqual(JSON.parse(JSON.stringify(sent.data)),{session_id:"a"});
  acknowledge({ok:true});await tick();assert.equal(stop.disabled,true);assert.equal(target.querySelector(".send").disabled,true);
  h.update("a",{busy:false});assert.equal(stop.hidden,true);assert.equal(target.querySelector(".send").disabled,false);
  assert.match(target.querySelector(".chat-interrupt-status").textContent,/本轮已停止/);assert.equal(target.querySelector("textarea").value,"中断后继续编辑的草稿");
  assert(!calls.some(call=>/send|wake|archive/.test(call.url)));h.chat.unmount();
});

test("interrupt failure is explicit and can be retried without discarding a draft",async()=>{
  const h=harness();let stops=0;
  const target=h.mount("a",async url=>{if(url==="/api/session/stop"){if(++stops===1)throw Error("temporary stop failure");return {ok:true};}return {...history(),busy:true};},{busy:true});await tick();
  const stop=target.querySelector(".chat-stop");stop.onclick();await tick();assert.equal(stop.disabled,false);assert.match(target.querySelector(".chat-interrupt-status").textContent,/temporary stop failure/);
  stop.onclick();await tick();assert.equal(stops,2);assert.equal(stop.disabled,true);h.chat.unmount();
});

test("external busy sessions explain why Crew cannot interrupt and idle sessions hide the control",async()=>{
  const h=harness(),calls=[];
  const target=h.mount("external",async url=>{calls.push(url);return {...history(),busy:true};},{busy:true,where:"vscode"});await tick();
  const stop=target.querySelector(".chat-stop");assert.equal(stop.hidden,false);assert.equal(stop.disabled,true);assert.match(target.querySelector(".chat-interrupt-status").textContent,/原工具/);
  stop.onclick();await tick();assert(!calls.includes("/api/session/stop"));h.update("external",{busy:false,where:"vscode"});assert.equal(stop.hidden,true);h.chat.unmount();
});
