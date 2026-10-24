'use strict';
// Real desktop home/preload rendering with simulated supervisor IPC snapshots.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output/home-health');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/home-health-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--health-qa-home='+home],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{const result=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));assert.equal(result.home,home);assert.equal(code,0);assert.equal(result.ok,true);console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(output,'evidence.json')}));}catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  require.cache[require.resolve(path.join(desktop,'src/main.cjs'))]={id:path.join(desktop,'src/main.cjs'),filename:path.join(desktop,'src/main.cjs'),loaded:true,exports:{}};
  const {app,BrowserWindow,ipcMain}=require('electron'),{pathToFileURL}=require('node:url'),chrome=require('../src/chrome.cjs');
  const home=process.argv.find(value=>value.startsWith('--health-qa-home=')).slice('--health-qa-home='.length),page=pathToFileURL(path.join(desktop,'src/home.html')).href;
  require('node:fs').mkdirSync(path.join(home,'electron'),{recursive:true});app.setPath('userData',path.join(home,'electron'));
  const evidence={home,checks:[],errors:[],screenshots:[],scope:'real home HTML/CSS/JS and real sandbox preload; supervisor snapshots and control IPC are simulated; no real backend or model started'};
  let window,statusReads=0,restarts=0,failCheck=false,finishBootstrap;
  let status={state:'healthy',checked_at:new Date().toISOString(),supervisor_pid:123,service_pid:456,url:'http://127.0.0.1:9999',recoveries:2,last_recovery_at:'2026-10-24T06:50:00Z',incidents:[{at:'2026-10-24T06:49:00Z',level:'warning',message:'检测到后台退出，已重新启动。'},{at:'2026-10-24T06:50:00Z',level:'info',message:'后台连接已恢复，所有工作区可继续连接。'}],restart:{allowed:false,reason:'后台正在运行，不会强制重启。'},instance_id:'isolated-visual-qa'};
  const tabs={tabs:[{id:'home',name:'工作区',project:null}],activeId:'home'};
  ipcMain.handle('desktop:bootstrap',()=>new Promise(resolve=>{finishBootstrap=resolve;}));
  ipcMain.handle('desktop:tools-snapshot',()=>({checking:false,tools:[],autoUpdate:false}));ipcMain.handle('desktop:ready',()=>{});ipcMain.handle('desktop:home-ready',()=>{window.webContents.send('desktop:home-action',{action:'admin'});});ipcMain.on('desktop:tabs-layout',()=>{});
  ipcMain.handle('desktop:chrome-ready',async()=>{await window.webContents.insertCSS(await fs.readFile(path.join(desktop,'src/chrome.css'),'utf8'),{cssOrigin:'author'});return{focused:true,maximized:false,tabs};});
  ipcMain.handle('desktop:chrome-action',(_event,command)=>{if(command==='admin')window.webContents.send('desktop:home-action',{action:'admin'});return true;});
  ipcMain.handle('desktop:admin-status',()=>{statusReads++;return status;});ipcMain.handle('desktop:admin-check',()=>{if(failCheck)throw Error('模拟：检测暂不可用');return {ok:true,status:'requested'};});
  ipcMain.handle('desktop:admin-restart',()=>{if(!status.restart.allowed)throw Error(status.restart.reason);restarts++;status={...status,state:'recovering',restart:{allowed:false,reason:'正在恢复，请稍候。'}};return{ok:true};});
  app.whenReady().then(async()=>{
    try{
      await fs.mkdir(output,{recursive:true});window=new BrowserWindow({width:1220,height:850,show:false,...chrome.windowOptions(),webPreferences:{preload:path.join(desktop,'src/preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false,additionalArguments:['--crew-desktop-config='+encodeURIComponent(JSON.stringify({home:page,origin:status.url,error:'file:///never'}))]}});
      const wc=window.webContents,js=source=>wc.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function wait(source,label){const end=Date.now()+12000;while(Date.now()<end){if(await js(source))return;await pause(40);}throw Error('Timed out: '+label);}
      const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
      async function check(label,source){assert(await js(source),label);evidence.checks.push(label);}
      async function capture(name){await pause(300);const file=path.join(output,name+'.png');await fs.writeFile(file,(await wc.capturePage(undefined,{stayHidden:true})).toPNG());evidence.screenshots.push(file);}
      wc.on('console-message',event=>{if(event.level==='error')evidence.errors.push(event.message);});await window.loadURL(page);
      await wait('!document.querySelector("#admin-page").hidden&&document.querySelector("#admin-state").textContent==="运行正常"','admin independent home-ready');
      await check('administrator opens through the native handshake even when workspace bootstrap is still pending','document.querySelector("#service").textContent==="正在连接…"');
      finishBootstrap({projects:[],service:{url:status.url,owned:false},pendingAction:null});await wait('document.querySelector("#service").textContent==="已连接"','workspace bootstrap later completes');
      await check('one global administrator is available above workspaces and in the persistent title bar','document.querySelector("aside nav").firstElementChild.id==="nav-admin"&&Boolean(document.querySelector("#crew-desktop-titlebar .crew-chrome-admin"))&&document.querySelector("#admin-recoveries").textContent==="2"');
      await check('a healthy live service cannot be restarted and the reason is readable','document.querySelector("#admin-restart").disabled&&document.querySelector("#admin-restart-reason").textContent.includes("不会强制重启")');await capture('01-global-admin-healthy');
      const before=statusReads;await click('#nav-workspaces');await pause(5300);assert.equal(statusReads,before);evidence.checks.push('leaving the administrator pane cancels its five-second status polling');
      await click('.crew-chrome-admin');await wait('!document.querySelector("#admin-page").hidden','global chrome action');
      status={...status,state:'stopped',service_pid:null,restart:{allowed:true,reason:'已确认后台退出，可以安全恢复。'}};await click('#admin-check');await wait('!document.querySelector("#admin-restart").disabled','safe restart allowed');
      await check('only the backend permission enables safe recovery','document.querySelector("#admin-state").textContent==="后台已停止"&&document.querySelector("#admin-restart-reason").textContent.includes("已确认后台退出")');await capture('02-safe-recovery');
      await click('#admin-restart');await wait('document.querySelector("#admin-state").textContent==="正在自动恢复"','recovery actual state');assert.equal(restarts,1);evidence.checks.push('explicit safe recovery shows the subsequent recovering snapshot instead of claiming completion');
      failCheck=true;await click('#admin-check');await wait('!document.querySelector("#admin-error").hidden','check error');
      await check('a failed check remains visible and retryable while recovery stays guarded','document.querySelector("#admin-state").textContent==="检测失败"&&!document.querySelector("#admin-check").disabled&&document.querySelector("#admin-restart").disabled');await capture('03-detection-error');
      failCheck=false;await click('#admin-check');await wait('document.querySelector("#admin-error").hidden','check recovery');evidence.checks.push('manual detection retry clears an earlier transport error');
      assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
    }catch(error){evidence.errors.push(error.stack||String(error));await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
  });
}
