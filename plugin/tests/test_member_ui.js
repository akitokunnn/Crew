"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web/settings-ui.js'),'utf8'),sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web/member-ui.js'),'utf8'),sandbox);
const ui=sandbox.window.CrewMembers,plain=value=>JSON.parse(JSON.stringify(value));
test('member defaults follow leader override, global member choice, then parent brain without mutating inputs',()=>{
  const leader={runtime:'claude',model:'parent',effort:'high'},global={runtime:'codex',model:'global',effort:'low'},own={runtime:'codex',model:'own',effort:'high'};
  assert.deepEqual(plain(ui.effective(leader,null)),{runtime:'claude',model:'parent',effort:'high',speed:null});
  assert.deepEqual(plain(ui.effective({...leader,runtime:'codex',speed:'priority'},null)).speed,'priority');
  assert.deepEqual(plain(ui.effective(leader,global)),{...global,speed:null});
  leader.member_defaults=own;const value=ui.effective(leader,global);assert.deepEqual(plain(value),{...own,speed:null});value.model='changed';assert.equal(own.model,'own');
});
test('member creation validates discovered capabilities and never silently switches an explicit unavailable carrier',()=>{
  const catalog=[{id:'codex',available:true,models:[{id:'native',efforts:['low','high']}]}];
  assert.equal(ui.validate({runtime:null,model:null,effort:null},catalog).runtime,'codex');
  assert.throws(()=>ui.validate({runtime:'claude',model:null,effort:null},catalog),/请选择已就绪/);
  assert.throws(()=>ui.validate({runtime:'codex',model:'native',effort:'ultra'},catalog),/不支持/);
  assert.equal(ui.validate({runtime:'codex',model:'custom-model',effort:'future'},catalog).effort,'future');
  const fast=[{id:'codex',available:true,models:[{id:'native',efforts:[],speeds:[{id:'priority',name:'Fast'}]}]},{id:'claude',available:true,models:[]}];
  assert.equal(ui.validate({runtime:'codex',model:'native',effort:null,speed:'priority'},fast).speed,'priority');
  assert.equal(ui.validate({runtime:'codex',model:'native',effort:null,speed:'default'},fast).speed,'default');
  assert.throws(()=>ui.validate({runtime:'codex',model:'native',effort:null,speed:'ultrafast'},fast),/速度/);
  assert.throws(()=>ui.validate({runtime:'claude',model:null,effort:null,speed:'priority'},fast),/只适用于 Codex/);
});
