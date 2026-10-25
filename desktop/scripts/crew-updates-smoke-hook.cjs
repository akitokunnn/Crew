'use strict';
// Test hook: real Electron UI, IPC, update lock, database backup, stop and restart.
// Claude plugin inspection is a declared fixture; no real CLI config is touched.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {execFile}=require('node:child_process'),{promisify}=require('node:util');
const run=promisify(execFile),{app,ipcMain}=require('electron');
const desktop=path.resolve(__dirname,'..'),output=path.join(desktop,'smoke-output','crew-updates');
const core=require('../src/core.cjs'),{CrewUpdates}=require('../src/crew-updates.cjs');
CrewUpdates.prototype._online=async function(){this.state.online={release:null,main:{version:'0.2.5',sha:'a'.repeat(40),url:'https://github.com/akitokunnn/Crew/tree/'+ 'a'.repeat(40)},main_comparison:-1,release_comparison:null,checked_at:new Date().toISOString()};this.state.online_error=null;this.state.online_checking=false;this._emit();};
const original=CrewUpdates.prototype._helper;
CrewUpdates.prototype._helper=async function(operation,args=[]){
  if(operation!=='inspect')return original.call(this,operation,args);
  const source='import json,sys;sys.path.insert(0,sys.argv[1]);from crew import updater;print(json.dumps({"ok":True,"operation":"inspect","target":updater.target_info(sys.argv[1]),"cli":updater.cli_entry(sys.argv[2]),"plugin":{"state":"not_installed"}}))';
  const result=await run(this.config.python,['-c',source,this.config.backendDir,this.config.home],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  return JSON.parse(result.stdout);
};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.once('browser-window-created',(_event,win)=>{
  ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>{});
  win.webContents.once('did-finish-load',async()=>{
    const evidence={home:core.options(process.argv.slice(1)).home,plugin:'fixture: not installed; no Claude CLI/config access',modelTurnsStarted:0};
    const js=source=>win.webContents.executeJavaScript(source,true);
    async function until(predicate,label){for(let i=0;i<400;i++){if(await predicate())return;await pause(50);}throw new Error('等待超时：'+label);}
    try{
      await fs.mkdir(output,{recursive:true});
      await until(()=>js('typeof crewDesktop!=="undefined" && !!document.querySelector("#crew-update-check")'),'bridge');
      const bootstrap=await js('crewDesktop.bootstrap()');
      const parent=path.join(evidence.home,'workspaces');await fs.mkdir(parent);
      const project=await core.newWorkspace(parent,'更新试用',bootstrap.service.url);
      await js('crewDesktop.openProject('+JSON.stringify(project.name)+')');
      await js('crewDesktop.crewUpdateCheck()');
      evidence.before=await js('crewDesktop.crewUpdateSnapshot().then(s=>s.backend)');
      assert.equal(await js('document.querySelector("#crew-main-version").textContent'),'0.2.5');
      assert.equal(await js('document.querySelector("#crew-release-version").textContent'),'尚无正式发布包');
      evidence.online={main:'0.2.5',release:null,fixture:true};
      const old=path.join(evidence.home,'old-cli');await fs.mkdir(path.join(old,'.claude-plugin'),{recursive:true});
      await fs.writeFile(path.join(old,'.claude-plugin','plugin.json'),JSON.stringify({version:'0.0.0'}));
      await fs.writeFile(path.join(evidence.home,'runtime.json'),JSON.stringify({plugin_root:old,python:'python',updated_at:'fixture'}));
      await js('document.querySelector(".crew-chrome-brand").click();document.querySelector("#nav-tools").click();document.querySelector("#crew-update-check").click()');
      await until(()=>js('!document.querySelector("#crew-update-apply").disabled'),'update button enabled');
      const tabBefore=await js('document.querySelectorAll(".crew-tab").length');
      await js('document.querySelector("#crew-update-apply").click()');
      await until(()=>js('crewDesktop.crewUpdateSnapshot().then(s=>["done","error"].includes(s.phase))'),'update completion');
      const state=await js('crewDesktop.crewUpdateSnapshot()');assert.equal(state.phase,'done',JSON.stringify(state));
      await until(()=>js('document.querySelector("#crew-update-phase").textContent==="已更新" && !document.querySelector("#crew-update-check").disabled'),'success shown in UI');
      evidence.after=state.backend;evidence.backup=state.backup;
      assert.equal(evidence.before.port,evidence.after.port);assert.notEqual(evidence.before.instance_id,evidence.after.instance_id);
      assert.equal(await js('document.querySelectorAll(".crew-tab").length'),tabBefore);
      assert.equal((await core.projects(bootstrap.service.url))[0].name,project.name);
      await fs.access(state.backup.path);
      await js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await fs.writeFile(path.join(output,'crew-updates.png'),(await win.webContents.capturePage()).toPNG());
      evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
    }catch(error){evidence.error=error.stack;await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
  });
});
