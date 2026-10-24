'use strict';
// Actual supervisor + service + trusted desktop IPC, always in a new isolated home.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output/home-health-live');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/home-health-live-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--smoke-test','--isolated-sources','--home',home,'--smoke-output',path.join(output,'unused.json')],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{
      const result=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));assert.equal(result.home,home);assert.equal(code,0);assert.equal(result.ok,true);
      try{await fs.access(path.join(home,'web.json'));throw Error('Owned service registry remains');}catch(error){if(error.code!=='ENOENT')throw error;}
      for(const pid of [result.snapshot.supervisor_pid,result.snapshot.service_pid]){let alive=true;try{process.kill(pid,0);}catch(_){alive=false;}assert.equal(alive,false,'Owned fixture process must exit: '+pid);}
      console.log(JSON.stringify({ok:true,checks:result.checks.length,ownedProcessesExited:true,evidence:path.join(output,'evidence.json')}));
    }catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,ipcMain}=require('electron'),evidence={checks:[],errors:[],screenshots:[],scope:'real isolated supervisor, backend, desktop main, trusted preload and UI; detection only, no model turn or recovery request'};
  app.once('browser-window-created',(_event,window)=>{
    const wc=window.webContents;wc.setBackgroundThrottling(false);ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>{});
    wc.once('did-finish-load',async()=>{
      const js=source=>wc.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await pause(80);}throw Error('Timed out: '+label);}
      try{
        await fs.mkdir(output,{recursive:true});evidence.home=process.argv[process.argv.indexOf('--home')+1];
        await until(()=>js('Boolean(window.crewDesktop)&&document.querySelector("#service").textContent==="已连接"'),'desktop bootstrap');
        let snapshot;await until(async()=>{snapshot=await js('window.crewDesktop.adminStatus()');return snapshot.state==='healthy';},'actual supervisor health');
        assert(Number.isInteger(snapshot.supervisor_pid)&&Number.isInteger(snapshot.service_pid));assert.notEqual(snapshot.supervisor_pid,snapshot.service_pid);assert.notEqual(new URL(snapshot.url).port,'47811');assert.equal(snapshot.restart.allowed,false);evidence.snapshot=snapshot;evidence.checks.push('the actual home-owned supervisor reports a distinct healthy backend and protects it from recovery');
        await js('document.querySelector(".crew-chrome-admin").click()');await until(()=>js('!document.querySelector("#admin-page").hidden&&document.querySelector("#admin-state").textContent==="运行正常"'),'global administrator entry');
        assert(await js('document.querySelector("#admin-restart").disabled'));evidence.checks.push('the real global chrome command opens a pane with safe recovery disabled for the live service');
        const receipt=await js('window.crewDesktop.adminCheck()');assert.equal(receipt.ok,true);assert.equal(receipt.status,'requested');evidence.receipt=receipt;
        await until(async()=>{const value=await js('window.crewDesktop.adminStatus()');if(value.last_request_id!==receipt.request_id)return false;assert(!value.last_request_error,value.last_request_error);evidence.afterCheck=value;return true;},'actual check receipt processed');evidence.checks.push('a detection receipt travels through the real control file and is confirmed by the supervisor request ID');
        await js('document.querySelector("#admin-check").click()');await until(()=>js('document.querySelector("#admin-note").textContent.includes("已请求检测")||document.querySelector("#admin-note").textContent.includes("检测请求已处理")'),'UI detection acknowledgement');
        await pause(350);await js('(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));})()');const screenshot=path.join(output,'01-real-supervisor.png');await fs.writeFile(screenshot,(await wc.capturePage(undefined,{stayHidden:true})).toPNG());evidence.screenshots.push(screenshot);evidence.checks.push('the real pane shows current health and request acknowledgement without launching a model');
        evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
      }catch(error){evidence.errors.push(error.stack||String(error));await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
    });
  });
}
