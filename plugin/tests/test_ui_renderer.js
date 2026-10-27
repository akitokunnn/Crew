"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create}=require("../crew/web/ui-renderer.js");
function fixture(){
  const listeners=new Map(),timers=new Map();let next=0,writes=0,replaced=false;
  const doc={activeElement:null,addEventListener:(type,fn)=>listeners.set(type,fn),removeEventListener:type=>listeners.delete(type)};
  const field=value=>({value,dataset:{preserve:"comment-draft"},selectionStart:0,selectionEnd:0,focus(){doc.activeElement=this;},setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}});
  const before=field("yi shou quan"),after=field("raw pinyin from markup"),oldScroll={className:"comments-list",scrollTop:146,scrollLeft:3},newScroll={className:"comments-list",scrollTop:0,scrollLeft:0};
  const host={dataset:{},isConnected:true,contains:node=>node===before||node===after,querySelectorAll:selector=>selector==="[data-preserve]"?[replaced?after:before]:selector.startsWith(".page")?[replaced?newScroll:oldScroll]:[]};
  Object.defineProperty(host,"innerHTML",{set(value){this.markup=value;writes++;replaced=true;}});
  doc.activeElement=before;
  const renderer=create({document:doc,setTimeout:fn=>{const id=++next;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)});
  return {renderer,host,before,after,newScroll,listeners,timers,writes:()=>writes,emit:(type,event={})=>listeners.get(type)?.(event),flush(){for(const [id,fn]of [...timers]){timers.delete(id);fn();}}};
}
test("IME queues the latest update per root, then preserves the committed value, caret and reading position",()=>{
  const f=fixture();f.emit("compositionstart",{target:f.before});f.renderer.replace(f.host,"first response");f.renderer.replace(f.host,"latest response");
  assert.equal(f.writes(),0);assert.equal(f.renderer.pending(),1);
  f.emit("compositionend");f.before.value="已授权";f.before.selectionStart=3;f.before.selectionEnd=3;f.flush();
  assert.equal(f.writes(),1);assert.equal(f.host.markup,"latest response");assert.equal(f.after.value,"已授权");assert.equal(f.after.selectionStart,3);
  assert.equal(f.newScroll.scrollTop,146);assert.equal(f.newScroll.scrollLeft,3);assert.equal(f.renderer.pending(),0);
});
test("returning to already rendered HTML cancels an older deferred update",()=>{
  const f=fixture();f.host.dataset.render="current";f.emit("compositionstart",{target:f.before});f.renderer.replace(f.host,"stale");f.renderer.replace(f.host,"current");f.emit("compositionend");f.flush();
  assert.equal(f.writes(),0);assert.equal(f.renderer.pending(),0);
});
test("detached feature roots and teardown cannot receive a deferred write",()=>{
  const f=fixture();f.emit("compositionstart",{target:f.before});f.renderer.replace(f.host,"waiting");f.host.isConnected=false;f.emit("compositionend");f.flush();assert.equal(f.writes(),0);
  f.host.isConnected=true;f.renderer.replace(f.host,"current");f.emit("compositionstart",{target:f.after});f.renderer.replace(f.host,"waiting again");f.emit("compositionend");f.renderer.destroy();f.flush();
  assert.equal(f.writes(),1);assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
});
