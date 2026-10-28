"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const tick=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
function harness(request) {
  const listeners=new Map(),calls=[],saved=new Map(),notices=[],scroll={scrollTop:128};
  const dialog={open:false,showModal(){this.open=true;},close(){this.open=false;}},status={textContent:""},portal={innerHTML:""};
  const host={innerHTML:"",closest:()=>scroll,querySelectorAll:()=>[],querySelector:selector=>selector===".td-dialog"?dialog:selector===".td-dialog-host"?portal:selector===".td-restore-status"?status:null,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
  const sandbox={window:null,URLSearchParams,localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)},CrewChat:{renderMarkdown:text=>text},CrewDOMRenderer:{create:()=>({replace:(target,html)=>{target.innerHTML=html;},destroy(){}})}};sandbox.window=sandbox;
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/task-deliverables.js"),"utf8"),sandbox);
  const client={request:async(url,body)=>{calls.push({url,body});return request(url,body);},resourceURL:url=>url};
  const ui=sandbox.CrewTaskDeliverables.create({host,client,project:"primary",taskId:"task",sessionId:"leader",toast:text=>notices.push(text)});
  const click=async(action,extra={})=>{const button={disabled:false,dataset:{deliverableAction:action,...extra}};await listeners.get("click")({target:{closest:()=>button}});return button;};
  return{ui,host,calls,saved,notices,listeners,click,dialog,status};
}
const query=(url,extra={})=>url.includes("/report")?{report:{version:1,markdown:"# 已保留的报告"}}:url.includes("/attachments")?{attachments:[]}:{versions:[{id:"v1",label:"阶段 v1",version:1,files:[{path:"app.txt",delivered_sha256:"target",delivered_size:3}]}],...extra};

test("artifact restore is previewed first and an uncertain retry keeps the exact expected files and operation ID",async()=>{
  let restoreCount=0;const expected={"app.txt":"current"};
  const h=harness(async(url,body)=>{
    if(url.includes("/preview"))return{version_id:"v1",can_restore:true,files:[{path:"app.txt",action:"write"}],expected_files:expected};
    if(url.includes("/restore")){if(++restoreCount===1)throw Error("response lost after native effect");return{ok:true,status:"completed"};}
    return query(url);
  });await tick();await h.click("preview",{versionId:"v1"});assert.equal(h.dialog.open,true);assert.equal(restoreCount,0);
  await h.ui.refresh();assert.equal(h.dialog.open,true,"background refresh must not dismiss a restoration review");
  await h.click("restore");assert.match(h.status.textContent,/尚未确认/);await h.click("restore");
  const calls=h.calls.filter(c=>c.url.includes("/restore"));assert.equal(calls.length,2);assert.equal(calls[0].body.request_id,calls[1].body.request_id);assert.deepEqual(JSON.parse(JSON.stringify(calls[1].body.expected_files)),expected);assert.match(h.status.textContent,/已按预览恢复/);h.ui.destroy();assert.equal(h.listeners.size,0);
});

test("an accepted report save never discards text written while its request was pending",async()=>{
  let release;const h=harness(async(url,body)=>body&&url.includes("/report")?new Promise(done=>{release=done;}):query(url));await tick();await h.click("edit-report");
  const input=value=>h.listeners.get("input")({target:{matches:selector=>selector===".td-report-form textarea",value}});
  input("第一版修改");const submit={disabled:false},error={textContent:""},form={matches:selector=>selector===".td-report-form",querySelector:selector=>selector==='[type="submit"]'?submit:error};
  const pending=h.listeners.get("submit")({target:form,preventDefault(){}});input("提交期间继续写的新内容");release({report:{version:2,markdown:"第一版修改"}});await pending;
  const draft=JSON.parse(h.saved.get("crew.report-draft:primary:task"));assert.equal(draft.markdown,"提交期间继续写的新内容");assert.match(h.host.innerHTML,/提交期间继续写的新内容/);h.ui.destroy();
});
