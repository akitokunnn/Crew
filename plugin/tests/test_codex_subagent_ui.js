"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const escape=value=>String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const sandbox={window:{},document:{addEventListener(){}},location:{search:""},URLSearchParams,CrewChat:{renderInline:escape},CrewContext:{ring:()=>""},CrewActivity:{markup:()=>""}};
sandbox.createRuntimeCatalog=require("../crew/web/runtime-catalog.js").create;
sandbox.createComments=require("./comments_fixture.cjs").createComments;
vm.createContext(sandbox);
const source=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
vm.runInContext(source.replace("  setup();\n})();","  window.ui={codexMarkup,leaderNode,memberNode,setState(value){state=value;runtimeCatalog=createRuntimeCatalog({snapshot:()=>({catalog:state.runtimes,choices:state.ai})});commentsFeature=createComments(()=>state,document);}};\n})();"),sandbox);
const ui=sandbox.window.ui;
const task=extra=>({id:"child",task_name:"检查界面 <safe>",source:"native_subagent",status:"running",started_at:new Date().toISOString(),ended_at:null,...extra});
test("native task status renders spinner, check only on success, and separate failure/pause/closed/unknown labels",()=>{
  for(const [status,label] of Object.entries({running:"运行中",succeeded:"已完成",failed:"失败",interrupted:"已暂停",closed:"已关闭",unknown:"状态未确认"})){
    const html=ui.codexMarkup(task({status,detail:'fixture <error> "quoted"'}));
    assert(html.includes('data-codex-source="native_subagent"'));assert(html.includes(`aria-label="${label}"`));
    assert.equal(html.includes('class="spin"'),status==="running");assert.equal(html.includes('xs ok'),status==="succeeded");
    assert(!html.includes("undefined"));assert(!html.includes("<safe>"));assert(!html.includes("<error>"));assert(html.includes("&lt;error&gt;"));
  }
});
test("leader and member nodes both include native child task rows while preserving ordinary dispatch rows",()=>{
  const person={session_id:"worker",name:"负责人",runtime:"codex",status:"idle",priority:null,pending:0,goal:"隔离验收",task:"核对流程",where:"bg",codex:[task()],codex_older:0,context:null,activity:null,applied:null,model:null,effort:null};
  ui.setState({leaders:[],members:[],runtimes:[],ai:{}});
  assert(ui.leaderNode(person,true).includes("检查界面 &lt;safe&gt;"));assert(ui.memberNode(person).includes("检查界面 &lt;safe&gt;"));
  const dispatch=ui.codexMarkup(task({source:"dispatch",status:"succeeded",ended_at:new Date().toISOString()}));
  assert(dispatch.includes('data-codex-source="dispatch"'));assert(dispatch.includes("xs ok"));
});
test("both carrier labels use the same task rows and distinguish their real source",()=>{
  for(const [runtime,label] of [["claude","Claude 子代理"],["codex","Codex 子代理"]]){
    const html=ui.codexMarkup(task({runtime}));assert(html.includes(label));assert(!html.includes(runtime==="claude"?"Codex 子代理":"Claude 子代理"));
  }
});
test("native names are single line plain text even for legacy prompt names; full details remain available",()=>{
  const renderCalls=[];
  sandbox.CrewChat.renderInline=value=>{renderCalls.push(value);return escape(value);};
  for(const runtime of ["codex","claude"]){
    const prompt='你在 Crew 内工作。\n用户的原始要求 <safe>\n只处理当前目标';
    const html=ui.codexMarkup(task({runtime,task_name:prompt,task_description:prompt}));
    assert(!renderCalls.length,"native names must not become Markdown blocks or links");
    assert(html.includes('class="n" title="'+escape(prompt)+'">你在 Crew 内工作。 用户的原始要求 &lt;safe&gt; 只处理当前目标</span>'));
    assert(!html.includes('<safe>'));
    const longName='🧪'.repeat(100),longHtml=ui.codexMarkup(task({runtime,task_name:longName}));
    assert(longHtml.includes('title="'+longName+'">'+'🧪'.repeat(47)+'…</span>'));
  }
  sandbox.CrewChat.renderInline=escape;
});
