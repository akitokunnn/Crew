'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {LaunchSettings,readSync,relaunchArgs,save,shape,rememberPort}=require('../src/launch-settings.cjs');
const core=require('../src/core.cjs');
const PYTHON=require('../src/python.cjs').defaultPython();
const {CrewUpdates}=require('../src/crew-updates.cjs');
const {OperationsClient}=require('../src/operations.cjs');
const {promisify}=require('node:util'),{execFile}=require('node:child_process');
const net=require('node:net');

async function fixture(t,options={}) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'crew-launch-settings-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const home=path.join(root,'source home'),target=path.join(root,'目标 HOME'),source=path.join(root,'backend');
  await fs.mkdir(home);await fs.mkdir(target);await fs.mkdir(path.join(source,'crew'),{recursive:true});
  await fs.writeFile(path.join(source,'crew_supervisor.py'),'# fixture');await fs.writeFile(path.join(source,'crew','web.py'),'# fixture');
  await fs.mkdir(path.join(source,'.claude-plugin'));await fs.writeFile(path.join(source,'.claude-plugin','plugin.json'),JSON.stringify({name:'crew',version:'0.0.0-test'}));
  const config={home,backendDir:source,bundledBackendDir:source,backendSource:'bundled',python:options.badCurrentPython?'missing-python':'fixture-python',port:0,launchSettingsPath:path.join(root,'preferences','launch-settings.json')};
  const calls=[],preparedHomes=[];
  const backend={owned:!options.borrowed,url:'http://127.0.0.1:52345',child:{pid:100,exitCode:options.exited?0:null},async stop(){calls.push('stop');this.child.exitCode=0;}};
  const health=url=>({ok:true,service:'crew',home:url==='http://127.0.0.1:52346'?target:home,pid:101,instance_id:'fixture-instance',version:'fixture',plugin_root:source,port:52345});
  const updates={pending:null,checking:null,cancel(){calls.push('cancel-update');},async _lock(python){calls.push('lock');assert.equal(python,'fixture-python');return{assert(){},async release(){calls.push('unlock');}};},async _helper(action,args,python){calls.push(action);assert.equal(action,'stopped');assert.equal(python,'fixture-python');assert.ok(args.includes(options.startupFailure?'100':'101'));if(options.stopFailure)throw Error('仍有后台进程存活');}};
  const operations={async request(action,body,{python}){calls.push(action);assert.equal(python,'fixture-python');if(action==='prepare-home'){assert.ok([home,target].includes(body.home));assert.equal(typeof body.reset_deployment,'boolean');preparedHomes.push({...body});if(options.resetFailure)throw Error('部署记录归档失败，设置未保存');return{ok:true};}assert.equal(body.source,home);assert.equal(body.target,target);if(action==='home-inspect')return{can_copy:options.cannotCopy!==true,can_connect:options.targetKind==='crew',target_kind:options.targetKind||'empty',already_copied:options.alreadyCopied===true,reason:'目标不是可用数据目录'};if(options.copyFailure)throw Error('SQLite复制失败，源目录保留');return{ok:true};}};
  const request=async(url,route,body)=>{
    calls.push(route);
    if(options.startupFailure)throw Error('启动失败后不应调用坏 HTTP');
    if(options.offline)throw Object.assign(Error('connection refused'),{code:'ECONNREFUSED'});
    if(route==='/api/admin/health')return health(url);
    if(route==='/api/admin/connection/plan')return{sessions:[{session_id:'one',name:'组长',project:'测试',running:true,busy:false}],goals_count:2,running_count:1,repair_active:false,operations_chat_busy:true};
    if(route==='/api/admin/connection/prepare'){assert.deepEqual(body,{instance_id:'fixture-instance'});if(options.prepareFailure)throw Error('运维修复仍在运行');return{ok:true,pid:101,instance_id:'fixture-instance',managed_pids:[102],gate_id:'fixture-gate'};}
    throw Error('Unexpected route '+route);
  };
  const hostHealth={ok:true,service:'crew',version:options.hostVersion||'0.0.0-test',home:'/Users/host/.crew',instance_id:'host-instance',host_name:'书房的 Mac',pid:7,warnings:[]};
  const saved=[{id:'saved-host',name:'192.168.1.20:47812',url:'http://192.168.1.20:47812',device_id:'device-1',paired_at:'2026-11-19T08:00:00.000Z'}];
  const remotes={async list(){return saved;},async credential(id){const item=saved.find(entry=>entry.id===id);if(!item)throw Error('已保存的局域网设备不存在，请在连接设置中重新选择后台');return{...item,token:'t'.repeat(43)};},
    async pair(body){calls.push('pair');return{...saved[0],paired:body};}};
  const remoteRequest=async(credential,route)=>{
    calls.push('remote '+route);assert.equal(credential.token,'t'.repeat(43));
    if(options.revoked)throw Object.assign(Error('设备授权已失效，请在主机上重新生成配对码'),{status:401});
    if(route==='/api/access/context')return{client:options.browserClient?'browser':'desktop'};
    if(route==='/api/admin/health')return hostHealth;
    throw Error('Unexpected host route '+route);
  };
  const manager=new LaunchSettings(config,{backend,updates,operations,remotes,remoteRequest,bundledBackend:source,request,
    probePort:options.realPort?undefined:async()=>{calls.push('probe-port');},
    run:async(exe,args,settings)=>{calls.push('python-version');assert.equal(exe,'fixture-python');assert.deepEqual(args,['-c',"import sys;print('%d.%d'%sys.version_info[:2])"]);assert.equal(settings.windowsHide,true);return{stdout:options.oldPython?'3.8':'3.13'};},
    onApplied:async()=>{calls.push('relaunch');assert.ok(readSync(config.launchSettingsPath));}});
  const settings={mode:'local',home,source:'bundled',backend_directory:source,service_url:'',port:0,python:'fixture-python',remote_backend:''};
  if(options.startupFailure){backend.startupError='后台启动失败';backend.supervised=true;backend.url='http://127.0.0.1:0';}
  return{root,home,target,source,config,backend,calls,manager,settings,preparedHomes};
}

