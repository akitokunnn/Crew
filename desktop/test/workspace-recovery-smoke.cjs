'use strict';
// Real shell + isolated backend. PATH has no AI CLIs; no model turn can start.
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const desktop=path.resolve(__dirname,'..'),core=require('../src/core.cjs');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(os.tmpdir(),'crew-recovery-'));
    const python=spawnSync('python',['-c','import sys;print(sys.executable)'],{encoding:'utf8',windowsHide:true});assert.equal(python.status,0,python.stderr);
    const env={...process.env,PATH:home,CREW_RECOVERY_HOME:home};for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
    const child=spawn(require('electron'),['--require',__filename,desktop,'--smoke-test','--home',home,'--smoke-output',path.join(home,'unused.json'),'--python',python.stdout.trim(),'--isolated-sources'],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
    const timeout=setTimeout(()=>child.kill(),60000);child.on('exit',async code=>{clearTimeout(timeout);process.exitCode=code===null?1:code;try{await fs.access(path.join(home,'web.json'));throw Error('isolated backend did not exit');}catch(error){if(error.code!=='ENOENT'){console.error(error);process.exitCode=1;}}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,ipcMain,dialog,webContents}=require('electron');
  app.on('web-contents-created',(_event,contents)=>contents.setBackgroundThrottling(false));
  app.once('browser-window-created',(_event,win)=>{
    const main=win.webContents;main.setBackgroundThrottling(false);
    ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>({ok:true}));
    main.once('did-finish-load',async()=>{
      const run=source=>main.executeJavaScript(source,true),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(check,label){const end=Date.now()+12000;while(Date.now()<end){if(await check())return;await pause(40);}throw Error('Timed out: '+label);}
      const click=selector=>run(`document.querySelector(${JSON.stringify(selector)}).click()`);
      let originalPicker,view;
      try{
        await until(()=>run('document.querySelector("#service").textContent==="已连接"'),'home');
        const initial=await run('window.crewDesktop.bootstrap()'),url=initial.service.url,home=process.env.CREW_RECOVERY_HOME;
        const parent=path.join(home,'workspaces');await fs.mkdir(parent);
        const project=await core.newWorkspace(parent,'Original folder',url);await fs.mkdir(path.join(project.root,'src'));
        const python=process.argv[process.argv.indexOf('--python')+1];
        const source=`import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom crew import board,store,chat\nhome=Path(sys.argv[2]);root=Path(sys.argv[3])\nconn=store.connect(home)\nboard.join(conn,'recovery-leader','Original folder','leader',root,None,None,role='leader')\nboard.join(conn,'recovery-member','Original folder','member',root/'src',None,None,group_name='leader')\nchat.Journal(home/'chat'/'recovery-leader.jsonl').emit('message',id='history',role='user',text='Preserved history')\nconn.close()\n`;
        const seeded=spawnSync(python,['-',path.resolve(desktop,'../plugin'),home,project.root],{input:source,encoding:'utf8',windowsHide:true});assert.equal(seeded.status,0,seeded.stderr);
        main.__testBackend=url;
        await run(`window.crewDesktop.openProject(${JSON.stringify(project.name)})`);

        await until(()=>{view=webContents.getAllWebContents().find(wc=>wc.getURL().includes('?project='+encodeURIComponent(project.name)));return view;},'project view');
        await until(()=>view.executeJavaScript('Boolean(document.querySelector("[data-act=leader][data-id=recovery-leader]"))'),'leader');
        await view.executeJavaScript('document.querySelector("[data-act=leader][data-id=recovery-leader]").click()');
        await until(()=>view.executeJavaScript('Boolean(document.querySelector(".composer textarea"))'),'composer');
        const origin=await view.executeJavaScript('(()=>{const input=document.querySelector(".composer textarea");input.value="Unsent draft";input.dispatchEvent(new Event("input",{bubbles:true}));return performance.timeOrigin;})()');
        const before=await core.request(url,'/api/project/settings?project='+encodeURIComponent(project.name));
        await core.request(url,'/api/project/update',{project:project.name,display_name:'Renamed display',root:project.root,revision:before.revision});
        await until(()=>run('[...document.querySelectorAll("#crew-desktop-tabs [role=tab]")].some(node=>node.textContent.includes("Renamed display"))'),'native tab name');
        assert.equal(await view.executeJavaScript('document.querySelector(".proj").textContent'),'Renamed display');
        assert.equal(await view.executeJavaScript('document.querySelector(".composer textarea").value'),'Unsent draft');assert.equal(await view.executeJavaScript('performance.timeOrigin'),origin);
        await click('#crew-desktop-tabs [data-tab-id=home]');
        await until(()=>run('document.querySelector("#crew-desktop-tabs [data-tab-id=home]").getAttribute("aria-selected")=="true"'),'home tab selected');
        const renamed=path.join(parent,'Moved folder');await fs.rename(project.root,renamed);
        await click('#refresh');await until(()=>run('!document.querySelector("#project-locations").hidden'),'missing directory notice');
        assert.equal((await run('window.crewDesktop.bootstrap()')).projects[0].directory_state.status,'missing');
        originalPicker=dialog.showOpenDialog;dialog.showOpenDialog=async()=>({canceled:false,filePaths:[renamed]});
        await click('#existing');await until(()=>run('Boolean(document.querySelector(".workspace-recovery[open]"))'),'relink choice');
        assert.equal(await run('document.querySelector(".workspace-recovery .recovery-actions button:last-child").disabled'),true);
        await run(`(()=>{const select=document.querySelector('.workspace-recovery select');select.value=${JSON.stringify(project.name)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
        await click('.workspace-recovery .recovery-actions button:last-child');
        await until(()=>run('Boolean(document.querySelector(".workspace-settings-dialog[open] [name=root]"))'),'prefilled settings');
        assert.equal(await run('document.querySelector(".workspace-settings-dialog [name=root]").value'),renamed);
        assert.equal(await run('document.querySelector(".workspace-settings-dialog").checkVisibility()'),true,'relink settings must be visible above the workspace');
        await click('.workspace-settings-dialog button[type=submit]');await click('[data-workspace-action=confirm]');
        await until(()=>run('document.querySelector("[data-workspace-note]").textContent.includes("已停止并迁移")'),'migration receipt');
        const updated=await core.request(url,'/api/project/settings?project='+encodeURIComponent(project.name));
        assert.equal(updated.project.name,project.name);assert.equal(updated.project.display_name,'Renamed display');assert.equal(updated.sessions.length,2);
        for(const session of updated.sessions){assert.equal(session.running,false);assert.equal(session.busy,false);assert(session.cwd.toLowerCase().startsWith(renamed.toLowerCase()));}
        assert.equal((await core.projects(url)).length,1);
        assert.match(await fs.readFile(path.join(home,'chat','recovery-leader.jsonl'),'utf8'),/Preserved history/);
        assert.equal(await run('document.querySelectorAll(".workspace-recovery").length'),0);
        console.log('PASS: external display rename syncs native tab/page without reload or draft loss; external folder rename offers relink; existing leader/member/history preserved; no duplicate or running model.');
        dialog.showOpenDialog=originalPicker;app.quit();
      }catch(error){console.error(error);console.error(JSON.stringify({backend:main.__testBackend?await core.request(main.__testBackend,'/api/state?project=Original%20folder').then(state=>state.project):null,native:await run('document.querySelector("#crew-desktop-tabs").textContent'),page:view&&!view.isDestroyed()?await view.executeJavaScript('({title:document.title,brand:document.querySelector(".proj")?.textContent})'):null}));if(originalPicker)dialog.showOpenDialog=originalPicker;app.once('will-quit',()=>app.exit(1));app.quit();}
    });
  });
}
