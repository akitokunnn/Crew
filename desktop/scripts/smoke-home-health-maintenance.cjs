'use strict';
// Actual normal-mode main and maintenance guard, isolated HOME only. No backend
// mocks: the harness suppresses window.show/focus so it cannot disturb the user.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output/home-health-maintenance');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/home-health-maintenance-'));
    const admin=path.join(home,'desktop/admin');await fs.mkdir(admin,{recursive:true});
    await fs.writeFile(path.join(admin,'maintenance.json'),JSON.stringify({at:new Date().toISOString(),reason:'隔离验收：等待维护完成'}));
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--normal','--isolated-sources','--home',home],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{
      const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));assert.equal(evidence.home,home);assert.equal(code,0);assert.equal(evidence.ok,true);
      assert.equal(evidence.startupError.exitCode,78);assert.equal(evidence.rawStatus.service_pid,null);assert.equal(evidence.rawStatus.state,'paused');
      let alive=true;try{process.kill(evidence.rawStatus.supervisor_pid,0);}catch(_){alive=false;}assert.equal(alive,false,'Maintenance supervisor must have exited');
      await assert.rejects(fs.access(path.join(home,'crew.db')));await assert.rejects(fs.access(path.join(home,'web.json')));assert.deepEqual(evidence.networkRequests,[]);
      console.log(JSON.stringify({ok:true,checks:evidence.checks.length,maintenanceExit:78,noBackend:true,evidence:path.join(output,'evidence.json')}));
    }catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app}=require('electron'),core=require('../src/core.cjs'),evidence={checks:[],errors:[],screenshots:[],networkRequests:[],scope:'actual normal desktop main, real supervisor exit78 guard, trusted file-backed administrator UI; hidden test window, isolated maintenance home without registry, no model or backend start'};
  const originalFetch=global.fetch;global.fetch=(url,...args)=>{evidence.networkRequests.push(String(url));return originalFetch(url,...args);};
  const originalStart=core.Backend.prototype.start;
  core.Backend.prototype.start=async function(){assert(!this.config.smoke,'Must exercise the actual normal startup failure path');try{return await originalStart.call(this);}catch(error){evidence.startupError={message:error.message,exitCode:error.exitCode};throw error;}};
  app.once('browser-window-created',(_event,window)=>{
    window.show=()=>{evidence.showAttempts=(evidence.showAttempts||0)+1;};window.focus=()=>{};
    const wc=window.webContents;wc.setBackgroundThrottling(false);
    wc.once('did-finish-load',async()=>{
      const js=source=>wc.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await pause(80);}throw Error('Timed out: '+label);}
      try{
        await fs.mkdir(output,{recursive:true});evidence.home=process.argv[process.argv.indexOf('--home')+1];
        await until(()=>js('Boolean(window.crewDesktop)&&!document.querySelector("#admin-page").hidden&&document.querySelector("#admin-state").textContent==="维护中 / 已暂停"'),'administrator opens despite failed workspace bootstrap');
        assert.equal(evidence.startupError.exitCode,78);assert.equal(window.isVisible(),false);assert(evidence.showAttempts>0);evidence.checks.push('the actual normal-mode startup receives supervisor exit78 but still creates the hidden desktop shell');
        evidence.snapshot=await js('window.crewDesktop.adminStatus()');assert.equal(evidence.snapshot.state,'paused');assert.equal(evidence.snapshot.restart.allowed,false);assert.equal(evidence.snapshot.service_pid,null);
        assert(await js('document.querySelector("#admin-restart").disabled'));assert.match(await js('document.querySelector("#admin-restart-reason").textContent'),/暂停/);evidence.checks.push('the native pending admin action bypasses rejected bootstrap and renders maintenance with recovery disabled');
        await until(()=>js('document.querySelector("#service").textContent==="连接失败"'),'real unavailable workspace bootstrap');assert.deepEqual(evidence.networkRequests,[]);evidence.checks.push('the real workspace bootstrap rejects offline before making any network request, while the independent file-backed administrator remains readable');
        evidence.rawStatus=JSON.parse(await fs.readFile(path.join(evidence.home,'desktop/admin/status.json'),'utf8'));assert.equal(evidence.rawStatus.state,'paused');assert.equal(evidence.rawStatus.service_pid,null);evidence.checks.push('the actual supervisor writes paused state without a backend PID or database');
        const refusal=await js('window.crewDesktop.adminRestart().then(()=>null,error=>error.message)');assert.match(refusal,/暂停/);evidence.checks.push('the trusted recovery IPC independently refuses maintenance recovery');
        await pause(350);await js('(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));})()');
        const screenshot=path.join(output,'01-maintenance-offline-admin.png');await fs.writeFile(screenshot,(await wc.capturePage(undefined,{stayHidden:true})).toPNG());evidence.screenshots.push(screenshot);
        evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
      }catch(error){evidence.errors.push(error.stack||String(error));await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
    });
  });
}
