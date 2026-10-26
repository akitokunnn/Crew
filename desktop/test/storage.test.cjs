'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const core=require('../src/core.cjs'),storage=require('../src/storage.cjs');

test('opening a new folder preserves the existing relocation choice without preparing an unnamed workspace',async()=>{
  const request=core.request,existing=core.existingWorkspace,calls=[];
  const relocation={relocation:{directory:'D:/moved',projects:[{name:'old'}]}};
  core.request=async(_base,route)=>{calls.push(route);return {status:'new'};};
  core.existingWorkspace=async()=>relocation;
  try{assert.equal(await storage.openDirectory('D:/moved','base'),relocation);assert.deepEqual(calls,['/api/workspace-data/discover']);}
  finally{core.request=request;core.existingWorkspace=existing;}
});

test('opening ready history refreshes by identity without another upgrade prompt',async()=>{
  const original=core.request,calls=[];const project={name:'p',root:'D:/project',workspace_id:'stable'};
  core.request=async(_base,route,body)=>{calls.push([route,body]);return route.endsWith('/discover')?{status:'ready',project}:{project};};
  try{const result=await storage.prepare(project,'http://127.0.0.1:7122',()=>{throw Error('unexpected prompt');});assert.equal(result.workspace_id,'stable');assert.deepEqual(calls.map(c=>c[0]),['/api/workspace-data/discover','/api/workspace-data/open']);}finally{core.request=original;}
});
test('cancelling a detected legacy history never registers an empty workspace',async()=>{
  const original=core.request,calls=[];
  core.request=async(_base,route)=>{calls.push(route);return {status:'upgrade_required',candidates:[{id:'old',counts:{sessions:7}}]};};
  try{assert.equal(await storage.openDirectory('D:/project','base',async()=>null),undefined);assert.deepEqual(calls,['/api/workspace-data/discover']);}finally{core.request=original;}
});
test('a chosen old source is passed explicitly and a portable archive needs no execution setup',async()=>{
  const original=core.request,calls=[];
  core.request=async(_base,route,body)=>{calls.push([route,body]);return route.endsWith('/discover')?{status:'upgrade_required',candidates:[{id:'old',counts:{sessions:7}}]}:{project:{name:'p',workspace_id:'stable'}};};
  try{await storage.prepare({name:'p',root:'D:/project'},'base',async()=>({action:'upgrade',candidate_id:'old'}));assert.equal(calls[1][1].candidate_id,'old');const archive={name:'storage:stable',read_only:true};assert.equal(await storage.prepare(archive,'base'),archive);assert.equal(calls.length,2);}finally{core.request=original;}
});
test('an identity marker with missing history reports recovery instead of registering empty data',async()=>{
  const original=core.request;let count=0;core.request=async()=>{count++;return {status:'history_missing',message:'请先导入历史'};};
  try{await assert.rejects(storage.openDirectory('D:/project','base',()=>null),/导入历史/);assert.equal(count,1);}finally{core.request=original;}
});
test('an older borrowed backend gives an actionable update message instead of creating empty history',async()=>{
  const http=require('node:http');let requests=0;
  const server=http.createServer((request,response)=>{requests++;response.writeHead(404,{'Content-Type':'application/json'});response.end(JSON.stringify({error:'接口不存在'}));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{await assert.rejects(storage.openDirectory('D:/project','http://127.0.0.1:'+server.address().port,()=>null),error=>error.status===404&&/统一后台与插件/.test(error.message));assert.equal(requests,1);}finally{await new Promise(resolve=>server.close(resolve));}
});
