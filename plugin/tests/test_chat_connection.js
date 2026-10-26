"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function node(){const classes=new Set(),children=[],attributes=new Map();return {value:"",textContent:"",innerHTML:"",hidden:false,disabled:false,style:{},dataset:{},scrollTop:0,scrollHeight:0,clientHeight:200,firstChild:null,children,contains:()=>false,querySelectorAll:()=>[],setAttribute(name,value){attributes.set(name,String(value));},getAttribute:name=>attributes.get(name)??null,removeAttribute:name=>attributes.delete(name),
  insertBefore(child,before){const old=children.indexOf(child);if(old>=0)children.splice(old,1);const index=before?children.indexOf(before):-1;children.splice(index<0?children.length:index,0,child);child.parentNode=this;this.firstChild=children[0]||null;for(let i=0;i<children.length;i++)children[i].nextSibling=children[i+1]||null;},
  remove(){const parent=this.parentNode;if(!parent)return;const index=parent.children.indexOf(this);if(index>=0)parent.children.splice(index,1);parent.firstChild=parent.children[0]||null;for(let i=0;i<parent.children.length;i++)parent.children[i].nextSibling=parent.children[i+1]||null;this.parentNode=null;},
  classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x);},contains:x=>classes.has(x)}};}
function host(){const nodes=new Map();return {...node(),querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);}};}
function harness(){
  const timers=new Map(),saved=new Map(),streams=[],connections=[],frames=new Map(),handlers=new Map();let next=1;
  const sandbox={matchMedia:()=>({matches:true}),atob,btoa,URLSearchParams,AbortController,TextDecoder,
    localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)},
    document:{visibilityState:"visible",activeElement:null,querySelectorAll:()=>[],addEventListener(type,fn){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(fn);},removeEventListener(type,fn){handlers.get(type)?.delete(fn);},createElement:node},
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

test("compact watcher waits for native completion and reports cancellation or closed transport",async()=>{
  for(const terminal of [{type:"compact",seq:2,pre_tokens:50000,post_tokens:10000},
    {type:"compact_cancelled",reason:"上一轮未成功结束"},{type:"status",status:"offline"},{type:"error",text:"native failed"}]){
    const h=harness(),streams=[],finished=[],toasts=[];
    h.sandbox.EventSource=class{static CLOSED=2;constructor(url){this.url=url;streams.push(this);}close(){this.closed=true;}};
    await h.chat.watchCompact("member",{state:{project:{name:"fixture"}},api:async()=>({events:[{seq:1}]}),toast:text=>toasts.push(text),onFinish:event=>finished.push(event)});
    assert(streams[0].url.includes("session_id=member"));assert(streams[0].url.includes("after=1"));
    streams[0].onmessage({data:JSON.stringify({type:"turn_result",success:true})});assert.equal(finished.length,0);
    streams[0].onmessage({data:JSON.stringify(terminal)});assert.equal(finished.length,1);assert(streams[0].closed);
    assert.equal(toasts.length,terminal.type==="compact"?1:0);
  }
  const h=harness(),finished=[];let stream;
  h.sandbox.EventSource=class{static CLOSED=2;constructor(){stream=this;this.readyState=0;}close(){this.closed=true;}};
  await h.chat.watchCompact("member",{state:{project:{name:"fixture"}},api:async()=>history(),toast(){},onFinish:event=>finished.push(event)});
  stream.onerror();assert.equal(finished.length,0,"a reconnect is not a completed or failed compaction");
  stream.readyState=2;stream.onerror();assert.equal(finished.length,1);assert(stream.closed);
});

