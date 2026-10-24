'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {Administrator} = require('../src/admin.cjs');
async function setup(t) {
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'crew-admin-'));
  t.after(()=>fs.rm(home,{recursive:true,force:true}));
  const directory=path.join(home,'desktop','admin');await fs.mkdir(directory,{recursive:true});
  const admin=new Administrator(home);
  const value={instance_id:'test-instance',supervisor_pid:process.pid,state:'failed',checked_at:new Date().toISOString(),incidents:[],restart:{allowed:true}};
  const save=record=>fs.writeFile(path.join(directory,'status.json'),JSON.stringify(record));
  return {admin,directory,value,save};
}
test('administrator is local to the Crew home and refuses requests without a live supervisor',async t=>{
  const {admin,directory,value,save}=await setup(t);
  assert.equal((await admin.snapshot()).state,'unmanaged');
  await assert.rejects(admin.request('recover'),/尚未启动/);
  await save({...value,supervisor_pid:0});
  assert.equal((await admin.snapshot()).state,'unknown');
  await assert.rejects(admin.request('check'),/未运行/);
  await assert.rejects(fs.access(path.join(directory,'control.json')));
});
test('fresh recovery writes an exact generation and unique receipt, while running service recovery is refused',async t=>{
  const {admin,directory,value,save}=await setup(t);await save(value);
  const result=await admin.request('recover');
  const control=JSON.parse(await fs.readFile(path.join(directory,'control.json'),'utf8'));
  assert.deepEqual(control,{instance_id:value.instance_id,request_id:result.request_id,action:'recover'});
  await save({...value,state:'healthy',restart:{allowed:false,reason:'后台仍在运行'}});
  await assert.rejects(admin.request('recover'),/仍在运行/);
  await admin.request('check');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory,'control.json'),'utf8')).action,'check');
});
test('maintenance and stale state cannot silently restart Crew',async t=>{
  const {admin,directory,value,save}=await setup(t);await save({...value,checked_at:'2020-01-01T00:00:00Z'});
  await assert.rejects(admin.request('recover'),/过期/);
  await save(value);await fs.writeFile(path.join(directory,'maintenance.json'),JSON.stringify({reason:'用户暂停'}));
  assert.equal((await admin.snapshot()).state,'paused');
  await assert.rejects(admin.request('recover'),/暂停/);
  await fs.writeFile(path.join(directory,'maintenance.json'),'{');
  await assert.rejects(admin.request('check'),/维护记录/);
});

test('file-backed incident report remains readable without supervisor or Web, and cannot read outside HOME',async t=>{
  const {admin,directory}=await setup(t),root=path.join(directory,'operations');await fs.mkdir(root);
  const report=path.join(root,'report.html');await fs.writeFile(report,'<!doctype html><p>before broken → after fixed</p>');
  await fs.writeFile(path.join(root,'state.json'),JSON.stringify({active_worker:null,incidents:[{id:'fixture',status:'awaiting_merge',attempts:1,report}]}));
  const offline=await admin.snapshot();assert.equal(offline.state,'unmanaged');assert.match(offline.operations.report.html,/broken/);assert.equal(offline.operations.incidents[0].attempts,1);
  await fs.writeFile(path.join(root,'state.json'),JSON.stringify({incidents:[{id:'fixture',report:path.join(directory,'status.json')}]}));
  assert.match((await admin.snapshot()).operations.error,/InvalidReportPath/);
});
