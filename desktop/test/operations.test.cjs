'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const {Writable} = require('node:stream');
const {OperationsClient} = require('../src/operations.cjs');

function helper(reply = {busy:false,messages:[]}, failure = null, stderr = '') {
  const calls = [];
  const execute = (file, args, options, callback) => {
    const call = {file,args,options,input:''}; calls.push(call);
    const stdin = new Writable({write(chunk, encoding, done) { call.input += chunk.toString(); done(); }});
    stdin.on('finish', () => callback(failure, typeof reply === 'string' ? reply : JSON.stringify(reply), stderr));
    return {stdin};
  };
  return {execute,calls};
}

test('operations helper sends exact text through stdin and works without a service URL', async () => {
  const {execute,calls} = helper();
  const config = {python:'fixture-python',home:'C:/测试 Crew/home',backendDir:'C:/测试 Crew/plugin'};
  const client = new OperationsClient(config, execute);
  const body = {text:'后台断线了\n路径含 ` $() " 和中文',message_id:'fixture-message'};
  assert.deepEqual(await client.request('send',body),{busy:false,messages:[]});
  assert.equal(calls.length,1);
  const call = calls[0];
  assert.equal(call.file,'fixture-python');
  assert.deepEqual(call.args,['-m','crew.operations_chat','--home',config.home,'--backend',config.backendDir,'--action','send']);
  assert.deepEqual(JSON.parse(call.input),body);
  assert.ok(!call.args.includes(body.text));
  assert.equal(call.options.cwd,config.backendDir);
  assert.equal(call.options.env.PYTHONPATH,path.resolve(config.backendDir));
  assert.equal(call.options.env.PYTHONIOENCODING,'utf-8');
  assert.equal(call.options.windowsHide,true);
  assert.equal(call.options.shell,undefined);
});

test('operations helper removes inherited AI identity only in its child environment', async t => {
  const names = ['CREW_SESSION_ID','CREW_NAME','CLAUDE_CODE_SESSION_ID','CODEX_THREAD_ID'];
  const previous = Object.fromEntries(names.map(name => [name,process.env[name]]));
  t.after(() => { for (const name of names) { if (previous[name] === undefined) delete process.env[name]; else process.env[name] = previous[name]; } });
  for (const name of names) process.env[name] = 'outer-identity';
  const {execute,calls} = helper();
  await new OperationsClient({home:'.',backendDir:'.'},execute).request('snapshot');
  for (const name of names) {
    assert.equal(calls[0].options.env[name],undefined);
    assert.equal(process.env[name],'outer-identity');
  }
  assert.deepEqual(JSON.parse(calls[0].input),{});
});

test('operations client uses the materialized backend and whitelisted maintenance actions', async () => {
  const {execute,calls} = helper({configured:true});
  const config = {home:'fixture-home',backendDir:'bundled-backend'};
  const client = new OperationsClient(config,execute);
  config.backendDir = 'materialized-backend';
  for (const action of ['snapshot','interrupt','config-get','config-save']) await client.request(action,{enabled:false});
  assert.equal(calls.length,4);
  assert.ok(calls.every(call => call.options.cwd === 'materialized-backend'));
  assert.equal(calls[0].file,'python');
  await assert.rejects(client.request('restart-everything'),/不支持的运维操作/);
  assert.equal(calls.length,4);
});

test('operations helper preserves the Python error and rejects malformed responses', async () => {
  const failed = helper('',new Error('Command failed'),'真实配置错误：仓库不存在\n');
  await assert.rejects(new OperationsClient({home:'.',backendDir:'.'},failed.execute).request('config-save',{}),/^Error: 真实配置错误：仓库不存在$/);
  const malformed = helper('not-json');
  await assert.rejects(new OperationsClient({home:'.',backendDir:'.'},malformed.execute).request('snapshot'),/运维响应无法读取/);
});

test('brain settings use the local helper while HTTP is disconnected, with time for model discovery', async () => {
  const fixture = {ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,
    operations:{runtime:'codex',model:'fixture-ops',effort:'high'}},execution:{codex:'workspace-write',claude:'default'},
    runtimes:[{id:'codex',available:true,models:[{id:'fixture-ops',efforts:['high']}]}],home:'fixture-home',backend:'fixture-backend'};
  const {execute,calls} = helper(fixture);
  const client = new OperationsClient({home:'fixture-home',backendDir:'fixture-backend',serviceUrl:'http://127.0.0.1:0'},execute);
  assert.deepEqual(await client.request('settings-get'),fixture);
  const body = {ai:fixture.ai,execution:fixture.execution};
  assert.deepEqual(await client.request('settings-save',body),fixture);
  assert.equal(calls[0].args.at(-1),'settings-get');
  assert.equal(calls[0].options.timeout,60000);
  assert.equal(calls[1].args.at(-1),'settings-save');
  assert.equal(calls[1].options.timeout,30000);
  assert.deepEqual(JSON.parse(calls[1].input),body);
  assert.ok(calls.every(call => !call.args.includes('http://127.0.0.1:0')));
});

test('control helpers use bundled code even when the selected custom backend is missing',async()=>{
  const {execute,calls}=helper();
  const client=new OperationsClient({home:'fixture-home',backendDir:'missing-custom-source',helperBackendDir:'bundled-control-source'},execute);
  for(const action of ['snapshot','home-inspect','home-copy','reset-deployment','prepare-home'])await client.request(action,{source:'old',target:'new'});
  assert.ok(calls.every(call=>call.options.cwd==='bundled-control-source'&&call.args[5]==='bundled-control-source'));
  assert.equal(calls[0].options.timeout,30000);assert.equal(calls[1].options.timeout,60000);assert.equal(calls[2].options.timeout,600000);
  client.config.python='missing-python';await client.request('home-inspect',{source:'old',target:'new'},{python:'selected-python'});
  assert.equal(calls.at(-1).file,'selected-python');assert.equal(client.config.python,'missing-python');
});

test('real Python helper reads and saves isolated policy without HTTP or a model', async t => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(),'crew-operations-helper-'));
  t.after(() => fs.rm(temporary,{recursive:true,force:true}));
  const home = path.join(temporary,'测试 HOME');
  const backendDir = path.resolve(__dirname,'../../plugin');
  const client = new OperationsClient({home,backendDir});
  assert.deepEqual(await client.request('snapshot'),{messages:[],busy:false,error:null,working_since:null,
    history_count:0,older_count:0,brain:null});
  const initial = await client.request('config-get');
  assert.equal(initial.configured,false);
  assert.equal(initial.policy.enabled,false);
  await assert.rejects(fs.access(home),{code:'ENOENT'});
  const policy = {...initial.policy,enabled:false,repository:'C:/暂不配置源代码',timeout_seconds:73,merge_ref:'开发主干'};
  const saved = await client.request('config-save',policy);
  assert.equal(saved.configured,true);
  assert.deepEqual(saved.policy,policy);
  assert.deepEqual((await client.request('config-get')).policy,policy);
  assert.deepEqual((await client.request('interrupt')).messages,[]);
  await assert.rejects(client.request('send',{text:'',message_id:'fixture'}),/请输入 1 到 8000 字的运维问题/);
  await assert.rejects(client.request('config-save',{...policy,timeout_seconds:900}),/修复超时必须在 1 到 600 秒之间/);
  assert.deepEqual((await client.request('config-get')).policy,policy);
  await assert.rejects(fs.access(path.join(home,'desktop','admin','operations','chat','worker.log')),{code:'ENOENT'});
});