test("a goal-start tool followed by identity handoff and native interruption renders as transferred, not executing forever",async()=>{
  // Minimized from the real journal: tool -> handoff -> interrupted turn -> offline; no tool_result exists.
  const events=[{seq:1,type:"status",status:"working"},
    {seq:2,type:"assistant",id:"start",content:[{type:"tool_use",id:"call-start",name:"crew_goal",input:{action:"start",goal_id:"goal"}}]},
    {seq:3,type:"identity_handoff",target_execution:"execution",text:"当前 Crew 身份已转交执行；保留此段历史，旧原生进程停止。"},
    {seq:4,type:"turn_result",status:"interrupted",success:false},
    {seq:5,type:"status",status:"offline"}];
  const h=harness(),calls=[],routed=[];
  const target=h.mount("leader",async url=>{calls.push(url);return {...history(),events,read_seq:5};},{},{onRouted:id=>routed.push(id)});await tick();h.flushFrames();
  const html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert(!html.includes("正在执行…"),"the former general turn has already ended");
  assert(!html.includes('aria-label="执行中"'));assert(html.includes("已转交"));
  assert(!html.includes("执行失败"));assert(calls.every(url=>url.startsWith("/api/chat/history?")));
  assert(html.includes('data-chat-goal="goal"'));
  await target.querySelector(".chat-inner").onclick({target:{closest:selector=>selector==="[data-chat-goal]"?{dataset:{chatGoal:"goal"}}:null}});
  assert.deepEqual(routed,["goal"]);assert(calls.every(url=>url.startsWith("/api/chat/history?")));
  h.chat.unmount();
});

test("live handoff closes a pending tool, while a later native result retains its actual failure and output",async()=>{
  const h=harness(),target=h.mount("leader",async()=>history());await tick();
  const stream=h.connections[0],html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  stream.emit({seq:1,type:"status",status:"working"});
  stream.emit({seq:2,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"crew_goal",input:{action:"start",goal_id:"goal"}}]});
  await tick();h.flushFrames();assert(html().includes("正在执行…"));
  stream.emit({seq:3,type:"identity_handoff",target_execution:"execution"});
  stream.emit({seq:4,type:"turn_result",status:"interrupted"});
  stream.emit({seq:5,type:"status",status:"offline"});await tick();h.flushFrames();
  assert(html().includes("已转交"));assert(!html().includes("正在执行…"));
  stream.emit({seq:6,type:"tool_result",tool_use_id:"tool",is_error:true,content:"目标启动被拒绝的真实原因"});await tick();h.flushFrames();
  assert(html().includes("执行失败"));assert(html().includes("目标启动被拒绝的真实原因"));assert(!html().includes("已转交"));h.chat.unmount();
});

test("terminal turn receipts stop waiting before a separate status event arrives",async()=>{
  for(const event of [{type:"turn_result",status:"completed"},{type:"turn_result",status:"failed"},
    {type:"turn_result",status:"interrupted"},{type:"identity_handoff",target_execution:"task"}]){
    const h=harness(),target=h.mount("leader",async()=>history());await tick();
    const stream=h.connections[0],progress=()=>target.querySelector(".chat-inner").children.filter(item=>item.className==="chat-activity");
    stream.emit({seq:1,type:"status",status:"working"});await tick();h.flushFrames();assert.equal(progress().length,1);
    stream.emit({seq:2,...event});await tick();h.flushFrames();
    assert.equal(progress().length,0,"a terminal receipt is sufficient to stop waiting");
    assert.equal(target.querySelector(".chat-stop").hidden,true);
    h.update("leader",{busy:true,last_seq:2});h.flushFrames();assert.equal(progress().length,0,"the same snapshot cannot revive an ended turn");
    stream.emit({seq:3,type:"status",status:"working"});await tick();h.flushFrames();assert.equal(progress().length,1,"new work still shows progress");
    h.chat.unmount();
  }
});

test("replayed legacy goal handoff cannot regain a waiting spinner from the same presence snapshot",async()=>{
  const events=[{seq:1,type:"status",status:"working"},
    {seq:2,type:"assistant",id:"start",content:[{type:"tool_use",id:"call",name:"Bash",input:{command:"crew goal start task"}}]},
    {seq:3,type:"turn_result",status:"interrupted",reason:"identity_handoff"},
    {seq:4,type:"status",status:"offline"},{seq:5,type:"identity_handoff",target_execution:"task"}];
  const h=harness(),read=async()=>({...history(),events,read_seq:5}),extra={busy:true,last_seq:5};
  for(let opened=0;opened<2;opened++){
    const target=h.mount("leader",read,extra);await tick();h.flushFrames();
    h.update("leader",extra);h.flushFrames();
    const rows=target.querySelector(".chat-inner").children;
    assert(rows.some(item=>item.innerHTML.includes("已转交执行")));
    assert.equal(rows.filter(item=>item.className==="chat-activity").length,0);
    assert.equal(target.querySelector(".chat-stop").hidden,true);
    h.chat.unmount();
  }
});

