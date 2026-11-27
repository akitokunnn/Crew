"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const read=name=>fs.readFileSync(path.join(__dirname,"../../frontend",name),"utf8").replace(/\r\n/g,"\n");
// app.js on the workspace data layer (app-data.js): snapshots from the injected api, a fake change stream and timers.
function harness(api){
  const retry={disabled:false},main={innerHTML:"",dataset:{},querySelector:()=>retry},notices=[],streams=[],timers=[];
  const navigation={hidden:true},projectName={textContent:""},connection={hidden:true,dataset:{},textContent:""};
  const input={value:"",focus(){this.focused=true;},addEventListener(_type,fn){this.change=fn;}};
  const send={disabled:true},error={textContent:""};
  const form={elements:{goal:input},dataset:{},querySelector:selector=>selector===".error-line"?error:send,addEventListener(_type,fn){this.submit=()=>fn({preventDefault(){}});}};
  const backdrop={innerHTML:"",dataset:{},classList:new Set(),querySelector:selector=>selector==="form"?form:null};
  backdrop.classList.remove=backdrop.classList.delete.bind(backdrop.classList);
  let renders=0,nextTimer=1;
  class FakeEventSource{
    constructor(url){this.url=url;this.closed=false;this.listeners={};streams.push(this);}
    addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
    close(){this.closed=true;}
    emit(name,data){for(const fn of this.listeners[name]||[])fn({data:JSON.stringify(data)});}
    fail(){this.onerror({});}
  }
  const location={hash:"",pathname:"/",search:"?project=fixture",origin:"http://127.0.0.1:47811"};
  const sandbox={window:{},document:{activeElement:null,addEventListener(){},getElementById:id=>({main,backdrop,"mobile-navigation":navigation,"mobile-project":projectName,"connection-status":connection})[id]||null},
    location,history:{replaceState(_state,_title,url){location.hash=new URL(url,location.origin).hash;}},localStorage:{setItem(){}},CrewChat:{setTask(){}},URL,URLSearchParams,EventSource:FakeEventSource,
    setTimeout:(fn,delay)=>{const id=nextTimer++;timers.push({id,fn,delay});return id;},clearTimeout:id=>{const index=timers.findIndex(timer=>timer.id===id);if(index>=0)timers.splice(index,1);},
    setInterval(){throw Error("工作台不应再定时轮询");},CrewActivity:{sync(){}},CrewSound:{observe(){}}};
  vm.createContext(sandbox);
  vm.runInContext(read("browser-utils.js"),sandbox);
  vm.runInContext(read("app-data.js"),sandbox);
  sandbox.CrewAppData=sandbox.window.CrewAppData;
  vm.runInContext(read("app.js").replace("  setup();\n})();",`const route=syncRoute;window.ui={loadState,startWorkspace,enableRoute(){syncRoute=route;},services(values){api=values.api;toast=values.toast;applyTheme=()=>{};renderSettings=()=>{};readRoute=()=>{view="all";};render=()=>{values.rendered();document.getElementById("main").innerHTML="已恢复的工作区";};renderSidebar=()=>{};syncRoute=()=>{};syncSidecar=()=>{};refreshUsage=()=>{};}};\n})();`),sandbox);
  // Room badges load independently of workspace snapshots and opening submissions.
  const request=(path,...args)=>path==="/api/op/war_room.list" ? Promise.resolve({rooms:[]}) : api(path,...args);
  sandbox.window.ui.services({api:request,toast:message=>notices.push(message),rendered:()=>{renders+=1;}});
  return {ui:sandbox.window.ui,main,retry,notices,streams,timers,connection,location,backdrop,form,input,send,error,
    setGoal(value){input.value=value;input.change();},get renders(){return renders;}};
}
const state=()=>({project:{name:"fixture"},leaders:[],members:[],decisions:[],goals:[],todos:[],attention:[],notification:{channels:[],error:null},sounds:{events:[]}});
const snapshot=seq=>({seq,data:state()});

test("initial state failure shows the actual escaped error, reconnects on its own and retry can recover without toast",async()=>{
  let calls=0,release;const h=harness(async()=>{if(++calls===1)throw Error("prices.json <missing>");return new Promise(resolve=>{release=resolve;});});
  await h.ui.loadState();assert(h.main.innerHTML.includes("Crew 加载失败"));assert(h.main.innerHTML.includes("prices.json &lt;missing&gt;"));assert(h.main.innerHTML.includes("正在自动重连"));assert.equal(h.notices.length,0);
  assert.equal(h.timers.length,1,"the data layer schedules its own retry");
  const retry=h.retry.onclick();assert.equal(h.retry.disabled,true);assert.equal(calls,2);release(snapshot(3));await retry;assert.equal(h.retry.disabled,false);assert.equal(h.main.innerHTML,"已恢复的工作区");
  assert.equal(h.timers.length,0,"a manual recovery cancels the scheduled retry");
  assert.deepEqual(h.streams.map(stream=>stream.url),["/api/events?after=3&project=fixture"]);
});

