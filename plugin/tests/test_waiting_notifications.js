"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/waiting-notifications.js"), {create:visibility} = require("../crew/web/ui-visibility.js");
const person = (id = "leader", changes = {}) => ({session_id:id, status:"idle", running:true, busy:false, last_seq:1, last_reply_id:null, ...changes});
const snapshot = changes => ({leaders:[person()], members:[], decisions:[], attention:[], notification:{channels:[{state:"connected"}],error:null}, ...changes});
function setup() {
  const played = [], visible = visibility(), doc = {visibilityState:"visible",hasFocus:()=>true};
  const notifications = create({sound:{play:kind=>played.push(kind)},visibility:visible,document:doc}); notifications.sync(snapshot());
  return {played, visible, doc, notifications};
}
test("one accepted general send owns one generation; stale snapshot cannot complete it, streamed/duplicate replies stay quiet", () => {
  const f = setup(), requestedGeneration = f.notifications.generation(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:1});
  assert.equal(f.notifications.generation(),1); const reply = snapshot({leaders:[person("leader",{last_seq:2,last_reply_id:"reply"})]});
  f.notifications.sync(reply,{requestedGeneration}); assert.deepEqual(f.played,[]);
  f.notifications.sync(reply,{requestedGeneration:1}); assert.deepEqual(f.played,["complete"]);
  f.notifications.sync(reply); f.notifications.sync(snapshot({leaders:[person("leader",{last_seq:3,last_reply_id:"reply"})]})); assert.deepEqual(f.played,["complete"]);
  f.notifications.acceptedSend({sessionId:"leader",afterSeq:3,goalId:42}); assert.equal(f.notifications.generation(),1);
});
test("same-sequence, busy and unknown statuses cannot complete a send; accepted stop prevents completion", () => {
  const f = setup(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:2});
  for (const changes of [{last_seq:2},{last_seq:3,busy:true},{last_seq:3,status:null},{last_seq:3,status:"unknown"}]) f.notifications.sync(snapshot({leaders:[person("leader",changes)]}));
  assert.deepEqual(f.played,[]); f.notifications.acceptedStop("leader"); f.notifications.sync(snapshot({leaders:[person("leader",{last_seq:4})]})); assert.deepEqual(f.played,[]);
});
test("leader and member completions coalesce; fresh refresh consumes completion without emitting later", () => {
  const f = setup(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:1}); f.notifications.acceptedSend({sessionId:"member",afterSeq:1});
  f.notifications.sync(snapshot({leaders:[person("leader",{last_seq:2})],members:[person("member",{last_seq:2})]})); assert.deepEqual(f.played,["complete"]);
  f.notifications.acceptedSend({sessionId:"leader",afterSeq:2}); const done = snapshot({leaders:[person("leader",{last_seq:3})]});
  f.notifications.sync(done,{fresh:true}); f.notifications.sync(done); assert.deepEqual(f.played,["complete"]);
});
test("decision/error cues keep original precedence, clear affected waiters, and channel failures emit once", () => {
  const f = setup(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:1});
  const decision = snapshot({decisions:[{id:1,held:false,leader_session_id:"leader"}],attention:[{id:2,kind:"session_error",session_id:"leader"}],leaders:[person("leader",{last_reply_id:"r",last_seq:2})]});
  f.notifications.sync(decision); assert.deepEqual(f.played,["decision","error"]); f.notifications.sync(decision); assert.deepEqual(f.played,["decision","error"]);
  const failure = snapshot({notification:{channel:{state:"rejected"},error:null}}); f.notifications.sync(failure); f.notifications.sync(failure); assert.deepEqual(f.played,["decision","error","error"]);
});
test("reply reading uses shared obstruction, document focus, visibility and active general leader only", () => {
  const f = setup(), reading = {view:"chat",leaderId:"leader",readOnlyId:null}; let blocked = false;
  f.visible.register("menu",()=>blocked); let seq = 0;
  const next = () => f.notifications.sync(snapshot({leaders:[person("leader",{last_reply_id:`r${++seq}`})]}),{reading});
  next(); assert.deepEqual(f.played,[]); blocked = true; next(); blocked = false;
  f.doc.visibilityState = "hidden"; next(); f.doc.visibilityState = "visible";
  f.doc.hasFocus = () => false; next(); f.doc.hasFocus = () => true;
  reading.readOnlyId = "member"; next(); reading.readOnlyId = null; reading.view = "all"; next(); assert.deepEqual(f.played,Array(5).fill("reply"));
});
test("removed/offline sessions do not leave future completion claims, and destroy ignores late sends and snapshots", () => {
  const f = setup(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:1}); f.notifications.sync(snapshot({leaders:[]}));
  f.notifications.sync(snapshot({leaders:[person("leader",{last_seq:2})]})); assert.deepEqual(f.played,[]);
  f.notifications.acceptedSend({sessionId:"leader",afterSeq:2}); f.notifications.sync(snapshot({leaders:[person("leader",{running:false,last_seq:3})]})); assert.deepEqual(f.played,[]);
  f.notifications.destroy(); const generation = f.notifications.generation(); f.notifications.acceptedSend({sessionId:"leader",afterSeq:3}); f.notifications.sync(snapshot({leaders:[person("leader",{last_reply_id:"late",last_seq:4})]}));
  assert.equal(f.notifications.generation(),generation); assert.deepEqual(f.played,[]);
});