test('launch preview checks source and Python and counts sessions without stopping or saving',async t=>{
  const f=await fixture(t),result=await f.manager.validate({settings:{...f.settings,port:54321},home_action:'existing'});
  assert.equal(result.impact.connection_changed,true);assert.equal(result.impact.running_count,1);assert.equal(result.impact.goals_count,2);assert.equal(result.impact.operations_chat_busy,true);
  assert.ok(!f.calls.includes('lock'));assert.ok(!f.calls.includes('stop'));await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});
});

test('existing connection reads its actual HOME and never prepares or stops the borrowed source',async t=>{
  const f=await fixture(t,{borrowed:true});
  const result=await f.manager.apply({settings:{...f.settings,mode:'existing',home:'ignored old HOME',service_url:'http://127.0.0.1:52346/'},home_action:'existing'});
  assert.equal(result.settings.home,await fs.realpath(f.target));assert.equal(result.settings.service_url,'http://127.0.0.1:52346');assert.equal(result.restarting,true);
  assert.ok(!f.calls.includes('stop'));assert.ok(!f.calls.includes('home-copy'));assert.ok(!f.calls.includes('/api/admin/connection/prepare'));
  assert.ok(!f.calls.includes('prepare-home'));assert.ok(!f.calls.includes('reset-deployment'));
});

test('an existing service can connect while its hidden custom source is missing and preserve that inactive choice',async t=>{
  const f=await fixture(t,{borrowed:true,badCurrentPython:true}),missing=path.join(f.root,'missing custom backend');
  f.config.backendSource='custom';f.config.backendDir=path.join(f.root,'old missing custom backend');
  const body={settings:{...f.settings,mode:'existing',source:'custom',backend_directory:missing,service_url:'http://127.0.0.1:52346'},home_action:'existing'};
  const preview=await f.manager.validate(body);
  assert.equal(preview.settings.backend_directory,missing);assert.equal(preview.impact.source_changed,false);assert.equal(preview.source_version,'fixture');
  const result=await f.manager.apply(body);assert.equal(result.ok,true);assert.equal(result.settings.home,await fs.realpath(f.target));
  assert.equal(readSync(f.config.launchSettingsPath).backend_directory,missing);assert.equal(readSync(f.config.launchSettingsPath).python,'fixture-python');
  assert.ok(!f.calls.includes('prepare-home'));assert.ok(!f.calls.includes('home-copy'));assert.ok(!f.calls.includes('stop'));
});

