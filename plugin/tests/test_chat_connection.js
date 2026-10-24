"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function node(){const classes=new Set(),children=[];return {value:"",textContent:"",innerHTML:"",hidden:false,disabled:false,style:{},dataset:{},scrollTop:0,scrollHeight:0,clientHeight:200,firstChild:null,children,contains:()=>false,querySelectorAll:()=>[],setAttribute(){},
  insertBefore(child,before){const old=children.indexOf(child);if(old>=0)children.splice(old,1);const index=before?children.indexOf(before):-1;children.splice(index<0?children.length:index,0,child);this.firstChild=children[0]||null;for(let i=0;i<children.length;i++)children[i].nextSibling=children[i+1]||null;},
  classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x);},contains:x=>classes.has(x)}};}
function host(){const nodes=new Map();return {...node(),querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);}};}
function harness(){
  const timers=new Map(),saved=new Map(),streams=[],connections=[],frames=new Map(),handlers=new Map();let next=1;
  const sandbox={matchMedia:()=>({matches:true}),atob,btoa,URLSearchParams,AbortController,TextDecoder,
    localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)},
    document:{visibilityState:"visible",activeElement:null,addEventListener(type,fn){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(fn);},removeEventListener(type,fn){handlers.get(type)?.delete(fn);},createElement:node},
    setTimeout:(fn,ms)=>{const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>{const id=next++;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
    fetch:async(url,{signal})=>{streams.push(String(url));let pending;const queue=[],stream={url:String(url),signal,emit(event){const chunk={value:new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),done:false};if(pending){const {resolve}=pending;pending=null;resolve(chunk);}else queue.push(chunk);}};connections.push(stream);
      signal.addEventListener("abort",()=>{pending?.reject(Object.assign(Error("aborted"),{name:"AbortError"}));pending=null;},{once:true});
      return {ok:true,body:{getReader:()=>({read:()=>queue.length?Promise.resolve(queue.shift()):new Promise((resolve,reject)=>{pending={resolve,reject};})})}};}};
  sandbox.window=sandbox;vm.createContext(sandbox);
  for(const file of ["vendor/markdown-it.min.js","chat.js"])vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",file),"utf8"),sandbox);
  function mount(id,api,extra={},options={}){const target=host(),session={session_id:id,name:id,busy:false,last_seq:0,activity:null,where:"bg",...extra};sandbox.CrewChat.mount({host:target,session,state:{project:{name:"fixture"},leaders:[session],members:[]},api,toast(){},...options});return target;}
  function update(id,extra={}){const session={session_id:id,name:id,busy:false,last_seq:1,activity:null,where:"bg",...extra};sandbox.CrewChat.update({project:{name:"fixture"},leaders:[session],members:[]});}
  return {chat:sandbox.CrewChat,mount,update,timers,saved,streams,connections,handlers,sandbox,flushFrames(){for(const [id,fn] of frames){frames.delete(id);fn();}}};
}
const history=()=>({events:[],busy:false,read_seq:0});

