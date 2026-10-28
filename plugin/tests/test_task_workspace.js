"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const task = (id = "a", extra = {}) => ({id,title:`任务 ${id}`,primary_project:"primary",projects:["primary","related"],leader_session_id:"leader",request:"明确的目标",acceptance:[{id:"criterion",title:"验收依据"}],constraints:["保留并行成果"],mode:"manual",authorized:false,state:"discussion",execution_status:"queued",review_status:"none",version:1,...extra});
const leader = {session_id:"leader",name:"负责人",role:"leader",activity:null}, member = {session_id:"member",crew_name:"施工成员",role:"member",runtime:"codex",leader_session_id:"leader"};
const detail = (id, extra = {}) => ({task:task(id,extra),members:[leader,member],executions:[],documents:[],reports:[],notifications:{receipts:[]}});
function harness(request) {
  const elements = new Map(), listeners = new Map(), routes = new Map(), saved = new Map(), calls = [], mounted = [], navigated = [], notices = [];
  function node() {return {innerHTML:"",hidden:false,inert:false,scrollTop:0,isConnected:true,dataset:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},focus(){},getBoundingClientRect:()=>({top:0}),querySelector:selector=>element(selector),querySelectorAll:()=>[],addEventListener(){},removeEventListener(){}};}
  const element = selector => {if(selector === ".tw-details-form") return elements.get(selector) || null;if(!elements.has(selector))elements.set(selector,node());return elements.get(selector);};
  const host = {...node(),addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
  const actions = {register(owner,handlers){for(const [name,fn]of Object.entries(handlers))routes.set(name,fn);return()=>{for(const name of Object.keys(handlers))routes.delete(name);};}};
  const client = {request:async(url,body)=>{calls.push({url,body});return request(url,body);}};
  const sandbox = {window:null,URLSearchParams,matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),setTimeout,clearTimeout,requestAnimationFrame:fn=>fn(),localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)},document:{activeElement:null},confirm:()=>true,
    CrewChat:{mount(options){mounted.push(options);return{update(){},unmount(){}};}}};sandbox.window=sandbox;
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/task-workspace.js"),"utf8"),sandbox);
  const ui=sandbox.CrewTaskWorkspace.create({host,client,actions,navigate:value=>navigated.push(value),toast:text=>notices.push(text)});
  const invoke=(name,dataset={})=>routes.get(name)({element:{dataset}});
  return{ui,host,client,calls,mounted,navigated,notices,saved,elements,listeners,invoke};
}
const state = () => ({project:{name:"primary",root:"C:/fixture"},leaders:[leader],members:[member],decisions:[],todos:[]});

test("task workspace follows stable task/person IDs and uses the primary project for both history and queue",async()=>{
  const h=harness(async(url,body)=>url.startsWith("/api/task?")?detail("a"):url.startsWith("/api/state?")?state():{task:task("a",{version:2}),receipt:{status:"queued"}});
  await h.ui.open({taskId:"a",project:"related",sessionId:"member"});
  assert.equal(h.mounted.length,1);const chat=h.mounted[0];assert.equal(chat.goalId,"a");assert.equal(chat.session.session_id,"member");assert.equal(chat.session.name,"施工成员");assert.equal(chat.state.project.name,"primary");assert.equal(chat.memberConversation,true);
  await chat.onQueue({text:"当前轮后验证边界",sessionId:"member",requestId:"queue-id"});
  const queued=h.calls.find(c=>c.url.startsWith("/api/tasks/schedule"));assert.match(queued.url,/project=primary/);assert.equal(queued.body.task_id,"a");assert.equal(queued.body.version,1);assert.equal(queued.body.text,"当前轮后验证边界");assert.equal(queued.body.evidence.text,queued.body.text);assert.equal(queued.body.request_id,"queue-id");
  h.invoke("tw-leader");assert.deepEqual(JSON.parse(JSON.stringify(h.navigated[0])),{view:"task",taskId:"a",project:"primary",sessionId:"leader"});assert(!h.calls.some(c=>c.url.includes("/chat/send")));h.ui.destroy();assert.equal(h.listeners.size,0);
});

test("a late detail response cannot mount the previous task or overwrite the newly selected scope",async()=>{
  let release;const h=harness(async url=>{if(url.includes("task_id=a"))return new Promise(done=>{release=done;});if(url.startsWith("/api/task?"))return detail("b");return state();});
  const old=h.ui.open({taskId:"a",project:"primary"});await h.ui.open({taskId:"b",project:"primary"});release(detail("a"));await old;
  assert.equal(h.mounted.length,1);assert.equal(h.mounted[0].goalId,"b");h.ui.destroy();
});

test("task detail conflict retains edited text and only an explicit rebase changes its expected version",async()=>{
  let version=1,fail=true;const h=harness(async(url,body)=>{if(url.startsWith("/api/task?"))return detail("a",{version});if(url.startsWith("/api/state?"))return state();if(url.startsWith("/api/tasks/update")){if(fail){version=2;throw Object.assign(Error("任务已由另一方更新"),{status:409});}return{task:task("a",{version:3,title:body.title,request:body.request})};}throw Error(url);});
  await h.ui.open({taskId:"a",project:"primary"});h.invoke("tw-panel",{panel:"details"});
  const controls={title:{value:"我的任务标题"},request:{value:"用户尚未提交的新要求"},acceptance:{value:"用户验收条件"},constraints:{value:"保留其他修改"},projects:{value:"related"}};
  const form={matches:selector=>selector===".tw-details-form",elements:{namedItem:key=>controls[key]}};h.elements.set(".tw-details-form",form);
  h.listeners.get("input")({target:{closest:()=>form}});await h.listeners.get("submit")({target:form,preventDefault(){}});
  const persisted=JSON.parse(h.saved.get("crew.task-details:primary:a"));assert.equal(persisted.request,"用户尚未提交的新要求");assert.equal(persisted.version,1);assert.match(h.elements.get(".tw-drawer").innerHTML,/版本 2/);
  h.invoke("tw-rebase");fail=false;await h.listeners.get("submit")({target:form,preventDefault(){}});
  const updates=h.calls.filter(c=>c.url.startsWith("/api/tasks/update"));assert.equal(updates[0].body.version,1);assert.equal(updates[1].body.version,2);assert.equal(updates[1].body.request,"用户尚未提交的新要求");assert.equal(h.saved.has("crew.task-details:primary:a"),false);h.elements.delete(".tw-details-form");h.ui.destroy();
});