test('owned HOME copy pauses and drains before copy, saves atomically, then requests relaunch',async t=>{
  const f=await fixture(t);
  const result=await f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'});
  assert.equal(result.ok,true);assert.equal(result.impact.copy_required,true);assert.equal(readSync(f.config.launchSettingsPath).home,f.target);
  for(const [before,after]of [['lock','/api/admin/connection/prepare'],['/api/admin/connection/prepare','stop'],['stop','stopped'],['stopped','home-copy'],['home-copy','prepare-home'],['prepare-home','relaunch'],['relaunch','unlock']])assert.ok(f.calls.indexOf(before)<f.calls.indexOf(after),before+' before '+after);
  assert.deepEqual(f.preparedHomes,[{home:f.target,reset_deployment:true}]);
});

test('deployment reset failure after copy leaves settings untouched and never requests a restart',async t=>{
  const f=await fixture(t,{resetFailure:true});
  await assert.rejects(f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'}),/部署记录归档失败/);
  assert.ok(f.calls.includes('home-copy'));assert.ok(f.calls.includes('prepare-home'));assert.equal(f.backend.child.exitCode,0);
  assert.ok(!f.calls.includes('relaunch'));assert.ok(f.calls.includes('unlock'));await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});
});

test('changing only the local port preserves the current verified deployment',async t=>{
  const f=await fixture(t),deployment=path.join(f.home,'desktop','admin','deployment.json');
  await fs.mkdir(path.dirname(deployment),{recursive:true});await fs.writeFile(deployment,'{"verified":{"commit":"keep-current-repair"}}');
  const result=await f.manager.apply({settings:{...f.settings,port:54321},home_action:'existing'});
  assert.equal(result.ok,true);assert.ok(!f.calls.includes('reset-deployment'));assert.deepEqual(f.preparedHomes,[{home:f.home,reset_deployment:false}]);
  assert.equal(await fs.readFile(deployment,'utf8'),'{"verified":{"commit":"keep-current-repair"}}');
});

test('selecting a different backend source archives the old deployment before the chosen source is saved',async t=>{
  const f=await fixture(t);
  const result=await f.manager.apply({settings:{...f.settings,source:'custom',backend_directory:f.source},home_action:'existing'});
  assert.equal(result.ok,true);assert.deepEqual(f.preparedHomes,[{home:f.home,reset_deployment:true}]);
  assert.ok(f.calls.indexOf('stopped')<f.calls.indexOf('prepare-home'));assert.ok(f.calls.indexOf('prepare-home')<f.calls.indexOf('relaunch'));
  assert.equal(readSync(f.config.launchSettingsPath).source,'custom');
});

test('copy failure leaves paused source stopped and does not save or relaunch',async t=>{
  const f=await fixture(t,{copyFailure:true});
  await assert.rejects(f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'}),/SQLite复制失败/);
  assert.equal(f.backend.child.exitCode,0);assert.ok(f.calls.includes('unlock'));assert.ok(!f.calls.includes('relaunch'));await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});
});

