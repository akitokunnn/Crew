"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
function harness(api){
  const retry={disabled:false},main={innerHTML:"",dataset:{},querySelector:()=>retry},notices=[];
  const sandbox={window:{},document:{addEventListener(){},getElementById:id=>id==="main"?main:null},location:{search:"?project=fixture"},URLSearchParams,CrewActivity:{sync(){}}};
  vm.createContext(sandbox);
  const source=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(source.replace("  setup();\n})();",`window.ui={loadState,setScope(id,parent){view="chat";readOnlyId=id;leaderId=parent;},scope:()=>({leaderId,readOnlyId}),services(values){api=values.api;toast=values.toast;waitingNotifications={generation:()=>0,sync(){}};appearance={sync(){}};usageUI={update(){}};readRoute=()=>{view="all";};render=()=>{document.getElementById("main").innerHTML="已恢复的工作区";};syncRoute=()=>{};}};\n})();`),sandbox);
  sandbox.window.ui.services({api,toast:message=>notices.push(message)});return {ui:sandbox.window.ui,main,retry,notices};
}
const state=()=>({project:{name:"fixture"},leaders:[],members:[],decisions:[],goals:[]});
test("initial state failure shows the actual escaped error and retry can recover without toast or another timer",async()=>{
  let calls=0,release;const h=harness(async()=>{if(++calls===1)throw Error("prices.json <missing>");return new Promise(resolve=>{release=resolve;});});
  await h.ui.loadState();assert(h.main.innerHTML.includes("Crew 加载失败"));assert(h.main.innerHTML.includes("prices.json &lt;missing&gt;"));assert(h.main.innerHTML.includes("每 3 秒自动重试"));assert.equal(h.notices.length,0);
  const retry=h.retry.onclick();assert.equal(h.retry.disabled,true);assert.equal(calls,2);release(state());await retry;assert.equal(h.retry.disabled,false);assert.equal(h.main.innerHTML,"已恢复的工作区");
});
test("a later state failure preserves the current workspace and reports the real error",async()=>{
  let calls=0;const h=harness(async()=>{if(++calls===1)return state();throw Error("backend unavailable");});
  await h.ui.loadState();h.main.innerHTML="当前对话和未发送草稿";await h.ui.loadState();assert.equal(h.main.innerHTML,"当前对话和未发送草稿");assert.deepEqual(h.notices,["backend unavailable"]);
});
test("an open member conversation follows its latest parent after a move or leave without changing the person",async()=>{
  let parent="leader";
  const h=harness(async()=>({...state(),members:[{session_id:"member",leader_session_id:parent}]}));
  await h.ui.loadState();h.ui.setScope("member","leader");parent="other";await h.ui.loadState();
  assert.deepEqual(JSON.parse(JSON.stringify(h.ui.scope())),{leaderId:"other",readOnlyId:"member"});
  parent=null;await h.ui.loadState();assert.deepEqual(JSON.parse(JSON.stringify(h.ui.scope())),{leaderId:null,readOnlyId:"member"});
});
