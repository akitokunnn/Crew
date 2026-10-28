'use strict';
// Source-only cold/warm/borrowed lifecycle benchmark, plus the portable backend
// materialization stage measured separately. Never uses an installed Crew HOME.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
const {performance}=require('node:perf_hooks');
const current=path.resolve(__dirname,'..');
const options=Object.fromEntries(process.argv.slice(2).map(value=>{const index=value.indexOf('=');return[value.slice(2,index),value.slice(index+1)];}));
const desktop=path.resolve(options.desktop||current),core=require(path.join(desktop,'src/core.cjs'));
const {Administrator}=require(path.join(desktop,'src/admin.cjs'));
const output=path.resolve(options.output||path.join(current,'.cache','lifecycle-performance-'+(options.label||'current')+'.json'));
const cleanEnvironment=()=>{const env={...process.env,PYTHONIOENCODING:'utf-8'};for(const name of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','ELECTRON_RUN_AS_NODE'])delete env[name];return env;};
const round=value=>Math.round(value*10)/10;
function included(source,item){const relative=path.relative(source,item),parts=relative.split(path.sep);return parts[0]!=='tests'&&!parts.includes('__pycache__')&&!relative.endsWith('.pyc')&&!parts.some(name=>name.includes('-test-'));}
async function tracedMaterialize(source,home){
  const original={readFile:fs.readFile,readdir:fs.readdir,cp:fs.cp},io={reads:0,read_bytes:0,read_ms:0,directories:0,directory_ms:0,copy_ms:0};
  fs.readFile=async function(file,...args){const at=performance.now();try{const value=await original.readFile.call(this,file,...args);if(path.relative(source,String(file)).split(path.sep)[0]!=='..'){io.reads++;io.read_bytes+=value.length;}return value;}finally{io.read_ms+=performance.now()-at;}};
  fs.readdir=async function(...args){const at=performance.now();try{io.directories++;return await original.readdir.apply(this,args);}finally{io.directory_ms+=performance.now()-at;}};
  fs.cp=async function(...args){const at=performance.now();try{return await original.cp.apply(this,args);}finally{io.copy_ms+=performance.now()-at;}};
  const at=performance.now();
  try{const retained=await core.materializeBackend(source,home);return{ms:round(performance.now()-at),retained,io:Object.fromEntries(Object.entries(io).map(([key,value])=>[key,round(value)]))};}
  finally{Object.assign(fs,original);}
}
async function waitForExit(child,timeout=60000){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{child.kill();reject(Error('Isolated lifecycle benchmark timed out; child PID '+child.pid));},timeout);child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('exit',(code,signal)=>{clearTimeout(timer);resolve({code,signal});});});}
async function stopPrivateService(home){
  const administrator=new Administrator(home),state=await administrator.snapshot();
  if(['stopped','unmanaged'].includes(state.state))return;
  await administrator.request('shutdown');
  const deadline=Date.now()+20000;
  while(Date.now()<deadline){
    const value=await administrator.snapshot();
    if(value.state==='stopped'){
      let alive=true;try{process.kill(value.service_pid,0);}catch(error){if(error.code==='ESRCH')alive=false;else throw error;}
      if(!alive)return;
    }
    await new Promise(resolve=>setTimeout(resolve,30));
  }
  throw Error('Private lifecycle backend failed to stop: '+home);
}
async function main(){
  await fs.mkdir(path.dirname(output),{recursive:true});
  const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'crew-lifecycle-perf-'));
  const plugin=path.resolve(desktop,'../plugin'),source=path.join(temporary,'extracted-backend'),retainedHome=path.join(temporary,'materialized-home');
  await fs.cp(plugin,source,{recursive:true,filter:item=>included(plugin,item)});
  const record={label:options.label||'current',desktop,version:JSON.parse(await fs.readFile(path.join(desktop,'package.json'),'utf8')).version,temporary,
    scope:'Actual Electron source app, private HOME, forbidden native/model factories. Cold means empty HOME, not an OS disk-cache flush.',materialize:[],runs:[]};
  record.materialize.push({kind:'cold-target',...await tracedMaterialize(source,retainedHome)});
  for(let index=0;index<3;index++)record.materialize.push({kind:'warm-target',...await tracedMaterialize(source,retainedHome)});
  const guard=path.join(temporary,'carrier-guard');await fs.mkdir(guard);
  await fs.writeFile(path.join(guard,'sitecustomize.py'),`from pathlib import Path
from crew import chat
_init=chat.Manager.__init__
def guarded(self,home,plugin_root,agents_provider,process_factory=None):
 with (Path(home)/'model-factories-guarded.txt').open('a',encoding='utf-8') as stream:stream.write('Manager\\n')
 def forbidden(*args,**kwargs):
  with (Path(home)/'model-start-attempts.txt').open('a',encoding='utf-8') as stream:stream.write('forbidden native start\\n')
  raise OSError('Lifecycle benchmark forbids native/model turns')
 _init(self,home,plugin_root,agents_provider,forbidden)
chat.Manager.__init__=guarded
`,'utf8');
  const environment=()=>({...cleanEnvironment(),PYTHONPATH:guard+path.delimiter+plugin,CREW_PERF_DESKTOP:desktop});
  const home=path.join(temporary,'ui-home');
  const kinds=['cold-home','warm-home','borrowed-service','normal-quit','used-home-tray-stress'];
  if(options.only)assert(kinds.includes(options.only),'Unknown lifecycle scenario: '+options.only);
  for(const kind of kinds.filter(value=>!options.only||value===options.only)){
    const selectedHome=kind==='used-home-tray-stress'?path.join(temporary,'used-home'):home;
    const file=path.join(temporary,kind+'.json'),env={...environment(),CREW_PERF_OUTPUT:file,...(kind==='used-home-tray-stress'?{CREW_PERF_STRESS:'1'}:{})};
    const guardedBefore=await fs.readFile(path.join(selectedHome,'model-factories-guarded.txt'),'utf8').catch(error=>{if(error.code==='ENOENT')return '';throw error;});
    if(kind==='used-home-tray-stress'){
      const seed=`import sys
from contextlib import closing
from pathlib import Path
from crew import board,chat,goals,runtimes,store
home=Path(sys.argv[1])
with closing(store.connect(home)) as conn:
 for project_index in range(3):
  root=home.parent/('workspace-'+str(project_index));root.mkdir()
  name='used-workspace-'+str(project_index);board.init_project(conn,name,root)
  for leader_index in range(3):
   group='leader-'+str(project_index)+'-'+str(leader_index)
   for person_index in range(4):
    sid=group+'-'+str(person_index);role='leader' if person_index==0 else 'member';label=group if person_index==0 else sid
    board.join(conn,sid,name,label,root,root,None,task='Historical fixture work, never a new model request',role=role,group_name=None if person_index==0 else group)
    runtimes.set(conn,sid,'claude' if person_index%2==0 else 'codex')
    journal=chat.Journal(home/'chat'/(sid+'.jsonl'))
    for turn in range(12):
     journal.message(sid+'-input-'+str(turn),'Historical input '+str(turn)+' '+('existing evidence '*16))
     journal.emit('assistant',id=sid+'-reply-'+str(turn),content=[dict(type='text',text='Historical fixture reply '+('verified evidence '*16))])
    journal.set_status('offline');board.set_status(conn,sid,'offline')
    if person_index==0:
     for round_index in range(2):
      goal=goals.create(conn,name,sid,'Historical goal '+str(round_index),'Preserve previous evidence; no automatic work.',['No native turn'])
      execution=goals.create_execution(conn,goal['id'],sid,runtime='claude')
      goals.set_execution_status(conn,execution['id'],'paused');goals.request_pause(conn,goal['id']);goals.confirm_paused(conn,goal['id'])
      history=chat.Journal(home/'chat'/(execution['id']+'.jsonl'))
      history.message('history-input','Saved goal history')
      history.emit('assistant',id='history-reply',content=[dict(type='text',text='Saved work remains paused')]);history.set_status('offline')
 conn.execute('UPDATE leader_notices SET sent_at=created_at')
`;
      const seeded=spawnSync('python',['-',selectedHome],{input:seed,cwd:path.dirname(desktop),env,encoding:'utf8',windowsHide:true});
      assert.equal(seeded.status,0,seeded.stderr||String(seeded.error));
      record.used_fixture={workspaces:3,leaders:9,members:27,goals:18,general_journals:36,goal_journals:18};
    }
    let owner;
    if(kind==='borrowed-service'){
      const {spawn:nativeSpawn}=require('node:child_process');
      owner=new core.Backend({home,backendDir:plugin,python:'python',port:0,isolated:true,smoke:true},{spawn:(command,args,settings)=>nativeSpawn(command,args,{...settings,env:{...settings.env,...environment()}})});
      await owner.start();
    }
    try{
      const detached=['normal-quit','used-home-tray-stress'].includes(kind);
      const args=['--require',path.join(__dirname,'benchmark-lifecycle-hook.cjs'),desktop,...(detached?[]:['--smoke-test']),'--home',selectedHome,'--port','0','--smoke-output',path.join(temporary,'unused.json'),'--isolated-sources'];
      if(owner)args.push('--service-url',owner.url);
      const at=performance.now(),launchEpoch=Date.now(),child=spawn(require('electron'),args,{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
      let errors='';child.stderr.on('data',chunk=>{errors=(errors+chunk).slice(-20000);});child.stdout.resume();
      const ended=await waitForExit(child),exitEpoch=Date.now(),wall=performance.now()-at;
      const value=JSON.parse(await fs.readFile(file,'utf8'));
      assert.equal(ended.code,0,errors);assert.equal(value.health.home,selectedHome);assert(value.health.ok);assert(value.renderer&&!value.renderer.nodeAvailable);
      assert.equal(value.owned_service_exited,!detached);assert.deepEqual(value.errors,[]);
      let serviceAlive=true;try{process.kill(value.health.pid,0);}catch(error){if(error.code==='ESRCH')serviceAlive=false;else throw error;}
      assert.equal(serviceAlive,Boolean(owner)||detached,'Actual backend PID liveness must match the requested quit behavior');
      if(detached){
        assert.equal(value.stop_calls.length,0);assert.equal(value.disconnect_calls.length,1);
        const reconnected=new core.Backend({home:selectedHome,backendDir:plugin,python:'python',port:0,isolated:true},
          {spawn(){throw Error('Reopening must reuse the detached backend');}});
        assert.deepEqual(await reconnected.start(),{url:value.service.url,owned:false});
        const health=await core.request(reconnected.url,'/api/admin/health');
        assert.equal(health.pid,value.health.pid);assert.equal(health.instance_id,value.health.instance_id);
        value.reconnected_same_service=true;
      }
      await assert.rejects(fs.access(path.join(selectedHome,'model-start-attempts.txt')),{code:'ENOENT'});
      const guardedAfter=await fs.readFile(path.join(selectedHome,'model-factories-guarded.txt'),'utf8');
      assert(guardedAfter.length>guardedBefore.length,'The selected backend must actually install its forbidden model factory');
      const quit=value.events.find(item=>item.name==='quit-requested'),paint=value.events.find(item=>item.name==='shell-paint-ready');
      const summary={kind,wall_ms:round(wall),renderer_ready_ms:value.events.find(item=>item.name==='renderer-ready').epoch_ms-launchEpoch,shell_paint_ms:paint?paint.epoch_ms-launchEpoch:null,
        true_quit_ms:exitEpoch-quit.epoch_ms,backend_start_ms:round(value.start_calls[0].ms),backend_stop_ms:value.stop_calls.map(item=>round(item.ms)),owned:value.service.owned,service_alive_after_exit:serviceAlive,
        model_factories_guarded:(guardedAfter.slice(guardedBefore.length).match(/Manager/g)||[]).length,
        ...(value.stress?{stress_errors:value.stress.errors.length,late_callback_pulses:value.stress.after_destroyed,workspaces_ready_ms:value.events.find(item=>item.name==='workspaces-ready').epoch_ms-launchEpoch}:{}),details:value};
      record.runs.push(summary);
      if(owner)assert(Array.isArray(await core.projects(owner.url)));
      else if(!detached)await assert.rejects(fs.access(path.join(selectedHome,'web.json')),{code:'ENOENT'});
      console.log(JSON.stringify({kind,...Object.fromEntries(Object.entries(summary).filter(([key])=>key!=='details'))}));
    }finally{if(owner)await owner.stop();else if(['normal-quit','used-home-tray-stress'].includes(kind))await stopPrivateService(selectedHome);}
  }
  record.model_turns_started=0;await fs.writeFile(output,JSON.stringify(record,null,2));
  console.log(JSON.stringify({output,version:record.version,materialize:record.materialize.map(item=>({kind:item.kind,ms:item.ms,io:item.io})),runs:record.runs.map(({details,...item})=>item)}));
  if(options.label!=='before')assert(record.runs.every(item=>!item.stress_errors),'Queued tray/second-instance callbacks failed; see performance evidence');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