test("a later state failure preserves the current workspace and reports the real error",async()=>{
  let calls=0;const h=harness(async()=>{if(++calls===1)return snapshot(1);throw Error("backend unavailable");});
  await h.ui.loadState();h.main.innerHTML="当前对话和未发送草稿";await h.ui.loadState();assert.equal(h.main.innerHTML,"当前对话和未发送草稿");assert.deepEqual(h.notices,["backend unavailable"]);
});

test("a dropped stream shows the disconnection, resumes from the last sequence and live changes render without a request",async()=>{
  let calls=0;const h=harness(async path=>{calls+=1;assert.equal(path,"/api/op/workspace.snapshot");return snapshot(4);});
  await h.ui.loadState();
  const [first]=h.streams;first.emit("ready",{seq:4});assert.equal(h.connection.hidden,true);
  first.onmessage({data:JSON.stringify({seq:5,type:"todo.updated",entity:"1",data:{id:1,title:"新待办",blocking:false,created_at:"2026-11-27T00:00:00.000Z"}})});
  assert.equal(h.renders,2);assert.equal(calls,1);
  first.fail();
  assert.equal(h.connection.hidden,false);assert.equal(h.connection.textContent,"已断开，正在重连…");
  h.timers.shift().fn();
  const [,second]=h.streams;assert.equal(second.url,"/api/events?after=5&project=fixture");
  second.emit("ready",{seq:5});assert.equal(h.connection.hidden,true);assert.equal(calls,1,"resuming never takes another snapshot");
});

test("the workspace no longer polls the snapshot",()=>{
  assert(!read("app.js").includes("setInterval(loadState"));
  assert(read("index.html").indexOf('src="/app-data.js"')<read("index.html").indexOf('src="/app.js"'));
});

test("the opening form rejects blank goals and repeated in-flight submits, then enters the reserved room",async()=>{
  let release;
  const calls=[],h=harness(async(path,body)=>{
    if(path==="/api/op/workspace.snapshot") return snapshot(1);
    calls.push([path,body]);
    if(path==="/api/op/assistant.open") return {session_id:"workspace-helper"};
    return new Promise(resolve=>{release=resolve;});
  });
  await h.ui.loadState();
  h.ui.enableRoute();
  h.ui.startWorkspace("war-room");
  assert(h.input.focused);
  assert.match(h.backdrop.innerHTML,/这次想一起搞定什么？/);
  h.setGoal(" \n ");
  assert(h.send.disabled);
  await h.form.submit();
  assert.equal(calls.length,0);
  const title="🧪".repeat(31);
  h.setGoal(title+"\n核对结果");
  assert.equal(h.send.disabled,false);
  const first=h.form.submit();
  await h.form.submit();
  const [[assistant, assistantArgs],[open, args]]=calls;
  assert.equal(calls.length,2);
  assert.equal(assistant,"/api/op/assistant.open");
  assert.equal(Object.keys(assistantArgs).length,0); // The workspace transport puts project in the URL.
  assert.equal(open,"/api/op/war_room.open");
  assert.equal(args.lead,"workspace-helper");
  assert.equal(args.title,"🧪".repeat(30));
  assert.equal(args.goal,title+"\n核对结果");
  assert.ok(args.request_id);
  release({id:42});
  await first;
  assert.equal(h.location.hash,"#war-room=42");
  assert.equal(h.backdrop.innerHTML,"");
  assert.deepEqual(h.notices,[]);
});

test("a retry keeps the same opening ID, shows the real failure and follows the occupied room",async()=>{
  const requests=[];
  const h=harness(async(path,body)=>{
    if(path==="/api/op/workspace.snapshot") return snapshot(1);
    if(path==="/api/op/assistant.open") return {session_id:"workspace-helper"};
    requests.push(body.request_id);
    if(requests.length===1) throw Error("写入超时");
    throw Object.assign(Error("本工作区已有开放的作战室"),{details:{code:"room_occupied",detail:{room_id:17}}});
  });
  await h.ui.loadState();
  h.ui.enableRoute();
  h.ui.startWorkspace("war-room");
  h.setGoal("保留这次输入");
  await h.form.submit();
  assert.equal(h.error.textContent,"写入超时");
  assert.equal(h.input.value,"保留这次输入");
  assert.equal(h.send.disabled,false);
  await h.form.submit();
  assert.deepEqual(requests,[requests[0],requests[0]]);
  assert.equal(h.location.hash,"#war-room=17");
  assert.deepEqual(h.notices,["这个工作区已经有一个作战室，先带你过去"]);
});