test("legacy offline history stops the spinner but keeps the missing tool result explicitly unconfirmed",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),read_seq:2,events:[
    {seq:1,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"crew_goal",input:{action:"start"}}]},
    {seq:2,type:"status",status:"offline"}]}));await tick();h.flushFrames();
  const html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert(!html.includes("正在执行…"));assert(html.includes("会话已离线"));assert(html.includes("结果未确认"));
  assert(!html.includes('class="ok"'));assert(!html.includes("执行失败"));h.chat.unmount();
});

test("a confirmed handoff after native output drains upgrades the interrupted tool and preserves a later real receipt",async()=>{
  const h=harness(),target=h.mount("member",async()=>history());await tick();
  const stream=h.connections[0],html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  stream.emit({seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"Bash",input:{command:"crew goal start goal"}}]});
  stream.emit({seq:2,type:"turn_result",status:"interrupted",reason:"identity_handoff"});
  stream.emit({seq:3,type:"status",status:"offline"});
  stream.emit({seq:4,type:"identity_handoff",target_execution:"execution"});await tick();h.flushFrames();
  assert(html().includes("已转交执行"));assert(!html().includes("本轮已中断"));assert(!html().includes("正在执行…"));
  stream.emit({seq:5,type:"tool_result",tool_use_id:"old-tool",content:"具体执行结果",is_error:false});await tick();h.flushFrames();
  assert(html().includes("具体执行结果"));assert(!html().includes("已转交执行"));h.chat.unmount();
});

test("losing the browser stream does not mark a still running native tool offline",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),busy:true,events:[
    {seq:1,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"Bash",input:{command:"long command"}}]}]}));
  await tick();h.flushFrames();
  assert(target.querySelector(".chat-inner").children.some(item=>item.innerHTML.includes("正在执行…")));h.chat.unmount();
});

test("a later handoff never rewrites the failure of an earlier turn's tool",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),events:[
    {seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"Bash",input:{command:"old failure"}}]},
    {seq:2,type:"turn_result",status:"failed"},
    {seq:3,type:"assistant",id:"new",content:[{type:"tool_use",id:"new-tool",name:"crew_goal",input:{action:"start"}}]},
    {seq:4,type:"turn_result",status:"interrupted",reason:"identity_handoff"},
    {seq:5,type:"status",status:"offline"},
    {seq:6,type:"identity_handoff",target_execution:"new-execution"}]}));await tick();h.flushFrames();
  const rows=target.querySelector(".chat-inner").children;
  const old=rows.find(item=>item.innerHTML.includes("old failure"));
  assert(old.innerHTML.includes("本轮失败"));assert(!old.innerHTML.includes("已转交"));
  assert(rows.some(item=>item.innerHTML.includes("已转交执行")));h.chat.unmount();
});

test("terminal turns without a tool result remain explicitly unknown, and do not close a later turn's new tool",async()=>{
  for(const [status,label]of [["completed","本轮已结束"],["interrupted","本轮已中断"],["failed","本轮失败"]]){
    const h=harness(),target=h.mount("leader",async()=>({...history(),read_seq:2,events:[
      {seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"crew_goal",input:{action:"status"}}]},
      {seq:2,type:"turn_result",status}]}));await tick();h.flushFrames();
    let html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
    assert(html.includes(label));assert(html.includes("未收到"));assert(!html.includes('class="ok"'));assert(!html.includes("正在执行…"));
    h.connections[0].emit({seq:3,type:"assistant",id:"new",content:[{type:"tool_use",id:"new-tool",name:"crew_goal",input:{action:"status"}}]});await tick();h.flushFrames();
    html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");assert(html.includes(label));assert(html.includes("正在执行…"));h.chat.unmount();
  }
});