test('after a failed copy, applying the original settings explicitly reconnects the stopped backend',async t=>{
  const f=await fixture(t,{copyFailure:true});
  await assert.rejects(f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'}),/SQLite复制失败/);
  assert.equal((await f.manager.get()).reconnect_required,true);
  const preview=await f.manager.validate({settings:f.settings,home_action:'existing'});
  assert.equal(preview.impact.reconnect_required,true);assert.equal(preview.impact.home_changed,false);assert.equal(preview.impact.connection_changed,false);
  const result=await f.manager.apply({settings:f.settings,home_action:'existing'});
  assert.equal(result.restarting,true);assert.equal(f.calls.filter(value=>value==='stop').length,1);assert.equal(f.calls.filter(value=>value==='relaunch').length,1);
  assert.deepEqual(f.preparedHomes,[{home:f.home,reset_deployment:false}]);
  assert.equal(readSync(f.config.launchSettingsPath).home,f.home);
});

test('after a launch-settings save failure, a user retry of the original settings can recover the stopped service',async t=>{
  const f=await fixture(t);await fs.mkdir(f.config.launchSettingsPath,{recursive:true});
  await assert.rejects(f.manager.apply({settings:{...f.settings,port:54321},home_action:'existing'}));
  assert.equal(f.backend.child.exitCode,0);assert.ok(!f.calls.includes('relaunch'));
  await fs.rmdir(f.config.launchSettingsPath);
  const result=await f.manager.apply({settings:f.settings,home_action:'existing'});
  assert.equal(result.impact.reconnect_required,true);assert.equal(result.restarting,true);assert.equal(readSync(f.config.launchSettingsPath).port,0);
  assert.equal(f.calls.filter(value=>value==='relaunch').length,1);
});

test('unchanged healthy settings need no restart, but an explicitly retried startup failure does',async t=>{
  const healthy=await fixture(t),unchanged=await healthy.manager.apply({settings:healthy.settings,home_action:'existing'});
  assert.equal(unchanged.restarting,false);assert.equal(unchanged.impact.reconnect_required,false);assert.ok(!healthy.calls.includes('lock'));
  const failed=await fixture(t,{startupFailure:true}),result=await failed.manager.apply({settings:failed.settings,home_action:'existing'});
  assert.equal(result.restarting,true);assert.equal(result.impact.reconnect_required,true);assert.ok(failed.calls.includes('stop'));assert.ok(failed.calls.includes('relaunch'));
});

test('a borrowed live HOME copy is refused during preview with one health probe and no copy or stop',async t=>{
  const f=await fixture(t,{borrowed:true});
  await assert.rejects(f.manager.validate({settings:{...f.settings,home:f.target},home_action:'copy'}),/请先退出原后台，或选择使用目标目录数据/);
  assert.equal(f.calls.filter(call=>call==='/api/admin/health').length,1);assert.ok(!f.calls.includes('home-inspect'));assert.ok(!f.calls.includes('home-copy'));assert.ok(!f.calls.includes('stop'));assert.ok(!f.calls.includes('lock'));
});

test('a completed previous HOME copy can be reused after a later launch-save failure',async t=>{
  const f=await fixture(t,{targetKind:'crew',cannotCopy:true,alreadyCopied:true});
  const preview=await f.manager.validate({settings:{...f.settings,home:f.target},home_action:'copy'});
  assert.equal(preview.inspection.already_copied,true);
  assert.equal((await f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'})).ok,true);
});

test('a missing saved Python can be repaired using the explicitly selected interpreter for every helper',async t=>{
  for(const exited of [true,false]){
    const f=await fixture(t,{badCurrentPython:true,exited});
    assert.equal(f.config.python,'missing-python');
    const result=await f.manager.apply({settings:{...f.settings,home:f.target},home_action:'copy'});
    assert.equal(result.ok,true);assert.equal(readSync(f.config.launchSettingsPath).python,'fixture-python');
    assert.ok(f.calls.includes('home-inspect'));assert.ok(f.calls.includes('home-copy'));assert.ok(f.calls.includes('lock'));
    if(!exited)assert.ok(f.calls.includes('stopped'));
  }
});

test('a running borrowed backend cannot appear to accept a new local port or Python',async t=>{
  const f=await fixture(t,{borrowed:true});
  await assert.rejects(f.manager.apply({settings:{...f.settings,port:54321},home_action:'existing'}),/其他窗口启动/);
  assert.ok(!f.calls.includes('lock'));assert.ok(!f.calls.includes('stop'));await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});
});

test('a target HOME with a running backend must use existing service mode',async t=>{
  const f=await fixture(t,{targetKind:'crew'});
  await fs.writeFile(path.join(f.target,'web.json'),JSON.stringify({url:'http://127.0.0.1:52346'}));
  await assert.rejects(f.manager.validate({settings:{...f.settings,home:f.target},home_action:'existing'}),/目标 HOME 已有运行中的后台/);
  assert.ok(!f.calls.includes('stop'));
});

test('an empty HOME can start from zero while a non-Crew nonempty target is refused',async t=>{
  const empty=await fixture(t,{borrowed:true}),result=await empty.manager.validate({settings:{...empty.settings,home:empty.target},home_action:'existing'});
  assert.equal(result.impact.copy_required,false);assert.equal(result.inspection.target_kind,'empty');
  const blocked=await fixture(t,{targetKind:'nonempty'});
  await assert.rejects(blocked.manager.validate({settings:{...blocked.settings,home:blocked.target},home_action:'existing'}),/目标不是可用数据目录/);
});

test('a different offline existing HOME is prepared before a local launch without stopping the borrowed source',async t=>{
  const f=await fixture(t,{borrowed:true,targetKind:'crew'});
  const result=await f.manager.apply({settings:{...f.settings,home:f.target},home_action:'existing'});
  assert.equal(result.ok,true);assert.deepEqual(f.preparedHomes,[{home:f.target,reset_deployment:true}]);
  assert.ok(!f.calls.includes('stop'));assert.ok(!f.calls.includes('/api/admin/connection/prepare'));assert.ok(!f.calls.includes('home-copy'));
  assert.ok(f.calls.indexOf('prepare-home')<f.calls.indexOf('relaunch'));
});

test('source and Python checks fail before a live session is paused',async t=>{
  const f=await fixture(t);
  await assert.rejects(f.manager.apply({settings:{...f.settings,source:'custom',backend_directory:f.target},home_action:'existing'}),/ENOENT/);
  assert.ok(!f.calls.includes('lock'));assert.ok(!f.calls.includes('stop'));
  const old=await fixture(t,{oldPython:true});await assert.rejects(old.manager.validate({settings:old.settings,home_action:'existing'}),/Python 3.11/);
  const missing=await fixture(t);await fs.unlink(path.join(missing.source,'.claude-plugin','plugin.json'));await assert.rejects(missing.manager.validate({settings:missing.settings,home_action:'existing'}),/ENOENT/);assert.ok(!missing.calls.includes('stop'));
});

test('a real occupied loopback port and unsupported URL port are rejected before stopping sessions',async t=>{
  const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>listener.close(resolve)));
  const f=await fixture(t,{realPort:true});
  await assert.rejects(f.manager.validate({settings:{...f.settings,port:listener.address().port},home_action:'existing'}),{code:'EADDRINUSE'});
  await assert.rejects(f.manager.validate({settings:{...f.settings,port:80},home_action:'existing'}),/Crew 服务必须使用本机 loopback 地址/);
  assert.ok(!f.calls.includes('lock'));assert.ok(!f.calls.includes('stop'));
});

