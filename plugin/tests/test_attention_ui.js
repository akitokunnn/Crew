"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict");
const {createAttention}=require("./attention_fixture.cjs"),{create:createActions}=require("../crew/web/ui-actions.js");
function fixture(overrides={}) {
  const state={leaders:[{session_id:"lead",name:"负责人",trust:{enabled:false}}],members:[{session_id:"member",leader_session_id:"lead"}],
    todos:[{id:1,session_id:"member",leader_session_id:"lead",title:"确认录音环境",status:"open",version:2,steps:["保留录音"],verification:"有结果",created_at:"2026-10-27T00:00:00Z"},
      {id:2,session_id:"lead",leader_session_id:"lead",goal_id:"task-a",title:"本任务待办",version:1},
      {id:3,session_id:"other",leader_session_id:"other",title:"别组待办",version:1}],
    decisions:[{id:10,session_id:"lead",leader_session_id:"lead",asker:"负责人",question:"怎么复测",options:["原始选项A","原始选项B"],presentation_stage:"ready"},
      {id:11,session_id:"member",leader_session_id:"lead",goal_id:"task-a",held:true,question:"挂起的问题",options:[]}]};
  const events=new Map(),calls=[],notices=[],navigation=[];let changes=0,refreshes=0,focused=0;
  const doc={addEventListener(type,fn){if(!events.has(type))events.set(type,new Set());events.get(type).add(fn);},
    removeEventListener(type,fn){events.get(type)?.delete(fn);},getElementById:()=>({focus(){focused++;}})};
  const actions=createActions({onError:error=>notices.push(error.message)});
  const ports={actions,api:async(path,data)=>{calls.push({path,data});return{before:{status:"open"},version:3,routed_to:null};},
    refresh:async()=>refreshes++,notify:(...args)=>notices.push(args),changed:()=>changes++,navigate:(...args)=>navigation.push(args),...overrides};
  const feature=createAttention(()=>state,doc,ports);
  return {feature,state,calls,notices,navigation,actions,events,doc,ports,changes:()=>changes,refreshes:()=>refreshes,focused:()=>focused,
    emit(type,event){for(const handler of events.get(type)||[])handler(event);},
    click(act,id,extra={}){return actions.dispatch(act,{element:{dataset:{act,id:String(id),...extra}}});}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test("one attention projection scopes general, task and member views without leaking another group's work",()=>{
  const f=fixture(),general=f.feature.forChat({ownerId:"lead"}),task=f.feature.forChat({ownerId:"lead",goalId:"task-a"});
  assert.equal(general.count,3);assert.equal(general.totalWithHeld,4);assert(!general.markup.includes("别组待办"));
  assert.equal(task.count,1);assert.equal(task.held.length,1);assert(!task.markup.includes("确认录音环境"));
  const member=f.feature.forChat({ownerId:"lead",goalId:"task-a",member:true});
  assert.equal(member.count,0);assert.equal(member.markup,"");
});

test("feature actions own expanded cards, resolution drafts and composition-time input without requesting a render per keystroke",()=>{
  const f=fixture();f.click("todo-toggle",1);assert.equal(f.feature.todoState(1).expanded,true);
  f.click("todo-action",1,{status:"cancelled"});assert.equal(f.focused(),1);
  const changes=f.changes(),form={dataset:{todoForm:"1"}},target={name:"note",value:"yi shou quan",closest:()=>form};
  f.emit("input",{target,isComposing:true});assert.equal(f.feature.todoState(1).draft.note,"yi shou quan");assert.equal(f.changes(),changes);
  target.value="已授权，保留完整原话";f.emit("input",{target});
  f.click("todo-action",1,{status:"done"});assert.equal(f.feature.todoState(1).draft.note,"已授权，保留完整原话");
  f.click("todo-action-close",1);assert.equal(f.feature.todoState(1).draft,null);
});

test("duplicate submits issue one mutation and retain the exact note and error until an explicit retry",async()=>{
  let reject;const calls=[];
  const f=fixture({api:async(path,data)=>{calls.push({path,data});return new Promise((_resolve,rejectRequest)=>reject=rejectRequest);}});
  f.feature.startTodoAction(1,"done");f.feature.setTodoNote(1,"  录音已就绪  ");
  const first=f.feature.resolveTodo(1);await f.feature.resolveTodo(1);assert.equal(calls.length,1);assert.equal(f.feature.todoState(1).saving,true);
  reject(Error("真实保存失败"));await first;
  assert.equal(f.feature.todoState(1).draft.note,"  录音已就绪  ");assert.equal(f.feature.todoState(1).draft.error,"真实保存失败");
  assert.equal(f.feature.todoState(1).saving,false);assert.equal(calls.length,1);
  assert.equal(calls[0].data.note,"录音已就绪");assert.equal(calls[0].data.expected_version,2);
});

test("a refreshed todo version preserves the user's cancellation reason and requires another deliberate submit",async()=>{
  const calls=[];
  const f=fixture({api:async(path,data)=>{
    calls.push({path,data});
    throw Error("version conflict");
  }});
  f.feature.startTodoAction(1,"cancelled");f.feature.setTodoNote(1,"已经换方案");f.state.todos[0].version=3;
  await f.feature.resolveTodo(1);const view=f.feature.todoState(1);
  assert.deepEqual(calls,[{path:"/api/todo/resolve",data:{id:1,status:"cancelled",note:"已经换方案",expected_version:2}}]);
  assert.equal(view.draft.note,"已经换方案");assert.equal(view.draft.version,3);assert.match(view.draft.error,/请确认后再提交/);
  assert.equal(view.expanded,true);
});

test("resolution form uses the current input and undo uses the exact successful server receipt",async()=>{
  const f=fixture();f.feature.startTodoAction(1,"done");let prevented=false;
  f.emit("submit",{target:{dataset:{todoForm:"1"},elements:{note:{value:"完成后可以继续"}}},preventDefault(){prevented=true;}});
  await settle();assert.equal(prevented,true);assert.equal(f.feature.todoState(1).draft,null);
  assert.deepEqual(f.calls[0],{path:"/api/todo/resolve",data:{id:1,status:"done",note:"完成后可以继续",expected_version:2}});
  const undo=f.notices[0][1];assert.equal(typeof undo,"function");await undo();
  assert.deepEqual(f.calls[1],{path:"/api/todo/undo",data:{id:1,before:{status:"open"},version:3}});
});

test("decision options send the original option text and free answers retain the decision identity",async()=>{
  const f=fixture();f.state.decisions[0].brief={title:"需要拍板",options:[{label:"短标题A"},{label:"短标题B"}]};
  f.click("answer-option",10,{option:"1"});await settle();
  assert.deepEqual(f.calls[0],{path:"/api/answer",data:{id:10,text:"原始选项B"}});
  f.emit("submit",{target:{dataset:{answerForm:"10"},elements:{answer:{value:"  我选另外一个方案  "}}},preventDefault(){}});await settle();
  assert.deepEqual(f.calls[1],{path:"/api/answer",data:{id:10,text:"我选另外一个方案"}});
});

test("discussion follows the returned conversation scope while holding remains a separate feature command",async()=>{
  const calls=[],f=fixture({api:async(path,data)=>{calls.push({path,data});return{conversation_goal_id:null};}});
  f.state.decisions[0].goal_id="task-a";
  f.click("decision-discuss",10);await settle();assert.deepEqual(f.navigation,[["lead",null]]);
  f.click("decision-hold",10);await settle();assert.deepEqual(calls[1],{path:"/api/decision/hold",data:{id:10}});
});

test("detail expansion is owned by the feature and teardown removes commands and DOM listeners",()=>{
  const f=fixture();f.emit("toggle",{target:{dataset:{decisionContext:"10"},open:true}});
  assert(f.feature.decisionCard(f.state.decisions[0]).includes('data-decision-context="10" open'));
  f.feature.destroy();assert.equal(f.actions.dispatch("todo-toggle",{element:{dataset:{id:"1"}}}),false);
  assert([...f.events.values()].every(handlers=>handlers.size===0));
});

test("a successful receipt after feature teardown is not repeated and cannot redraw or notify the departed view",async()=>{
  let release;const f=fixture({api:async()=>new Promise(resolve=>release=resolve)});
  f.feature.startTodoAction(1,"done");const work=f.feature.resolveTodo(1),changes=f.changes();
  f.feature.destroy();release({before:{status:"open"},version:3});await work;
  assert.equal(f.feature.todoState(1).draft,null);assert.equal(f.changes(),changes);
  assert.equal(f.refreshes(),0);assert.equal(f.notices.length,0);
});

for (const stage of ["request", "refresh"]) test(`discussion cannot navigate after teardown during ${stage}`, async()=>{
  let release, requests=0;
  const f=fixture({
    api:async()=>{requests++;return stage==="request"?new Promise(resolve=>release=resolve):{conversation_goal_id:"task-a"};},
    refresh:async()=>stage==="refresh"?new Promise(resolve=>release=resolve):undefined
  });
  f.click("decision-discuss",10);await settle();assert.equal(typeof release,"function");
  f.feature.destroy();release({conversation_goal_id:"task-a"});await settle();
  assert.equal(requests,1);assert.deepEqual(f.navigation,[]);
});

for (const action of ["decision-hold", "decision-discuss"]) test(`${action} cannot present a late failure after teardown`,async()=>{
  let reject,requests=0;
  const f=fixture({api:async()=>{requests++;return new Promise((_resolve,rejectRequest)=>reject=rejectRequest);}});
  f.click(action,10);await settle();f.feature.destroy();
  reject(Error("late transport failure"));await settle();
  assert.equal(requests,1);assert.deepEqual(f.notices,[]);assert.deepEqual(f.navigation,[]);
});