test("starting a queued goal rebinds the app to the execution journal, closes the empty stream and preserves its draft and reading position",async()=>{
  const h=harness(),nodes=new Map(),calls=[];
  let execution=null;
  const person={session_id:"leader",name:"测试负责人",runtime:"codex",model:null,effort:null,busy:false,last_seq:0,activity:null,where:"bg",priority:null,status:"idle",codex:[]};
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

test("the general stop waits for its process-exit receipt and then confirms the identity is stopped",async()=>{
  const h=harness(),calls=[];let acknowledge;
  const target=h.mount("a",async(url,data)=>{calls.push({url,data});if(url==="/api/session/stop")return new Promise(resolve=>{acknowledge=resolve;});return {...history(),busy:true};},{busy:true});await tick();
  assert(target.innerHTML.includes('class="chat-stop"'));const stop=target.querySelector(".chat-stop");assert.equal(stop.hidden,false);assert.equal(stop.disabled,false);
  target.querySelector("textarea").value="中断后继续编辑的草稿";target.querySelector("textarea").oninput();
  stop.onclick();await tick();assert.equal(stop.disabled,true);assert.equal(stop.textContent,"");assert.equal(stop.getAttribute("aria-busy"),"true");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  const sent=calls.find(call=>call.url==="/api/session/stop");assert.deepEqual(JSON.parse(JSON.stringify(sent.data)),{session_id:"a"});
  acknowledge({ok:true});await tick();assert.equal(stop.disabled,true);assert.equal(stop.hidden,true);assert.equal(target.querySelector(".send").disabled,false);
  h.update("a",{busy:false});assert.equal(stop.hidden,true);assert.equal(target.querySelector(".send").disabled,false);
  assert.equal(stop.getAttribute("aria-busy"),"false");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);assert.equal(target.querySelector("textarea").value,"中断后继续编辑的草稿");
  assert(!calls.some(call=>/send|wake|archive/.test(call.url)));h.chat.unmount();
});

test("the general stop confirmation locks repeated clicks and cancellation sends no stop request",async()=>{
  const h=harness(),calls=[];let resolve,confirmations=0;const target=h.mount("leader",async(url)=>{calls.push(url);return history();},{busy:true},{beforeStop:()=>{confirmations++;return new Promise(done=>{resolve=done;});}});await tick();h.update("leader",{busy:true});const button=target.querySelector(".chat-stop");button.onclick();button.onclick();assert.equal(confirmations,1);assert.equal(button.disabled,true);assert.equal(button.getAttribute("aria-busy"),"true");resolve(false);await tick();assert(!calls.includes("/api/session/stop"));assert.equal(button.disabled,false);assert.equal(button.getAttribute("aria-busy"),"false");h.chat.unmount();
});

test("goal interruption follows its live journal even when the person's general carrier is offline",async()=>{
  const h=harness(),calls=[];const target=h.mount("a",async(url,data)=>{calls.push({url,data});return url==="/api/chat/interrupt"?{ok:true}:{...history(),busy:true,execution_id:"execution"};},{where:"offline",running:false},{goalId:"goal"});await tick();
  const stop=target.querySelector(".chat-stop");assert.equal(stop.disabled,false);assert.equal(stop.getAttribute("aria-label"),"停止当前回复");stop.onclick();await tick();assert.equal(stop.disabled,true);assert.equal(stop.hidden,false,"goal ACK still awaits journal exit");assert.equal(stop.getAttribute("aria-busy"),"true");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  h.connections[0].emit({seq:1,type:"status",status:"idle"});await tick();assert.equal(stop.hidden,true);assert.equal(stop.getAttribute("aria-busy"),"false");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);assert.equal(calls[1].url,"/api/chat/interrupt");h.chat.unmount();
});

