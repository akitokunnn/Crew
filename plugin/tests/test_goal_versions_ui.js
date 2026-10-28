"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
function node(){const children=new Map();return {innerHTML:"",textContent:"",isConnected:true,showModal(){},close(){},remove(){this.isConnected=false;},replaceChildren(){this.innerHTML="";},querySelectorAll(){return[];},querySelector(key){if(!children.has(key))children.set(key,node());return children.get(key);}};}
async function harness(handler){const dialog=node(),calls=[],sandbox={window:{},crypto:{randomUUID:()=>"restore-id"},document:{createElement:()=>dialog,body:{append(){}}}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-versions-ui.js"),"utf8"),sandbox);await sandbox.window.CrewGoalVersions.open({goal:{id:"goal",title:"作品"},api:async(path,body)=>{calls.push(body);return body.action==="versions.list"?{versions:[]}:handler(body);}});return{dialog,calls,preview:version=>dialog.querySelector('[data-versions]').onclick({target:{closest:()=>({dataset:{version}})}}),restore:()=>dialog.querySelector('[data-preview]').onclick({target:{closest:()=>true}})};}
test("failed selection clears a previous version preview so it cannot restore an unseen version",async()=>{
 const h=await harness(body=>{if(body.data.version_id==="B")throw Error("B 文件不可读");return{version_id:"A",can_restore:true,expected_files:{"app.js":"before"},files:[{path:"app.js",action:"write"}]};});
 await h.preview("A");assert.match(h.dialog.querySelector('[data-preview]').innerHTML,/data-restore/);
 await h.preview("B");assert.equal(h.dialog.querySelector('[data-preview]').innerHTML,"");assert.match(h.dialog.querySelector('[role=status]').textContent,/B 文件不可读/);
 await h.restore();assert(!h.calls.some(call=>call.action==="version.restore"));
});
test("restore binds the shown version and preserves the actual conflict reason without reporting success",async()=>{
 const h=await harness(body=>body.action==="versions.preview"?{version_id:"shown-A",can_restore:true,expected_files:{"app.js":"before"},files:[{path:"app.js",action:"write"}]}:{ok:false,reason:"预览后用户修改了文件",preview:{can_restore:false}});
 await h.preview("A");await h.restore();const call=h.calls.find(item=>item.action==="version.restore");assert.equal(call.data.version_id,"shown-A");assert.equal(h.dialog.querySelector('[role=status]').textContent,"预览后用户修改了文件");assert.equal(h.dialog.querySelector('[data-preview]').innerHTML,"");
 await h.restore();assert.equal(h.calls.filter(call=>call.action==="version.restore").length,1);
});
