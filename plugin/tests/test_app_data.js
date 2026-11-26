"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),vm=require("node:vm"),fs=require("node:fs"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../../frontend/app-data.js"),"utf8");

function load(){const sandbox={window:{},URL};vm.runInNewContext(source,sandbox);return sandbox.window.CrewAppData;}
const todo=(id,extra={})=>({id,title:`待办 ${id}`,blocking:false,created_at:`2026-11-27T00:00:0${id}.000Z`,status:"open",...extra});

// A fake server: snapshots, SSE streams, writes and timers, all under the test's control.
function harness({project="demo",snapshots=[]}={}){
  const streams=[],timers=[],statuses=[],errors=[],renders=[],requests=[];
  let shellState=null,nextTimer=1;
  class FakeEventSource{
    constructor(url){this.url=url;this.closed=false;this.listeners={};streams.push(this);}
    addEventListener(name,fn){(this.listeners[name]||=[]).push(fn);}
    close(){this.closed=true;}
    send(change){this.onmessage({data:JSON.stringify(change),lastEventId:String(change.seq)});}
    emit(name,data){for(const fn of this.listeners[name]||[])fn({data:JSON.stringify(data)});}
    fail(){this.onerror({});}
  }
  const queue=[...snapshots];
  const ctx={
    origin:"http://127.0.0.1:47811",get project(){return project;},get state(){return shellState;},
    EventSource:FakeEventSource,
    fetch:async(url,init)=>{requests.push({url,method:init.method||"GET",body:init.body});return {ok:true,json:async()=>({ok:true})};},
    api:async path=>{requests.push({url:path,method:"GET"});const next=queue.shift();if(next instanceof Error)throw next;if(next===undefined)throw new Error("no snapshot queued");return next;},
    setTimeout:(fn,delay)=>{const id=nextTimer++;timers.push({id,fn,delay});return id;},
    clearTimeout:id=>{const index=timers.findIndex(timer=>timer.id===id);if(index>=0)timers.splice(index,1);},
    onState(next,previous,meta){renders.push({next,previous,meta});shellState=next;},
    onStatus(status,detail){statuses.push([status,detail]);},
    onError(error){errors.push(error.message);},
  };
  const data=load().create(ctx);
  const open=()=>streams.filter(stream=>!stream.closed);
  const fire=()=>{const timer=timers.shift();timer.fn();return timer.delay;};
  return {data,ctx,streams,open,timers,fire,statuses,errors,renders,requests,queue,get state(){return shellState;},set state(value){shellState=value;}};
}
const snapshot=(seq,todos=[])=>({seq,data:{project:{name:"demo"},todos,todo_count:todos.length,leaders:[]}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
// Values built inside the vm realm compare by structure only after crossing back as plain data.
const eq=(actual,expected)=>assert.deepEqual(JSON.parse(JSON.stringify(actual)),expected);

test("a snapshot is followed by one subscription from its sequence, and changes merge by entity",async()=>{
  const h=harness({snapshots:[snapshot(5,[todo(1)])]});
  await h.data.loadState();
  assert.equal(h.renders[0].meta.source,"snapshot");assert.equal(h.data.seq,5);
  eq(h.open().map(stream=>stream.url),["/api/events?after=5&project=demo"]);
  const [stream]=h.streams;
  // Backlog committed before the subscription waits for "ready" and renders once.
  stream.send({seq:6,type:"todo.updated",entity:"2",data:todo(2,{blocking:true})});
  stream.send({seq:7,type:"todo.updated",entity:"1",data:todo(1,{title:"改过的标题"})});
  assert.equal(h.renders.length,1);
  stream.emit("ready",{seq:7});
  assert.equal(h.renders.length,2);assert.equal(h.renders[1].meta.catchUp,true);
  eq(h.state.todos.map(item=>[item.id,item.title]),[[2,"待办 2"],[1,"改过的标题"]]);
  assert.equal(h.state.todo_count,2);assert.equal(h.data.status,"live");
  // The previous value is untouched, so the shell can still compare old and new state.
  eq(h.renders[1].previous.todos.map(item=>item.title),["待办 1"]);
  stream.send({seq:8,type:"todo.removed",entity:"2",data:null});
  eq(h.state.todos.map(item=>item.id),[1]);assert.equal(h.state.todo_count,1);
  assert.equal(h.renders[2].meta.catchUp,false);
  // A change at or below the applied sequence is already in the state.
  stream.send({seq:8,type:"todo.removed",entity:"1",data:null});
  assert.equal(h.state.todos.length,1);assert.equal(h.renders.length,3);
});

test("a dropped stream resumes from the last applied sequence without a new snapshot",async()=>{
  const h=harness({snapshots:[snapshot(3)]});
  await h.data.loadState();
  h.streams[0].emit("ready",{seq:3});
  h.streams[0].send({seq:4,type:"todo.updated",entity:"1",data:todo(1)});
  h.streams[0].fail();
  assert.equal(h.data.status,"disconnected");assert.equal(h.open().length,0);
  eq(h.timers.map(timer=>timer.delay),[1000]);
  h.fire();
  eq(h.open().map(stream=>stream.url),["/api/events?after=4&project=demo"]);
  h.streams[1].fail();
  assert.equal(h.fire(),2000);
  h.streams[2].emit("ready",{seq:4});
  assert.equal(h.data.status,"live");
  // Only the first load asked for a snapshot.
  eq(h.requests.map(request=>request.url),["/api/snapshot"]);
  // A successful subscription resets the back-off.
  h.streams[2].fail();assert.equal(h.fire(),1000);
});

test("snapshot-required closes the stream, takes one new snapshot and opens exactly one subscription",async()=>{
  const h=harness({snapshots:[snapshot(3,[todo(1)]),snapshot(40,[todo(9)])]});
  await h.data.loadState();
  h.streams[0].emit("ready",{seq:3});
  h.streams[0].fail();h.fire();
  h.streams[1].emit("snapshot-required",{reason:"pruned"});
  await tick();
  assert.equal(h.renders.at(-1).meta.source,"snapshot");assert.equal(h.renders.at(-1).meta.reason,"pruned");
  eq(h.state.todos.map(item=>item.id),[9]);
  eq(h.open().map(stream=>stream.url),["/api/events?after=40&project=demo"]);
  assert.equal(h.timers.length,0);
  assert.ok(h.statuses.some(([status,detail])=>status==="resyncing"&&detail==="pruned"));
  // Events from the closed stream are ignored.
  h.streams[1].send({seq:41,type:"todo.removed",entity:"9",data:null});
  assert.equal(h.state.todos.length,1);
});

test("reconnecting and resyncing never send a write again",async()=>{
  const h=harness({snapshots:[snapshot(1),snapshot(9)]});
  await h.data.loadState();
  await h.data.request("/api/todo/resolve",{id:1,status:"done"});
  h.streams[0].fail();h.fire();
  h.streams[1].emit("snapshot-required",{reason:"ahead"});await tick();
  h.streams[2].fail();h.fire();
  const writes=h.requests.filter(request=>request.method==="POST");
  eq(writes,[{url:"/api/todo/resolve?project=demo",method:"POST",body:JSON.stringify({id:1,status:"done"})}]);
  assert.equal(h.requests.filter(request=>request.url==="/api/snapshot").length,2);
});

test("a failed snapshot is reported to the caller, shown as disconnected and taken again later",async()=>{
  const h=harness({snapshots:[new Error("backend unavailable"),snapshot(2,[todo(1)])]});
  await assert.rejects(h.data.loadState(),/backend unavailable/);
  assert.equal(h.data.status,"disconnected");assert.equal(h.open().length,0);
  eq(h.timers.map(timer=>timer.delay),[1000]);
  h.fire();await tick();
  eq(h.state.todos.map(item=>item.id),[1]);
  eq(h.open().map(stream=>stream.url),["/api/events?after=2&project=demo"]);
  // A failed automatic resync has no caller, so it reports through onError and backs off.
  h.queue.push(new Error("still down"),snapshot(5));
  h.streams[0].emit("snapshot-required",{reason:"backlog"});await tick();
  eq(h.errors,["still down"]);
  assert.equal(h.fire(),1000);await tick();
  assert.equal(h.data.seq,5);assert.equal(h.open().length,1);
});

test("concurrent loads share one snapshot and a fresh request takes one more after it",async()=>{
  const h=harness({snapshots:[snapshot(1),snapshot(2),snapshot(3)]});
  await Promise.all([h.data.loadState(),h.data.loadState()]);
  assert.equal(h.requests.length,1);
  await Promise.all([h.data.loadState(),h.data.loadState(true)]);
  assert.equal(h.requests.length,3);assert.equal(h.data.seq,3);
  assert.equal(h.open().length,1);
});

test("an unknown change stops the stream with a reload message, while shell errors are not swallowed",async()=>{
  const h=harness({snapshots:[snapshot(1)]});
  await h.data.loadState();
  h.streams[0].emit("ready",{seq:1});
  h.streams[0].send({seq:2,type:"leader.updated",entity:"x",data:{}});
  assert.equal(h.data.status,"failed");assert.match(h.errors[0],/leader\.updated.*刷新页面/);
  assert.equal(h.open().length,0);assert.equal(h.timers.length,0);
  const other=harness({snapshots:[snapshot(1)]});
  await other.data.loadState();
  other.streams[0].emit("ready",{seq:1});
  other.ctx.onState=()=>{throw new Error("render failed");};
  assert.throws(()=>other.streams[0].send({seq:2,type:"todo.updated",entity:"1",data:todo(1)}),/render failed/);
  assert.notEqual(other.data.status,"failed");
});

test("project scoping and write errors follow the former api helper",async()=>{
  const h=harness({project:null,snapshots:[snapshot(0)]});
  assert.equal(h.data.queryPath("/api/events?after=0"),"/api/events?after=0");
  const scoped=harness();
  assert.equal(scoped.data.queryPath("/api/settings"),"/api/settings");
  assert.equal(scoped.data.queryPath("/api/state?x=1"),"/api/state?x=1&project=demo");
  scoped.ctx.fetch=async()=>({ok:false,status:409,json:async()=>({error:"人工待办已被更新"})});
  await assert.rejects(scoped.data.request("/api/todo/resolve",{id:1}),error=>error.status===409&&error.message==="人工待办已被更新");
});

test("merge is pure and keeps the snapshot order of each kind",()=>{
  const {merge}=load();
  const base={todos:[todo(1),todo(3)],todo_count:2,leaders:[{session_id:"a"}]};
  const next=merge(base,[{seq:1,type:"todo.updated",entity:"2",data:todo(2,{blocking:true})},{seq:2,type:"todo.removed",entity:"3",data:null}]);
  eq(next.todos.map(item=>item.id),[2,1]);assert.equal(next.todo_count,2);
  eq(base.todos.map(item=>item.id),[1,3]);assert.equal(next.leaders,base.leaders);
});

test("workspace lists merge by key in the server's order",()=>{
  const {merge}=load();
  const base={projects:[{name:"beta",display_name:null,theme:"lotus"}],
    decisions:[{id:4,question:"四"}],
    goals:[{id:"b",priority:0,created_at:"2026-11-27T00:00:02.000Z"},{id:"c",priority:0,created_at:"2026-11-27T00:00:03.000Z"}]};
  const next=merge(base,[
    {seq:1,type:"project.updated",entity:"alpha",data:{name:"alpha",display_name:"甲",theme:"celadon"}},
    {seq:2,type:"decision.updated",entity:"2",data:{id:2,question:"二"}},
    {seq:3,type:"decision.removed",entity:"4",data:null},
    // Higher priority first, then creation time, then id; a later change of the same task replaces it.
    {seq:4,type:"goal.updated",entity:"a",data:{id:"a",priority:0,created_at:"2026-11-27T00:00:01.000Z"}},
    {seq:5,type:"goal.updated",entity:"z",data:{id:"z",priority:3,created_at:"2026-11-27T00:00:09.000Z"}},
    {seq:6,type:"goal.updated",entity:"c",data:{id:"c",priority:0,created_at:"2026-11-27T00:00:00.500Z"}},
    {seq:7,type:"project.removed",entity:"beta",data:null}]);
  eq(next.projects.map(item=>item.name),["alpha"]);
  eq(next.decisions.map(item=>item.id),[2]);
  eq(next.goals.map(item=>item.id),["z","c","a","b"]);
  eq(base.goals.map(item=>item.id),["b","c"]);
});

test("fields are replaced whole and parts keep the fields other sources own",()=>{
  const {merge}=load();
  const base={project:{name:"demo",display_name:null,root:"C:/demo",theme:"celadon"},ai:{default:null},domains:{version:0,paths:{}},
    channel:{unread:0,latest_id:0,digesting:true,digest_error:null,digest_id:null},
    sounds:{preferences:null,revision:0,catalog:{},missing:[],events:[{id:"reply:x"}]},
    limits:{claude:null,codex:null},usage_scanned_at:null,usage_error:null,leaders:[]};
  const next=merge(base,[
    {seq:1,type:"workspace.updated",entity:"demo",data:{name:"demo",display_name:"演示",root:"C:/demo",theme:"lotus"}},
    {seq:2,type:"ai.updated",entity:"ai",data:{default:{runtime:"codex"}}},
    {seq:3,type:"domains.updated",entity:"demo",data:{version:2,paths:{s1:[{id:"p",name:"感知"}]}}},
    {seq:4,type:"channel.updated",entity:"demo",data:{unread:3,latest_id:9,digest_id:null}},
    {seq:5,type:"sound_settings.updated",entity:"sounds",data:{preferences:{muted:true},revision:1,catalog:{presets:{}},missing:[]}},
    {seq:6,type:"limits.updated",entity:"limits",data:{limits:{claude:null,codex:{used_percent:42}},usage_scanned_at:"2026-11-27T00:01:00+00:00",usage_error:null}}]);
  assert.equal(next.project.display_name,"演示");assert.equal(next.ai.default.runtime,"codex");assert.equal(next.domains.version,2);
  eq(next.channel,{unread:3,latest_id:9,digesting:true,digest_error:null,digest_id:null});
  eq(next.sounds.events,[{id:"reply:x"}]);assert.equal(next.sounds.revision,1);
  assert.equal(next.limits.codex.used_percent,42);assert.equal(next.usage_scanned_at,"2026-11-27T00:01:00+00:00");
  assert.equal(next.leaders,base.leaders);assert.equal(base.channel.unread,0);assert.equal(base.limits.codex,null);
});

test("a removed field or a part with other fields than registered is a change the page cannot merge",()=>{
  const {merge}=load();
  const base={ai:{},channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null}};
  assert.throws(()=>merge(base,[{seq:1,type:"ai.removed",entity:"ai",data:null}]),/ai\.removed.*刷新页面/);
  assert.throws(()=>merge(base,[{seq:1,type:"channel.updated",entity:"demo",data:{unread:1,latest_id:1}}]),/channel\.updated.*字段/);
  assert.throws(()=>merge(base,[{seq:1,type:"channel.updated",entity:"demo",data:{unread:1,latest_id:1,digest_id:null,digesting:true}}]),/字段/);
  assert.throws(()=>merge(base,[{seq:1,type:"toString.updated",entity:"x",data:{}}]),/toString\.updated/);
});
