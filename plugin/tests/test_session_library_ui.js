"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../crew/web/session-library.js'),'utf8'),sandbox);
const {matches}=sandbox.window.CrewSessionLibrary;
const base={category:'available',runtime:'codex',running:null,title:'声纹识别旧方案',name:null,group:null,project_name:'MIB',cwd:'C:/MIB/Temp/speaker',native_id:'native-123'};
const filter=extra=>({category:'available',runtime:'all',status:'all',query:'',...extra});
test('default view groups fresh and named ungrouped conversations without archives or background jobs',()=>{
  assert(matches(base,filter()));assert(matches({...base,category:'ungrouped'},filter()));
  for(const category of ['team','archived','background'])assert(!matches({...base,category},filter()));
  assert(matches({...base,category:'archived'},filter({category:'archived'})));
});
test('search combines words across title, workspace, path and native id without requiring remembered exact names',()=>{
  assert(matches(base,filter({query:'声纹 mib speaker'})));assert(matches(base,filter({query:'NATIVE-123'})));
  assert(!matches(base,filter({query:'声纹 stock'})));
});
test('native carrier, observed running state and grouping are independent filters',()=>{
  assert(!matches(base,filter({runtime:'claude'})));assert(!matches(base,filter({status:'running'})));
  assert(matches({...base,running:true},filter({status:'running'})));
  assert(matches({...base,category:'ungrouped'},filter({status:'ungrouped'})));
  assert(!matches(base,filter({status:'ungrouped'})));
});
