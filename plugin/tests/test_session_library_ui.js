"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../frontend/session-library.js'),'utf8'),sandbox);
const {matches,canAdopt,executionMessages,categories}=sandbox.window.CrewSessionLibrary;
const base={category:'available',runtime:'codex',running:null,title:'声纹识别旧方案',name:null,group:null,project_name:'MIB',cwd:'C:/MIB/Temp/speaker',native_id:'native-123',war_rooms:[{id:1,title:'采集评审'}]};
const filter=extra=>({category:'available',runtime:'all',status:'all',query:'',...extra});
test('default view groups fresh and named ungrouped conversations without archives or background jobs',()=>{
  assert(matches(base,filter()));assert(matches({...base,category:'ungrouped'},filter()));
  for(const category of ['team','archived','background','workspace_assistant','system_admin','task_history'])assert(!matches({...base,category},filter()));
  assert(matches({...base,category:'archived'},filter({category:'archived'})));
});
test('search combines words across title, workspace, path and native id without requiring remembered exact names',()=>{
  assert(matches(base,filter({query:'声纹 mib speaker'})));assert(matches(base,filter({query:'NATIVE-123'})));
  assert(!matches(base,filter({query:'声纹 stock'})));
  assert(matches(base,filter({query:'采集评审'})));
});
test('native carrier, observed running state and grouping are independent filters',()=>{
  assert(!matches(base,filter({runtime:'claude'})));assert(!matches(base,filter({status:'running'})));
  assert(matches({...base,running:true},filter({status:'running'})));
  assert(matches({...base,category:'ungrouped'},filter({status:'ungrouped'})));
  assert(!matches(base,filter({status:'ungrouped'})));
});

test('only external and ungrouped rows may adopt, even if a history row carries an adoptable flag',()=>{
  assert(canAdopt({...base, adoptable:true}));
  assert(canAdopt({...base, category:'ungrouped', adoptable:true}));
  for (const category of ['system_admin','workspace_assistant','team','archived','background','task_history']) {
    assert(!canAdopt({...base, category, adoptable:true}));
  }
  assert(categories.some(([id,label])=>id==='system_admin'&&label==='系统管理员'));
  assert(categories.some(([id,label])=>id==='workspace_assistant'&&label==='工作区助手'));
});

test('a selected execution preview uses conversation text and skips tools and intermediate partial copies',()=>{
  const messages=executionMessages([
    {type:'message',id:'u',text:'检查构建'},
    {type:'text',id:'a',text:'检查中'},
    {type:'assistant',id:'a',content:[{type:'tool_use',id:'tool',input:{}},{type:'text',text:'检查完成'}]},
    {type:'tool_result',tool_use_id:'tool',text:'private tool output'}
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(messages)),[{role:'user',text:'检查构建'},{role:'assistant',text:'检查完成'}]);
});
