'use strict';
// Instrument the selected source app, retaining its actual startup and quit
// handlers. All services and Python carrier guards belong to a private HOME.
const fs=require('node:fs'),path=require('node:path');
const {performance}=require('node:perf_hooks');
const electron=require('electron'),{app,ipcMain,webContents}=electron;
const selectedDesktop=app.isPackaged?path.join(process.resourcesPath,'app.asar'):process.env.CREW_PERF_DESKTOP;
const core=require(path.join(selectedDesktop,'src/core.cjs'));
const began=performance.now(),record={events:[],start_calls:[],stop_calls:[],disconnect_calls:[],requests:[],errors:[]};
const mark=name=>{const value=performance.now()-began;record.events.push({name,ms:value,epoch_ms:Date.now()});fs.writeFileSync(process.env.CREW_PERF_OUTPUT+'.progress.json',JSON.stringify(record,null,2));return value;};
const nativeFetch=global.fetch;
global.fetch=async function(input,...args){const at=performance.now();try{return await nativeFetch.call(this,input,...args);}finally{record.requests.push({url:String(input),ms:performance.now()-at});}};
let backend,ready=false,mainWindow,tray,openTray,pulseTimer;
if(process.env.CREW_PERF_STRESS==='1'){
  const Module=require('node:module'),load=Module._load;
  Module._load=function(name,parent,...args){
    const value=load.call(this,name,parent,...args);
    if(name!=='electron'||parent?.filename!==path.join(selectedDesktop,'src/main.cjs'))return value;
    return {...value,Tray:function(...input){tray=new value.Tray(...input);const setMenu=tray.setContextMenu.bind(tray);tray.setContextMenu=menu=>{openTray=menu.items.find(item=>item.label==='打开 Crew')?.click;return setMenu(menu);};return tray;}};
  };
  record.stress={errors:[],pulses:0,after_destroyed:0,hide_restore:[],workspaces:[]};
}
function pulse(afterDestroyed=false){
  if(!record.stress)return;
  record.stress.pulses++;if(afterDestroyed)record.stress.after_destroyed++;
  for(const [name,callback] of [['tray-double-click',()=>tray?.emit('double-click')],['tray-menu-open',()=>openTray?.()],['second-instance',()=>app.emit('second-instance',{},[],process.cwd())]]){
    try{callback();}catch(error){record.stress.errors.push({name,after_destroyed:afterDestroyed,error:error.stack||String(error)});}
  }
}
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
app.commandLine.appendSwitch('disable-features','CalculateNativeWinOcclusion');
const start=core.Backend.prototype.start,stop=core.Backend.prototype.stop,disconnect=core.Backend.prototype.disconnect;
core.Backend.prototype.start=async function(...args){
  backend=this;const at=mark('backend-start');
  const originalSpawn=this.spawn;
  this.spawn=(...input)=>{mark('backend-spawn');const child=originalSpawn(...input);child.once('exit',()=>mark('owned-supervisor-exit'));return child;};
  try{const value=await start.apply(this,args);record.start_calls.push({ms:performance.now()-began-at,owned:value.owned});mark('backend-verified');return value;}
  catch(error){record.errors.push(error.stack||String(error));throw error;}
};
core.Backend.prototype.stop=async function(...args){const at=mark('backend-stop');try{return await stop.apply(this,args);}finally{record.stop_calls.push({ms:performance.now()-began-at,owned:this.owned,exit_code:this.child?.exitCode??null});mark('backend-stop-complete');}};
core.Backend.prototype.disconnect=async function(...args){const at=mark('backend-disconnect');try{return await disconnect.apply(this,args);}finally{record.disconnect_calls.push({ms:performance.now()-began-at,owned:this.owned,detached:this.detached});mark('backend-disconnect-complete');}};
app.whenReady().then(()=>mark('electron-ready'));
app.on('before-quit',()=>mark('before-quit'));
app.on('will-quit',()=>mark('will-quit'));
app.on('browser-window-created',(_event,window)=>{
  mark('window-created');mainWindow=window;
  if(record.stress){
    const nativeFocus=window.focus.bind(window),showInactive=window.showInactive.bind(window);
    window.setOpacity(0);window.show=()=>showInactive();window.focus=()=>{if(window.isDestroyed())nativeFocus();};
    window.on('closed',()=>{for(let index=0;index<12;index++)pulse(true);});
  }else{window.show=()=>{};window.focus=()=>{};}
  window.once('ready-to-show',()=>mark('shell-paint-ready'));
  window.webContents.once('did-finish-load',()=>mark('shell-loaded'));
  window.webContents.on('console-message',event=>{if(event.level==='error')record.errors.push(event.message);});
  ipcMain.removeHandler('desktop:ready');
  ipcMain.handle('desktop:ready',async(_event,info)=>{
    if(ready)return;ready=true;mark('renderer-ready');record.renderer=info;
    try{
      const health=await core.request(backend.url,'/api/admin/health');
      record.health={home:health.home,pid:health.pid,instance_id:health.instance_id,plugin_root:health.plugin_root,version:health.version,ok:health.ok};
      record.desktop_version=app.getVersion();record.packaged=app.isPackaged;
      record.service={url:backend.url,owned:backend.owned};
      let workbench;
      const readyDeadline=Date.now()+10000;
      while(Date.now()<readyDeadline){
        workbench=webContents.getAllWebContents().find(contents=>{try{const url=new URL(contents.getURL());return url.origin===backend.url&&url.pathname==='/'&&!url.searchParams.size;}catch(_){return false;}});
        if(workbench&&!workbench.isLoading()&&await workbench.executeJavaScript('document.body.classList.contains("workbench")&&window.CrewClient.current.mode==="real"&&Boolean(document.querySelector("#main h1"))'))break;
        workbench=null;await new Promise(resolve=>setTimeout(resolve,20));
      }
      if(!workbench)throw Error('Default real service workbench did not become ready');
      const preferences=workbench.getLastWebPreferences();
      if(preferences.preload||preferences.nodeIntegration||!preferences.sandbox||!preferences.contextIsolation)throw Error('Workbench must remain an unprivileged service document');
      record.workbench={url:workbench.getURL(),nodeAvailable:await workbench.executeJavaScript('typeof require!=="undefined"'),bridgeAvailable:await workbench.executeJavaScript('typeof window.crewDesktop!=="undefined"')};
      if(record.workbench.nodeAvailable||record.workbench.bridgeAvailable)throw Error('Service workbench received privileged desktop access');
      mark('workbench-ready');
      if(record.stress){
        const workspaces=await core.projects(backend.url);
        for(const project of workspaces){
          mark('prepare-workspace:'+project.name);
          // These seeded journals deliberately represent legacy history. Upgrade
          // only this private fixture through the real API before opening it,
          // so a native migration-choice dialog cannot stall the benchmark.
          const discovery=await core.request(backend.url,'/api/workspace-data/discover',{root:project.root});
          if(discovery.status!=='ready'){
            mark('upgrade-workspace:'+project.name);
            if(discovery.candidates?.length!==1)throw Error('Expected exactly one private history candidate');
            await core.request(backend.url,'/api/workspace-data/open',{root:project.root,action:'upgrade',candidate_id:discovery.candidates[0].id},120000);
            (record.stress.history_upgrades||=[]).push(project.name);
          }
          mark('open-workspace:'+project.name);
          await window.webContents.executeJavaScript(`window.crewDesktop.openProject(${JSON.stringify(project.name)})`);
          const deadline=Date.now()+10000;
          let found=false;
          while(Date.now()<deadline&&!found){
            for(const contents of webContents.getAllWebContents()){
              if(contents.isDestroyed()||!contents.getURL().startsWith(backend.url))continue;
              const url=new URL(contents.getURL());if(url.searchParams.get('project')!==project.name)continue;
              found=await contents.executeJavaScript('document.body.classList.contains("workbench")&&document.querySelector("#main h1")?.textContent.includes('+JSON.stringify(project.display_name||project.name)+')');if(found)break;
            }
            if(!found)await new Promise(resolve=>setTimeout(resolve,20));
          }
          if(!found)throw Error('Used HOME workspace never rendered: '+project.name);
          record.stress.workspaces.push(project.name);
        }
        mark('workspaces-ready');
        if(!tray||!openTray)throw Error('Real tray and its open callback were not created');
        for(let index=0;index<5;index++){
          const at=performance.now();window.close();
          if(window.isDestroyed()||window.isVisible())throw Error('Closing the private window did not hide it');
          tray.emit('double-click');
          if(!window.isVisible())throw Error('The real tray callback did not restore the private window');
          record.stress.hide_restore.push(performance.now()-at);
        }
        pulseTimer=setInterval(()=>pulse(mainWindow.isDestroyed()),2);
      }
      mark('quit-requested');app.quit();
    }catch(error){record.errors.push(error.stack||String(error));app.quit();}
  });
});
app.on('quit',(_event,code)=>{
  clearInterval(pulseTimer);
  mark('app-quit');record.exit_code=code;record.elapsed_ms=performance.now()-began;
  let serviceAlive=false;
  if(record.health?.pid)try{process.kill(record.health.pid,0);serviceAlive=true;}catch(error){if(error.code!=='ESRCH')throw error;}
  record.owned_service_exited=!backend?.owned||!serviceAlive;
  record.frontend_pipe_bridge=Boolean(backend?.frontendPipe);
  record.exit_handles=process._getActiveHandles().map(handle=>({type:handle.constructor.name,pid:handle.pid,
    ref:handle._handle?.hasRef?.(),destroyed:handle.destroyed}));
  record.child_pipes=backend?.child?{ref:backend.child._handle?.hasRef?.(),stdin:backend.child.stdin.destroyed,
    stdout:backend.child.stdout.destroyed,stderr:backend.child.stderr.destroyed}:null;
  fs.writeFileSync(process.env.CREW_PERF_OUTPUT,JSON.stringify(record,null,2));
});
