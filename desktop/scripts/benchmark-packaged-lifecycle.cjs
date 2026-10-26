'use strict';
// Compare the same release in unpacked and portable form. Every launch has a
// private HOME and extraction directory; this never connects an installed HOME.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process');
const {performance}=require('node:perf_hooks');
const desktop=path.resolve(__dirname,'..');
const options=Object.fromEntries(process.argv.slice(2).map(value=>{const at=value.indexOf('=');return[value.slice(2,at),value.slice(at+1)];}));
const round=value=>Math.round(value*10)/10;
async function main(){
  const version=JSON.parse(await fs.readFile(path.join(desktop,'package.json'),'utf8')).version;
  const output=path.resolve(options.output||path.join(desktop,'.cache','packaged-lifecycle-'+version+'.json'));
  const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'crew-packaged-perf-'));
  const guard=path.join(temporary,'guard');await fs.mkdir(guard);
  // Defer patching until the extracted package imports chat. No source checkout
  // is placed on PYTHONPATH, so both binaries must use their bundled backend.
  await fs.writeFile(path.join(guard,'sitecustomize.py'),`import builtins,sys
from pathlib import Path
original_import=builtins.__import__
def guarded_import(*args,**kwargs):
 result=original_import(*args,**kwargs)
 module=sys.modules.get('crew.chat')
 if module is not None and hasattr(module,'Manager') and not getattr(module.Manager,'_benchmark_guard',False):
  original_init=module.Manager.__init__
  def guarded_init(self,home,plugin_root,agents_provider,process_factory=None):
   with (Path(home)/'model-factories-guarded.txt').open('a',encoding='utf-8') as stream:stream.write('Manager\\n')
   def forbidden(*args,**kwargs):
    with (Path(home)/'model-start-attempts.txt').open('a',encoding='utf-8') as stream:stream.write('forbidden native start\\n')
    raise OSError('Packaged lifecycle benchmark forbids native/model turns')
   original_init(self,home,plugin_root,agents_provider,forbidden)
  module.Manager.__init__=guarded_init
  module.Manager._benchmark_guard=True
 return result
builtins.__import__=guarded_import
`,'utf8');
  const record={version,temporary,scope:'Private HOME, real packaged binaries, forbidden model factories. Cold HOME does not imply an OS disk-cache flush. pre_electron_ms includes SFX extraction and native process creation.',runs:[]};
  for(const [kind,executable] of [['unpacked',options.unpacked||path.join(desktop,'dist','win-unpacked','Crew.exe')],['portable',options.portable||path.join(desktop,'dist','Crew-'+version+'-portable.exe')]]){
    await fs.access(executable);
    const home=path.join(temporary,kind+'-home');
    for(const pass of ['cold-home','warm-home']){
      const guardedBefore=await fs.readFile(path.join(home,'model-factories-guarded.txt'),'utf8').catch(error=>{if(error.code==='ENOENT')return '';throw error;});
      const evidence=path.join(temporary,kind+'-'+pass+'.json'),extract=path.join(temporary,kind+'-'+pass+'-extract');await fs.mkdir(extract);
      const env={...process.env,TEMP:extract,TMP:extract,PYTHONPATH:guard,PYTHONIOENCODING:'utf-8'};
      for(const name of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[name];
      const at=performance.now(),launched=Date.now();
      const child=spawn(path.resolve(executable),['--smoke-test','--isolated-sources','--home',home,'--port','0','--smoke-output',evidence],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
      let stderr='';child.stdout.resume();child.stderr.on('data',part=>{stderr=(stderr+part).slice(-20000);});
      const code=await new Promise((resolve,reject)=>{
        const timeout=setTimeout(()=>{child.kill();reject(Error('Private packaged benchmark timed out: '+kind+' '+pass+' '+stderr));},120000);
        child.once('error',error=>{clearTimeout(timeout);reject(error);});child.once('exit',value=>{clearTimeout(timeout);resolve(value);});
      });
      assert.equal(code,0,stderr);
      const result=JSON.parse(await fs.readFile(evidence,'utf8')),timing=result.timing;
      assert.equal(result.desktopVersion,version,'Compare actual app versions, not artifact filenames');
      assert.equal(result.home,home);assert(result.ownedServiceExited&&result.service.owned&&result.renderer&&!result.renderer.nodeAvailable);
      assert(timing&&Number.isFinite(timing.process_created_at)&&Number.isFinite(timing.renderer_ready_at),'Packaged smoke must expose real Electron creation and renderer-ready epochs');
      assert(timing.process_created_at>=launched-20&&timing.renderer_ready_at>=timing.process_created_at);
      await assert.rejects(fs.access(path.join(home,'web.json')),{code:'ENOENT'});
      await assert.rejects(fs.access(path.join(home,'model-start-attempts.txt')),{code:'ENOENT'});
      const guardedAfter=await fs.readFile(path.join(home,'model-factories-guarded.txt'),'utf8');
      assert(guardedAfter.length>guardedBefore.length,'The bundled backend must actually install its forbidden model factory');
      const sample={kind,pass,executable:path.resolve(executable),evidence,outer_to_renderer_ms:timing.renderer_ready_at-launched,
        pre_electron_ms:timing.process_created_at-launched,electron_to_renderer_ms:timing.renderer_ready_at-timing.process_created_at,
        whole_smoke_ms:round(performance.now()-at),backend_owned:result.service.owned,model_factories_guarded:(guardedAfter.slice(guardedBefore.length).match(/Manager/g)||[]).length};
      record.runs.push(sample);console.log(JSON.stringify(sample));
    }
  }
  record.model_turns_started=0;await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(record,null,2));
  console.log(JSON.stringify({output,version,runs:record.runs}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