test('a failed owned supervisor is stopped through its own control pipe without calling broken HTTP',async t=>{
  const f=await fixture(t,{startupFailure:true,badCurrentPython:true});
  const result=await f.manager.apply({settings:{...f.settings,port:54321},home_action:'existing'});
  assert.equal(result.ok,true);assert.ok(f.calls.includes('stop'));assert.ok(f.calls.includes('stopped'));assert.ok(f.calls.includes('relaunch'));
  assert.ok(!f.calls.some(call=>call.startsWith('/api/')));assert.equal(readSync(f.config.launchSettingsPath).port,54321);
});

test('prepare or final exit failure never saves a misleading connection setting',async t=>{
  for(const options of [{prepareFailure:true},{stopFailure:true}]){
    const f=await fixture(t,options);
    await assert.rejects(f.manager.apply({settings:{...f.settings,port:54321},home_action:'existing'}),/仍在运行|存活/);
    assert.ok(!f.calls.includes('relaunch'));assert.ok(f.calls.includes('unlock'));await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});
  }
});

test('get separates configured source from verified service and explicitly reports offline facts',async t=>{
  const live=await fixture(t),result=await live.manager.get();assert.equal(result.actual_service.version,'fixture');assert.equal(result.actual_service.plugin_root,live.source);assert.equal(result.actual_service_error,null);
  const offline=await fixture(t,{offline:true}),unavailable=await offline.manager.get();assert.equal(unavailable.actual_service,null);assert.match(unavailable.actual_service_error,/connection refused/);
});

test('saved launch settings load before userData, explicit CLI takes priority, malformed files remain errors',async t=>{
  const f=await fixture(t);await save(f.config.launchSettingsPath,{...f.settings,home:f.target,source:'custom',python:'stored-python',port:51234});
  const loaded=core.options([],{launchSettingsPath:f.config.launchSettingsPath});assert.equal(loaded.home,f.target);assert.equal(loaded.backendDir,f.source);assert.equal(loaded.python,'stored-python');assert.equal(loaded.port,51234);assert.equal(loaded.userData,path.join(f.target,'desktop','ui'));
  const overridden=core.options(['--home',f.home,'--port','0','--python','explicit-python'],{launchSettingsPath:f.config.launchSettingsPath});assert.equal(overridden.home,f.home);assert.equal(overridden.python,'explicit-python');assert.equal(overridden.port,0);
  await fs.writeFile(f.config.launchSettingsPath,'broken json');assert.throws(()=>core.options([],{launchSettingsPath:f.config.launchSettingsPath}),/无法读取 Crew 启动设置/);
});

