"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{fixture,person}=require("./people_fixture.cjs");
const {create:createVisibility}=require("../crew/web/ui-visibility.js");
const state=()=>({leaders:[person("leader"),person("other")],members:[person("member",{leader_session_id:"leader",group:"leader"}),person("second",{runtime:"codex",leader_session_id:"other",group:"other"})]});
test("person menus preserve role, carrier and group capabilities without sharing application state",()=>{
  const value=state(),f=fixture(()=>value),leader=f.module.sessionMenu(value.leaders[0]),member=f.module.sessionMenu(value.members[0]);
  assert(leader.includes('data-act="close-group"'));assert(leader.includes('data-act="compact-department"'));assert(!member.includes('data-act="compact-department"'));
  assert.match(member,/data-act="move-to" data-id="member"[^>]*data-group="other"/);assert(!member.includes('data-group="leader"'));
  assert.match(f.module.editorHandoffReason(value.members[1]),/暂不支持/);assert.equal(f.module.editorHandoffReason(value.members[0]),"");
  assert(leader.includes('data-act="compact"'));assert(member.includes('data-act="compact"'));
  for(const runtime of ["claude","codex"])for(const role of ["leader","member"]){
    const item=role==="leader"?value.leaders[0]:value.members[0];item.runtime=runtime;
    const menu=f.module.sessionMenu(item),editor=menu.match(/<button[^>]*data-act="open-vscode"[^>]*>/)?.[0];
    assert(editor);assert.equal(editor.includes("disabled"),runtime==="codex");
    assert(menu.includes(`data-act="${role==="leader"?"leader":"member-history"}"`));
  }
});
test("renaming owns the exact draft, real error and submission guard and retries only when asked",async()=>{
  const value=state(),f=fixture(()=>value);let release,fail=true;
  f.ports.api=async(path,body)=>{f.calls.push({path,body});if(fail)throw Error("native rename failed");return new Promise(resolve=>release=resolve);};
  f.module.openRename("member");f.module.setRename("  新名字  ");await f.module.saveRename();
  assert.equal(f.module.snapshot().dialog.draft,"  新名字  ");assert.equal(f.module.snapshot().dialog.error,"native rename failed");assert.equal(f.module.snapshot().dialog.saving,false);
  fail=false;const accepted=f.module.saveRename();await f.module.saveRename();assert.equal(f.calls.length,2);assert.equal(f.module.closeDialog(),false,"saving form stays owned until its receipt");
  release({native_warnings:["native label retained"]});await accepted;assert.equal(f.module.snapshot().dialog,null);assert.equal(f.calls[1].body.name,"新名字");assert.match(f.notices[0][0],/名字已更新.*native label retained/);
});
test("an accepted old rename cannot close or focus a new person's form after navigation",async()=>{
  const value=state(),f=fixture(()=>value);let release;f.ports.api=()=>new Promise(resolve=>release=resolve);
  f.module.openRename("member");f.module.setRename("新名字");const pending=f.module.saveRename();f.module.close();f.module.openRename("second");const restores=f.restores();
  release({});await pending;assert.equal(f.module.snapshot().dialog.id,"second");assert.equal(f.restores(),restores);
});
test("a late editor-open receipt cannot steal a newer card or reopen a closed scope",async()=>{
  const value=state(),f=fixture(()=>value);let release;f.ports.api=()=>new Promise(resolve=>release=resolve);
  f.module.openCard("member");const opening=f.module.openEditor("member");f.module.openCard("second");release({needs_window_ready:true,cwd:"C:/fixture",needs_confirmation:true});await opening;
  assert.equal(f.module.cardId(),"second");assert.equal(f.module.snapshot().handoff,null);assert.equal(f.module.snapshot().dialog,null);
  await assert.rejects(()=>f.module.openEditor("second"),/暂不支持/);
});
test("a card handoff continues once with its captured native confirmation and clears after acceptance",async()=>{
  const value=state(),f=fixture(()=>value);let release;f.ports.api=async(path,body)=>{f.calls.push({path,body});return path==="/api/open"?{needs_window_ready:true,cwd:"C:/fixture",needs_confirmation:true}:new Promise(resolve=>release=resolve);};
  f.module.openCard("member");await f.module.openEditor("member");assert.equal(f.module.snapshot().handoff.session_id,"member");
  const continuation=f.module.continueEditor("member");await f.module.continueEditor("member");assert.equal(f.calls.length,2);release({ok:true});await continuation;
  assert.deepEqual(f.calls[1],{path:"/api/open/continue",body:{session_id:"member",confirm:true}});assert.equal(f.module.snapshot().handoff,null);
});
test("roster changes capture stable source identity and archive undo uses the actual receipt",async()=>{
  const value=state(),f=fixture(()=>value);await f.module.moveTo("member","other");assert.deepEqual(f.calls[0].body,{session_id:"member",name:"member",from:"leader",to:"other"});
  await f.module.changeSession("member","/api/session/archive","已归档",true);const undo=f.notices.at(-1)[1];await undo();
  assert.deepEqual(f.calls.at(-1),{path:"/api/session/undo",body:{session_id:"member",before:{fixture:true}}});
});
test("stop confirmation is explicit, replacing or destroying it resolves false and releases obstruction",async()=>{
  const value=state(),visibility=createVisibility(),f=fixture(()=>value,{visibility,relatedGoals:()=>[{title:"相关任务"}]});
  const cancelled=f.module.confirmStop(value.leaders[0]);assert.equal(visibility.blocked(),true);f.module.closeDialog();assert.equal(await cancelled,false);
  const confirmed=f.module.confirmStop(value.leaders[0]);f.module.acceptStop();assert.equal(await confirmed,true);assert.equal(f.calls.length,0);
  const destroyed=f.module.confirmStop(value.leaders[0]);const restores=f.restores();f.module.destroy();assert.equal(await destroyed,false);assert.equal(f.restores(),restores);assert.equal(visibility.blocked(),false);
  assert([...f.handlers.values()].every(handlers=>handlers.size===0));
});
