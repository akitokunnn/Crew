"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web/runtime-catalog.js'),'utf8'),sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web/member-ui.js'),'utf8'),sandbox);
const ui=sandbox.window.CrewMembers,plain=value=>JSON.parse(JSON.stringify(value));
test('member defaults preserve explicit overrides and leave an unconfigured role for the backend without copying its leader',()=>{
  const leader={runtime:'claude',model:'parent',effort:'high'},global={runtime:'codex',model:'global',effort:'low'},own={runtime:'codex',model:'own',effort:'high'};
  assert.deepEqual(plain(ui.effective(leader,null)),{runtime:null,model:null,effort:null});
  assert.deepEqual(plain(ui.effective(leader,global)),global);
  leader.member_defaults=own;const value=ui.effective(leader,global);assert.deepEqual(plain(value),own);value.model='changed';assert.equal(own.model,'own');
});
test('member creation validates discovered capabilities and never silently switches an explicit unavailable carrier',()=>{
  const catalog=[{id:'codex',available:true,models:[{id:'native',efforts:['low','high']}]}];
  assert.deepEqual(plain(ui.validate({runtime:null,model:null,effort:null},catalog)),{});
  assert.deepEqual(plain(ui.validate({runtime:null,model:'explicit-model',effort:'explicit-effort'},catalog)),{model:'explicit-model',effort:'explicit-effort'});
  assert.throws(()=>ui.validate({runtime:'claude',model:null,effort:null},catalog),/请选择已就绪/);
  assert.throws(()=>ui.validate({runtime:'codex',model:'native',effort:'ultra'},catalog),/不支持/);
  assert.equal(ui.validate({runtime:'codex',model:'custom-model',effort:'future'},catalog).effort,'future');
});

function formHarness({leader = {}, globalChoice = null, catalog = [{id:'codex',available:true,models:[]}], response = {session_id:'created'}} = {}) {
  const listeners=new Map(),fields=new Map(),calls=[],notices=[];
  const element=()=>({value:'',disabled:false,textContent:'',innerHTML:'',focus(){}});
  const overlay={hidden:true,innerHTML:'',querySelector(selector){if(!fields.has(selector))fields.set(selector,element());return fields.get(selector);},querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners.set(name,fn)};
  const scope={window:{},document:{createElement:()=>overlay,body:{append(){}},activeElement:null}};
  vm.createContext(scope);for(const file of ['runtime-catalog.js','settings-ui.js','member-ui.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web',file),'utf8'),scope);
  const member=scope.window.CrewMembers.create({getLeader:()=>({session_id:'leader',name:'负责人',runtime:'claude',model:'parent-model',effort:'high',...leader}),globalChoice:()=>globalChoice,catalog:()=>catalog,api:async(url,body)=>{calls.push({url,body:plain(body)});return response;},toast:text=>notices.push(text),onChanged:async()=>{}});
  const change=(name,value,checked)=>listeners.get('change')({target:{name,value,checked,dataset:{}}});
  const input=(name,value)=>listeners.get('input')({target:{name,value,dataset:{}}});
  return{member,overlay,calls,notices,change,input,submit:()=>listeners.get('submit')({preventDefault(){}})};
}

test('unconfigured member creation omits an implicit brain and reports the backend selection source without claiming a loaded model',async()=>{
  const h=formHarness({response:{session_id:'created',selection_resolution:{selected_runtime:'codex',reason:'使用成员角色首选',loaded:'not_confirmed'}}});
  h.member.openCreate('leader');h.input('name','施工成员');h.input('task','验证真实边界');await h.submit();
  assert.deepEqual(h.calls,[{url:'/api/spawn',body:{name:'施工成员',task:'验证真实边界',leader_session_id:'leader',role:'member'}}]);
  assert(h.notices[0].includes('Codex'));assert(h.notices[0].includes('使用成员角色首选'));assert(h.notices[0].includes('尚未确认本次模型加载'));
});

test('explicit member defaults remain whole choices while empty per-leader defaults remove the override instead of saving an empty object',async()=>{
  const inherited=formHarness();inherited.member.openDefaults('leader');await inherited.submit();assert.equal(inherited.calls[0].body.selection,null);
  const empty=formHarness();empty.member.openDefaults('leader');empty.change('inherit','',false);await empty.submit();assert.equal(empty.calls[0].body.selection,null);
  const configured={runtime:'codex',model:'chosen-model',effort:'high'},explicit=formHarness({leader:{member_defaults:configured}});
  explicit.member.openDefaults('leader');await explicit.submit();assert.deepEqual(explicit.calls[0].body.selection,configured);
  const partial=formHarness({leader:{member_defaults:{runtime:null,model:'chosen-model',effort:null}}});partial.member.openDefaults('leader');await partial.submit();assert.deepEqual(partial.calls[0].body.selection,{runtime:null,model:'chosen-model',effort:null});
});

test('an explicit global member choice is sent unchanged and an unavailable explicit carrier keeps its actual error',async()=>{
  const globalChoice={runtime:'codex',model:null,effort:null},h=formHarness({globalChoice});h.member.openCreate('leader');h.input('name','测试成员');h.input('task','核对配置');await h.submit();
  assert.deepEqual(h.calls[0].body,{name:'测试成员',task:'核对配置',leader_session_id:'leader',role:'member',...globalChoice});
  const unavailable=formHarness({leader:{member_defaults:{runtime:'claude',model:null,effort:null}},catalog:[{id:'claude',available:false,error:'Claude 原生载体未安装'},{id:'codex',available:true}]});
  unavailable.member.openCreate('leader');unavailable.input('name','测试成员');unavailable.input('task','核对配置');await unavailable.submit();assert.equal(unavailable.calls.length,0);assert(unavailable.overlay.innerHTML.includes('Claude 原生载体未安装'));
});
