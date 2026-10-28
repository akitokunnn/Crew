"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"),vm=require("node:vm"),fs=require("node:fs"),path=require("node:path");
const window={};vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../crew/web/native-handoff-ui.js"),"utf8"),{window});
const ui=window.CrewNativeHandoff;
const id="11111111-1111-4111-8111-111111111111";
test("handoff always refers to the visible conversation and never offers unsupported Claude desktop continuity",()=>{
  const leader={session_id:"L",runtime:"codex",native_handoff:{native_id:id,state:"crew"}};
  assert.match(ui.markup(leader),/在 Codex 桌面继续/);
  assert.match(ui.markup(leader,{runtime:"codex",native_handoff:{native_id:"goal-thread",state:"external"}}),/回到 Crew/);
  assert.equal(ui.markup({...leader,runtime:"claude"}),"");
  assert.match(ui.markup({...leader,native_handoff:{native_id:id,state:"returning"}}),/disabled aria-busy="true"/);
  const interrupted=ui.markup({...leader,native_handoff:{native_id:id,state:"external",last_error:"上次交接中断，<原因>保留"}});
  assert.match(interrupted,/回到 Crew/);assert.doesNotMatch(interrupted,/disabled/);
  assert.match(interrupted,/上次交接中断，&lt;原因&gt;保留/);
});
test("the OS is asked to open Codex only after the backend releases and refreshes the exact scope",async()=>{
  const calls=[];
  await ui.act("handoff","L","fitness",{api:async(path,body)=>{calls.push([path,body.session_id,body.goal_id]);return {url:`codex://threads/${id}`};},refresh:async()=>calls.push("refresh"),open:url=>calls.push(url),notice:()=>{}});
  assert.deepEqual(calls,[["/api/session/native-handoff","L","fitness"],"refresh",`codex://threads/${id}`]);
});
test("a failed release does not open another app or silently switch conversation",async()=>{
  let opened=false,refreshed=false;
  await assert.rejects(ui.act("handoff","L",null,{api:async()=>{throw Error("本轮仍在执行");},refresh:async()=>{refreshed=true;},open:()=>{opened=true;},notice:()=>{}}),/本轮仍在执行/);
  assert.equal(opened,false);assert.equal(refreshed,false);
});
