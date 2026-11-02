'use strict';
// Compare the same release in unpacked, portable and extracted ZIP form. Every launch has a
// private HOME and extraction directory; this never connects an installed HOME.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
const {createHash}=require('node:crypto');
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
  const record={version,temporary,scope:'Private HOME, real packaged binaries, forbidden model factories. Cold HOME does not imply an OS disk-cache flush. pre_electron_ms includes SFX extraction and native process creation. ZIP extraction is measured once, separately from both daily launches.',runs:[]};
  const unpacked=path.resolve(options.unpacked||path.join(desktop,'dist','win-unpacked','Crew.exe'));
  const variants=[['unpacked',unpacked],['portable',options.portable||path.join(desktop,'dist','Crew-'+version+'-portable.exe')]];
  const archive=path.resolve(options.zip||path.join(desktop,'dist','Crew-'+version+'-win-x64.zip'));
  const zipExists=await fs.access(archive).then(()=>true,error=>{if(error.code==='ENOENT'&&!options.zip)return false;throw error;});
  if(zipExists){
    const destination=path.join(temporary,'zip-application'),at=performance.now();
    const extractionEnv={...process.env,PYTHONPATH:guard,PYTHONIOENCODING:'utf-8'};
    for(const name of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete extractionEnv[name];
    const extraction=spawnSync('python',['-',archive,destination,temporary],{input:`import json,shutil,stat,sys
from pathlib import Path,PurePosixPath
from zipfile import ZipFile
archive=Path(sys.argv[1]).resolve();destination=Path(sys.argv[2]).resolve();private=Path(sys.argv[3]).resolve()
if destination.parent!=private or not private.name.startswith('crew-packaged-perf-'):
 raise ValueError('ZIP extraction target must be this benchmark private directory')
destination.mkdir()
with ZipFile(archive) as package:
 members=package.infolist()
 for member in members:
  relative=PurePosixPath(member.filename.replace('\\\\','/'))
  if relative.is_absolute() or '..' in relative.parts or any(':' in part for part in relative.parts) or stat.S_ISLNK(member.external_attr>>16):
   raise ValueError('Unsafe ZIP entry: '+member.filename)
  target=(destination/Path(*relative.parts)).resolve()
  if not target.is_relative_to(destination):raise ValueError('ZIP entry escaped the private directory')
  if member.is_dir():target.mkdir(parents=True,exist_ok=True)
  else:
   target.parent.mkdir(parents=True,exist_ok=True)
   with package.open(member) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
 print(json.dumps(dict(file_count=sum(not member.is_dir() for member in members),uncompressed_bytes=sum(member.file_size for member in members))))
`,encoding:'utf8',windowsHide:true,timeout:60000,env:extractionEnv});
    assert.equal(extraction.status,0,extraction.stderr||String(extraction.error));
    const extractionMs=round(performance.now()-at),validated=[];
    for(const relative of ['resources/app.asar','resources/backend/crew_desktop.py','resources/backend/crew_supervisor.py','resources/backend/crew/chat.py','resources/backend/crew/channel_digest.py']){
      const source=await fs.readFile(path.join(path.dirname(unpacked),relative)),extracted=await fs.readFile(path.join(destination,relative));
      const hash=value=>createHash('sha256').update(value).digest('hex');
      assert(source.length>0);assert.equal(hash(extracted),hash(source),'ZIP resource must equal the final unpacked build: '+relative);
      if(relative.startsWith('resources/backend/'))assert.equal(hash(source),hash(await fs.readFile(path.join(desktop,'../plugin',relative.slice('resources/backend/'.length)))),'The final backend bundle must match current source: '+relative);
      validated.push({path:relative,bytes:extracted.length,sha256:hash(extracted)});
    }
    record.zip={archive,destination,compressed_bytes:(await fs.stat(archive)).size,extraction_ms:extractionMs,...JSON.parse(extraction.stdout),validated_resources:validated};
    variants.push(['zip-extracted',path.join(destination,'Crew.exe')]);
    console.log(JSON.stringify({kind:'zip-one-time-extraction',...record.zip}));
  }
  for(const [kind,executable] of variants){
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
  console.log(JSON.stringify({output,version,...(record.zip?{zip:record.zip}:{}),runs:record.runs}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
