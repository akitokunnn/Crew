"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{createComments,portal:makePortal}=require("./comments_fixture.cjs"),{create:createVisibility}=require("../crew/web/ui-visibility.js");
function fixture(overrides={}){
  const people={leaders:[{session_id:"lead",name:"登录"}],members:[{session_id:"member",name:"连接助手",leader_session_id:"lead"}]},listeners=new Map(),nodes=new Map(),calls=[],notices=[];let renders=0,restores=0,focuses=0;
  const textarea={value:"",focus(){focuses++;}},form={dataset:{sessionId:"lead"},elements:{text:textarea}};nodes.set("comment-text",textarea);nodes.set("comments-form",form);
  const doc={getElementById:id=>nodes.get(id),addEventListener:(type,fn)=>listeners.set(type,fn),removeEventListener:type=>listeners.delete(type)},portal=makePortal(doc),visibility=createVisibility();
  const ports={portal,visibility,focus:{capture:()=>"original opener",restore:()=>restores++},replace(host,html){renders++;host.innerHTML=html;const id=html.match(/data-session-id="([^"]+)"/)?.[1];if(id)form.dataset.sessionId=id;},
    api:async(path,body)=>{calls.push({path,body});return body?{id:1}:{comments:[]};},notify:text=>notices.push(text),...overrides};
  const module=createComments(()=>people,doc,ports);
  return{module,people,portal,visibility,textarea,form,listeners,calls,notices,ports,renders:()=>renders,restores:()=>restores,focuses:()=>focuses};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test("group and member entry points retain exact ownership and escape text through the rendering port",()=>{
  const f=fixture();assert(f.module.button(f.people.leaders[0],true).includes('data-id="lead"'));assert(f.module.button(f.people.members[0]).includes('data-id="member"'));
  const html=f.module.markup(f.people.members[0],{comments:[{text:"<script>comment</script>"}],draft:"补充说明"});assert(html.includes("登录 组 · 评论会转告 leader"));assert(html.includes("&lt;script&gt;comment&lt;/script&gt;"));assert(!html.includes("<script>"));
});
test("failed writes preserve the exact draft and reason through a successful read, then retry only explicitly",async()=>{
  let fail=true;const f=fixture({api:async(path,body)=>{f.calls.push({path,body});if(body&&fail)throw Error("暂时无法发送");return body?{id:1}:{comments:[{text:"已保存"}]};}});
  await f.module.post("lead","  保留这段反馈  ");await f.module.load("lead");assert.equal(f.module.snapshot("lead").draft,"  保留这段反馈  ");assert.equal(f.module.snapshot("lead").error,"暂时无法发送");assert.equal(f.calls.filter(call=>call.body).length,1);
  fail=false;await f.module.post("lead","  保留这段反馈  ");assert.equal(f.module.snapshot("lead").draft,"");assert.equal(f.module.snapshot("lead").error,null);assert.equal(f.calls[2].body.text,"保留这段反馈");
});
test("late reads populate their own cache and cannot replace or focus another person's open comments",async()=>{
  const releases=new Map(),f=fixture({api:path=>new Promise(resolve=>releases.set(path,resolve))});f.module.open("lead");f.module.open("member");const renders=f.renders(),focuses=f.focuses();
  releases.get("/api/comments?session_id=lead")({comments:[{text:"A comments"}]});await settle();assert.equal(f.renders(),renders);assert.equal(f.focuses(),focuses);assert.equal(f.form.dataset.sessionId,"member");assert.equal(f.module.snapshot("lead").comments[0].text,"A comments");
  releases.get("/api/comments?session_id=member")({comments:[{text:"B comments"}]});await settle();assert(f.portal.innerHTML.includes("B comments"));assert(!f.portal.innerHTML.includes("A comments"));
});
test("a closed or different portal cannot lose its draft or focus when an older write succeeds",async()=>{
  let release;const f=fixture({api:async(_path,body)=>body?new Promise(resolve=>release=resolve):{comments:[]}});f.module.open("lead");await settle();const saving=f.module.post("lead","A draft");f.module.close();f.module.open("member");f.module.setDraft("member","B draft");f.textarea.value="B draft";await settle();const focuses=f.focuses();
  release({id:1});await saving;assert.equal(f.module.snapshot("lead").draft,"");assert.equal(f.module.snapshot("member").draft,"B draft");assert.equal(f.textarea.value,"B draft");assert.equal(f.focuses(),focuses);assert.equal(f.notices.length,0);
});
test("duplicate writes are suppressed and closing by Escape or the mask unregisters the reading obstruction",async()=>{
  let release;const f=fixture({api:async(_path,body)=>body?new Promise(resolve=>release=resolve):{comments:[]}});f.module.open("lead");await settle();assert.equal(f.visibility.blocked(),true);
  const saving=f.module.post("lead","note");await f.module.post("lead","duplicate");let prevented=false;f.listeners.get("keydown")({key:"Escape",preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(f.visibility.blocked(),false);assert.equal(f.restores(),1);
  release({id:1});await saving;f.module.open("lead");f.portal.events.get("click")({target:f.portal});assert.equal(f.visibility.blocked(),false);assert.equal(f.restores(),2);
});
test("destroyed owners cannot render or publish late responses and release all listeners and visibility registration",async()=>{
  let release;const f=fixture({api:()=>new Promise(resolve=>release=resolve)});f.module.open("lead");const renders=f.renders();f.module.destroy();release({comments:[{text:"late"}]});await settle();assert.equal(f.renders(),renders);assert.equal(f.portal.events.size,0);assert.equal(f.listeners.size,0);assert.deepEqual(f.visibility.owners(),[]);
});
