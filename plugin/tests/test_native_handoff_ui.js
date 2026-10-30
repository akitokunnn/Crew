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
test("the controlled desktop opens through the backend without routing into an unrelated OS desktop",async()=>{
  const calls=[];
  await ui.act("handoff","L","fitness",{api:async(path,body)=>{calls.push([path,body.session_id,body.goal_id]);return {desktop_opened:true};},refresh:async()=>calls.push("refresh"),open:()=>assert.fail("must not use the system URL handler"),notice:()=>{}});
  assert.deepEqual(calls,[["/api/session/native-handoff","L","fitness"],"refresh"]);
});
test("an old backend without a controlled desktop cannot silently launch the ordinary desktop",async()=>{
  await assert.rejects(ui.act("handoff","L",null,{api:async()=>({url:`codex://threads/${id}`}),refresh:async()=>{},open:()=>assert.fail("unsafe OS routing"),notice:()=>assert.fail("unconfirmed desktop")}),/尚未确认就绪/);
});
test("a failed release does not open another app or silently switch conversation",async()=>{
  let opened=false,refreshed=false;
  await assert.rejects(ui.act("handoff","L",null,{api:async()=>{throw Error("本轮仍在执行");},refresh:async()=>{refreshed=true;},open:()=>{opened=true;},notice:()=>{}}),/本轮仍在执行/);
  assert.equal(opened,false);assert.equal(refreshed,true);
});
test("a failed return refreshes the saved native reason instead of keeping a previous writer error",async()=>{
  const person={session_id:"L",runtime:"codex",native_handoff:{native_id:id,state:"external",last_error:"previous active writer"}};
  let rendered=ui.markup(person),posts=0,refreshes=0;
  await assert.rejects(ui.act("return","L",null,{
    api:async()=>{posts++;throw Error("thread/resume: session is archived");},
    refresh:async()=>{refreshes++;person.native_handoff.last_error="thread/resume: session is archived";rendered=ui.markup(person);},
    open:()=>assert.fail("failed return cannot open another app"),notice:()=>assert.fail("failed return cannot claim success")
  }),/session is archived/);
  assert.match(rendered,/session is archived/);assert.doesNotMatch(rendered,/previous active writer/);
  assert.equal(posts,1);assert.equal(refreshes,1);
});
test("a failed refresh never masks the original operation error or retries the POST",async()=>{
  for(const action of ["return","handoff"]){
    const original=Error("native operation refused");let posts=0,refreshes=0;
    await assert.rejects(ui.act(action,"L",null,{
      api:async()=>{posts++;throw original;},refresh:async()=>{refreshes++;throw Error("state unavailable");},
      open:()=>assert.fail("failed operation cannot open"),notice:()=>assert.fail("failed operation cannot claim success")
    }),error=>error===original);
    assert.equal(posts,1);assert.equal(refreshes,1);
  }
});
test("a successful operation survives refresh failure without repeating it",async()=>{
  for(const action of ["return","handoff"]){
    const result={url:`codex://threads/${id}`,ok:true,desktop_opened:true};let posts=0,refreshes=0;const opened=[],notices=[];
    assert.equal(await ui.act(action,"L",null,{
      api:async()=>{posts++;return result;},refresh:async()=>{refreshes++;throw Error("state unavailable");},
      open:url=>opened.push(url),notice:text=>notices.push(text)
    }),result);
    assert.equal(posts,1);assert.equal(refreshes,1);
    assert.deepEqual(opened,[]);
    assert.match(notices.join(" "),/state unavailable/);
    assert.match(notices.join(" "),action==="handoff"?/已交给/:/已接回/);
  }
});