test("starting a queued goal rebinds the app to the execution journal, closes the empty stream and preserves its draft and reading position",async()=>{
  const h=harness(),nodes=new Map(),calls=[];
  let execution=null;
  const person={session_id:"leader",name:"测试负责人",runtime:"codex",model:null,effort:null,busy:false,last_seq:0,activity:null,where:"bg",priority:null};
  const goal={id:"goal",leader_session_id:"leader",title:"真实目标",request:"推进并交付",acceptance:[],status:"queued",current_round:1,requirement_version:1,version:1};
  const state={project:{name:"fixture"},leaders:[person],members:[],decisions:[],todos:[],goals:[goal],runtimes:[{id:"codex",name:"Codex",available:true,models:[],efforts:[]}]};
  const main=host();nodes.set("main",main);nodes.set("goal-drawer",host());
  Object.defineProperty(main,"innerHTML",{set(value){this.markup=value;for(const [,id]of value.matchAll(/id="([^"]+)"/g))nodes.set(id,host());},get(){return this.markup||"";}});
  h.sandbox.document.getElementById=id=>nodes.get(id)||null;
  h.sandbox.location={search:"?project=fixture",origin:"http://fixture.local"};
  h.sandbox.CrewActivity={markup:()=>""};h.sandbox.CrewContext={};
  const api=async(url,data)=>{
    calls.push({url,data});
    if(url==="/api/goals/action"){
      assert.equal(data.action,"start");Object.assign(goal,{status:"running",version:goal.version+1});
      execution={id:"execution-1",session_id:"leader",runtime:"codex",model:null,effort:null,round:1,requirement_version:1,generation:1};return {goal:{...goal}};
    }
    if(url.startsWith("/api/goal?"))return {goal:{...goal},executions:execution?[{...execution}]:[],context:{items:[],manifest:{}},reports:[]};
    if(url.startsWith("/api/chat/history?"))return {...history(),execution_id:execution?.id??null};
    throw Error("Unexpected request: "+url);
  };
  const appSource=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(appSource.replace("  setup();\n})();",'window.appFixture={renderChat,configure(value,client,request){state=value;goals=client;api=request;view="chat";leaderId="leader";goalId="goal";}};\n})();'),h.sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),h.sandbox);
  const goalUI=h.sandbox.CrewGoals.create({project:"fixture",state:()=>state,api,toast(){},onChange:()=>h.sandbox.appFixture.renderChat(),refreshState:async()=>h.sandbox.appFixture.renderChat()});
  h.sandbox.appFixture.configure(state,goalUI,api);h.sandbox.appFixture.renderChat();await tick();
  assert.equal(h.connections.length,1);const emptyStream=h.connections[0],queuedHost=nodes.get("chat-host");
  queuedHost.querySelector("textarea").value="目标开始后仍要保留的草稿";queuedHost.querySelector("textarea").oninput();
  queuedHost.querySelector(".chat-scroll").scrollTop=123;queuedHost.querySelector(".chat-scroll").onscroll();
  const button={disabled:false,dataset:{goalAct:"action",action:"start"}};
  for(const handler of h.handlers.get("click")||[])await handler({target:{closest:selector=>selector==="[data-goal-act]"?button:null}});
  await tick();
  assert.equal(emptyStream.signal.aborted,true);assert.equal(h.connections.length,2);
  assert.equal(calls.filter(call=>call.url.startsWith("/api/chat/history?")).length,2);
  const runningHost=nodes.get("chat-host");assert.notEqual(runningHost,queuedHost);
  assert.equal(runningHost.querySelector("textarea").value,"目标开始后仍要保留的草稿");assert.equal(runningHost.querySelector(".chat-scroll").scrollTop,123);
  h.connections[1].emit({seq:1,type:"text",id:"native-reply",text:"已经从真实 execution 接到回复"});await tick();h.flushFrames();
  assert(runningHost.querySelector(".chat-inner").children.some(child=>child.innerHTML.includes("已经从真实 execution 接到回复")));
  h.sandbox.appFixture.renderChat();assert.equal(h.connections.length,2,"unchanged execution must retain its stream");
  Object.assign(goal,{current_round:2,version:goal.version+1});execution={...execution,id:"execution-2",round:2,generation:1};
  await goalUI.refresh();await tick();assert.equal(h.connections[1].signal.aborted,true);assert.equal(h.connections.length,3);
  assert.equal(nodes.get("chat-host").querySelector("textarea").value,"目标开始后仍要保留的草稿");
  assert.equal(calls.filter(call=>call.url==="/api/goals/action").length,1);assert(!calls.some(call=>/chat\/send|wake/.test(call.url)));
  assert(h.connections.every(stream=>stream.url.includes("goal_id=goal")));h.chat.unmount();
});

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
  acknowledge({ok:true});await tick();assert.equal(stop.disabled,true);assert.equal(target.querySelector(".send").disabled,false);
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

test("busy input sends a stable idempotent message and keeps failed drafts until actual acceptance",async()=>{
  const h=harness(),calls=[];let sends=0;
  const target=h.mount("worker",async(url,data)=>{calls.push({url,data});if(url==="/api/chat/send"){if(++sends===1)throw Error("connection lost after send");return {accepted:true,delivery:"steered",message_id:data.message_id};}return {...history(),busy:true};},{busy:true});await tick();
  const input=target.querySelector("textarea");input.value="改为先验证短句";input.oninput();
  assert.equal(target.querySelector(".send").disabled,false);assert.match(target.querySelector(".hint").textContent,/直接补充方向/);
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.equal(input.value,"改为先验证短句");
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  const sent=calls.filter(call=>call.url==="/api/chat/send");assert.equal(sent.length,2);assert.equal(sent[0].data.message_id,sent[1].data.message_id);assert(sent[0].data.message_id);
  assert(!("goal_id" in sent[0].data));assert.equal(input.value,"");assert.equal(target.querySelector(".chat-inner").innerHTML,"");h.chat.unmount();
});

test("same member conversations isolate goal drafts, history, events and interrupt scope",async()=>{
  const h=harness(),calls=[],api=async(url,data)=>{calls.push({url,data});if(url==="/api/chat/interrupt")return {ok:true};return {...history(),busy:true};};
  const a=h.mount("member",api,{busy:true},{goalId:"a"});await tick();a.querySelector("textarea").value="目标A未发送";a.querySelector("textarea").oninput();
  a.querySelector(".chat-stop").onclick();await tick();assert.deepEqual(JSON.parse(JSON.stringify(calls.find(call=>call.url==="/api/chat/interrupt").data)),{session_id:"member",goal_id:"a"});
  const b=h.mount("member",async(url,data)=>{calls.push({url,data});return history();},{busy:true,last_seq:300},{goalId:"b"});await tick();assert.equal(b.querySelector("textarea").value,"");
  h.update("member",{busy:true,last_seq:500});assert.equal(b.querySelector(".chat-stop").hidden,true,"identity-level busy must not overwrite goal SSE state");
  b.querySelector("textarea").value="目标B草稿";b.querySelector("textarea").oninput();
  const restored=h.mount("member",async()=>history(),{},{goalId:"a"});await tick();assert.equal(restored.querySelector("textarea").value,"目标A未发送");
  assert(calls.some(call=>call.url.includes("history?")&&call.url.includes("goal_id=a")));assert(calls.some(call=>call.url.includes("history?")&&call.url.includes("goal_id=b")));
  assert(h.streams.some(url=>url.includes("goal_id=a")));assert(h.streams.some(url=>url.includes("goal_id=b")));
  assert(h.saved.has("crew.draft:fixture:member:goal:a"));assert(h.saved.has("crew.draft:fixture:member:goal:b"));h.chat.unmount();
});

test("first goal send and later execution rotation rebind history and SSE without an extra send",async()=>{
  const h=harness(),calls=[];let execution=null,sends=0;
  const target=h.mount("leader",async(url,data)=>{calls.push({url,data});if(url==="/api/chat/send"){execution=`execution-${++sends}`;return {accepted:true,delivery:"started",execution_id:execution};}return {...history(),execution_id:execution};},{},{goalId:"goal"});await tick();
  assert.equal(h.streams.length,1);
  for(const text of ["首次启动目标","下一代次继续"]){target.querySelector("textarea").value=text;target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.equal(target.querySelector("textarea").value,"");}
  assert.equal(sends,2);assert.equal(h.streams.length,3);assert.equal(calls.filter(call=>call.url.includes("history?")).length,3);
  assert(h.streams.every(url=>url.includes("goal_id=goal")));assert.equal(target.querySelector(".chat-connection-error").hidden,true);h.chat.unmount();
});

test("readonly status transitions preserve the same goal draft and reading position without sending",async()=>{
  const h=harness(),calls=[],api=async url=>{calls.push(url);return history();};
  const editable=h.mount("member",api,{web_chat:false,where:"offline"},{goalId:"goal",readonly:false});await tick();
  editable.querySelector("textarea").value="恢复后继续编辑的草稿";editable.querySelector("textarea").oninput();
  const scroll=editable.querySelector(".chat-scroll");scroll.scrollTop=123;scroll.onscroll();
  const paused=h.mount("member",api,{web_chat:false,where:"offline"},{goalId:"goal",readonly:true,readonlyReason:"总对话可讨论；继续工作先恢复/再开一轮。"});await tick();
  assert(paused.innerHTML.includes('class="readonly-note"'));assert(!paused.innerHTML.includes('class="composer-wrap"'));assert.equal(paused.querySelector(".chat-scroll").scrollTop,123);
  const resumed=h.mount("member",api,{web_chat:false,where:"offline"},{goalId:"goal",readonly:false});await tick();
  assert.equal(resumed.querySelector("textarea").value,"恢复后继续编辑的草稿");assert.equal(resumed.querySelector(".chat-scroll").scrollTop,123);
  assert(calls.every(url=>url.startsWith("/api/chat/history?")));h.chat.unmount();
});

test("opening readonly first restores a persisted draft when work is explicitly resumed",async()=>{
  const h=harness();h.saved.set("crew.draft:fixture:member:goal:goal",JSON.stringify({text:"上次保留的草稿",images:[]}));
  h.mount("member",async()=>history(),{web_chat:false},{goalId:"goal",readonly:true});await tick();
  const resumed=h.mount("member",async()=>history(),{web_chat:false},{goalId:"goal",readonly:false});await tick();
  assert.equal(resumed.querySelector("textarea").value,"上次保留的草稿");h.chat.unmount();
});

test("an accepted in-flight send can finish after readonly remount without restoring an already sent draft",async()=>{
  const h=harness();let release,sends=0;const api=async(url)=>{if(url==="/api/chat/send"){sends++;return new Promise(resolve=>{release=resolve;});}return history();};
  const target=h.mount("member",api,{},{goalId:"goal",readonly:false});await tick();target.querySelector("textarea").value="这条已经提交";target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  h.mount("member",api,{},{goalId:"goal",readonly:true});await tick();release({accepted:true,delivery:"started"});await tick();
  assert.equal(JSON.parse(h.saved.get("crew.draft:fixture:member:goal:goal")).text,"");
  const resumed=h.mount("member",api,{},{goalId:"goal",readonly:false});await tick();assert.equal(resumed.querySelector("textarea").value,"");assert.match(resumed.querySelector(".chat-delivery-status").textContent,/已接收消息/);assert.equal(sends,1);h.chat.unmount();
});