test("an accepted general send follows the backend goal receipt and preserves both scoped drafts without routing a failure",async()=>{
  const h=harness(),calls=[],routed=[];let failed=true;
  const goal=h.mount("leader",async()=>({...history(),execution_id:"execution"}),{},{goalId:"goal"});await tick();goal.querySelector("textarea").value="任务未发送草稿";goal.querySelector("textarea").oninput();
  const general=h.mount("leader",async(url,data)=>{calls.push({url,data});if(url!=="/api/chat/send")return history();if(failed)throw Error("连接失败");return {accepted:true,goal_id:"goal",execution_id:"execution",delivery:"steered"};},{},{onRouted:id=>{routed.push(id);h.mount("leader",async()=>({...history(),execution_id:"execution"}),{},{goalId:id});}});await tick();
  general.querySelector("textarea").value="开始了吗";general.querySelector("textarea").oninput();general.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.deepEqual(routed,[]);assert.equal(general.querySelector("textarea").value,"开始了吗");
  failed=false;general.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.deepEqual(routed,["goal"]);assert(h.connections[h.connections.length-2].signal.aborted);assert.equal(JSON.parse(h.saved.get("crew.draft:fixture:leader")).text,"");assert.equal(JSON.parse(h.saved.get("crew.draft:fixture:leader:goal:goal")).text,"任务未发送草稿");
  const sends=calls.filter(call=>call.url==="/api/chat/send");assert.equal(sends.length,2);assert.equal(sends[0].data.message_id,sends[1].data.message_id);assert(!("goal_id"in sends[0].data));h.chat.unmount();
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
  assert.equal(target.querySelector(".send").disabled,false);assert.equal(target.querySelector(".send").hidden,false);assert.equal(target.querySelector(".hint").textContent,"");
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.equal(input.value,"改为先验证短句");
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  const sent=calls.filter(call=>call.url==="/api/chat/send");assert.equal(sent.length,2);assert.equal(sent[0].data.message_id,sent[1].data.message_id);assert(sent[0].data.message_id);
  assert(!("goal_id" in sent[0].data));assert.equal(input.value,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".send").hidden,true);h.chat.unmount();
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

test("a detached goal scroll event cannot reset the reading position while the same chat remounts",async()=>{
  const h=harness();const first=h.mount("leader",async()=>history(),{},{goalId:"goal"});await tick();const detached=first.querySelector(".chat-scroll");detached.scrollTop=160;detached.onscroll();
  h.mount("member",async()=>history(),{},{goalId:"goal"});await tick();
  let release;const restored=h.mount("leader",()=>new Promise(resolve=>{release=resolve;}),{},{goalId:"goal"});detached.scrollTop=0;detached.onscroll();release(history());await tick();assert.equal(restored.querySelector(".chat-scroll").scrollTop,160);h.chat.unmount();
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
  const resumed=h.mount("member",api,{},{goalId:"goal",readonly:false});await tick();assert.equal(resumed.querySelector("textarea").value,"");assert.equal(resumed.querySelector(".chat-delivery-status").hidden,true);assert.equal(sends,1);h.chat.unmount();
});

test("a busy turn has one progress row and swaps send for the icon stop until a follow-up draft is ready",async()=>{
  const h=harness(),calls=[];let accept;
  const target=h.mount("worker",async(url,data)=>{calls.push({url,data});if(url==="/api/chat/send")return new Promise(resolve=>{accept=resolve;});return {...history(),busy:true};},{busy:true});await tick();h.flushFrames();
  const input=target.querySelector("textarea"),send=target.querySelector(".send"),stop=target.querySelector(".chat-stop");
  const progress=()=>target.querySelector(".chat-inner").children.filter(child=>child.className==="chat-activity");
  assert.equal(progress().length,1,"busy without provider activity still shows a single progress row");
  assert.equal(target.querySelector(".hint").textContent,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  assert.equal(send.hidden,true);assert.equal(stop.hidden,false);assert.equal(stop.textContent,"");assert.equal(stop.getAttribute("aria-label"),"停止运行");
  input.value="先检查复现步骤";input.oninput();assert.equal(send.hidden,false);assert.equal(send.disabled,false);assert.equal(stop.hidden,false);
  target.querySelector("form").onsubmit({preventDefault(){}});assert.equal(send.disabled,true);await tick();
  accept({accepted:true,delivery:"steered"});await tick();h.flushFrames();
  assert.equal(calls.filter(call=>call.url==="/api/chat/send").length,1);assert.equal(input.value,"");assert.equal(send.hidden,true);
  assert.equal(progress().length,1);assert.equal(target.querySelector(".hint").textContent,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  h.update("worker",{busy:false});h.flushFrames();assert.equal(progress().length,0);assert.equal(stop.hidden,true);assert.equal(send.hidden,false);h.chat.unmount();
});


test("task history keeps earlier executions separate from live IDs, cursors and attachments",async()=>{
  const h=harness(),calls=[];
  const target=h.mount("leader",async(url,data)=>{
    calls.push({url,data});
    if(url==="/api/chat/read")return {read_seq:data.seq};
    return {...history(),execution_id:"new-execution",read_seq:3,
      previous_executions:[{execution_id:"old-execution",round:1,generation:1,events:[
        {seq:1,type:"message",id:"same-message",source:"human",text:"Original discussion",images:["old.png"]},
        {seq:2,type:"assistant",id:"same-answer",content:[{type:"text",text:"Original answer"},{type:"tool_use",id:"same-tool",name:"Bash",input:{command:"old command"}}]},
        {seq:3,type:"tool_result",tool_use_id:"same-tool",content:"Original tool result"},
        {seq:4,type:"status",status:"offline"}]}],
      events:[{seq:1,type:"message",id:"same-message",source:"human",text:"Continue discussion"},
        {seq:2,type:"assistant",id:"same-answer",content:[{type:"text",text:"Current answer"},{type:"tool_use",id:"same-tool",name:"Bash",input:{command:"new command"}}]},
        {seq:3,type:"status",status:"working"}]};
  },{},{goalId:"goal"});await tick();h.flushFrames();
  const html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert(html().includes("Original discussion"));assert(html().includes("Original answer"));assert(html().includes("Continue discussion"));assert(html().includes("Current answer"));
  assert(html().includes("execution_id=old-execution"));assert(html().includes("Original tool result"));
  assert.equal((html().match(/aria-label="执行中"/g)||[]).length,1,"only the current tool is still running");
  assert(h.streams[0].endsWith("after=3"),"old journal sequence must not hide new SSE events");
  h.connections[0].emit({seq:4,type:"tool_result",tool_use_id:"same-tool",content:"Current tool result"});await tick();h.flushFrames();
  assert(html().includes("Original tool result"));assert(html().includes("Current tool result"));
  const oldNode=target.querySelector(".chat-inner").children.find(item=>item.innerHTML.includes("Original tool result"));
  const button={...node(),dataset:{chatFold:"history:old-execution:tool:same-tool"}};
  oldNode.contains=value=>value===button;
  await target.querySelector(".chat-inner").onclick({target:{closest:selector=>selector==="[data-chat-fold]"?button:null}});
  assert.equal(button.getAttribute("aria-expanded"),"true");
  h.connections[0].emit({seq:5,type:"text",id:"live-tail",text:"Next live text"});await tick();h.flushFrames();
  assert(oldNode.dataset.html.includes('aria-expanded="true"'),"historical tool stays expanded on live updates");
  assert.equal((html().match(/Original answer/g)||[]).length,1);
  assert(calls.every(call=>call.url!=="/api/chat/send"));h.chat.unmount();
});


test("execution continuation keeps visible history through a failed reload and recovers without resending",async()=>{
  const h=harness();let reads=0,sends=0;
  const oldEvents=[{seq:1,type:"message",id:"old",source:"human",text:"Keep visible discussion"}];
  const target=h.mount("leader",async(url,data)=>{
    if(url==="/api/chat/send"){sends++;return {accepted:true,execution_id:"new"};}
    if(url==="/api/chat/read")return {read_seq:data.seq};
    if(++reads===2)throw Error("history temporarily unavailable");
    return {...history(),execution_id:reads===1?"old":"new",read_seq:1,
      previous_executions:reads===1?[]:[{execution_id:"old",events:oldEvents}],
      events:reads===1?oldEvents:[{seq:1,type:"message",id:"new",source:"human",text:"Continue this task"}]};
  },{},{goalId:"goal"});await tick();
  const html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  target.querySelector("textarea").value="Continue this task";target.querySelector("textarea").oninput();
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
  assert(html().includes("Keep visible discussion"));assert.equal(sends,1);
  assert.equal(target.querySelector(".chat-connection-error").hidden,false);
  const retry=[...h.timers.values()].find(timer=>timer.ms===3000);assert(retry);retry.fn();await tick();h.flushFrames();
  assert(html().includes("Keep visible discussion"));assert(html().includes("Continue this task"));
  assert.equal((html().match(/Keep visible discussion/g)||[]).length,1);assert.equal(sends,1);
  assert.equal(target.querySelector(".chat-connection-error").hidden,true);h.chat.unmount();
});
