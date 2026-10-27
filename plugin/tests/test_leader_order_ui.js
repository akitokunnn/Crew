"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create}=require("../crew/web/leader-order.js");
function fixture(overrides={}){
  const rows=[{session_id:"a",name:"A",priority:1},{session_id:"b",name:"B",priority:2},{session_id:"c",name:"C",priority:3},{session_id:"d",name:"D",priority:null},{session_id:"e",name:"E",priority:null}],handlers=new Map(),calls=[],notices=[];let refreshes=0;
  const sidebar={classList:{remove(){}},querySelectorAll:()=>[]},ports={document:{addEventListener:(type,handler)=>handlers.set(type,handler),removeEventListener:type=>handlers.delete(type)},sidebar,
    leaders:()=>rows,project:()=>"fixture",savePriority:async body=>calls.push({type:"priority",body}),saveOrder:async body=>calls.push({type:"order",body}),refresh:async()=>refreshes++,notify:(...args)=>notices.push(args),changed(){},...overrides};
  const module=create(ports);return{module,rows,ports,calls,notices,handlers,refreshes:()=>refreshes};
}
test("priority insertion keeps three stable IDs and undo captures the original ordering",async()=>{
  const f=fixture();await f.module.setPriority("d",2);assert.deepEqual(f.calls[0],{type:"priority",body:{project:"fixture",ranks:["a","d","b"]}});
  assert.match(f.notices[0][0],/C 已移出优先/);await f.notices[0][1]();assert.deepEqual(f.calls[1].body.ranks,["a","b","c"]);
});
test("normal reordering preserves the pinned slots and moving beyond either boundary is a no-op",async()=>{
  const f=fixture();await f.module.move("e",-1);assert.deepEqual(f.calls[0],{type:"order",body:{project:"fixture",order:["a","b","c","e","d"]}});
  await f.module.move("d",-1);assert.equal(f.calls.length,1);assert.deepEqual(f.module.ids(),["a","b","c"]);
});
test("the same pending ordering transaction gates menu actions and retains its real failure",async()=>{
  let reject;const f=fixture({savePriority:()=>new Promise((_resolve,failure)=>reject=failure)});
  const pending=f.module.setPriority("d",1);assert.equal(f.module.isInteracting(),true);await f.module.move("a",1);
  reject(Error("priority storage failed"));await assert.rejects(pending,/priority storage failed/);assert.equal(f.module.isSaving(),false);assert.equal(f.refreshes(),1);
});
test("destroy releases all drag listeners and accepted late ordering cannot refresh a detached view",async()=>{
  let release;const f=fixture({saveOrder:()=>new Promise(resolve=>release=resolve)}),pending=f.module.saveNormalOrder(["e","d"]);
  f.module.destroy();release({ok:true});await pending;assert.equal(f.handlers.size,0);assert.equal(f.refreshes(),0);
});
test("a drag drop uses the same normal-order transaction and clears pointer feedback before refresh",async()=>{
  const make=()=>{const values=new Set();return{classList:{add:(...names)=>names.forEach(name=>values.add(name)),remove:(...names)=>names.forEach(name=>values.delete(name)),contains:name=>values.has(name)}};};
  const zone={...make(),dataset:{leaderZone:"normal"}},source={...make(),dataset:{id:"e"},closest:()=>zone},target={...make(),dataset:{id:"d"},offsetHeight:20,getBoundingClientRect:()=>({top:20})};
  zone.querySelectorAll=()=>[target,source];target.closest=selector=>selector.includes("data-leader-zone")?zone:target;
  const sidebar={...make(),querySelectorAll:()=>[zone,source,target]},f=fixture({sidebar,requestAnimationFrame:fn=>fn()}),transfer={setData(){}};
  f.handlers.get("dragstart")({target:{closest:()=>source},dataTransfer:transfer});assert.equal(f.module.isInteracting(),true);
  f.handlers.get("dragover")({target,dataTransfer:transfer,clientY:21,preventDefault(){}});
  f.handlers.get("drop")({target,preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.calls[0].body.order,["a","b","c","e","d"]);assert.equal(source.classList.contains("dragging"),false);assert.equal(f.module.isInteracting(),false);
});