test('isolated launch ignores real preferences and relaunch preserves profile/isolation while replacing old overrides',async t=>{
  const f=await fixture(t);await fs.mkdir(path.dirname(f.config.launchSettingsPath),{recursive:true});await fs.writeFile(f.config.launchSettingsPath,'broken production fixture');
  const config=core.options(['--home',f.home,'--isolated-sources','--profile','test'],{launchSettingsPath:f.config.launchSettingsPath});assert.equal(config.home,f.home);assert.equal(config.launchSettingsPath,path.join(f.home,'desktop','launch-settings.json'));
  const args=relaunchArgs(['app','--home=old','--backend-dir','old-source','--service-url=http://127.0.0.1:1','--port','12','--python','old-python','--profile','test','--isolated-sources','--launch-settings','old.json'],f.config.launchSettingsPath);
  assert.deepEqual(args,['app','--profile','test','--isolated-sources','--launch-settings',f.config.launchSettingsPath]);
});

test('atomic save failure preserves the previous destination without replacing its contents',async t=>{
  const f=await fixture(t),destination=path.join(f.root,'blocked-settings');await fs.mkdir(destination);await fs.writeFile(path.join(destination,'keep.txt'),'existing data');
  await assert.rejects(save(destination,f.settings));assert.equal(await fs.readFile(path.join(destination,'keep.txt'),'utf8'),'existing data');
});

test('real isolated HOME migration repairs missing saved source and Python using the chosen interpreter',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'crew-launch-real-'));t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const home=path.join(root,'old HOME'),target=path.join(root,'新 HOME'),helper=path.resolve(__dirname,'../../plugin'),settingsPath=path.join(root,'preferences','launch-settings.json');
  const env={...process.env,PYTHONPATH:helper,PYTHONIOENCODING:'utf-8'};
  for(const name of Object.keys(env))if(name.toUpperCase().startsWith('CREW_')||['CLAUDE_CODE_SESSION_ID','CODEX_THREAD_ID'].includes(name.toUpperCase()))delete env[name];
  await promisify(execFile)(PYTHON,['-c','from crew import install,store;import sys;install.install(sys.argv[1],sys.argv[2]);store.connect(sys.argv[1]).close()',home,helper],{cwd:helper,env,windowsHide:true});
  await fs.writeFile(path.join(home,'important-note.txt'),'原始历史与文档保留');
  const config={home,backendDir:path.join(root,'missing-custom-source'),helperBackendDir:helper,bundledBackendDir:helper,backendSource:'custom',python:'missing-crew-python',port:0,launchSettingsPath:settingsPath};
  const backend={owned:false,url:'http://127.0.0.1:0',child:null};
  const updates=new CrewUpdates(config,backend);t.after(()=>updates.close());
  let relaunched=0;
  const manager=new LaunchSettings(config,{backend,updates,operations:new OperationsClient(config),bundledBackend:helper,
    request:()=>{throw Error('No service or model may be started in this fixture');},onApplied:()=>{relaunched++;}});
  const result=await manager.apply({settings:{mode:'local',home:target,source:'bundled',backend_directory:helper,service_url:'',port:0,python:PYTHON,remote_backend:''},home_action:'copy'});
  assert.equal(result.ok,true);assert.equal(relaunched,1);assert.equal(readSync(settingsPath).python,PYTHON);assert.equal(readSync(settingsPath).source,'bundled');
  assert.equal(await fs.readFile(path.join(home,'important-note.txt'),'utf8'),'原始历史与文档保留');assert.equal(await fs.readFile(path.join(target,'important-note.txt'),'utf8'),'原始历史与文档保留');
  assert.equal(await fs.readFile(path.join(home,'crew.db')).then(value=>value.subarray(0,15).toString()),'SQLite format 3');
  assert.equal((await new OperationsClient({home:target,backendDir:helper}).request('snapshot')).busy,false);
});

