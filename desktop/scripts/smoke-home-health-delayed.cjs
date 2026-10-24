'use strict';
// Actual main/core/supervisor, with initialization gated inside a real backend
// after it obtains the HOME lock. An additional one-shot snapshot injection
// checks main's independent HTTP validation of an invalid old URL candidate.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),http=require('node:http'),{spawn}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output/home-health-delayed');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/home-health-delayed-')),backendDir=path.join(home,'bootstrap-shim');await fs.mkdir(backendDir);
    const source=path.resolve(desktop,'../plugin');
    const oldRequests=[],oldServer=http.createServer((request,response)=>{oldRequests.push({at:Date.now(),path:request.url});response.writeHead(404,{'Content-Type':'application/json'});response.end('{"error":"retired fixture endpoint"}');});
    await new Promise(resolve=>oldServer.listen(0,'127.0.0.1',resolve));const oldURL='http://127.0.0.1:'+oldServer.address().port;assert.notEqual(new URL(oldURL).port,'47811');
    const retired=spawn('python',['-c','pass'],{windowsHide:true,stdio:'ignore'});await new Promise((resolve,reject)=>{retired.once('error',reject);retired.once('exit',code=>code===0?resolve():reject(Error('Retired fixture process failed')));});
    const admin=path.join(home,'desktop/admin');await fs.mkdir(admin,{recursive:true});
    await fs.writeFile(path.join(admin,'status.json'),JSON.stringify({instance_id:'retired_fixture_instance',state:'stopped',checked_at:new Date().toISOString(),supervisor_pid:retired.pid,service_pid:retired.pid,url:oldURL,backend_evidence:{service_pid:retired.pid,url:oldURL},incidents:[],restart:{allowed:false,reason:'retired fixture'}}));
    const backendWrapper=path.join(backendDir,'delayed_backend.py');
    await fs.writeFile(backendWrapper,[
      'import sys, time','sys.stdout.reconfigure(encoding="utf-8")','sys.stderr.reconfigure(encoding="utf-8")',
      'sys.path.insert(0, '+JSON.stringify(source)+')','import crew_desktop','original_serve = crew_desktop.serve',
      'def delayed_serve(args, home):','    deadline = time.monotonic() + 45',
      '    while not (home / "release-ready").exists():',
      '        if time.monotonic() >= deadline: raise RuntimeError("QA backend initialization gate timed out")','        time.sleep(0.025)',
      '    return original_serve(args, home)','crew_desktop.serve = delayed_serve','raise SystemExit(crew_desktop.main())',''
    ].join('\n'));
    await fs.writeFile(path.join(backendDir,'crew_supervisor.py'),[
      'import sys, time','sys.stdout.reconfigure(encoding="utf-8")','sys.stderr.reconfigure(encoding="utf-8")',
      'sys.path.insert(0, '+JSON.stringify(source)+')','from crew import supervisor','original_init = supervisor.Supervisor.__init__',
      'def fixture_init(self, *args, **kwargs):',
      '    kwargs.update(command_factory=lambda port: [sys.executable, '+JSON.stringify(backendWrapper)+'], startup_timeout=0.2, check_interval=0.1)',
      '    original_init(self, *args, **kwargs)','supervisor.Supervisor.__init__ = fixture_init',
      'raise SystemExit(supervisor.main())',''
    ].join('\n'));
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--normal','--isolated-sources','--home',home,'--backend-dir',backendDir],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{
      const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));assert.equal(evidence.home,home);assert.equal(code,0);assert.equal(evidence.ok,true);
      await assert.rejects(fs.access(path.join(home,'web.json')));
      for(const pid of [evidence.recovered.supervisor_pid,evidence.recovered.service_pid]){let alive=true;try{process.kill(pid,0);}catch(_){alive=false;}assert.equal(alive,false,'Owned fixture PID must exit: '+pid);}
      assert(oldRequests.length>0,'Main must actually validate the injected old candidate over HTTP');assert(oldRequests.every(request=>request.at>=evidence.injectedCandidateAt),'The natural degraded path must never request the retired URL');evidence.retiredCandidateHTTP=oldRequests;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
      console.log(JSON.stringify({ok:true,checks:evidence.checks.length,ownedProcessesExited:true,timing:evidence.timing,evidence:path.join(output,'evidence.json')}));
    }catch(error){console.error(error);process.exitCode=1;}finally{oldServer.close();}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,webContents}=require('electron'),core=require('../src/core.cjs'),{Administrator}=require('../src/admin.cjs'),evidence={checks:[],errors:[],screenshots:[],timing:{},scope:'normal desktop main with test-only 100ms timeout; actual supervisor launches a backend gated after HOME lock; natural degraded snapshot must keep old URL only in backend_evidence; separate injected old candidate must fail actual HTTP validation; real recovery, homepage and workspace view; no model turn'};
  const began=Date.now(),options=core.options,start=core.Backend.prototype.start,stop=core.Backend.prototype.stop;
  let backendRef,injectOldOnce=false;const originalSnapshot=Administrator.prototype.snapshot;
  Administrator.prototype.snapshot=async function(){const value=await originalSnapshot.call(this);if(injectOldOnce){injectOldOnce=false;return {...value,state:'degraded',url:evidence.initial.backend_evidence.url};}return value;};
  core.options=(...args)=>({...options(...args),startupTimeoutMs:100});
  core.Backend.prototype.start=async function(){backendRef=this;const at=Date.now();try{return await start.call(this);}catch(error){evidence.timing.startRejectedMs=Date.now()-at;evidence.startupError=error.message;evidence.supervisorAtTimeout={pid:this.child?.pid,killed:this.child?.killed,exitCode:this.child?.exitCode};throw error;}};
  core.Backend.prototype.stop=async function(){const at=Date.now();try{return await stop.call(this);}finally{evidence.timing.explicitStopMs=Date.now()-at;}};
  app.once('browser-window-created',(_event,window)=>{
    window.show=()=>{};window.focus=()=>{};
    const wc=window.webContents;wc.setBackgroundThrottling(false);
    wc.once('did-finish-load',async()=>{
      const js=source=>wc.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await pause(70);}throw Error('Timed out: '+label);}
      async function persist(){await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));}
      async function capture(name){await fs.mkdir(output,{recursive:true});await pause(350);await js('(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));})()');const file=path.join(output,name);await fs.writeFile(file,(await wc.capturePage(undefined,{stayHidden:true})).toPNG());evidence.screenshots.push(file);}
      try{
        evidence.home=process.argv[process.argv.indexOf('--home')+1];assert.match(evidence.startupError,/超时/);assert(evidence.timing.startRejectedMs<1500);assert.equal(evidence.supervisorAtTimeout.killed,false);assert.equal(evidence.supervisorAtTimeout.exitCode,null);evidence.checks.push('supervised startup rejects its test deadline promptly without waiting for or killing the gated real child');
        await until(()=>js('!document.querySelector("#admin-page").hidden&&document.querySelector("#service").textContent==="连接失败"'),'offline administrator remains reachable');
        await until(()=>js('document.querySelector("#admin-state").textContent==="服务响应异常"'),'real administrator poll displays degraded while the new backend initializes');
        evidence.initial=await js('window.crewDesktop.adminStatus()');assert.equal(evidence.initial.state,'degraded');
        assert.equal(evidence.initial.supervisor_pid,evidence.supervisorAtTimeout.pid);assert(Number.isInteger(evidence.initial.service_pid));assert.notEqual(evidence.initial.service_pid,evidence.initial.backend_evidence.service_pid);assert.equal(evidence.initial.url,null);assert(evidence.initial.backend_evidence.url);assert.equal(new URL(backendRef.url).port,'0');assert.match(backendRef.startupError,/超时/);
        const initialBootstrapError=await js('window.crewDesktop.bootstrap().then(()=>null,error=>error.message)');assert.match(initialBootstrapError,/超时/);assert(await js('document.querySelector("#admin-restart").disabled'));evidence.timing.adminReachedMs=Date.now()-began;evidence.checks.push('the real supervisor has a new live backend PID and degraded state, publishes current URL null while retaining old evidence Y, and the actual pane remains reachable without clearing startupError');
        await js('document.querySelector(".admin-details").open=true');await capture('01-degraded-new-pid-no-current-url.png');
        evidence.injectedCandidateAt=Date.now();injectOldOnce=true;evidence.rejectedCandidate=await js('window.crewDesktop.adminStatus()');assert.equal(evidence.rejectedCandidate.url,evidence.initial.backend_evidence.url);assert(evidence.rejectedCandidate.incidents.some(item=>item.message.includes('后台尚不能读取工作区')));assert.equal(new URL(backendRef.url).port,'0');assert.match(backendRef.startupError,/超时/);
        assert.match(await js('window.crewDesktop.bootstrap().then(()=>null,error=>error.message)'),/超时/);assert(await js('!document.querySelector("#admin-page").hidden'));evidence.checks.push('a separate one-shot old-Y candidate is actually rejected over HTTP; main preserves the offline origin and startupError and still serves the administrator snapshot');
        await fs.writeFile(path.join(evidence.home,'release-ready'),'ready');
        await until(()=>js('document.querySelector("#admin-state").textContent==="运行正常"'),'administrator poll observes the real recovered backend');evidence.recovered=await js('window.crewDesktop.adminStatus()');assert.equal(evidence.recovered.state,'healthy');
        assert.equal(evidence.recovered.supervisor_pid,evidence.initial.supervisor_pid);assert.equal(evidence.recovered.service_pid,evidence.initial.service_pid);assert.notEqual(evidence.recovered.url,evidence.initial.backend_evidence.url);assert.notEqual(new URL(evidence.recovered.url).port,'47811');assert.equal(backendRef.startupError,null);assert.equal(backendRef.url,evidence.recovered.url);evidence.timing.healthyReachedMs=Date.now()-began;evidence.checks.push('releasing real backend initialization yields a verified healthy new URL Z distinct from Y under the same PIDs; only then does main clear startupError and reconnect the shell');
        await capture('02-healthy-verified-new-url.png');
        const parent=path.join(evidence.home,'workspaces');await fs.mkdir(parent);const project=await core.newWorkspace(parent,'延迟恢复项目',evidence.recovered.url);evidence.project=project.name;
        await js('document.querySelector("#nav-workspaces").click();document.querySelector("#refresh").click()');
        await until(()=>js('document.querySelector("#service").textContent==="已连接"&&document.querySelector("#projects .project strong")?.textContent==="延迟恢复项目"'),'homepage real refresh after recovery');evidence.checks.push('the existing homepage refresh reaches the recovered service and renders its actual registered workspace');
        await js('document.querySelector("#projects .project").click()');let child;
        await until(async()=>{child=webContents.getAllWebContents().find(candidate=>{try{return new URL(candidate.getURL()).origin===evidence.recovered.url;}catch(_){return false;}});return child&&!child.isLoading()&&await child.executeJavaScript('document.querySelector("#sidebar")?.textContent.includes("延迟恢复项目")');},'actual recovered workspace view');
        evidence.workspaceURL=child.getURL();assert.equal(new URL(evidence.workspaceURL).searchParams.get('project'),project.name);assert.equal(await child.executeJavaScript('typeof window.crewDesktop'),'undefined');
        assert(await js('document.querySelector("[data-project]")?.getAttribute("aria-selected")==="true"'));evidence.checks.push('opening the recovered workspace creates a real isolated WebContentsView at the new origin with its matching project and no desktop bridge');
        evidence.ok=true;evidence.timing.assertionsFinishedMs=Date.now()-began;await persist();
        app.once('will-quit',()=>{evidence.timing.willQuitMs=Date.now()-began;require('node:fs').writeFileSync(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));});app.quit();
      }catch(error){evidence.errors.push(error.stack||String(error));if(evidence.home)await fs.writeFile(path.join(evidence.home,'release-ready'),'cleanup');await persist();console.error(error);process.exitCode=1;app.quit();}
    });
  });
}
