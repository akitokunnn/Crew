'use strict';
// Exercises actual main/core and a gated real supervisor. Only the fixture's
// initial supervisor bootstrap waits; no IPC, HTTP or backend implementation is mocked.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output/home-health-delayed');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/home-health-delayed-')),backendDir=path.join(home,'bootstrap-shim');await fs.mkdir(backendDir);
    const source=path.resolve(desktop,'../plugin');
    await fs.writeFile(path.join(backendDir,'crew_supervisor.py'),[
      'import sys, time','sys.stdout.reconfigure(encoding="utf-8")','sys.stderr.reconfigure(encoding="utf-8")',
      'sys.path.insert(0, '+JSON.stringify(source)+')','from crew import supervisor','original = supervisor.Supervisor._bootstrap',
      'def delayed_bootstrap(self):','    deadline = time.monotonic() + 45',
      '    while not (self.home / "release-ready").exists() and not self._stop.is_set():',
      '        if time.monotonic() >= deadline: raise supervisor.SupervisorError("QA gate timed out")','        time.sleep(0.025)',
      '    if not self._stop.is_set(): return original(self)','supervisor.Supervisor._bootstrap = delayed_bootstrap',
      'raise SystemExit(supervisor.main())',''
    ].join('\n'));
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--normal','--isolated-sources','--home',home,'--backend-dir',backendDir],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{
      const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));assert.equal(evidence.home,home);assert.equal(code,0);assert.equal(evidence.ok,true);
      await assert.rejects(fs.access(path.join(home,'web.json')));
      for(const pid of [evidence.recovered.supervisor_pid,evidence.recovered.service_pid]){let alive=true;try{process.kill(pid,0);}catch(_){alive=false;}assert.equal(alive,false,'Owned fixture PID must exit: '+pid);}
      console.log(JSON.stringify({ok:true,checks:evidence.checks.length,ownedProcessesExited:true,timing:evidence.timing,evidence:path.join(output,'evidence.json')}));
    }catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,webContents}=require('electron'),core=require('../src/core.cjs'),evidence={checks:[],errors:[],timing:{},scope:'normal desktop main with test-only 100ms timeout; gated real supervisor, isolated port0 backend, trusted administrator, homepage refresh and actual workspace view; no model turn'};
  const began=Date.now(),options=core.options,start=core.Backend.prototype.start,stop=core.Backend.prototype.stop;
  core.options=(...args)=>({...options(...args),startupTimeoutMs:100});
  core.Backend.prototype.start=async function(){const at=Date.now();try{return await start.call(this);}catch(error){evidence.timing.startRejectedMs=Date.now()-at;evidence.startupError=error.message;evidence.supervisorAtTimeout={pid:this.child?.pid,killed:this.child?.killed,exitCode:this.child?.exitCode};throw error;}};
  core.Backend.prototype.stop=async function(){const at=Date.now();try{return await stop.call(this);}finally{evidence.timing.explicitStopMs=Date.now()-at;}};
  app.once('browser-window-created',(_event,window)=>{
    window.show=()=>{};window.focus=()=>{};
    const wc=window.webContents;wc.setBackgroundThrottling(false);
    wc.once('did-finish-load',async()=>{
      const js=source=>wc.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await pause(70);}throw Error('Timed out: '+label);}
      async function persist(){await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));}
      try{
        evidence.home=process.argv[process.argv.indexOf('--home')+1];assert.match(evidence.startupError,/超时/);assert(evidence.timing.startRejectedMs<1500);assert.equal(evidence.supervisorAtTimeout.killed,false);assert.equal(evidence.supervisorAtTimeout.exitCode,null);evidence.checks.push('supervised startup rejects its test deadline promptly without waiting for or killing the gated real child');
        await until(()=>js('!document.querySelector("#admin-page").hidden&&document.querySelector("#service").textContent==="连接失败"'),'offline administrator remains reachable');
        await until(async()=>{evidence.initial=await js('window.crewDesktop.adminStatus()');return evidence.initial.state==='starting';},'live starting supervisor snapshot');
        assert.equal(evidence.initial.supervisor_pid,evidence.supervisorAtTimeout.pid);assert.equal(evidence.initial.service_pid,null);assert(await js('document.querySelector("#admin-restart").disabled'));evidence.timing.adminReachedMs=Date.now()-began;evidence.checks.push('actual normal main opens the administrator while workspace bootstrap fails and the still-starting supervisor cannot be force-restarted');
        await fs.writeFile(path.join(evidence.home,'release-ready'),'ready');
        await until(()=>js('document.querySelector("#admin-state").textContent==="运行正常"'),'administrator poll observes the real recovered backend');evidence.recovered=await js('window.crewDesktop.adminStatus()');assert.equal(evidence.recovered.state,'healthy');
        assert.equal(evidence.recovered.supervisor_pid,evidence.initial.supervisor_pid);assert(Number.isInteger(evidence.recovered.service_pid));assert.notEqual(new URL(evidence.recovered.url).port,'47811');evidence.timing.healthyReachedMs=Date.now()-began;evidence.checks.push('releasing the fixture gate starts the real backend under the same supervisor and the trusted status handler reconnects the empty shell origin');
        const parent=path.join(evidence.home,'workspaces');await fs.mkdir(parent);const project=await core.newWorkspace(parent,'延迟恢复项目',evidence.recovered.url);evidence.project=project.name;
        await js('document.querySelector("#nav-workspaces").click();document.querySelector("#refresh").click()');
        await until(()=>js('document.querySelector("#service").textContent==="已连接"&&document.querySelector("#projects .project strong")?.textContent==="延迟恢复项目"'),'homepage real refresh after recovery');evidence.checks.push('the existing homepage refresh reaches the recovered service and renders its actual registered workspace');
        await js('document.querySelector("#projects .project").click()');let child;
        await until(async()=>{child=webContents.getAllWebContents().find(candidate=>{try{return new URL(candidate.getURL()).origin===evidence.recovered.url;}catch(_){return false;}});return child&&!child.isLoading()&&await child.executeJavaScript('document.querySelector("#sidebar")?.textContent.includes("延迟恢复项目")');},'actual recovered workspace view');
        evidence.workspaceURL=child.getURL();assert.equal(new URL(evidence.workspaceURL).searchParams.get('project'),project.name);assert.equal(await child.executeJavaScript('typeof window.crewDesktop'),'undefined');
        assert(await js('document.querySelector("[data-project]")?.getAttribute("aria-selected")==="true"'));evidence.checks.push('opening the recovered workspace creates a real isolated WebContentsView at the new origin with its matching project and no desktop bridge');
        evidence.ok=true;evidence.timing.assertionsFinishedMs=Date.now()-began;await persist();
        app.once('will-quit',()=>{evidence.timing.willQuitMs=Date.now()-began;require('node:fs').writeFileSync(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));});app.quit();
      }catch(error){evidence.errors.push(error.stack||String(error));await persist();console.error(error);process.exitCode=1;app.quit();}
    });
  });
}