test('launch settings saved before LAN hosts are read with an explicit empty device and saved in the current structure',async t=>{
  const f=await fixture(t),{remote_backend:_device,...legacy}=f.settings;
  await fs.mkdir(path.dirname(f.config.launchSettingsPath),{recursive:true});await fs.writeFile(f.config.launchSettingsPath,JSON.stringify(legacy));
  assert.deepEqual(readSync(f.config.launchSettingsPath),f.settings);
  await save(f.config.launchSettingsPath,readSync(f.config.launchSettingsPath));
  assert.deepEqual(Object.keys(JSON.parse(await fs.readFile(f.config.launchSettingsPath,'utf8'))).sort(),Object.keys(f.settings).sort());
  for(const value of [{...legacy,extra:''},{...f.settings,mode:'remote'},{...f.settings,remote_backend:'saved-host'},{...legacy,remote:''}]){
    await fs.writeFile(f.config.launchSettingsPath,JSON.stringify(value));assert.throws(()=>readSync(f.config.launchSettingsPath),/无法读取 Crew 启动设置/);
  }
  assert.throws(()=>shape({...f.settings,mode:'remote'}),/请选择要连接的局域网设备/);
  assert.equal(shape({...f.settings,mode:'remote',remote_backend:'saved-host'}).remote_backend,'saved-host');
});

test('switching a borrowed window to a LAN host checks only the host and touches no local Python, lock or HOME',async t=>{
  const f=await fixture(t,{borrowed:true,hostVersion:'0.3.99',badCurrentPython:true});
  f.config.backendDir=path.join(f.root,'missing backend');
  const body={settings:{...f.settings,mode:'remote',remote_backend:'saved-host',home:'ignored draft HOME',port:4321},home_action:'existing'};
  const preview=await f.manager.validate(body);
  assert.deepEqual(preview.remote,{id:'saved-host',name:'192.168.1.20:47812',url:'http://192.168.1.20:47812',version:'0.3.99',local_version:'0.0.0-test'});
  assert.equal(preview.settings.home,f.home,'this computer keeps its own HOME for switching back');assert.equal(preview.settings.port,0);assert.equal(preview.settings.python,'missing-python');
  assert.equal(preview.impact.connection_changed,true);assert.equal(preview.impact.running_count,0);assert.equal(preview.service.home,'/Users/host/.crew');
  const result=await f.manager.apply(body);assert.equal(result.restarting,true);
  assert.deepEqual(readSync(f.config.launchSettingsPath),{...f.settings,mode:'remote',remote_backend:'saved-host',python:'missing-python'});
  for(const call of ['python-version','lock','stop','prepare-home','home-inspect','probe-port','/api/admin/health'])assert.ok(!f.calls.includes(call),call);
  assert.deepEqual(f.calls.filter(call=>call.startsWith('remote ')),['remote /api/access/context','remote /api/admin/health','remote /api/access/context','remote /api/admin/health']);
});

test('switching an owned local backend to a LAN host safely stops its sessions before saving the connection',async t=>{
  const f=await fixture(t);
  const result=await f.manager.apply({settings:{...f.settings,mode:'remote',remote_backend:'saved-host'},home_action:'existing'});
  assert.equal(result.restarting,true);assert.equal(result.impact.running_count,1);
  for(const [before,after]of [['lock','/api/admin/connection/prepare'],['/api/admin/connection/prepare','stop'],['stop','stopped'],['stopped','relaunch'],['relaunch','unlock']])assert.ok(f.calls.indexOf(before)<f.calls.indexOf(after),before+' before '+after);
  assert.ok(!f.calls.includes('prepare-home'));assert.equal(readSync(f.config.launchSettingsPath).mode,'remote');
});

test('a LAN host that does not accept this device stops the switch before anything is saved',async t=>{
  for(const options of [{borrowed:true,revoked:true},{borrowed:true,browserClient:true}]){
    const f=await fixture(t,options);
    await assert.rejects(f.manager.apply({settings:{...f.settings,mode:'remote',remote_backend:'saved-host'},home_action:'existing'}),/重新生成配对码|重新配对/);
    await assert.rejects(fs.access(f.config.launchSettingsPath),{code:'ENOENT'});assert.ok(!f.calls.includes('relaunch'));
  }
  const missing=await fixture(t,{borrowed:true});
  await assert.rejects(missing.manager.validate({settings:{...missing.settings,mode:'remote',remote_backend:'forgotten'},home_action:'existing'}),/已保存的局域网设备不存在/);
});

