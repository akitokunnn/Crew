"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create}=require("../crew/web/runtime-models.js"),{create:createCatalog}=require("../crew/web/runtime-catalog.js");
function fixture(discover=async()=>({runtimes:[]})){
  const person={session_id:"person",runtime:"codex",model:null,effort:null,where:"offline",context:null},element={dataset:{id:"person"}};
  const snapshot={catalog:[{id:"codex",name:"Codex",available:true,models:[],efforts:[]}]},catalog=createCatalog({snapshot:()=>snapshot,discover});let anchor=null,shows=0,hides=0;const writes=[],notices=[];
  const menu={show(target,html){anchor=target;shows++;this.html=html;},hide(){anchor=null;hides++;},anchor:()=>anchor,isCurrent:target=>target!==null&&anchor===target};
  const ports={catalog,findSession:()=>person,menu,icon:()=>"",runtimeBadge:()=>"Codex",save:async(id,changes)=>writes.push({id,changes}),refresh:async()=>{},notify:text=>notices.push(text)};
  return{module:create(ports),ports,catalog,menu,element,person,writes,notices,shows:()=>shows,hides:()=>hides};
}
test("a late discovery result updates an open menu but cannot reopen a menu the user closed",async()=>{
  let release;const f=fixture(()=>new Promise(resolve=>release=resolve));const pending=f.module.open(f.element);await Promise.resolve();assert.equal(f.shows(),1);f.menu.hide();release({runtimes:[{id:"codex",available:true,models:[{id:"new",name:"New"}],efforts:[]}]});await pending;assert.equal(f.shows(),1);
  const reopened=f.module.open(f.element);await Promise.resolve();release({runtimes:[{id:"codex",available:true,models:[{id:"new",name:"New"}],efforts:[]}]});await reopened;assert.equal(f.shows(),3);assert(f.menu.html.includes('data-model="new"'));
});
test("a setting request uses the stable person's identity and cannot hide a newer unrelated menu",async()=>{
  const f=fixture();let release;f.ports.save=async(id,changes)=>{f.writes.push({id,changes});return new Promise(resolve=>release=resolve);};f.menu.show(f.element,"old");const pending=f.module.change("person","model",null);const other={dataset:{id:"other"}};f.menu.show(other,"new menu");release({});await pending;
  assert.equal(f.menu.anchor(),other);assert.equal(f.hides(),0);assert.deepEqual(f.writes,[{id:"person",changes:{model:null}}]);
});
test("different rendered labels do not bypass carrier-specific model and effort validation",async()=>{
  const f=fixture();await assert.rejects(f.module.change("person","model","claude-model"),/模型列表已更新/);await assert.rejects(f.module.change("person","effort","max"),/不支持/);assert.equal(f.writes.length,0);
});