test('a LAN window switches back to this computer without probing the host as a borrowed local service',async t=>{
  const f=await fixture(t,{borrowed:true,targetKind:'crew'});
  Object.assign(f.backend,{remote:true,owned:false,child:null,url:'http://192.168.1.20:47812',startupError:'授权已失效，请在主机上重新生成配对码'});
  f.config.remoteBackend='saved-host';
  const current=await f.manager.get();
  assert.equal(current.settings.mode,'remote');assert.equal(current.settings.remote_backend,'saved-host');assert.equal(current.current.service_url,'');
  assert.equal(current.reconnect_required,true);assert.equal(current.remote_backends[0].id,'saved-host');assert.equal(typeof current.device_name,'string');
  const result=await f.manager.apply({settings:{...current.settings,mode:'local',remote_backend:''},home_action:'existing'});
  assert.equal(result.restarting,true);assert.deepEqual(f.preparedHomes,[{home:f.home,reset_deployment:false}]);
  assert.ok(!f.calls.includes('stop'));assert.ok(!f.calls.includes('/api/admin/connection/plan'));
  assert.equal(f.calls.filter(call=>call==='/api/admin/health').length,1,'only the LAN health read in get(); the host is never treated as a local service');
  assert.deepEqual(readSync(f.config.launchSettingsPath),{...f.settings,mode:'local',remote_backend:''});
});

test('re-pairing a saved host passes only the address, code and device name',async t=>{
  const f=await fixture(t,{borrowed:true});
  const result=await f.manager.pair({url:'192.168.1.20:47812',code:'12345678',device_name:'书房电脑'});
  assert.equal(result.backend.id,'saved-host');assert.deepEqual(result.backend.paired,{url:'192.168.1.20:47812',code:'12345678',device_name:'书房电脑'});
  await assert.rejects(f.manager.pair({url:'192.168.1.20:47812',code:'12345678',device_name:'x',token:'injected'}),/只能包含/);
});

test('an automatically chosen local port is remembered once and only for normal local launches', async t => {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'crew-remember-port-'));t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const config=()=>({home:path.join(root,'home'),backendDir:path.join(root,'backend'),bundledBackendDir:path.join(root,'backend'),backendSource:'bundled',python:'python3',port:0,launchSettingsPath:path.join(root,'prefs','launch-settings.json')});
  const first=config();
  assert.equal(await rememberPort(first,'http://127.0.0.1:53538'),true);
  assert.equal(first.port,53538);
  assert.deepEqual({mode:readSync(first.launchSettingsPath).mode,port:readSync(first.launchSettingsPath).port,python:readSync(first.launchSettingsPath).python},{mode:'local',port:53538,python:'python3'});
  // Already fixed: a later automatic port never overwrites it.
  assert.equal(await rememberPort(config(),'http://127.0.0.1:60000'),false);
  assert.equal(readSync(first.launchSettingsPath).port,53538);
  // An existing local file on automatic keeps its other fields and only gains the port.
  await save(first.launchSettingsPath,shape({...readSync(first.launchSettingsPath),port:0,python:'/opt/homebrew/bin/python3'}));
  assert.equal(await rememberPort(config(),'http://127.0.0.1:61000'),true);
  assert.deepEqual({port:readSync(first.launchSettingsPath).port,python:readSync(first.launchSettingsPath).python},{port:61000,python:'/opt/homebrew/bin/python3'});
  for (const changes of [{isolated:true},{smoke:true},{remoteBackend:'saved-host'},{serviceUrl:'http://127.0.0.1:47811'},{port:47811},{launchSettingsPath:''}]) {
    const other=config(); other.launchSettingsPath=path.join(root,'other-'+Object.keys(changes)[0],'launch-settings.json'); Object.assign(other,changes);
    assert.equal(await rememberPort(other,'http://127.0.0.1:62000'),false,JSON.stringify(changes));
    if (other.launchSettingsPath) assert.equal(readSync(other.launchSettingsPath),null);
  }
});

test('a configured port held by another program fails with a clear instruction before Python starts', async t => {
  const blocker=net.createServer();await new Promise(resolve=>blocker.listen({host:'127.0.0.1',port:0},resolve));t.after(()=>blocker.close());
  const port=blocker.address().port;let spawned=false;
  const backend=new core.Backend({home:await fs.mkdtemp(path.join(os.tmpdir(),'crew-port-busy-')),backendDir:__dirname,python:'python3',port});
  backend.spawn=()=>{spawned=true;throw Error('must not start');};
  await assert.rejects(backend.start(),error=>error.message.includes(`端口 ${port} 已被其他程序占用`)&&error.message.includes('连接与目录'));
  assert.equal(spawned,false);
});
